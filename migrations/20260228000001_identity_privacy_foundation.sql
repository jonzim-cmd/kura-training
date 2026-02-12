-- Privacy/Auth foundation v1:
-- - split identity mapping from core user data
-- - provide stable pseudonymous analysis subject IDs
-- - create immutable support access audit trail for break-glass flows

-- Identity links for login providers (email_password, google, apple).
CREATE TABLE IF NOT EXISTS user_identities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL
                        CHECK (provider IN ('email_password', 'google', 'apple')),
    provider_subject    TEXT NOT NULL,
    email_norm          TEXT,
    email_verified_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_subject)
);

CREATE INDEX IF NOT EXISTS idx_user_identities_user_id
    ON user_identities (user_id, provider);

CREATE INDEX IF NOT EXISTS idx_user_identities_provider_email
    ON user_identities (provider, email_norm)
    WHERE email_norm IS NOT NULL;

-- Stable pseudonymous subject IDs for analytics/debug paths.
CREATE TABLE IF NOT EXISTS analysis_subjects (
    user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    analysis_subject_id TEXT NOT NULL UNIQUE
                        CHECK (analysis_subject_id ~ '^asub_[a-z0-9]{32}$'),
    rotation_version    INTEGER NOT NULL DEFAULT 1 CHECK (rotation_version >= 1),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Break-glass support access audit log (append-only by policy).
CREATE TABLE IF NOT EXISTS support_access_audit (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor               TEXT NOT NULL CHECK (length(trim(actor)) > 0),
    target_user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason              TEXT NOT NULL CHECK (length(trim(reason)) >= 8),
    ticket_id           TEXT NOT NULL CHECK (length(trim(ticket_id)) > 0),
    requested_mode      TEXT NOT NULL
                        CHECK (requested_mode IN ('identity_lookup', 'incident_debug')),
    expires_at          TIMESTAMPTZ,
    details             JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_support_access_audit_target_user
    ON support_access_audit (target_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_access_audit_actor
    ON support_access_audit (actor, created_at DESC);

-- Backfill identity rows for existing local-email users.
INSERT INTO user_identities (
    user_id,
    provider,
    provider_subject,
    email_norm,
    email_verified_at
)
SELECT
    u.id,
    'email_password',
    lower(trim(u.email)),
    lower(trim(u.email)),
    NOW()
FROM users u
WHERE trim(u.email) <> ''
ON CONFLICT (provider, provider_subject) DO NOTHING;

-- Backfill stable pseudonymous subject IDs for all existing users.
INSERT INTO analysis_subjects (user_id, analysis_subject_id)
SELECT
    u.id,
    'asub_' || replace(gen_random_uuid()::text, '-', '')
FROM users u
ON CONFLICT (user_id) DO NOTHING;

-- Grants: API server needs read/write access to identity mapping and subject IDs.
GRANT SELECT, INSERT, UPDATE ON user_identities TO app_writer;
GRANT SELECT ON user_identities TO app_reader;

GRANT SELECT, INSERT, UPDATE ON analysis_subjects TO app_writer;
GRANT SELECT ON analysis_subjects TO app_reader;

GRANT SELECT, INSERT ON support_access_audit TO app_writer;
GRANT SELECT ON support_access_audit TO app_reader;

-- Keep support_access_audit append-only for app roles.
REVOKE UPDATE, DELETE ON support_access_audit FROM app_writer;
REVOKE UPDATE, DELETE ON support_access_audit FROM app_reader;
