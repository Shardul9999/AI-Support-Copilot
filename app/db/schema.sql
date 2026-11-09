CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tenants (
    slug       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO tenants (slug, name) VALUES ('acme', 'Acme Corp') ON CONFLICT (slug) DO NOTHING;
INSERT INTO tenants (slug, name) VALUES ('globex', 'Globex Corporation') ON CONFLICT (slug) DO NOTHING;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT NOT NULL DEFAULT 'acme',
    source       TEXT NOT NULL,
    chunk_index  INT NOT NULL,
    content      TEXT NOT NULL,
    embedding    VECTOR(768) NOT NULL,
    embed_model  TEXT NOT NULL DEFAULT 'gemini-embedding-001',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
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
    idempotency_key TEXT,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    resolution      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS tickets_tenant_idempotency_key
    ON tickets (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS tickets_tenant_id
    ON tickets (tenant_id);

CREATE TABLE IF NOT EXISTS semantic_cache (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    query_text      TEXT NOT NULL,
    query_embedding VECTOR(768) NOT NULL,
    response        JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours'
);

CREATE INDEX IF NOT EXISTS semantic_cache_tenant_id
    ON semantic_cache (tenant_id);

CREATE INDEX IF NOT EXISTS semantic_cache_expires_at
    ON semantic_cache (expires_at);

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

ALTER TABLE semantic_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE semantic_cache FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON semantic_cache;
CREATE POLICY tenant_isolation ON semantic_cache
    USING (tenant_id = current_setting('app.current_tenant', true))
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
