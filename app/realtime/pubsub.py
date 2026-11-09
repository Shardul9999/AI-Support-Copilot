import json
import os
import ssl

import redis
import redis.asyncio as aioredis

CHANNEL = "ticket_updates"

def _ssl_kwargs() -> dict:
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
                continue
            await on_message(json.loads(message["data"]))
    finally:
        await pubsub.close()
        await client.close()
