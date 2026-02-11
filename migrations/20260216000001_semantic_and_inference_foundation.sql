-- Semantic Layer + Bayesian Inference foundation
--
-- Adds:
-- - semantic catalog (global concepts + variants)
-- - catalog/user embeddings (JSONB canonical store, optional pgvector column)
-- - inference run telemetry (diagnostics and failures)

-- Try enabling pgvector, but keep migration non-fatal if unavailable.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION
    WHEN insufficient_privilege OR undefined_file THEN
        RAISE NOTICE 'pgvector extension unavailable; semantic layer will run in fallback mode';
END
$$;

-- ---------------------------------------------------------------------------
-- Global semantic catalog (shared domain vocabulary)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS semantic_catalog (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain          TEXT NOT NULL CHECK (domain IN ('exercise', 'food')),
    canonical_key   TEXT NOT NULL,
    canonical_label TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (domain, canonical_key)
);

CREATE TABLE IF NOT EXISTS semantic_variants (
    id          BIGSERIAL PRIMARY KEY,
    catalog_id  UUID NOT NULL REFERENCES semantic_catalog(id) ON DELETE CASCADE,
    variant_text TEXT NOT NULL,
    locale      TEXT,
    source      TEXT NOT NULL DEFAULT 'seed',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_variants_unique
    ON semantic_variants (catalog_id, lower(variant_text));

CREATE INDEX IF NOT EXISTS idx_semantic_catalog_domain
    ON semantic_catalog (domain, canonical_key);

CREATE INDEX IF NOT EXISTS idx_semantic_variants_lookup
    ON semantic_variants (lower(variant_text));

-- ---------------------------------------------------------------------------
-- Embeddings
-- JSONB is the canonical cross-environment store.
-- Optional vector columns are used when pgvector exists.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS semantic_catalog_embeddings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_id  UUID NOT NULL REFERENCES semantic_catalog(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    dimensions  INT NOT NULL CHECK (dimensions > 0),
    embedding   JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (catalog_id, model)
);

CREATE TABLE IF NOT EXISTS semantic_user_embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    domain          TEXT NOT NULL CHECK (domain IN ('exercise', 'food')),
    term_text       TEXT NOT NULL,
    canonical_key   TEXT,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    dimensions      INT NOT NULL CHECK (dimensions > 0),
    embedding       JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, domain, term_text, model)
);

CREATE INDEX IF NOT EXISTS idx_semantic_user_embeddings_lookup
    ON semantic_user_embeddings (user_id, domain, term_text);

-- Optional vector columns + ANN indexes (if pgvector is present)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        ALTER TABLE semantic_catalog_embeddings
            ADD COLUMN IF NOT EXISTS embedding_vec vector(384);
        ALTER TABLE semantic_user_embeddings
            ADD COLUMN IF NOT EXISTS embedding_vec vector(384);

        CREATE INDEX IF NOT EXISTS idx_semantic_catalog_embeddings_vec
            ON semantic_catalog_embeddings USING ivfflat (embedding_vec vector_cosine_ops)
            WITH (lists = 100);

        CREATE INDEX IF NOT EXISTS idx_semantic_user_embeddings_vec
            ON semantic_user_embeddings USING ivfflat (embedding_vec vector_cosine_ops)
            WITH (lists = 100);
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Inference run telemetry
-- Stores diagnostics / failures for Bayesian workers.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inference_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    projection_type TEXT NOT NULL CHECK (projection_type IN ('strength_inference', 'readiness_inference')),
    key             TEXT NOT NULL,
    engine          TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('success', 'failed', 'skipped')),
    diagnostics     JSONB NOT NULL DEFAULT '{}',
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_inference_runs_user_projection
    ON inference_runs (user_id, projection_type, key, started_at DESC);

-- User-scoped RLS for user embeddings and inference telemetry
ALTER TABLE semantic_user_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE inference_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY semantic_user_embeddings_user_isolation ON semantic_user_embeddings
    USING (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY semantic_user_embeddings_user_insert ON semantic_user_embeddings
    FOR INSERT
    WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY inference_runs_user_isolation ON inference_runs
    USING (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY inference_runs_user_insert ON inference_runs
    FOR INSERT
    WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);

-- Grants
GRANT SELECT ON semantic_catalog, semantic_variants, semantic_catalog_embeddings TO app_reader;
GRANT SELECT ON semantic_catalog, semantic_variants, semantic_catalog_embeddings TO app_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON semantic_catalog, semantic_variants, semantic_catalog_embeddings TO app_worker;

GRANT SELECT ON semantic_user_embeddings, inference_runs TO app_reader;
GRANT SELECT, INSERT, UPDATE ON semantic_user_embeddings, inference_runs TO app_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON semantic_user_embeddings, inference_runs TO app_worker;

GRANT USAGE, SELECT ON SEQUENCE semantic_variants_id_seq TO app_worker;
