#!/usr/bin/env bash
# setup-user.sh — Create a user account and API key for Kura
#
# Run this once after initial deployment to create your account.
# Uses the kura CLI image to run admin commands directly against the database.
#
# Prerequisites:
#   - Kura services running (./scripts/deploy.sh)
#   - docker/.env.production configured
#
# Usage:
#   ./scripts/setup-user.sh --email you@example.com --name "Your Name"

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Parse args ────────────────────────────────────────

EMAIL=""
DISPLAY_NAME=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --email)  EMAIL="$2"; shift 2 ;;
        --name)   DISPLAY_NAME="$2"; shift 2 ;;
        *)        error "Unknown argument: $1" ;;
    esac
done

if [ -z "$EMAIL" ] || [ -z "$DISPLAY_NAME" ]; then
    error "Usage: $0 --email you@example.com --name \"Your Name\""
fi

# ── Resolve paths and env ─────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ROOT_DIR}/docker/.env.production"
COMPOSE_FILE="${ROOT_DIR}/docker/compose.production.yml"

if [ ! -f "$ENV_FILE" ]; then
    error "Missing ${ENV_FILE}. Run deploy.sh first."
fi

# shellcheck disable=SC1090
source "$ENV_FILE"
# Resolve postgres container name (compose prefixes with project name)
PG_CONTAINER=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
    ps --format '{{.Name}}' kura-postgres 2>/dev/null | head -1)
PG_HOST="${PG_CONTAINER:-docker-kura-postgres-1}"
DB_URL="postgresql://kura:${KURA_DB_PASSWORD}@${PG_HOST}:5432/kura"

# Resolve Docker network name
NETWORK=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
    config --format json 2>/dev/null | python3 -c "
import sys,json
cfg=json.load(sys.stdin)
nets=cfg.get('networks',{})
for v in nets.values():
    if v.get('external'):
        print(v.get('name','')); break
" 2>/dev/null)
NETWORK="${NETWORK:-moltbot_moltbot-internal}"

# CLI image (built by deploy.sh --extract)
CLI_IMAGE="kura-cli:latest"
if ! docker image inspect "$CLI_IMAGE" >/dev/null 2>&1; then
    info "Building CLI image..."
    docker build --target cli -t kura-cli:latest -f "${ROOT_DIR}/Dockerfile" "$ROOT_DIR"
fi

# Helper: run kura CLI in a temporary container on the shared network
kura_cli() {
    docker run --rm --network "$NETWORK" \
        -e DATABASE_URL="$DB_URL" \
        "$CLI_IMAGE" "$@"
}

# ── Check services are running ────────────────────────

info "Checking kura-postgres is reachable..."
if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T kura-postgres \
    pg_isready -U kura -d kura >/dev/null 2>&1; then
    error "kura-postgres is not running. Start with: ./scripts/deploy.sh"
fi

# ── Create user ───────────────────────────────────────

# Generate a random password (user authenticates via API key, not password)
PASSWORD=$(openssl rand -base64 24)

info "Creating user: ${EMAIL}..."
USER_JSON=$(kura_cli admin create-user \
    --email "$EMAIL" \
    --password "$PASSWORD" \
    --display-name "$DISPLAY_NAME" 2>&1) || {
    if echo "$USER_JSON" | grep -q "duplicate key\|already exists\|23505"; then
        warn "User ${EMAIL} already exists."
        # Get user ID from DB
        USER_ID=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
            exec -T kura-postgres psql -U kura -d kura -t -A -c \
            "SELECT id FROM users WHERE email = '${EMAIL}';")
        USER_ID=$(echo "$USER_ID" | tr -d '[:space:]')
        if [ -z "$USER_ID" ]; then
            error "Could not find existing user ${EMAIL}"
        fi
        info "Found existing user: ${USER_ID}"
    else
        error "Failed to create user: ${USER_JSON}"
    fi
}

# Extract user_id from JSON output (if user was just created)
if [ -z "${USER_ID:-}" ]; then
    USER_ID=$(echo "$USER_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['user_id'])" 2>/dev/null) || {
        error "Could not parse user_id from: ${USER_JSON}"
    }
fi

info "User ID: ${USER_ID}"

# ── Create API key ────────────────────────────────────

info "Generating API key..."
KEY_JSON=$(kura_cli admin create-key \
    --user-id "$USER_ID" \
    --label "agent-primary" 2>&1) || {
    error "Failed to create API key: ${KEY_JSON}"
}

API_KEY=$(echo "$KEY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])" 2>/dev/null) || {
    error "Could not parse api_key from: ${KEY_JSON}"
}

# ── Output ────────────────────────────────────────────

echo ""
info "════════════════════════════════════════════════════"
info " User setup complete!"
info "════════════════════════════════════════════════════"
echo ""
info "User ID:  ${USER_ID}"
info "Email:    ${EMAIL}"
info "API Key:  ${API_KEY}"
echo ""
info "Configure Fred's gateway with:"
echo ""
echo "  KURA_API_URL=http://kura-api:3000"
echo "  KURA_API_KEY=${API_KEY}"
echo ""
info "Test from inside the gateway container:"
echo ""
echo "  KURA_API_URL=http://kura-api:3000 KURA_API_KEY=${API_KEY} /workspace/bin/kura health"
echo ""
echo -e "${RED}IMPORTANT: Save the API key now — it cannot be retrieved later!${NC}"
