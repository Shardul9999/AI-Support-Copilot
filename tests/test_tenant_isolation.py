"""Phase 7 verification: proves Row-Level Security is storage-enforced, not
just app-level filtering — a query that "forgets" the tenant filter must
still return zero rows for another tenant's data, because Postgres appends
the policy itself. Requires a live Supabase connection (DATABASE_URL,
APP_DATABASE_URL) — not mocked, per this project's established practice of
verifying against the real thing rather than assuming.

Run: python -m pytest tests/test_tenant_isolation.py -v
"""
import os

import psycopg
import pytest
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

load_dotenv()

from app.db.session import get_admin_connection, get_connection

ZERO_VECTOR = [0.0] * 768


@pytest.fixture(scope="module")
def seeded_chunk_ids():
    """Insert one knowledge_chunks row per tenant (acme, globex) via the admin
    connection, so there's real cross-tenant data to try to leak. Both tenants
    already exist in `tenants` (seeded by schema.sql)."""
    ids = {}
    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            for tenant in ("acme", "globex"):
                cur.execute(
                    """
                    INSERT INTO knowledge_chunks (tenant_id, source, chunk_index, content, embedding)
                    VALUES (%s, 'test_isolation.md', 0, %s, %s)
                    RETURNING id
                    """,
                    (tenant, f"isolation test chunk for {tenant}", ZERO_VECTOR),
                )
                ids[tenant] = cur.fetchone()[0]
    yield ids
    with get_admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM knowledge_chunks WHERE id = ANY(%s)", (list(ids.values()),))


def test_query_without_tenant_filter_returns_zero_cross_tenant_rows(seeded_chunk_ids):
    """The actual Phase 7 demo goal: a query that forgets to filter by
    tenant_id must still return zero rows for another tenant's data — RLS,
    not app code, is what's actually protecting this."""
    globex_id = seeded_chunk_ids["globex"]

    with get_connection(tenant_id="acme") as conn:
        with conn.cursor() as cur:
            # deliberately omits "AND tenant_id = ..." — the exact bug RLS exists to catch
            cur.execute("SELECT id FROM knowledge_chunks WHERE id = %s", (globex_id,))
            assert cur.fetchone() is None


def test_vector_search_without_tenant_filter_also_isolated(seeded_chunk_ids):
    """Same proof, but against the actual <=> vector operator, not a scalar
    lookup — pgvector queries get the same protection as everything else,
    because RLS is a storage-layer policy, not something bolted onto
    specific query shapes."""
    with get_connection(tenant_id="acme") as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM knowledge_chunks ORDER BY embedding <=> %s::vector LIMIT 100",
                (ZERO_VECTOR,),
            )
            ids = {row[0] for row in cur.fetchall()}

    assert seeded_chunk_ids["globex"] not in ids
    assert seeded_chunk_ids["acme"] in ids


def test_tenant_scoped_query_still_sees_its_own_data(seeded_chunk_ids):
    """Sanity check the other direction — RLS isn't just blanket-denying
    everything, a correctly-scoped connection still sees its own tenant."""
    with get_connection(tenant_id="acme") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM knowledge_chunks WHERE id = %s", (seeded_chunk_ids["acme"],))
            assert cur.fetchone() is not None


def test_no_tenant_set_at_all_sees_nothing(seeded_chunk_ids):
    """current_setting('app.current_tenant', true) returns NULL if SET LOCAL
    was never called for this transaction, and tenant_id = NULL is never true
    in SQL — so a connection that skips tenant resolution entirely (not just
    skips the WHERE clause) still can't see any tenant's rows. Fail-closed,
    not fail-open. Opens app_role directly, bypassing get_connection(), since
    that helper always sets a tenant — this test needs a connection that
    deliberately doesn't."""
    conn = psycopg.connect(os.environ["APP_DATABASE_URL"])
    register_vector(conn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM knowledge_chunks WHERE id = ANY(%s)", (list(seeded_chunk_ids.values()),))
            assert cur.fetchall() == []
    finally:
        conn.rollback()
        conn.close()
