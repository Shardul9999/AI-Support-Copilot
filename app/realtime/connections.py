"""In-memory, per-instance registry of locally-connected websocket clients,
keyed by ticket_id. Each connected client gets its own asyncio.Queue instead
of the registry holding the WebSocket directly, so the ws handler can wait on
"next pushed update OR next client message" concurrently (see app/main.py).

This registry only knows about connections on *this* process. Fanning updates
out across multiple FastAPI instances is Redis pub/sub's job (pubsub.py) —
every instance's subscriber loop calls dispatch_local() with every message,
and dispatch_local() only ever touches this instance's own queues."""
import asyncio

_registry: dict[int, set[asyncio.Queue]] = {}


def register(ticket_id: int, queue: asyncio.Queue) -> None:
    _registry.setdefault(ticket_id, set()).add(queue)


def unregister(ticket_id: int, queue: asyncio.Queue) -> None:
    queues = _registry.get(ticket_id)
    if queues is None:
        return
    queues.discard(queue)
    if not queues:
        _registry.pop(ticket_id, None)


async def dispatch_local(ticket_id: int, message: dict) -> None:
    for queue in list(_registry.get(ticket_id, ())):
        await queue.put(message)
