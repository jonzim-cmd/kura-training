-- Auth tables: users, API keys, OAuth authorization codes, access tokens, refresh tokens

-- Users table (account, not per-event)
CREATE TABLE users (
    id              UUID PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    display_name    TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- API Keys (machine-to-machine, hashed storage)
CREATE TABLE api_keys (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    key_hash        TEXT NOT NULL UNIQUE,
    key_prefix      TEXT NOT NULL,
    label           TEXT NOT NULL,
    scopes          TEXT[] NOT NULL DEFAULT '{}',
    expires_at      TIMESTAMPTZ,
    last_used_at    TIMESTAMPTZ,
    is_revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_api_keys_user ON api_keys (user_id);
CREATE INDEX idx_api_keys_hash ON api_keys (key_hash) WHERE is_revoked = FALSE;

-- OAuth Authorization Codes (short-lived, one-time use)
CREATE TABLE oauth_authorization_codes (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    code_hash       TEXT NOT NULL UNIQUE,
    client_id       TEXT NOT NULL,
    redirect_uri    TEXT NOT NULL,
    code_challenge  TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    used_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- OAuth Access Tokens (short-lived, 1 hour)
CREATE TABLE oauth_access_tokens (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    token_hash      TEXT NOT NULL UNIQUE,
    client_id       TEXT NOT NULL,
    scopes          TEXT[] NOT NULL DEFAULT '{}',
    expires_at      TIMESTAMPTZ NOT NULL,
    is_revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_access_tokens_user ON oauth_access_tokens (user_id);
CREATE INDEX idx_access_tokens_hash ON oauth_access_tokens (token_hash) WHERE is_revoked = FALSE;

-- OAuth Refresh Tokens (long-lived, 90 days, rotation)
CREATE TABLE oauth_refresh_tokens (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id),
    token_hash      TEXT NOT NULL UNIQUE,
    access_token_id UUID NOT NULL REFERENCES oauth_access_tokens(id),
    client_id       TEXT NOT NULL,
    scopes          TEXT[] NOT NULL DEFAULT '{}',
    expires_at      TIMESTAMPTZ NOT NULL,
    is_revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_refresh_tokens_user ON oauth_refresh_tokens (user_id);

-- Permissions for existing roles
GRANT SELECT, INSERT, UPDATE ON users TO app_writer;
GRANT SELECT, INSERT, UPDATE ON api_keys TO app_writer;
GRANT SELECT, INSERT, UPDATE ON oauth_authorization_codes TO app_writer;
GRANT SELECT, INSERT, UPDATE ON oauth_access_tokens TO app_writer;
GRANT SELECT, INSERT, UPDATE ON oauth_refresh_tokens TO app_writer;
GRANT SELECT ON users, api_keys, oauth_access_tokens, oauth_refresh_tokens TO app_reader;
