# app/main.py  —  pip install -r requirements.txt
from dotenv import load_dotenv

load_dotenv()  # must run before any app-local import that reads env vars at module load time (embed.py, session.py)

import instructor
from fastapi import FastAPI
from groq import Groq

from app.models import SupportResolution, Ticket
from app.retrieval.embed import embed_query
from app.retrieval.fuse import reciprocal_rank_fusion
from app.retrieval.keyword_search import keyword_search
from app.retrieval.rerank import rerank
from app.retrieval.vector_search import vector_search

app = FastAPI()
client = Groq()  # reads GROQ_API_KEY from env
structured_client = instructor.from_groq(client, mode=instructor.Mode.TOOLS)

MODEL = "openai/gpt-oss-120b"
DEFAULT_TENANT = "acme"  # single-tenant placeholder until Phase 7 makes this per-request
CONFIDENCE_THRESHOLD = 0.72


@app.post("/tickets")
def handle_ticket(ticket: Ticket) -> SupportResolution:
    query = f"{ticket.subject}\n\n{ticket.body}"

    query_embedding = embed_query(query)
    semantic = vector_search(query_embedding, tenant_id=DEFAULT_TENANT, top_k=20)
    keyword = keyword_search(query, tenant_id=DEFAULT_TENANT, top_k=20)
    fused = reciprocal_rank_fusion(semantic, keyword, top_k=20)
    chunks = rerank(query, fused, top_k=5)
    context = "\n\n---\n\n".join(f"[chunk_id={c['id']}] {c['content']}" for c in chunks)

    resolution = structured_client.chat.completions.create(
        model=MODEL,
        max_tokens=500,
        response_model=SupportResolution,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a support agent for Acme. Answer only from these excerpts, "
                    "each tagged with a chunk_id. Cite the chunk_id(s) you actually used "
                    "in `citations`. If the excerpts don't contain the answer, set action "
                    "to 'escalate' or 'need_info' and explain why in escalation_reason, "
                    "rather than guessing.\n\n" + context
                ),
            },
            {"role": "user", "content": query},
        ],
    )

    # Citations are only trustworthy if they point at chunks we actually retrieved —
    # a model can still name an id that wasn't in context.
    retrieved_ids = {c["id"] for c in chunks}
    resolution.citations = [cid for cid in resolution.citations if cid in retrieved_ids]

    # Threshold is a safety net independent of the model's own judgment: even if it
    # said "resolve", low self-reported confidence forces a human in the loop.
    if resolution.confidence < CONFIDENCE_THRESHOLD and resolution.action == "resolve":
        resolution.action = "escalate"
        resolution.escalation_reason = (
            resolution.escalation_reason
            or f"Confidence {resolution.confidence:.2f} below {CONFIDENCE_THRESHOLD} threshold"
        )

    # The model sometimes leaves `answer` empty when it escalates instead of resolving
    # (defensible — it's declining to fabricate a substantive response) but an empty
    # string is a worse customer-facing message than an honest holding reply.
    if resolution.action != "resolve" and not resolution.answer.strip():
        resolution.answer = (
            "Thanks for reaching out — this has been forwarded to a specialist "
            "who will follow up with you shortly."
        )

    return resolution
