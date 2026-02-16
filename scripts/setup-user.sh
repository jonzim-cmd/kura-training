#!/usr/bin/env bash
# setup-user.sh — Create a user account and API key for Kura
#
# Run this once after initial deployment to create your account.
# Uses the kura CLI image to run admin commands directly against the database.
#
# Connects to KURA_API_DATABASE_URL (Supabase or wherever the API runtime DB is).
#
# Prerequisites:
#   - Kura API has started at least once (migrations applied)
#   - docker/.env.production configured with KURA_API_DATABASE_URL
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
    error "Missing ${ENV_FILE}. Copy from .env.production.example and set required secrets."
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

# Use the runtime DB URL (same DB the API connects to)
DB_URL="${KURA_API_DATABASE_URL:?KURA_API_DATABASE_URL must be set in ${ENV_FILE}}"

# CLI image (built by deploy.sh or on demand)
CLI_IMAGE="kura-cli:latest"
if ! docker image inspect "$CLI_IMAGE" >/dev/null 2>&1; then
    info "Building CLI image..."
    docker build --target cli -t kura-cli:latest -f "${ROOT_DIR}/Dockerfile" "$ROOT_DIR"
fi

# Helper: run kura CLI in a temporary container
kura_cli() {
    docker run --rm \
        -e DATABASE_URL="$DB_URL" \
        "$CLI_IMAGE" "$@"
}

# ── Check DB is reachable ─────────────────────────────

info "Checking database is reachable..."
if ! kura_cli health >/dev/null 2>&1; then
    warn "CLI health check failed — the API may not have run migrations yet."
    warn "Make sure kura-api has started at least once before running this script."
    error "Database not reachable at KURA_API_DATABASE_URL."
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
        # Query user ID via CLI
        USER_ID=$(kura_cli admin create-user \
            --email "$EMAIL" \
            --password "$PASSWORD" \
            --display-name "$DISPLAY_NAME" 2>&1 | \
            python3 -c "import sys; print([l for l in sys.stdin if 'user_id' in l or 'id' in l][0])" 2>/dev/null) || true
        # Fallback: tell user to check manually
        if [ -z "${USER_ID:-}" ]; then
            error "User ${EMAIL} already exists. Check the database for the user ID and run:
  kura admin create-key --user-id <USER_ID> --label agent-primary"
        fi
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
info "Add to docker/.env.production:"
echo ""
echo "  KURA_API_KEY=${API_KEY}"
echo ""
info "Then start (or restart) the proxy:"
echo ""
echo "  docker compose --env-file docker/.env.production -f docker/compose.production.yml up -d kura-proxy"
echo ""
info "Fred uses the proxy (no key needed in gateway):"
echo ""
echo "  KURA_API_URL=http://kura-proxy:8320 /workspace/bin/kura health"
echo ""
echo -e "${RED}IMPORTANT: Save the API key now — it cannot be retrieved later!${NC}"
