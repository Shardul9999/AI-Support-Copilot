import json
import unittest.mock as mock
import pytest
from app.retrieval.rerank import rerank, _remote_rerank


def test_rerank_empty_chunks():
    assert rerank("any query", []) == []


def test_rerank_onnx_int8_active():
    """Verify that quantized INT8 ONNX session is active and functional."""
    from app.retrieval.rerank import _get_onnx_session

    session, tokenizer = _get_onnx_session()
    assert session is not None, "ONNX INT8 session should be loaded"
    assert tokenizer is not None, "Tokenizer should be loaded"

    query = "How do I return a purchased item?"
    chunks = [
        {"id": 1, "content": "Our store hours are Monday to Friday 9am to 5pm."},
        {"id": 2, "content": "You can return any purchased item within 14 days of receipt for a full refund."},
        {"id": 3, "content": "We ship via standard courier and express delivery."},
    ]
    ranked = rerank(query, chunks, top_k=1)
    assert len(ranked) == 1
    assert ranked[0]["id"] == 2


def test_rerank_local_model():
    """Verify local CrossEncoder reranks relevant chunks higher."""
    query = "How do I return a purchased item?"
    chunks = [
        {"id": 1, "content": "Our store hours are Monday to Friday 9am to 5pm."},
        {"id": 2, "content": "You can return any purchased item within 14 days of receipt for a full refund."},
        {"id": 3, "content": "We ship via standard courier and express delivery."},
    ]
    ranked = rerank(query, chunks, top_k=1)
    assert len(ranked) == 1
    assert ranked[0]["id"] == 2


def test_rerank_remote_service_mock(monkeypatch):
    """Verify when RERANK_SERVICE_URL is set, rerank() uses the decoupled service."""
    monkeypatch.setenv("RERANK_SERVICE_URL", "http://fake-inference:8001")

    query = "What is the warranty period?"
    chunks = [{"id": 10, "content": "Warranty is 1 year"}, {"id": 20, "content": "Unrelated topic"}]

    mock_response = mock.MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"ranked_chunks": [chunks[0]]}).encode("utf-8")

    with mock.patch("urllib.request.urlopen", return_value=mock_response):
        result = rerank(query, chunks, top_k=1)
        assert len(result) == 1
        assert result[0]["id"] == 10


def test_rerank_remote_service_fallback_on_failure(monkeypatch):
    """Verify if the remote inference service is down, rerank() falls back to local execution."""
    monkeypatch.setenv("RERANK_SERVICE_URL", "http://unreachable-host:9999")

    query = "return refund"
    chunks = [
        {"id": 1, "content": "Refund policy details"},
        {"id": 2, "content": "Something else entirely"},
    ]
    # Should fall back cleanly without raising an exception
    result = rerank(query, chunks, top_k=1)
    assert len(result) == 1
