"""One-off admin script (Phase 7): creates/rotates the least-privilege
`app_role` Postgres role the running app connects as, so the Row-Level
Security policies in schema.sql actually apply. The Supabase-provided
`postgres` role (DATABASE_URL) has rolbypassrls = true — confirmed live via
`SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user` — so every
RLS policy is silently ignored for it by design. app_role has no such
attribute, so policies apply to it normally.

Run once (or again to rotate the password): python -m scripts.setup_app_role
Writes/updates APP_DATABASE_URL directly into .env — never prints the
password, so it doesn't end up in shell history or a captured log.
"""
import os
import secrets
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()

from psycopg import sql

from app.db.session import get_admin_connection

ROLE = "app_role"
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


def _build_app_url(admin_url: str, password: str) -> str:
    parts = urlsplit(admin_url)
    user = ROLE
    if parts.username and "." in parts.username:
        # Supabase pooler requires <role>.<project-ref> to route properly
        tenant_suffix = parts.username.split(".", 1)[1]
        user = f"{ROLE}.{tenant_suffix}"
    netloc = f"{user}:{password}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _write_env(app_url: str) -> None:
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if not line.startswith("APP_DATABASE_URL=")]
    lines.append(f"APP_DATABASE_URL={app_url}")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def setup():
    password = secrets.token_urlsafe(24)
    role_ident = sql.Identifier(ROLE)

    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,))
            exists = cur.fetchone() is not None

            if exists:
                cur.execute(sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(role_ident, sql.Literal(password)))
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(role_ident, sql.Literal(password))
                )

            cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(role_ident))
            cur.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role_ident))
            # Covers tables/sequences created after this script runs (e.g. Phase 8's
            # semantic_cache) without having to re-run this script every phase.
            cur.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(
                    role_ident
                )
            )
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(role_ident))

    app_url = _build_app_url(os.environ["DATABASE_URL"], password)
    _write_env(app_url)
    print(f"{ROLE} {'rotated' if exists else 'created'} — APP_DATABASE_URL written to .env")


if __name__ == "__main__":
    setup()
