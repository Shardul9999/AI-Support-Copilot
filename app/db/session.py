import contextlib
import os
import threading
from typing import Generator

from dotenv import load_dotenv
import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

load_dotenv()

_app_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

def _reset_connection_state(conn: psycopg.Connection) -> None:
    """Reset session state before returning a connection to the pool.
    Guarantees no tenant context, temporary state, or open transaction
    survives across connection checkout cycles."""
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.rollback()
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_tenant', '', false)")
    conn.commit()

def get_app_pool() -> ConnectionPool:
    """Lazy thread-safe initialization of the psycopg connection pool for app_role."""
    global _app_pool
    if _app_pool is None or _app_pool.closed:
        with _pool_lock:
            if _app_pool is None or _app_pool.closed:
                _app_pool = ConnectionPool(
                    os.environ["APP_DATABASE_URL"],
                    min_size=int(os.environ.get("DB_POOL_MIN_SIZE", 2)),
                    max_size=int(os.environ.get("DB_POOL_MAX_SIZE", 20)),
                    timeout=float(os.environ.get("DB_POOL_TIMEOUT", 30.0)),
                    configure=register_vector,
                    reset=_reset_connection_state,
                    open=True,
                )
    return _app_pool

def close_pools() -> None:
    """Drain and close all active database pools. Called during application shutdown."""
    global _app_pool
    with _pool_lock:
        if _app_pool is not None and not _app_pool.closed:
            _app_pool.close()
            _app_pool = None

@contextlib.contextmanager
def get_connection(tenant_id: str) -> Generator[psycopg.Connection, None, None]:
    """Connection for request-serving code paths. Connects as the restricted
    `app_role` (APP_DATABASE_URL — see scripts/setup_app_role.py), not the
    Supabase-provided `postgres` role, which has BYPASSRLS and would silently
    ignore every RLS policy in schema.sql. tenant_id is set via SET LOCAL, so
    it applies only for the lifetime of the transaction this connection's
    `with` block runs, not the physical connection itself — the right scope
    once connections are ever pooled, even though each call here still opens
    its own fresh connection today."""
    pool = get_app_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant_id,))
        yield conn

def get_admin_connection() -> psycopg.Connection:
    """Unrestricted connection for schema/migration/ingestion scripts only —
    never used on a request-serving path. Bypasses RLS by role attribute
    (rolbypassrls), not by omission of a tenant filter."""
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    register_vector(conn)
    return conn
