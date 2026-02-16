#!/usr/bin/env bash
# deploy.sh — Build and deploy Kura on VPS
#
# Run from the kura-training project root on the VPS.
#
# Prerequisites:
#   - docker/ .env.production exists with KURA_DB_PASSWORD, KURA_API_KEY,
#     KURA_AGENT_MODEL_ATTESTATION_SECRET and public routing values set
#   - moltbot-internal Docker network exists
#   - DOCKER_HOST set for rootless Docker (if applicable)
#
# Usage:
#   ./scripts/deploy.sh              # Build + start
#   ./scripts/deploy.sh --extract    # Also extract CLI binary for Fred

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="${ROOT_DIR}/docker/compose.production.yml"
ENV_FILE="${ROOT_DIR}/docker/.env.production"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Preflight ─────────────────────────────────────────

if [ ! -f "$ENV_FILE" ]; then
    error "Missing ${ENV_FILE}. Copy from .env.production.example and set required secrets."
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

require_env() {
    local key="$1"
    local hint="$2"
    local value="${!key:-}"
    local trimmed
    trimmed="$(printf '%s' "$value" | tr -d '[:space:]')"
    if [ -z "$trimmed" ]; then
        error "${key} is missing/empty in ${ENV_FILE}. ${hint}"
    fi
    if [ "$value" = "CHANGE_ME" ]; then
        error "${key} is still set to CHANGE_ME in ${ENV_FILE}. ${hint}"
    fi
}

require_env "KURA_DB_PASSWORD" "Generate with: openssl rand -hex 24"
require_env "KURA_AGENT_MODEL_ATTESTATION_SECRET" "Generate with: openssl rand -hex 32"

# KURA_API_KEY is optional on first deploy (proxy skipped until setup-user.sh runs)
SKIP_PROXY=false
_api_key="${KURA_API_KEY:-}"
if [ -z "$_api_key" ] || [ "$_api_key" = "CHANGE_ME" ]; then
    warn "KURA_API_KEY not set — kura-proxy will be skipped."
    warn "After deploy, run: ./scripts/setup-user.sh --email you@example.com --name \"Your Name\""
    SKIP_PROXY=true
fi
require_env "KURA_API_DATABASE_URL" "Set Supabase DB URL for API runtime."
require_env "KURA_WORKER_DATABASE_URL" "Set Supabase DB URL for worker runtime."
require_env "KURA_WEB_PUBLIC_API_URL" "Set public API base URL for web runtime (e.g. https://api.withkura.com)."
require_env "KURA_WEB_PUBLIC_MCP_URL" "Set public MCP URL for setup UI (e.g. https://api.withkura.com/mcp)."
require_env "KURA_FRONTEND_URL" "Set canonical web URL for auth/reset links (e.g. https://withkura.com)."
require_env "KURA_CORS_ORIGINS" "Set allowed browser origins (comma-separated, e.g. https://withkura.com,https://www.withkura.com)."
require_env "SUPABASE_URL" "Set Supabase project URL (e.g. https://<project-ref>.supabase.co)."
require_env "SUPABASE_ANON_KEY" "Set Supabase anon key for social-login session validation."

# Resolve target DB URL for migration drift preflight.
TARGET_DATABASE_URL="${KURA_API_DATABASE_URL}"

# Check moltbot-internal network exists
if ! docker network inspect moltbot-internal >/dev/null 2>&1; then
    warn "moltbot-internal network not found. Creating it..."
    docker network create moltbot-internal
fi

# ── Build ─────────────────────────────────────────────

info "Building Docker images..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build

# Block deploy when DB migration state drifts from repository migrations.
info "Running migration drift preflight..."
"${ROOT_DIR}/scripts/check-migration-drift.sh" \
    --database-url "$TARGET_DATABASE_URL" \
    --migrations-dir "${ROOT_DIR}/migrations"

# ── Start ─────────────────────────────────────────────

info "Starting core services..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d kura-postgres kura-api kura-worker
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d kura-web

# nginx resolves upstream IPs on startup. Force proxy recreation so it always picks
# up the latest kura-api container IP after API recreation during deploy/rollback.
if [ "$SKIP_PROXY" = "false" ]; then
    info "Recreating kura-proxy to refresh upstream binding..."
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --force-recreate kura-proxy
else
    info "Skipping kura-proxy (KURA_API_KEY not set). Run setup-user.sh first."
fi

# ── Wait for healthy ──────────────────────────────────

info "Waiting for kura-api to become healthy..."
for i in $(seq 1 30); do
    if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T kura-api \
        curl -sf http://localhost:3000/health >/dev/null 2>&1; then
        info "kura-api is healthy!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        error "kura-api did not become healthy within 30 seconds. Check logs: docker compose -f $COMPOSE_FILE logs kura-api"
    fi
    sleep 1
done

info "Waiting for kura-worker to become healthy..."
for i in $(seq 1 30); do
    if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T kura-worker \
        curl -sf http://localhost:8081/health >/dev/null 2>&1; then
        info "kura-worker is healthy!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        warn "kura-worker not healthy yet — it may still be starting. Check logs."
    fi
    sleep 1
done

info "Waiting for kura-web to become healthy..."
for i in $(seq 1 30); do
    if docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T kura-web \
        node -e "fetch('http://localhost:3000').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" >/dev/null 2>&1; then
        info "kura-web is healthy!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        warn "kura-web not healthy yet — it may still be starting. Check logs."
    fi
    sleep 1
done

# ── Extract CLI binary ────────────────────────────────

if [[ "${1:-}" == "--extract" ]]; then
    CLI_DEST="${CLI_DEST:-$HOME/moltbot/workspace/bin}"
    info "Extracting kura CLI binary to ${CLI_DEST}..."
    mkdir -p "$CLI_DEST"

    # Build CLI image and extract binary
    docker build --target cli -t kura-cli:latest -f "${ROOT_DIR}/Dockerfile" "$ROOT_DIR"
    CONTAINER=$(docker create kura-cli:latest)
    docker cp "$CONTAINER:/usr/local/bin/kura" "$CLI_DEST/kura"
    docker rm "$CONTAINER" >/dev/null
    chmod +x "$CLI_DEST/kura"

    info "CLI binary installed at ${CLI_DEST}/kura"
    info "Test: KURA_API_URL=http://kura-proxy:8320 ${CLI_DEST}/kura health"
fi

# ── Summary ───────────────────────────────────────────

echo ""
info "════════════════════════════════════════════════════"
info " Kura deployment complete!"
info "════════════════════════════════════════════════════"
echo ""
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
echo ""
info "Logs: docker compose --env-file $ENV_FILE -f $COMPOSE_FILE logs -f"
info "Stop: docker compose --env-file $ENV_FILE -f $COMPOSE_FILE down"
echo ""

if [[ "${1:-}" != "--extract" ]]; then
    info "To extract CLI for Fred: ./scripts/deploy.sh --extract"
fi
