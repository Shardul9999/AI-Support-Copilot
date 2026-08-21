def reciprocal_rank_fusion(*ranked_lists: list[dict], k: int = 60, top_k: int = 20) -> list[dict]:
    """Merge ranked lists from different retrieval methods using rank, not score —
    cosine similarity (0-1) and ts_rank_cd (unbounded) live on different scales with
    no principled way to add them directly. RRF sidesteps that: score(d) = sum of
    1/(k + rank_in_that_list), rank-based so the scales never need reconciling."""
    scores: dict[int, float] = {}
    items: dict[int, dict] = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list, start=1):
            scores[item["id"]] = scores.get(item["id"], 0.0) + 1.0 / (k + rank)
            items[item["id"]] = item

    fused_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [items[cid] for cid in fused_ids[:top_k]]
