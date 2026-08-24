"""The Celery task. Deliberately a plain `def`, not `async def` — the guide's
spec has a real bug here (@shared_task async def never actually awaits), but
it doesn't even apply to our code: the whole pipeline (embed_query,
vector_search, keyword_search, rerank, the Groq call) has been synchronous
since Phase 2, so there's no coroutine to run and no asyncio.run() needed."""
from app.db.session import get_connection
from app.pipeline import resolve_ticket
from app.tasks.celery_app import celery_app


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    time_limit=120,
)
def process_ticket(self, ticket_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id, subject, body FROM tickets WHERE id = %s", (ticket_id,))
            row = cur.fetchone()

    if row is None:
        return  # ticket vanished — nothing to do, don't retry forever on a ghost id

    tenant_id, subject, body = row
    resolution = resolve_ticket(subject, body, tenant_id)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tickets
                SET status = %s, resolution = %s::jsonb, updated_at = now()
                WHERE id = %s
                """,
                (resolution.action, resolution.model_dump_json(), ticket_id),
            )
