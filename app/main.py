import asyncio
import contextlib
import json
import os
import pathlib
import uuid

from dotenv import load_dotenv

load_dotenv()

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

HEARTBEAT_INTERVAL = 30

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
    cur.execute("SELECT 1 FROM tenants WHERE slug = %s", (tenant_id,))
    return cur.fetchone() is not None

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
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
                if message.get("request_id"):
                    with tracer.start_as_current_span("websocket_push") as span:
                        span.set_attribute("request_id", message["request_id"])
                        span.set_attribute("ticket_id", ticket_id)
                        log_event("websocket_push", message["request_id"], ticket_id=ticket_id, status=message.get("status"))
                await websocket.send_json(message)
                if message.get("status") != "pending":
                    break

            if client_msg in done:
                client_msg.result()

            if not done:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        connections.unregister(ticket_id, queue)
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()

_FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent / "frontend"
app.mount("/ui", StaticFiles(directory=_FRONTEND_DIR, html=True), name="ui")
