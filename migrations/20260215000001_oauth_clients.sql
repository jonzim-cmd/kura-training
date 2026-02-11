-- OAuth client registry for authorize endpoint hardening.
-- Binds client_id to allowed redirect URI strategies.
--
-- For native CLI clients, loopback redirect URIs with random ports are allowed
-- (RFC 8252 style) when allow_loopback_redirect = TRUE.

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id               TEXT PRIMARY KEY,
    allowed_redirect_uris   TEXT[] NOT NULL DEFAULT '{}',
    allow_loopback_redirect BOOLEAN NOT NULL DEFAULT FALSE,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed default CLI client.
-- It uses dynamic loopback callbacks like http://127.0.0.1:<port>/callback.
INSERT INTO oauth_clients (client_id, allow_loopback_redirect, is_active)
VALUES ('kura-cli', TRUE, TRUE)
ON CONFLICT (client_id) DO NOTHING;

GRANT SELECT, INSERT, UPDATE ON oauth_clients TO app_writer;
GRANT SELECT ON oauth_clients TO app_reader;
