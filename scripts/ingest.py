import glob
import os

from dotenv import load_dotenv

load_dotenv()

from app.db.session import get_admin_connection
from app.retrieval.chunk import chunk
from app.retrieval.embed import embed_document

DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "kb")

def ingest():
    conn = get_admin_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM knowledge_chunks")
            cur.execute("DELETE FROM semantic_cache")
            total = 0
            for path in sorted(glob.glob(os.path.join(DOCS_DIR, "*.md"))):
                source = os.path.basename(path)
                text = open(path, encoding="utf-8").read()
                pieces = chunk(text, size=512, overlap=0.1)
                for i, piece in enumerate(pieces):
                    embedding = embed_document(piece)
                    cur.execute(
                        """
                        INSERT INTO knowledge_chunks (source, chunk_index, content, embedding)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (source, i, piece, embedding),
                    )
                    total += 1
                print(f"{source}: {len(pieces)} chunks")
    print(f"Ingested {total} chunks total")

if __name__ == "__main__":
    ingest()
