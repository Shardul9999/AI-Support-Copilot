"""Redis pub/sub glue for multi-instance websocket fanout.

Why this exists: a ticket is submitted through one FastAPI instance (or a
load balancer picks one), but the Celery worker that finishes the job doesn't
know which instance holds the client's websocket connection — and with two
or more uvicorn instances behind a proxy, it's very likely not the same
process at all. Direct in-process dispatch (worker calls the registry
function directly) would silently drop the update whenever the connected
client isn't on the same instance the worker happens to be talking to.
Redis pub/sub fixes this the standard way: every instance subscribes to one
shared channel at startup, so a single PUBLISH reaches all of them, and each
instance's local `app.realtime.connections` registry decides whether it
actually has a queue to deliver to.

publish_ticket_update() is sync — the Celery worker process is fully
synchronous (see app/tasks/process_ticket.py), so it uses the plain `redis`
client, not `redis.asyncio`. subscribe_forever() is async — it runs as a
background task inside the FastAPI event loop.
"""
import json
import os
import ssl

import redis
import redis.asyncio as aioredis

CHANNEL = "ticket_updates"


def _ssl_kwargs() -> dict:
    # Same reasoning as app/tasks/celery_app.py: Upstash's rediss:// endpoint is TLS,
    # and both the sync and async redis-py clients need ssl_cert_reqs spelled out
    # explicitly for a rediss:// URL or they refuse to connect.
    if os.environ["REDIS_URL"].startswith("rediss://"):
        return {"ssl_cert_reqs": ssl.CERT_REQUIRED}
    return {}


def publish_ticket_update(ticket_id: int, status: str, resolution: dict, request_id: str) -> None:
    client = redis.Redis.from_url(os.environ["REDIS_URL"], **_ssl_kwargs())
    try:
        client.publish(
            CHANNEL,
            json.dumps({"ticket_id": ticket_id, "status": status, "resolution": resolution, "request_id": request_id}),
        )
    finally:
        client.close()


async def subscribe_forever(on_message) -> None:
    client = aioredis.from_url(os.environ["REDIS_URL"], **_ssl_kwargs())
    pubsub = client.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue  # the subscribe-confirmation message, not an actual update
            await on_message(json.loads(message["data"]))
    finally:
        await pubsub.close()
        await client.close()
