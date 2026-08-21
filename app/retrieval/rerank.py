from sentence_transformers import CrossEncoder

_model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")


def rerank(query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
    """Reads query and chunk together in one pass (unlike vector/keyword search,
    which score each independently) — far more accurate, far too slow to run
    against every chunk, hence narrow-then-rerank instead of rerank-everything."""
    if not chunks:
        return []
    pairs = [(query, c["content"]) for c in chunks]
    scores = _model.predict(pairs)
    ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
    return [chunk for chunk, _ in ranked[:top_k]]
