"""Diagnostic script to verify all connections and prerequisites for AI Support Copilot.

Checks:
1. DATABASE_URL (admin connection & tables)
2. APP_DATABASE_URL (RLS restricted role & permissions)
3. REDIS_URL (Celery broker / Upstash Redis connection)
4. GEMINI_API_KEY (embedding API)
5. GROQ_API_KEY (LLM inference API)
"""
import os
import ssl
import sys

from dotenv import load_dotenv

load_dotenv()


def check_mark(ok: bool) -> str:
    return "\033[92m[PASS]\033[0m" if ok else "\033[91m[FAIL]\033[0m"


def run_diagnostics():
    print("=" * 60)
    print("AI Support Copilot: Environment & Connection Diagnostics")
    print("=" * 60)

    all_passed = True

    # 1. Admin Postgres connection
    print("\n1. Checking DATABASE_URL (Admin Database Connection)...")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print(f" {check_mark(False)} DATABASE_URL is missing in .env")
        all_passed = False
    else:
        try:
            import psycopg

            with psycopg.connect(db_url, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version();")
                    ver = cur.fetchone()[0]
                    print(f" {check_mark(True)} Connected to Postgres: {ver.split(',')[0]}")

                    # Check tables
                    cur.execute("""
                        SELECT table_name FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name IN ('tenants', 'tickets', 'knowledge_chunks', 'semantic_cache');
                    """)
                    tables = {row[0] for row in cur.fetchall()}
                    required_tables = {'tenants', 'tickets', 'knowledge_chunks', 'semantic_cache'}
                    missing = required_tables - tables
                    if missing:
                        print(f" {check_mark(False)} Missing tables: {missing}. Fix by running: python -m scripts.apply_schema")
                        all_passed = False
                    else:
                        print(f" {check_mark(True)} All required tables exist: {required_tables}")

                    # Check tenant seeds
                    cur.execute("SELECT slug FROM tenants;")
                    tenants = [row[0] for row in cur.fetchall()]
                    print(f" {check_mark(True)} Seeded tenants: {tenants}")

        except Exception as e:
            print(f" {check_mark(False)} Admin Postgres connection failed: {e}")
            all_passed = False

    # 2. App Role Postgres connection
    print("\n2. Checking APP_DATABASE_URL (Restricted App Role & RLS)...")
    app_url = os.environ.get("APP_DATABASE_URL")
    if not app_url:
        print(f" {check_mark(False)} APP_DATABASE_URL is missing in .env. Fix by running: python -m scripts.setup_app_role")
        all_passed = False
    else:
        try:
            import psycopg
            from pgvector.psycopg import register_vector

            with psycopg.connect(app_url, connect_timeout=10) as conn:
                register_vector(conn)
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('app.current_tenant', 'acme', true);")
                    cur.execute("SELECT COUNT(*) FROM tickets WHERE tenant_id = 'acme';")
                    cnt = cur.fetchone()[0]
                    print(f" {check_mark(True)} app_role connected and can query tickets (count={cnt})")
        except Exception as e:
            print(f" {check_mark(False)} app_role connection failed: {e}")
            print("  -> Hint: Re-run `python -m scripts.setup_app_role` to regenerate app_role credentials.")
            all_passed = False

    # 3. Redis connection
    print("\n3. Checking REDIS_URL (Celery Broker / PubSub)...")
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        print(f" {check_mark(False)} REDIS_URL is missing in .env")
        all_passed = False
    else:
        try:
            import redis

            ssl_kwargs = {"ssl_cert_reqs": ssl.CERT_REQUIRED} if redis_url.startswith("rediss://") else {}
            r = redis.Redis.from_url(redis_url, socket_timeout=10, **ssl_kwargs)
            res = r.ping()
            print(f" {check_mark(True)} Redis ping successful: {res}")
            r.close()
        except Exception as e:
            print(f" {check_mark(False)} Redis connection failed: {e}")
            print("  -> Hint: Check your REDIS_URL in .env or check if Upstash Redis database is active.")
            all_passed = False

    # 4. Gemini API Key
    print("\n4. Checking GEMINI_API_KEY...")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print(f" {check_mark(False)} GEMINI_API_KEY is missing in .env")
        all_passed = False
    else:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=gemini_key)
            resp = client.models.embed_content(
                model="gemini-embedding-001",
                contents="test connection",
                config=types.EmbedContentConfig(output_dimensionality=768),
            )
            print(f" {check_mark(True)} Gemini embedding API working (vector dim: {len(resp.embedding.values)})")
        except Exception as e:
            print(f" {check_mark(False)} Gemini embedding failed: {e}")
            all_passed = False

    # 5. Groq API Key
    print("\n5. Checking GROQ_API_KEY...")
    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        print(f" {check_mark(False)} GROQ_API_KEY is missing in .env")
        all_passed = False
    else:
        try:
            from groq import Groq

            client = Groq(api_key=groq_key)
            models = client.models.list()
            print(f" {check_mark(True)} Groq API working (accessible models count: {len(models.data)})")
        except Exception as e:
            print(f" {check_mark(False)} Groq API call failed: {e}")
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("\033[92mALL CHECKS PASSED!\033[0m Your environment is fully configured.")
    else:
        print("\033[91mSOME CHECKS FAILED.\033[0m Review the error messages above.")
    print("=" * 60)


if __name__ == "__main__":
    run_diagnostics()
