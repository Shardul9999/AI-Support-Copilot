CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tenants (
    slug       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 'acme' is the tenant every other table's DEFAULT already assumed since Phase 2.
-- 'globex' exists purely so Phase 7's isolation test has a second, real tenant to
-- prove separation against, instead of asserting "zero rows" against a tenant that
-- was never populated in the first place.
INSERT INTO tenants (slug, name) VALUES ('acme', 'Acme Corp') ON CONFLICT (slug) DO NOTHING;
INSERT INTO tenants (slug, name) VALUES ('globex', 'Globex Corporation') ON CONFLICT (slug) DO NOTHING;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT NOT NULL DEFAULT 'acme',   -- real enforcement lands in Phase 7 (RLS); column exists now so it isn't retrofitted later
    source       TEXT NOT NULL,                  -- filename/doc title, for citations
    chunk_index  INT NOT NULL,                   -- position within the source doc
    content      TEXT NOT NULL,
    embedding    VECTOR(768) NOT NULL,           -- Gemini gemini-embedding-001, requested at 768 dims
    embed_model  TEXT NOT NULL DEFAULT 'gemini-embedding-001',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED  -- keyword half of hybrid search
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw
    ON knowledge_chunks
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS knowledge_chunks_tenant_id
    ON knowledge_chunks (tenant_id);

CREATE INDEX IF NOT EXISTS knowledge_chunks_search_vector_gin
    ON knowledge_chunks
    USING GIN (search_vector);

CREATE TABLE IF NOT EXISTS tickets (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'acme',
    idempotency_key TEXT,                 -- dedupes repeat webhook deliveries; NULL if caller didn't supply one
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | resolve | escalate | need_info (mirrors SupportResolution.action once processed)
    resolution      JSONB,                 -- the full SupportResolution, once the worker finishes
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial (not full-table) unique index: multiple NULL idempotency_keys are fine,
-- only an actually-supplied key must be unique per tenant.
CREATE UNIQUE INDEX IF NOT EXISTS tickets_tenant_idempotency_key
    ON tickets (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS tickets_tenant_id
    ON tickets (tenant_id);

CREATE TABLE IF NOT EXISTS semantic_cache (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    query_text      TEXT NOT NULL,          -- the embedded query, kept for debugging/inspection only — never matched on directly
    query_embedding VECTOR(768) NOT NULL,
    response        JSONB NOT NULL,         -- the full cached SupportResolution
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours'
);

-- No dedicated ANN index (HNSW/IVFFlat): this table stays small (only high-confidence
-- `resolve` results get cached — see app/pipeline.py) relative to knowledge_chunks, so
-- an unindexed <=> scan per lookup is fine, same reasoning as the earlier semantic-cache
-- pgvector decision in DECISIONS.md. Revisit if cache row count ever gets large.
CREATE INDEX IF NOT EXISTS semantic_cache_tenant_id
    ON semantic_cache (tenant_id);

CREATE INDEX IF NOT EXISTS semantic_cache_expires_at
    ON semantic_cache (expires_at);

-- Phase 7: Row-Level Security. The Supabase-provided `postgres` role this schema is
-- applied as has rolbypassrls = true (confirmed live: SELECT rolbypassrls FROM
-- pg_roles WHERE rolname = current_user), so every policy below is a deliberate
-- no-op for that role — it's the escape hatch admin/migration/ingestion scripts
-- keep using. The running app must connect as `app_role` instead (see
-- scripts/setup_app_role.py) for these policies to actually do anything.
--
-- current_setting(..., true) — the `true` means "return NULL instead of raising"
-- if app.current_tenant was never set for this transaction. tenant_id = NULL is
-- never true in SQL, so a connection that skips tenant resolution entirely (not
-- just skips the WHERE clause) still sees zero rows — fail-closed, not fail-open.
ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON knowledge_chunks;
CREATE POLICY tenant_isolation ON knowledge_chunks
    USING (tenant_id = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true));

ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON tickets;
CREATE POLICY tenant_isolation ON tickets
    USING (tenant_id = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true));

-- Phase 8: semantic_cache holds cached ticket resolutions, which can carry customer
-- PII from the original ticket body — same tenant-isolation stakes as tickets itself.
ALTER TABLE semantic_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE semantic_cache FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON semantic_cache;
CREATE POLICY tenant_isolation ON semantic_cache
    USING (tenant_id = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
