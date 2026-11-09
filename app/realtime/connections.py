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
