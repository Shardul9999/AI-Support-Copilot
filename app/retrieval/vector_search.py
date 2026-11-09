from app.db.session import get_connection

def vector_search(query_embedding: list[float], tenant_id: str, top_k: int = 5) -> list[dict]:
    """Nearest-neighbour search over knowledge_chunks scoped to tenant_id."""
    with get_connection(tenant_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source, chunk_index, content,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM knowledge_chunks
                WHERE tenant_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (query_embedding, tenant_id, query_embedding, top_k),
            )
            rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "source": r[1],
            "chunk_index": r[2],
            "content": r[3],
            "similarity": r[4],
        }
        for r in rows
    ]
