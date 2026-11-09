import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from app.db.session import get_admin_connection
from app.retrieval.embed import embed_document

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "hf_faqs_raw.json")
EVAL_SET_PATH = os.path.join(os.path.dirname(__file__), "..", "evals", "globex_eval_set.jsonl")

CONTRADICTION_KEYWORDS = [
    "refund", "return", "shipping", "international", "nepal",
    "delivery", "payment", "paypal", "cash on delivery", "cod",
    "final sale"
]

def load_raw_faqs() -> list[dict]:
    if not os.path.exists(DATA_PATH):
        print(f"Error: Raw dataset not found at {DATA_PATH}.")
        print("Please ensure data/hf_faqs_raw.json is present.")
        sys.exit(1)
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def filter_safe_for_acme(items: list[dict]) -> list[dict]:
    safe = []
    for item in items:
        q = item["question"].lower()
        a = item["answer"].lower()
        if not any(k in q or k in a for k in CONTRADICTION_KEYWORDS):
            safe.append(item)
    return safe

def export_globex_eval_set(items: list[dict]):
    """Exports questions and answers as an evaluation test set for Globex."""
    os.makedirs(os.path.dirname(EVAL_SET_PATH), exist_ok=True)
    with open(EVAL_SET_PATH, "w", encoding="utf-8") as f:
        for idx, item in enumerate(items):
            record = {
                "id": idx + 1,
                "question": item["question"],
                "expected_answer": item["answer"],
                "source": "faq_dataset_huggingface",
            }
            f.write(json.dumps(record) + "\n")
    print(f"Exported {len(items)} evaluation cases to {EVAL_SET_PATH}")

def ingest_tenant(tenant_id: str, items: list[dict]):
    print(f"\n==========================================")
    print(f"Starting ingestion for tenant: [{tenant_id}] ({len(items)} FAQs)")
    print(f"==========================================")

    conn = get_admin_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM knowledge_chunks WHERE tenant_id = %s AND source = 'faq_dataset_huggingface'",
                (tenant_id,),
            )
            cur.execute("DELETE FROM semantic_cache WHERE tenant_id = %s", (tenant_id,))

            for i, item in enumerate(items):
                content = f"Question: {item['question']}\nAnswer: {item['answer']}"
                
                if i > 0 and i % 15 == 0:
                    time.sleep(0.5)

                embedding = embed_document(content)
                cur.execute(
                    """
                    INSERT INTO knowledge_chunks (tenant_id, source, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (tenant_id, "faq_dataset_huggingface", i, content, embedding),
                )
                if (i + 1) % 20 == 0 or (i + 1) == len(items):
                    print(f"[{tenant_id}] Ingested {i + 1}/{len(items)} chunks...")

    print(f"Successfully finished ingestion for [{tenant_id}].")

def main():
    parser = argparse.ArgumentParser(description="Ingest Hugging Face FAQs into knowledge_chunks.")
    parser.add_argument(
        "--tenant",
        choices=["globex", "acme", "both"],
        default="both",
        help="Target tenant to populate (default: both)",
    )
    args = parser.parse_args()

    items = load_raw_faqs()
    print(f"Loaded {len(items)} total FAQs from {DATA_PATH}")

    export_globex_eval_set(items)

    if args.tenant in ("globex", "both"):
        ingest_tenant("globex", items)

    if args.tenant in ("acme", "both"):
        safe_items = filter_safe_for_acme(items)
        ingest_tenant("acme", safe_items)

    print("\n All tasks completed successfully.")

if __name__ == "__main__":
    main()
