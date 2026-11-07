import os
import ssl

from dotenv import load_dotenv

load_dotenv()  # the worker process starts here, not via app/main.py — needs its own dotenv load

from celery import Celery

celery_app = Celery(
    "tickets",
    broker=os.environ["REDIS_URL"],
    backend=os.environ["REDIS_URL"],
    include=["app.tasks.process_ticket"],
)

# Upstash's rediss:// endpoint is TLS; Celery's redis backend/broker require the SSL
# verification mode to be spelled out explicitly, otherwise the worker fails at startup.
_redis_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}

celery_app.conf.update(
    task_acks_late=True,  # ack after the task finishes, not on receipt — a crashed worker gets its job redelivered
    worker_prefetch_multiplier=1,  # don't let one worker hoard jobs while others sit idle
    broker_use_ssl=_redis_ssl,
    redis_backend_use_ssl=_redis_ssl,
    # Redis broker only re-queues an unacked message after this many seconds (default 3600 —
    # a crashed worker's in-flight job would otherwise sit stuck for up to an hour). Set just
    # above the task's own time_limit=120 so a still-running task isn't redelivered while
    # legitimately in progress, but a genuinely dead worker recovers quickly.
    broker_transport_options={
        "visibility_timeout": 130,
        "health_check_interval": 25,
        "socket_keepalive": True,
        "retry_on_timeout": True,
    },
    redis_backend_transport_options={
        "health_check_interval": 25,
        "socket_keepalive": True,
        "retry_on_timeout": True,
    },
)
# time_limit/max_retries/default_retry_delay are set per-task on process_ticket itself (app/tasks/process_ticket.py)
