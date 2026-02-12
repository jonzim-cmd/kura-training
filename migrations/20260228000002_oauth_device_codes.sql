-- OAuth 2.0 Device Authorization Grant support.
-- Used for CLI/MCP login where browser loopback callback is unavailable.

CREATE TABLE IF NOT EXISTS oauth_device_codes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_code_hash    TEXT NOT NULL UNIQUE,
    user_code_hash      TEXT NOT NULL UNIQUE,
    client_id           TEXT NOT NULL,
    scopes              TEXT[] NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'denied', 'consumed', 'expired')),
    approved_user_id    UUID REFERENCES users(id),
    interval_seconds    INTEGER NOT NULL DEFAULT 5
                        CHECK (interval_seconds BETWEEN 2 AND 30),
    poll_count          INTEGER NOT NULL DEFAULT 0 CHECK (poll_count >= 0),
    last_polled_at      TIMESTAMPTZ,
    approved_at         TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oauth_device_codes_client_status
    ON oauth_device_codes (client_id, status, expires_at DESC);

CREATE INDEX IF NOT EXISTS idx_oauth_device_codes_approved_user
    ON oauth_device_codes (approved_user_id, approved_at DESC)
    WHERE approved_user_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE ON oauth_device_codes TO app_writer;
GRANT SELECT ON oauth_device_codes TO app_reader;
