"""The Celery task. Deliberately a plain `def`, not `async def` — the guide's
spec has a real bug here (@shared_task async def never actually awaits), but
it doesn't even apply to our code: the whole pipeline (embed_query,
vector_search, keyword_search, rerank, the Groq call) has been synchronous
since Phase 2, so there's no coroutine to run and no asyncio.run() needed."""
from app.db.session import get_connection
from app.pipeline import resolve_ticket
from app.realtime.pubsub import publish_ticket_update
from app.tasks.celery_app import celery_app
from app.telemetry.tracing import get_tracer, log_event, setup_tracing

setup_tracing("worker")
tracer = get_tracer()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    time_limit=120,
)
def process_ticket(self, ticket_id: int, tenant_id: str, request_id: str):
    with tracer.start_as_current_span("process_ticket") as span:
        span.set_attribute("request_id", request_id)
        span.set_attribute("ticket_id", ticket_id)
        span.set_attribute("tenant_id", tenant_id)
        log_event("task_started", request_id, ticket_id=ticket_id, tenant_id=tenant_id)

        # tenant_id comes from the caller (POST /tickets already resolved and validated
        # it — see app/main.py), not a fresh lookup here: get_connection needs a tenant
        # to scope its transaction *before* it can run any query at all under RLS, so
        # there's no RLS-safe way to "look up the tenant" from an unscoped connection.
        with get_connection(tenant_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT subject, body FROM tickets WHERE id = %s AND tenant_id = %s", (ticket_id, tenant_id)
                )
                row = cur.fetchone()

        if row is None:
            log_event("task_skipped_ghost_ticket", request_id, ticket_id=ticket_id)
            return  # ticket vanished (or tenant mismatch) — nothing to do, don't retry forever on a ghost id

        subject, body = row
        resolution = resolve_ticket(subject, body, tenant_id)

        with get_connection(tenant_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tickets
                    SET status = %s, resolution = %s::jsonb, updated_at = now()
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (resolution.action, resolution.model_dump_json(), ticket_id, tenant_id),
                )

        log_event("task_completed", request_id, ticket_id=ticket_id, action=resolution.action)
        publish_ticket_update(ticket_id, resolution.action, resolution.model_dump(), request_id)
