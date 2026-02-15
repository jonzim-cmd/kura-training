# Supabase Auth Compatibility Report (Strategie B) - 2026-02-15

## Scope

This verification is limited to `AUTH_STRATEGY=B` (DB-only migration).
Kura API remains token issuer/validator against `users`, `api_keys`, `oauth_*` tables.
No production auth path was switched to Supabase Auth token issuance.

## Test Execution

- Command:
  - `cargo test -p kura-api routes::auth::tests -- --nocapture`
- Database:
  - Supabase session pooler (`aws-1-eu-west-1.pooler.supabase.com:5432/postgres`)
- Result:
  - `15 passed; 0 failed; 0 ignored`

## Auth Flows Covered

- Email/password login credential validation (`authenticate_email_password_user_id`)
- OAuth authorization code exchange with PKCE:
  - reject wrong verifier without consuming code
  - accept valid verifier and mark code `used_at`
- Refresh token rotation:
  - revoke old access/refresh token pair
  - issue new pair
  - reject replay of old refresh token
- Device grant token polling:
  - accept approved device code once
  - transition status to `consumed`
  - reject second use

## Data Integrity During Test Run

- Test records used unique prefixes (`auth-login-*`, `pkce-*`, `refresh-*`, `device-*`)
- Cleanup script removed all prefixed users/clients and dependent OAuth rows
- Post-cleanup verification:
  - `users_total = 1`
  - `users_test = 0`
  - `clients_test = 0`

## Conclusion

Auth behavior required for launch remains stable on Supabase Postgres under Strategy B.
Current rollout keeps auth logic in Kura API and does not depend on Supabase Auth runtime endpoints.
