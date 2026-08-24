import os

import psycopg
from pgvector.psycopg import register_vector


def get_connection(tenant_id: str) -> psycopg.Connection:
    """Connection for request-serving code paths. Connects as the restricted
    `app_role` (APP_DATABASE_URL — see scripts/setup_app_role.py), not the
    Supabase-provided `postgres` role, which has BYPASSRLS and would silently
    ignore every RLS policy in schema.sql. tenant_id is set via SET LOCAL, so
    it applies only for the lifetime of the transaction this connection's
    `with` block runs, not the physical connection itself — the right scope
    once connections are ever pooled, even though each call here still opens
    its own fresh connection today."""
    conn = psycopg.connect(os.environ["APP_DATABASE_URL"])
    register_vector(conn)
    with conn.cursor() as cur:
        # set_config(..., is_local=true) is SET LOCAL's parameterized equivalent —
        # plain `SET LOCAL app.current_tenant = %s` fails: Postgres's SET command
        # only accepts a literal there, not a bind parameter (confirmed live:
        # "syntax error at or near '$1'" under the extended query protocol).
        cur.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant_id,))
    return conn


def get_admin_connection() -> psycopg.Connection:
    """Unrestricted connection for schema/migration/ingestion scripts only —
    never used on a request-serving path. Bypasses RLS by role attribute
    (rolbypassrls), not by omission of a tenant filter."""
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    register_vector(conn)
    return conn
