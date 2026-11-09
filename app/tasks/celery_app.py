import os
import ssl

from dotenv import load_dotenv

load_dotenv()

from celery import Celery
from kombu import Queue

celery_app = Celery(
    "tickets",
    broker=os.environ["REDIS_URL"],
    backend=os.environ["REDIS_URL"],
    include=["app.tasks.process_ticket"],
)

_redis_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}

celery_app.conf.update(
    task_default_queue="tickets",
    task_queues=(
        Queue("tickets", routing_key="tickets.#"),
        Queue("tickets_dlq", routing_key="dlq.#"),
    ),
    task_routes={
        "app.tasks.process_ticket.process_ticket": {"queue": "tickets"},
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_use_ssl=_redis_ssl,
    redis_backend_use_ssl=_redis_ssl,
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
