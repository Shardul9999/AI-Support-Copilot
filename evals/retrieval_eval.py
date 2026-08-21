"""Phase 4 retrieval eval — compares vector-only top-5 against hybrid+rerank
top-5 on a small labeled question set. Metric: precision@5 = fraction of
questions where the expected source document appears in the top-5 results.
Each doc in data/kb/ is currently exactly one chunk, so "expected source
appears" is equivalent to "the right chunk was retrieved."

Run from the repo root: python -m evals.retrieval_eval
"""
from dotenv import load_dotenv

load_dotenv()

from app.retrieval.embed import embed_query
from app.retrieval.fuse import reciprocal_rank_fusion
from app.retrieval.keyword_search import keyword_search
from app.retrieval.rerank import rerank
from app.retrieval.vector_search import vector_search

TENANT = "acme"

# (question, expected source filename)
QUESTIONS = [
    ("can I get my money back", "refund_policy.md"),
    ("error TXN_4471 on checkout", "error_codes.md"),
    ("do you ship to Nepal", "shipping_policy.md"),
    ("how do I delete my account", "account_management.md"),
    ("payment charged twice", "payments_and_billing.md"),
    ("when should I escalate to a human", "escalation_and_support.md"),
    ("my package never arrived", "shipping_policy.md"),
    ("I forgot my password", "account_management.md"),
    ("GST invoice missing", "payments_and_billing.md"),
    ("final sale item refund", "refund_policy.md"),
    ("error code TXN_9987 payment failed", "error_codes.md"),
    ("item arrived broken and damaged", "refund_policy.md"),
    ("is cash on delivery available", "payments_and_billing.md"),
    ("tracking link not updating in 3 days", "shipping_policy.md"),
    ("customer wants to discuss legal action", "escalation_and_support.md"),
]


def vector_only(query: str) -> list[dict]:
    return vector_search(embed_query(query), tenant_id=TENANT, top_k=5)


def hybrid_reranked(query: str) -> list[dict]:
    semantic = vector_search(embed_query(query), tenant_id=TENANT, top_k=20)
    keyword = keyword_search(query, tenant_id=TENANT, top_k=20)
    fused = reciprocal_rank_fusion(semantic, keyword, top_k=20)
    return rerank(query, fused, top_k=5)


def precision_at_k(method, k: int) -> tuple[int, int, list[str]]:
    hits = 0
    misses = []
    for question, expected_source in QUESTIONS:
        results = method(question)[:k]
        sources = [r["source"] for r in results]
        if expected_source in sources:
            hits += 1
        else:
            misses.append(f"  MISS: '{question}' expected {expected_source}, got {sources}")
    return hits, len(QUESTIONS), misses


if __name__ == "__main__":
    # precision@5 is nearly unfalsifiable with only 6 total documents in the KB —
    # top-5 out of ~6 candidates barely leaves room to fail. precision@1 (is the
    # single best result actually correct) is the metric with real signal at this
    # corpus size. Report both, but @5 is not the one to draw conclusions from yet.
    for k in (1, 5):
        print(f"=== Vector-only top-{k} ===")
        hits, total, misses = precision_at_k(vector_only, k)
        print(f"precision@{k}: {hits}/{total} ({100*hits/total:.0f}%)")
        for m in misses:
            print(m)

        print(f"\n=== Hybrid (vector + keyword, RRF) + rerank, top-{k} ===")
        hits, total, misses = precision_at_k(hybrid_reranked, k)
        print(f"precision@{k}: {hits}/{total} ({100*hits/total:.0f}%)")
        for m in misses:
            print(m)
        print()
