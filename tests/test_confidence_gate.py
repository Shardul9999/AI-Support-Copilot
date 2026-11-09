from unittest.mock import MagicMock, patch
import pytest
from dotenv import load_dotenv

load_dotenv()

from app.models import SupportResolution
from app.pipeline import CONFIDENCE_THRESHOLD, resolve_ticket

@pytest.fixture
def mock_pipeline_dependencies():
    with patch("app.pipeline.embed_query", return_value=[0.1] * 768), \
         patch("app.pipeline.semantic_cache.lookup", return_value=None), \
         patch("app.pipeline.semantic_cache.store") as mock_cache_store, \
         patch("app.pipeline.vector_search", return_value=[{"id": 1, "content": "policy chunk"}]), \
         patch("app.pipeline.keyword_search", return_value=[{"id": 1, "content": "policy chunk"}]), \
         patch("app.pipeline.reciprocal_rank_fusion", return_value=[{"id": 1, "content": "policy chunk"}]), \
         patch("app.pipeline.rerank", return_value=[{"id": 1, "content": "policy chunk"}]):
        yield mock_cache_store

def test_confidence_071_downgrades_to_escalate(mock_pipeline_dependencies):
    """0.71 is below the 0.72 threshold: forces downgrade to 'escalate' and blocks caching."""
    mock_cache_store = mock_pipeline_dependencies
    mock_llm_response = SupportResolution(
        answer="You can get a refund within 14 days.",
        citations=[1],
        confidence=0.71,
        action="resolve",
        escalation_reason=None,
    )

    with patch("app.pipeline.structured_client.chat.completions.create", return_value=mock_llm_response):
        res = resolve_ticket("Refund request", "Can I get a refund?", tenant_id="acme")

    assert res.action == "escalate"
    assert "below 0.72 threshold" in (res.escalation_reason or "")
    mock_cache_store.assert_not_called()

def test_confidence_072_boundary_resolves(mock_pipeline_dependencies):
    """0.72 is exactly on the boundary: clears the threshold, remains 'resolve', and is cached."""
    mock_cache_store = mock_pipeline_dependencies
    mock_llm_response = SupportResolution(
        answer="You can get a refund within 14 days.",
        citations=[1],
        confidence=0.72,
        action="resolve",
        escalation_reason=None,
    )

    with patch("app.pipeline.structured_client.chat.completions.create", return_value=mock_llm_response):
        res = resolve_ticket("Refund request", "Can I get a refund?", tenant_id="acme")

    assert res.action == "resolve"
    assert res.confidence == 0.72
    assert res.escalation_reason is None
    mock_cache_store.assert_called_once()

def test_confidence_073_above_threshold_resolves(mock_pipeline_dependencies):
    """0.73 is strictly above the 0.72 threshold: remains 'resolve' and is cached."""
    mock_cache_store = mock_pipeline_dependencies
    mock_llm_response = SupportResolution(
        answer="You can get a refund within 14 days.",
        citations=[1],
        confidence=0.73,
        action="resolve",
        escalation_reason=None,
    )

    with patch("app.pipeline.structured_client.chat.completions.create", return_value=mock_llm_response):
        res = resolve_ticket("Refund request", "Can I get a refund?", tenant_id="acme")

    assert res.action == "resolve"
    assert res.confidence == 0.73
    assert res.escalation_reason is None
    mock_cache_store.assert_called_once()
