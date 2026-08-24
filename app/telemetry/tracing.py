"""Basic OpenTelemetry tracing plus a request_id correlation string threaded
through API -> Celery task -> websocket push. Deliberately basic (console
exporter, no collector/Jaeger/Grafana) per ONE_WEEK_PLAN.md's cut-list — the
goal is BUILD_PLAN.md's Phase 9 verify step ("trace one ticket end-to-end by
correlation ID across all three hops"), not a production observability stack.

request_id is application-level, not OTel's own trace_id: each of the three
hops (API process, Celery worker process, and back in the API process for the
websocket push) creates its own independent OTel trace, since proper
distributed-trace-context propagation across a Celery broker is real added
complexity this project's scope doesn't call for. request_id is the thing
that actually lets a human correlate all three — findable by grepping any of
the three processes' console output for the same id, or by span attribute
in a real trace viewer if one's ever added."""
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
