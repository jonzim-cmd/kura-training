# Supabase Surface Usage Audit - 2026-02-16

## Scope

Objective: verify which Supabase product surfaces are currently used by runtime code, so hardening can happen without reducing app UX or functionality.

Scanned runtime paths:

- `web/src`
- `api/src`
- `workers/src`
- `web/package.json`
- `docker/compose.production.yml`
- `CLAUDE.md` decision record

## Findings

1. **Auth runtime is DB-only (Strategy B)**
   - `CLAUDE.md` pins `AUTH_STRATEGY=B`.
   - Auth source of truth stays in `users`, `api_keys`, `oauth_*` tables.
   - No runtime dependency on Supabase Auth endpoints.

2. **Frontend talks to internal API, not Supabase client APIs**
   - `web/src/lib/api.ts` uses `NEXT_PUBLIC_API_URL`.
   - `web/src/lib/auth-context.tsx` calls internal `/v1/auth/*` endpoints.
   - `web/package.json` has no `@supabase/*` dependency.

3. **No direct runtime usage of Supabase Realtime/Storage/GraphQL endpoints**
   - No runtime references to `supabase.co/realtime/v1`, `supabase.co/storage/v1`, `supabase.co/graphql/v1`.
   - No `createClient(...)` or Supabase JS client setup in runtime code.

4. **Database security baseline in runtime DB is already strict**
   - All `public` tables in local runtime DB had `RLS = enabled`.
   - No `public` table with zero policies was found.

## Decision Impact

To preserve UX while reducing attack surface:

- Keep: Supabase Postgres as managed DB backend.
- Candidate for disable/hardening (no current runtime dependency): Supabase REST/RPC exposure, GraphQL endpoint, Realtime feature, unused Storage buckets.

## Guardrail Added

A new architecture contract test was added:

- `tests/architecture/test_127_supabase_surface_unused_contract.py`

It fails CI if runtime code introduces direct Supabase client surfaces or Supabase SDK dependencies without an explicit decision/change.
