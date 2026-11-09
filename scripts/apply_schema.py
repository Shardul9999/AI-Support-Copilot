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
