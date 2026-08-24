"""Semantic cache: skips the full retrieve-and-generate pipeline when a new
ticket's embedding is near-identical to a previously *resolved* ticket's.
Deliberately stricter than retrieval's own similarity use (0.97, vs. the
0.72 confidence bar retrieval/generation clears) — a false cache hit hands
back a wrong answer with no model in the loop to catch it, so the bar for
"close enough" has to be much higher here than it is for "worth citing."

Both functions are pure store/lookup — the policy decision of *whether* a
given result is even eligible to be cached (action == "resolve", confident
enough) lives in app/pipeline.py, next to this pipeline's other business
rules (confidence override, empty-answer fallback), not in here."""
import json

from app.db.session import get_connection

SIMILARITY_THRESHOLD = 0.97  # see DECISIONS.md for how this was picked against the guide's adversarial pair
TTL = "24 hours"


def lookup(query_embedding: list[float], tenant_id: str) -> dict | None:
    with get_connection(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT response
                FROM semantic_cache
                WHERE tenant_id = %s
                  AND expires_at > now()
                  AND 1 - (query_embedding <=> %s::vector) >= %s
                ORDER BY query_embedding <=> %s::vector
                LIMIT 1
                """,
                (tenant_id, query_embedding, SIMILARITY_THRESHOLD, query_embedding),
            )
            row = cur.fetchone()

    return row[0] if row else None


def store(query_text: str, query_embedding: list[float], tenant_id: str, response: dict) -> None:
    with get_connection(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO semantic_cache (tenant_id, query_text, query_embedding, response, expires_at)
                VALUES (%s, %s, %s, %s::jsonb, now() + %s::interval)
                """,
                (tenant_id, query_text, query_embedding, json.dumps(response), TTL),
            )
