import json
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

from app.db.session import get_admin_connection
from app.pipeline import resolve_ticket

TENANT = "acme"
GOLDEN_SET_PATH = os.path.join(os.path.dirname(__file__), "golden_set.jsonl")

def _load_golden_set() -> list[dict]:
    with open(GOLDEN_SET_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

def _sources_for_citations(citation_ids: list[int]) -> set[str]:
    if not citation_ids:
        return set()
    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source FROM knowledge_chunks WHERE id = ANY(%s)", (citation_ids,))
            return {row[0] for row in cur.fetchall()}

@pytest.fixture(scope="module", autouse=True)
def clear_cache_for_clean_run():
    """Clear tenant semantic cache to test live pipeline behavior."""
    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM semantic_cache WHERE tenant_id = %s", (TENANT,))
    yield

@pytest.mark.parametrize("case", _load_golden_set(), ids=lambda c: c["subject"])
def test_golden_set_case(case):
    resolution = resolve_ticket(case["subject"], case["body"], TENANT)

    if case["expected_action"] == "resolve":
        assert resolution.action == "resolve", (
            f"expected resolve, got {resolution.action!r} ({resolution.escalation_reason!r})"
        )
    else:
        assert resolution.action != "resolve", f"expected escalate/need_info, got 'resolve': {resolution.answer!r}"

    if case["expected_sources"]:
        cited_sources = _sources_for_citations(resolution.citations)
        assert cited_sources & set(case["expected_sources"]), (
            f"expected a citation from {case['expected_sources']}, got sources {cited_sources} "
            f"(citation ids {resolution.citations})"
        )
