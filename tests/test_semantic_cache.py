import time

import pytest
from dotenv import load_dotenv

load_dotenv()

from app.cache import semantic_cache
from app.pipeline import resolve_ticket
from app.retrieval.embed import embed_query

TENANT = "acme"

@pytest.fixture
def cleanup_cache():
    """Track ids inserted during a test so they can be removed afterward —
    semantic_cache has no natural unique key to upsert against."""
    from app.db.session import get_admin_connection

    inserted_texts = []
    yield inserted_texts
    if inserted_texts:
        with get_admin_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM semantic_cache WHERE query_text = ANY(%s)", (inserted_texts,))

def test_adversarial_pair_does_not_collide(cleanup_cache):
    """Verify that an adversarial pair with subtle negation does not hit cache."""
    stored_query = "refund after 14 days"
    lookup_query = "refund within 14 days"
    cleanup_cache.append(stored_query)

    stored_embedding = embed_query(stored_query)
    semantic_cache.store(stored_query, stored_embedding, TENANT, {"answer": "cached", "action": "resolve"})

    lookup_embedding = embed_query(lookup_query)
    assert semantic_cache.lookup(lookup_embedding, TENANT) is None

def test_near_identical_query_hits(cleanup_cache):
    """Verify that near-identical queries above similarity threshold hit cache."""
    stored_query = "How many days do I have to request a refund?"
    lookup_query = stored_query + " "
    cleanup_cache.append(stored_query)

    cached_response = {"answer": "14 days", "action": "resolve", "confidence": 0.98, "citations": [1], "escalation_reason": None}
    semantic_cache.store(stored_query, embed_query(stored_query), TENANT, cached_response)

    hit = semantic_cache.lookup(embed_query(lookup_query), TENANT)
    assert hit == cached_response

def test_non_resolve_result_is_never_cached(cleanup_cache):
    """Verify that non-resolve ticket resolutions are never stored in cache."""
    query = f"Do you offer a loyalty rewards program with points? (cache-gate test {time.time()})"
    cleanup_cache.append(f"{query}\n\n")

    resolution = resolve_ticket(query, "", TENANT)
    assert resolution.action != "resolve"

    assert semantic_cache.lookup(embed_query(f"{query}\n\n"), TENANT) is None
