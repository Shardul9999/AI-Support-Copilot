# app/main.py  —  pip install -r requirements.txt
import uuid

from dotenv import load_dotenv

load_dotenv()  # must run before any app-local import that reads env vars at module load time (embed.py, session.py)

from fastapi import FastAPI, Header, HTTPException, Response

from app.db.session import get_connection
from app.models import Ticket
from app.tasks.process_ticket import process_ticket

app = FastAPI()

DEFAULT_TENANT = "acme"  # single-tenant placeholder until Phase 7 makes this per-request


@app.post("/tickets")
def handle_ticket(
    ticket: Ticket,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    idempotency_key = idempotency_key or str(uuid.uuid4())

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, resolution FROM tickets WHERE tenant_id = %s AND idempotency_key = %s",
                (DEFAULT_TENANT, idempotency_key),
            )
            existing = cur.fetchone()

            if existing:
                # Same idempotency key seen before — a repeat webhook delivery.
                # Return the original result instead of re-queuing the work.
                response.status_code = 200
                return {"ticket_id": existing[0], "status": existing[1], "resolution": existing[2]}

            cur.execute(
                """
                INSERT INTO tickets (tenant_id, idempotency_key, subject, body)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (DEFAULT_TENANT, idempotency_key, ticket.subject, ticket.body),
            )
            ticket_id = cur.fetchone()[0]

    process_ticket.delay(ticket_id)

    response.status_code = 202
    return {"ticket_id": ticket_id, "status": "pending"}


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, resolution FROM tickets WHERE id = %s AND tenant_id = %s",
                (ticket_id, DEFAULT_TENANT),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="ticket not found")

    return {"ticket_id": ticket_id, "status": row[0], "resolution": row[1]}
