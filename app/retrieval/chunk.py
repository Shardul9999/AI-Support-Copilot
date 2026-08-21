import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def chunk(text: str, size: int = 512, overlap: float = 0.1) -> list[str]:
    """Split text into ~`size`-token chunks with `overlap` fraction repeated
    between consecutive chunks, so a sentence split across a boundary still
    appears whole in at least one chunk."""
    tokens = _ENCODING.encode(text)
    if not tokens:
        return []

    step = max(1, int(size * (1 - overlap)))
    chunks = []
    start = 0
    while start < len(tokens):
        piece = tokens[start : start + size]
        chunks.append(_ENCODING.decode(piece))
        if start + size >= len(tokens):
            break
        start += step
    return chunks
