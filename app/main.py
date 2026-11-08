# app/main.py  —  pip install -r requirements.txt
import asyncio
import contextlib
import json
import os
import pathlib
import uuid

from dotenv import load_dotenv

load_dotenv()  # must run before any app-local import that reads env vars at module load time (embed.py, session.py)

from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app.db.session import close_pools, get_app_pool, get_connection
from app.middleware.auth import TenantAuthMiddleware
from app.middleware.rate_limit import RedisRateLimitMiddleware
from app.models import Ticket
from app.realtime import connections
from app.realtime.pubsub import subscribe_forever
from app.tasks.process_ticket import process_ticket
from app.telemetry.tracing import get_tracer, log_event, new_request_id, setup_tracing

setup_tracing("api")
tracer = get_tracer()

HEARTBEAT_INTERVAL = 30  # seconds between pings while a ticket is still pending


def _resolve_request_tenant(request: Request, header_tenant: str | None = None) -> str:
    """Resolve authenticated tenant_id from request state (set by TenantAuthMiddleware)
    or explicitly provided header. Rejects requests lacking tenant authentication."""
    tenant_id = getattr(request.state, "tenant_id", None) or header_tenant
    if not tenant_id:
        raise HTTPException(
            status_code=401,
            detail="Missing tenant credentials (provide X-API-Key, Bearer token, or X-Tenant-ID)",
        )
    return tenant_id


async def _dispatch_update(payload: dict) -> None:
    await connections.dispatch_local(payload["ticket_id"], payload)


def _tenant_exists(cur, tenant_id: str) -> bool:
    # tenants has no RLS (it's the global tenant directory, not per-tenant data), so
    # this is a plain lookup regardless of which tenant the connection's SET LOCAL used.
    cur.execute("SELECT 1 FROM tenants WHERE slug = %s", (tenant_id,))
    return cur.fetchone() is not None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # One background subscriber per instance, started at startup — this is what makes
    # a worker's publish() reach every instance, not just the one that happened to
    # enqueue the ticket. See app/realtime/pubsub.py for why this exists.
    subscriber = asyncio.create_task(subscribe_forever(_dispatch_update))
    yield
    subscriber.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await subscriber
    close_pools()


app = FastAPI(lifespan=lifespan)
app.add_middleware(RedisRateLimitMiddleware)
app.add_middleware(TenantAuthMiddleware)


@app.get("/healthz")
def healthz():
    """Liveness probe: verifies process is running and responding."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness probe: validates database connection pool and Redis reachability."""
    checks = {"database": "ok", "redis": "ok"}
    status_code = 200

    try:
        pool = get_app_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except Exception as exc:
        checks["database"] = f"unhealthy: {exc}"
        status_code = 503

    try:
        from app.realtime.pubsub import _ssl_kwargs
        import redis.asyncio as aioredis

        client = aioredis.from_url(os.environ["REDIS_URL"], **_ssl_kwargs())
        await client.ping()
        await client.aclose()
    except Exception as exc:
        checks["redis"] = f"unhealthy: {exc}"
        status_code = 503

    return Response(
        content=json.dumps(checks),
        status_code=status_code,
        media_type="application/json",
    )


@app.post("/tickets")
def handle_ticket(
    ticket: Ticket,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    tenant_id = _resolve_request_tenant(request, tenant_id)
    request_id = request_id or new_request_id()
    idempotency_key = idempotency_key or str(uuid.uuid4())
    response.headers["X-Request-ID"] = request_id

    with tracer.start_as_current_span("handle_ticket") as span:
        span.set_attribute("request_id", request_id)
        span.set_attribute("tenant_id", tenant_id)
        log_event("ticket_received", request_id, tenant_id=tenant_id)

        with get_connection(tenant_id) as conn:
            with conn.cursor() as cur:
                if not _tenant_exists(cur, tenant_id):
                    raise HTTPException(status_code=400, detail=f"unknown tenant: {tenant_id!r}")

                cur.execute(
                    "SELECT id, status, resolution FROM tickets WHERE tenant_id = %s AND idempotency_key = %s",
                    (tenant_id, idempotency_key),
                )
                existing = cur.fetchone()

                if existing:
                    # Same idempotency key seen before — a repeat webhook delivery.
                    # Return the original result instead of re-queuing the work.
                    response.status_code = 200
                    return {"ticket_id": existing[0], "status": existing[1], "resolution": existing[2]}

                cur.execute(
                    """
                    INSERT INTO tickets (tenant_id, idempotency_key, subject, body)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (tenant_id, idempotency_key, ticket.subject, ticket.body),
                )
                ticket_id = cur.fetchone()[0]

        span.set_attribute("ticket_id", ticket_id)
        log_event("ticket_enqueued", request_id, ticket_id=ticket_id, tenant_id=tenant_id)
        process_ticket.delay(ticket_id, tenant_id, request_id)

    response.status_code = 202
    return {"ticket_id": ticket_id, "status": "pending", "request_id": request_id}


@app.get("/tickets")
def list_tickets(
    request: Request,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    status: str | None = None,
    limit: int = 50,
):
    tenant_id = _resolve_request_tenant(request, tenant_id)
    with get_connection(tenant_id) as conn:
        with conn.cursor() as cur:
            if not _tenant_exists(cur, tenant_id):
                raise HTTPException(status_code=400, detail=f"unknown tenant: {tenant_id!r}")

            if status:
                cur.execute(
                    """
                    SELECT id, subject, body, status, resolution, created_at
                    FROM tickets
                    WHERE tenant_id = %s AND status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, status, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, subject, body, status, resolution, created_at
                    FROM tickets
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, limit),
                )
            rows = cur.fetchall()

    return [
        {
            "ticket_id": row[0],
            "subject": row[1],
            "body": row[2],
            "status": row[3],
            "resolution": row[4],
            "created_at": row[5].isoformat(),
        }
        for row in rows
    ]


