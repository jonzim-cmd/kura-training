-- Provider OAuth/connection domain model (tm5.8)

CREATE TABLE IF NOT EXISTS provider_connections (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL REFERENCES users(id),
    provider                    TEXT NOT NULL,
    provider_account_id         TEXT NOT NULL,
    auth_state                  TEXT NOT NULL
                                CHECK (auth_state IN (
                                    'linked',
                                    'refresh_required',
                                    'revoked',
                                    'error'
                                )),
    scopes                      TEXT[] NOT NULL DEFAULT '{}',
    consented_at                TIMESTAMPTZ,
    token_expires_at            TIMESTAMPTZ,
    token_rotated_at            TIMESTAMPTZ,
    access_token_ref            TEXT,
    refresh_token_ref           TEXT,
    token_fingerprint           TEXT,
    sync_cursor                 TEXT,
    last_sync_at                TIMESTAMPTZ,
    last_oauth_state_nonce      TEXT,
    revoked_at                  TIMESTAMPTZ,
    revoked_reason              TEXT,
    revoked_by                  TEXT,
    last_error_code             TEXT,
    last_error_at               TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by                  TEXT,
    updated_by                  TEXT,
    UNIQUE (user_id, provider, provider_account_id)
);

CREATE INDEX idx_provider_connections_user_provider
    ON provider_connections (user_id, provider, auth_state, updated_at DESC);

ALTER TABLE provider_connections ENABLE ROW LEVEL SECURITY;

CREATE POLICY provider_connections_user_isolation ON provider_connections
    USING (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY provider_connections_user_insert ON provider_connections
    FOR INSERT
    WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);

CREATE POLICY provider_connections_user_update ON provider_connections
    FOR UPDATE
    USING (user_id = current_setting('kura.current_user_id', true)::UUID)
    WITH CHECK (user_id = current_setting('kura.current_user_id', true)::UUID);

GRANT SELECT ON provider_connections TO app_reader;
GRANT SELECT, INSERT, UPDATE ON provider_connections TO app_writer;
GRANT SELECT, INSERT, UPDATE ON provider_connections TO app_worker;
