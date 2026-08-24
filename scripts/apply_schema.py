"""Applies app/db/schema.sql to the database at DATABASE_URL (the admin/owner
connection — schema changes need to create tables/roles/policies, not just
read/write rows, so this deliberately doesn't use the restricted app_role).
Safe to re-run: every statement in schema.sql is idempotent (IF NOT EXISTS,
DROP POLICY IF EXISTS, ON CONFLICT DO NOTHING).

Run from the repo root: python -m scripts.apply_schema
"""
import os

from dotenv import load_dotenv

load_dotenv()

import psycopg

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "db", "schema.sql")


def apply():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema_sql = f.read()

    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
    finally:
        conn.close()
    print("schema applied")


if __name__ == "__main__":
    apply()