@app.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: int,
    request: Request,
    tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
):
    tenant_id = _resolve_request_tenant(request, tenant_id)
    with get_connection(tenant_id) as conn:
        with conn.cursor() as cur:
            if not _tenant_exists(cur, tenant_id):
                raise HTTPException(status_code=400, detail=f"unknown tenant: {tenant_id!r}")

            cur.execute(
                "SELECT status, resolution FROM tickets WHERE id = %s AND tenant_id = %s",
                (ticket_id, tenant_id),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    return {"ticket_id": ticket_id, "status": row[0], "resolution": row[1]}


@app.websocket("/ws/tickets/{ticket_id}")
async def watch_ticket(websocket: WebSocket, ticket_id: int):
    # Browsers can't set custom headers on a WebSocket handshake (the JS WebSocket
    # constructor has no header API), so the browser UI passes ?tenant_id=... instead.
    # The header path stays first so scripts/watch_ticket.py keeps working unchanged.
    tenant_id = (
        websocket.headers.get("x-tenant-id")
        or websocket.query_params.get("tenant_id")
    )
    if not tenant_id:
        await websocket.accept()
        await websocket.close(code=4401, reason="missing tenant credentials")
        return
    await websocket.accept()

    with get_connection(tenant_id) as conn:
        with conn.cursor() as cur:
            if not _tenant_exists(cur, tenant_id):
                await websocket.close(code=4400, reason="unknown tenant")
                return

            cur.execute(
                "SELECT status, resolution FROM tickets WHERE id = %s AND tenant_id = %s",
                (ticket_id, tenant_id),
            )
            row = cur.fetchone()

    if row is None:
        await websocket.close(code=4404, reason="ticket not found")
        return

    queue: asyncio.Queue = asyncio.Queue()
    connections.register(ticket_id, queue)

    status, resolution = row
    if status != "pending":
        # Already finished by the time the client connected (a real race — the worker
        # can win it) — push the terminal state immediately rather than making the
        # client wait for a pub/sub message that will never come.
        await queue.put({"ticket_id": ticket_id, "status": status, "resolution": resolution})

    try:
        while True:
            update = asyncio.ensure_future(queue.get())
            client_msg = asyncio.ensure_future(websocket.receive_text())
            done, pending = await asyncio.wait(
                {update, client_msg}, timeout=HEARTBEAT_INTERVAL, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()

            if update in done:
                message = update.result()
                # request_id is only present on a genuine live pub/sub delivery (see
                # app/tasks/process_ticket.py) — the "already resolved before connect"
                # immediate-push path (below) has no live cross-process hop to trace.
                if message.get("request_id"):
                    with tracer.start_as_current_span("websocket_push") as span:
                        span.set_attribute("request_id", message["request_id"])
                        span.set_attribute("ticket_id", ticket_id)
                        log_event("websocket_push", message["request_id"], ticket_id=ticket_id, status=message.get("status"))
                await websocket.send_json(message)
                if message.get("status") != "pending":
                    break  # terminal state delivered — this ticket has nothing further to push

            if client_msg in done:
                client_msg.result()  # raises WebSocketDisconnect if the client hung up; ignore any other content (pong/keepalive)

            if not done:
                await websocket.send_json({"type": "ping"})  # heartbeat: nothing happened this interval
    except WebSocketDisconnect:
        pass
    finally:
        connections.unregister(ticket_id, queue)
        # The client may have already disconnected by the time we get here (e.g. it hung up
        # right as we delivered the terminal update) — Starlette raises RuntimeError if the
        # connection was never accepted-and-open, WebSocketDisconnect if it's accepted but the
        # client is already gone. Either way, closing an already-gone connection isn't an error.
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()


# Mounted last so it can never shadow an API route. Serving the UI from the API's own
# origin keeps fetch() and the websocket same-origin, so no CORS config is needed.
# Path is resolved from this file, not the cwd, so it works wherever uvicorn is started.
_FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent / "frontend"
app.mount("/ui", StaticFiles(directory=_FRONTEND_DIR, html=True), name="ui")
