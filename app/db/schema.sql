CREATE EXTENSION IF NOT EXISTS vector;

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
