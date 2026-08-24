from app.db.session import get_connection


def keyword_search(query: str, tenant_id: str, top_k: int = 20) -> list[dict]:
    """Keyword half of hybrid search — good at exact rare tokens (error codes,
    invoice numbers) that vector search is blind to. Postgres full-text search
    (ts_rank_cd + GIN index) stands in for BM25."""
    with get_connection(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, chunk_index, content,
                       ts_rank_cd(search_vector, plainto_tsquery('english', %s)) AS rank
                FROM knowledge_chunks
                WHERE tenant_id = %s
                  AND search_vector @@ plainto_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s
                """,
                (query, tenant_id, query, top_k),
            )
            rows = cur.fetchall()

    return [
        {"id": r[0], "source": r[1], "chunk_index": r[2], "content": r[3], "rank": r[4]}
        for r in rows
    ]
