import concurrent.futures
from dotenv import load_dotenv
import pytest

load_dotenv()

from app.db.session import get_connection, get_app_pool, close_pools


def test_pool_returns_clean_connection_on_reuse():
    """Verify that when a connection is returned to the pool, its tenant context
    is completely cleared so subsequent checkouts never inherit residual state."""
    with get_connection("acme") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_setting('app.current_tenant', true)")
            assert cur.fetchone()[0] == "acme"

    # Next checkout without tenant should have no residual tenant
    pool = get_app_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_setting('app.current_tenant', true)")
            val = cur.fetchone()[0]
            assert val is None or val == ""


def test_concurrent_checkouts_isolate_tenants():
    """Verify concurrent threads checking out from the pool simultaneously
    maintain strict tenant isolation without race conditions."""
    def run_tenant_query(tenant: str):
        with get_connection(tenant) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_setting('app.current_tenant', true)")
                return cur.fetchone()[0]

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(run_tenant_query, "acme" if i % 2 == 0 else "globex")
            for i in range(10)
        ]
        results = [f.result() for f in futures]

    expected = ["acme" if i % 2 == 0 else "globex" for i in range(10)]
    assert results == expected
