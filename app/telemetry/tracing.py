import logging
import uuid

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("ai-support-copilot")

_configured = False

def setup_tracing(service_name: str) -> None:
    """Call once per process (app/main.py and app/tasks/celery_app.py each do,
    at import time) — idempotent within a process."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True

def get_tracer():
    return trace.get_tracer("ai-support-copilot")

def new_request_id() -> str:
    return uuid.uuid4().hex[:12]

def log_event(event: str, request_id: str, **fields) -> None:
    """The primary human-grep-able correlation mechanism: every hop logs
    through this with the same request_id, so `grep request_id=<id>` across
    all three processes' output reconstructs one ticket's full journey without
    needing a trace viewer."""
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("request_id=%s event=%s %s", request_id, event, extra)
