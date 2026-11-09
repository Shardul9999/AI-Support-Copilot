import asyncio
import json
import sys

import websockets

async def watch(ticket_id: int, host: str, tenant_id: str) -> None:
    uri = f"{host}/ws/tickets/{ticket_id}"
    async with websockets.connect(uri, additional_headers={"X-Tenant-ID": tenant_id}) as ws:
        print(f"connected — watching ticket {ticket_id} on {host} (tenant={tenant_id})")
        async for raw in ws:
            message = json.loads(raw)
            if message.get("type") == "ping":
                print("(heartbeat)")
                continue
            print(json.dumps(message, indent=2))
            if message.get("status") and message["status"] != "pending":
                break

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.watch_ticket <ticket_id> [ws://host:port] [tenant_id]")
        raise SystemExit(1)
    ticket_id_arg = int(sys.argv[1])
    host_arg = sys.argv[2] if len(sys.argv) > 2 else "ws://localhost:8000"
    tenant_arg = sys.argv[3] if len(sys.argv) > 3 else "acme"
    asyncio.run(watch(ticket_id_arg, host_arg, tenant_arg))
