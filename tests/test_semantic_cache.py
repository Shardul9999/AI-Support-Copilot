"""Phase 8 verification: proves the semantic cache does what BUILD_PLAN.md's
Phase 8 verify step asks for — the guide's own adversarial pair ("refund
after 14 days" vs "refund within 14 days") must not collide — and that the
"never cache action != resolve or low-confidence" gate in app/pipeline.py
actually holds. Live only: real Gemini embeddings, and one real full-pipeline
call (Groq included) for the gating test, per this project's established
practice of verifying against the real thing rather than mocking.

Run: python -m pytest tests/test_semantic_cache.py -v
"""
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
    """The actual Phase 8 demo goal from BUILD_PLAN.md: cache a resolution for
    one half of the pair, then confirm a lookup for the other half misses."""
    stored_query = "refund after 14 days"
    lookup_query = "refund within 14 days"
    cleanup_cache.append(stored_query)

    stored_embedding = embed_query(stored_query)
    semantic_cache.store(stored_query, stored_embedding, TENANT, {"answer": "cached", "action": "resolve"})

    lookup_embedding = embed_query(lookup_query)
    assert semantic_cache.lookup(lookup_embedding, TENANT) is None


def test_near_identical_query_hits(cleanup_cache):
    """Positive counterpart to the test above — the cache must still hit for
    something, or "doesn't collide" would be true trivially by never matching
    anything. Live-measured (see DECISIONS.md): at this project's 0.97
    threshold, gemini-embedding-001 only reliably clears the bar for
    near-identical text — even a bare case change (0.94) or a mid-sentence
    extra space (0.93) falls short, well below the adversarial pair itself
    (0.91). A single *trailing* space measured 0.994, comfortably above
    threshold — used here as the smallest realistic variation that still
    isn't byte-identical."""
    stored_query = "How many days do I have to request a refund?"
    lookup_query = stored_query + " "  # trailing space only — measured 0.994 similarity, see DECISIONS.md
    cleanup_cache.append(stored_query)

    cached_response = {"answer": "14 days", "action": "resolve", "confidence": 0.98, "citations": [1], "escalation_reason": None}
    semantic_cache.store(stored_query, embed_query(stored_query), TENANT, cached_response)

    hit = semantic_cache.lookup(embed_query(lookup_query), TENANT)
    assert hit == cached_response


def test_non_resolve_result_is_never_cached(cleanup_cache):
    """Real integration test through app/pipeline.py, not just semantic_cache.py
    directly — a genuinely out-of-KB question (known from Phase 3 testing to
    reliably come back action="need_info") must not leave anything in the
    cache, proving the gate in resolve_ticket actually holds, not just that
    it looks correct on inspection."""
    query = f"Do you offer a loyalty rewards program with points? (cache-gate test {time.time()})"
    cleanup_cache.append(f"{query}\n\n")

    resolution = resolve_ticket(query, "", TENANT)
    assert resolution.action != "resolve"

    assert semantic_cache.lookup(embed_query(f"{query}\n\n"), TENANT) is None
