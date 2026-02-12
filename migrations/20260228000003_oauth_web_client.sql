-- Seed default OAuth client for web/native first-party OIDC login token issuance.

INSERT INTO oauth_clients (client_id, allow_loopback_redirect, is_active)
VALUES ('kura-web', FALSE, TRUE)
ON CONFLICT (client_id) DO NOTHING;
