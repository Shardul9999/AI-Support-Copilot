import instructor
from groq import Groq

from app.cache import semantic_cache
from app.models import SupportResolution
from app.retrieval.embed import embed_query
from app.retrieval.fuse import reciprocal_rank_fusion
from app.retrieval.keyword_search import keyword_search
from app.retrieval.rerank import rerank
from app.retrieval.vector_search import vector_search

client = Groq()
structured_client = instructor.from_groq(client, mode=instructor.Mode.TOOLS)

MODEL = "openai/gpt-oss-120b"
CONFIDENCE_THRESHOLD = 0.72

def resolve_ticket(subject: str, body: str, tenant_id: str) -> SupportResolution:
    query = f"{subject}\n\n{body}"

    query_embedding = embed_query(query)

    cached = semantic_cache.lookup(query_embedding, tenant_id)
    if cached is not None:
        return SupportResolution(**cached)

    semantic = vector_search(query_embedding, tenant_id=tenant_id, top_k=20)
    keyword = keyword_search(query, tenant_id=tenant_id, top_k=20)
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

    retrieved_ids = {c["id"] for c in chunks}
    resolution.citations = [cid for cid in resolution.citations if cid in retrieved_ids]

    if resolution.confidence < CONFIDENCE_THRESHOLD and resolution.action == "resolve":
        resolution.action = "escalate"
        resolution.escalation_reason = (
            resolution.escalation_reason
            or f"Confidence {resolution.confidence:.2f} below {CONFIDENCE_THRESHOLD} threshold"
        )

    if resolution.action != "resolve" and not resolution.answer.strip():
        resolution.answer = (
            "Thanks for reaching out — this has been forwarded to a specialist "
            "who will follow up with you shortly."
        )

    if resolution.action == "resolve" and resolution.confidence >= CONFIDENCE_THRESHOLD:
        semantic_cache.store(query, query_embedding, tenant_id, resolution.model_dump())

    return resolution
