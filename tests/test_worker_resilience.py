import json
import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from app.tasks.celery_app import celery_app
from app.tasks.process_ticket import process_ticket, push_to_dlq


def test_celery_task_configuration():
    """Verify task is configured with exponential backoff, jitter, and safety limits."""
    assert process_ticket.retry_backoff is True
    assert process_ticket.retry_jitter is True
    assert process_ticket.max_retries == 3
    assert process_ticket.time_limit == 120


def test_celery_app_queues_and_acks_late():
    """Verify queues, late acks, and worker lost rejection are configured."""
    queue_names = [q.name for q in celery_app.conf.task_queues]
    assert "tickets" in queue_names
    assert "tickets_dlq" in queue_names
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True


def test_push_to_dlq():
    """Verify dead-letter queue records poison pills with full diagnostic metadata."""
    from app.realtime.pubsub import _ssl_kwargs
    import redis

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL not configured")

    client = redis.Redis.from_url(redis_url, **_ssl_kwargs())
    test_ticket_id = 88888888
    try:
        push_to_dlq(
            ticket_id=test_ticket_id,
            tenant_id="acme",
            request_id="test-req-dlq",
            error="Simulated permanent LLM syntax error",
            payload={"subject": "Broken ticket", "body": "Explosive content"},
        )

        raw = client.lindex("tickets_dlq", 0)
        assert raw is not None
        entry = json.loads(raw)
        assert entry["ticket_id"] == test_ticket_id
        assert entry["tenant_id"] == "acme"
        assert entry["request_id"] == "test-req-dlq"
        assert "Simulated permanent" in entry["error"]
        assert entry["payload"]["subject"] == "Broken ticket"
    finally:
        # Pop test entry off the queue
        client.lpop("tickets_dlq")
        client.close()
