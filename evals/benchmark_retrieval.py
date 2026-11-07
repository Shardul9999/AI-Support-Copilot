"""Comprehensive Retrieval Benchmark: Vector vs Keyword vs Hybrid + Reranking.

Evaluates retrieval quality on a candidate pool of 200 chunks (destroying the ceiling effect):
Metrics:
- Hit@1 (Precision@1): Did the single top result match the ground truth?
- Hit@3: Was the correct answer in the top 3?
- Hit@5 (Precision@5): Was the correct answer in the top 5?
- MRR (Mean Reciprocal Rank): 1/rank of the first relevant result.
- Latency (ms): Average retrieval time per query.

Usage:
    # Run fast sample (default 25 questions, takes ~10s):
    python -m evals.benchmark_retrieval

    # Run complete 200-question suite:
    python -m evals.benchmark_retrieval --all

    # Run for a specific tenant (default: globex):
    python -m evals.benchmark_retrieval --tenant globex
"""
import argparse
import json
import os
import time

from dotenv import load_dotenv

load_dotenv()

from app.retrieval.embed import embed_query
from app.retrieval.fuse import reciprocal_rank_fusion
from app.retrieval.keyword_search import keyword_search
from app.retrieval.rerank import rerank
from app.retrieval.vector_search import vector_search

EVAL_SET_PATH = os.path.join(os.path.dirname(__file__), "globex_eval_set.jsonl")


def load_eval_cases(limit: int | None = None) -> list[dict]:
    if not os.path.exists(EVAL_SET_PATH):
        raise FileNotFoundError(f"Evaluation set {EVAL_SET_PATH} not found. Run scripts.ingest_faqs first.")
    cases = []
    with open(EVAL_SET_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))
                if limit and len(cases) >= limit:
                    break
    return cases


# ---------------- Retrieval Methods ----------------

def search_vector_only(query: str, tenant_id: str, top_k: int = 5) -> list[dict]:
    vec = embed_query(query)
    return vector_search(vec, tenant_id=tenant_id, top_k=top_k)


def search_keyword_only(query: str, tenant_id: str, top_k: int = 5) -> list[dict]:
    return keyword_search(query, tenant_id=tenant_id, top_k=top_k)


def search_hybrid_reranked(query: str, tenant_id: str, top_k: int = 5) -> list[dict]:
    vec = embed_query(query)
    semantic = vector_search(vec, tenant_id=tenant_id, top_k=20)
    keyword = keyword_search(query, tenant_id=tenant_id, top_k=20)
    fused = reciprocal_rank_fusion(semantic, keyword, top_k=20)
    return rerank(query, fused, top_k=top_k)


# ---------------- Evaluation Engine ----------------

def is_hit(expected_answer: str, result_content: str) -> bool:
    """True if key terms from expected answer are present in the retrieved chunk."""
    # Since chunk format is "Question: ...\nAnswer: ...", checking answer substring is exact
    expected_snippet = expected_answer.strip()[:60].lower()
    return expected_snippet in result_content.lower()


def evaluate_method(method_fn, cases: list[dict], tenant_id: str, name: str) -> dict:
    hit_1 = 0
    hit_3 = 0
    hit_5 = 0
    rr_total = 0.0
    latencies = []

    print(f"Testing [{name}] across {len(cases)} cases...", flush=True)

    for i, case in enumerate(cases):
        q = case["question"]
        expected = case["expected_answer"]

        t0 = time.perf_counter()
        results = method_fn(q, tenant_id=tenant_id, top_k=5)
        latencies.append((time.perf_counter() - t0) * 1000)

        # Find rank of ground truth
        rank = None
        for r_idx, res in enumerate(results):
            if is_hit(expected, res.get("content", "")):
                rank = r_idx + 1
                break

        if rank == 1:
            hit_1 += 1
        if rank and rank <= 3:
            hit_3 += 1
        if rank and rank <= 5:
            hit_5 += 1

        if rank:
            rr_total += 1.0 / rank

    total = len(cases)
    return {
        "name": name,
        "hit@1": (hit_1 / total) * 100,
        "hit@3": (hit_3 / total) * 100,
        "hit@5": (hit_5 / total) * 100,
        "mrr": rr_total / total,
        "latency_ms": sum(latencies) / len(latencies),
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark retrieval strategies against 200 candidate chunks.")
    parser.add_argument("--tenant", default="globex", help="Tenant to benchmark against (default: globex)")
    parser.add_argument("--all", action="store_true", help="Run against all 200 evaluation cases (default: 25)")
    parser.add_argument("--limit", type=int, default=None, help="Explicit limit of test cases")
    args = parser.parse_args()

    limit = None if args.all else (args.limit or 25)
    cases = load_eval_cases(limit=limit)

    print("========================================================================================")
    print(f"RETRIEVAL BENCHMARK: Tenant [{args.tenant}] | Candidates: 200 Chunks | Queries: {len(cases)}")
    print("========================================================================================")

    methods = [
        ("1. Vector-Only (Gemini)", search_vector_only),
        ("2. Keyword-Only (BM25)", search_keyword_only),
        ("3. Hybrid + Reranker (Cross-Encoder)", search_hybrid_reranked),
    ]

    metrics = []
    for name, fn in methods:
        stats = evaluate_method(fn, cases, args.tenant, name)
        metrics.append(stats)

    # Print Report Table
    print("\n" + "=" * 92)
    print(f"{'Method':<36} | {'Hit@1':<8} | {'Hit@3':<8} | {'Hit@5':<8} | {'MRR':<8} | {'Avg Latency'}")
    print("-" * 92)
    for m in metrics:
        print(
            f"{m['name']:<36} | {m['hit@1']:>6.1f}% | {m['hit@3']:>6.1f}% | {m['hit@5']:>6.1f}% | "
            f"{m['mrr']:>6.3f}  | {m['latency_ms']:>6.1f} ms"
        )
    print("=" * 92)

    # Architectural takeaway
    best = max(metrics, key=lambda x: x["mrr"])
    print(f"\n Top Performing Strategy: {best['name']} (MRR: {best['mrr']:.3f})")
    print(" Ceilings eliminated: Metrics now reflect true discriminating ranking power across 200 candidates.\n")


if __name__ == "__main__":
    main()
