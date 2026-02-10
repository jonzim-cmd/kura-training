#!/usr/bin/env bash
# seed-data.sh — Create 3 test users and inject synthetic training data
#
# Prerequisites:
#   - PostgreSQL running (docker compose -f docker/compose.yml up -d)
#   - API running (cargo run -p kura-api)
#
# Usage:
#   ./scripts/seed-data.sh              # 90 days per profile (default)
#   ./scripts/seed-data.sh 30           # 30 days per profile
#   ./scripts/seed-data.sh --novel-fields   # include novel fields + orphaned event types
#   KURA_API_URL=http://host:3000 ./scripts/seed-data.sh

set -euo pipefail

# Parse arguments: [days] [--novel-fields]
DAYS="90"
NOVEL_FLAGS=""
for arg in "$@"; do
    case "$arg" in
        --novel-fields) NOVEL_FLAGS="--novel-fields" ;;
        [0-9]*)         DAYS="$arg" ;;
    esac
done
API_URL="${KURA_API_URL:-http://localhost:3000}"
DB_URL="${DATABASE_URL:-postgres://kura:kura_dev_password@localhost:5432/kura}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Preflight checks ───────────────────────────────────

info "Checking prerequisites..."

# Check API is reachable
if ! curl -sf "${API_URL}/health" > /dev/null 2>&1; then
    error "API not reachable at ${API_URL}. Start it with: cargo run -p kura-api"
fi
info "API running at ${API_URL}"

# Check datagen is available
if ! (cd "${ROOT_DIR}/datagen" && uv run datagen --help > /dev/null 2>&1); then
    error "datagen not available. Run: cd datagen && uv sync"
fi
info "datagen available"

# ── Create users and keys ──────────────────────────────

declare -A USER_IDS
declare -A API_KEYS

PROFILES=("beginner" "intermediate" "advanced")
EMAILS=("beginner@test.kura.dev" "intermediate@test.kura.dev" "advanced@test.kura.dev")
NAMES=("Test Beginner" "Test Intermediate" "Test Advanced")

for i in 0 1 2; do
    profile="${PROFILES[$i]}"
    email="${EMAILS[$i]}"
    name="${NAMES[$i]}"

    info "Creating user: ${email} (${profile})..."

    # Create user (may already exist — check first)
    user_json=$(DATABASE_URL="${DB_URL}" cargo run -p kura-cli --quiet -- \
        admin create-user --email "${email}" --password "test-${profile}" \
        --display-name "${name}" 2>&1) || {
        # User might already exist (unique constraint)
        if echo "$user_json" | grep -q "duplicate key\|already exists\|23505"; then
            warn "User ${email} already exists — fetching user_id from DB..."
            user_id=$(psql "${DB_URL}" -t -A -c "SELECT id FROM users WHERE email = '${email}'")
            if [ -z "$user_id" ]; then
                error "Could not find user_id for ${email}"
            fi
            USER_IDS[$profile]="$user_id"

            info "Creating new API key for existing user ${profile}..."
            key_json=$(DATABASE_URL="${DB_URL}" cargo run -p kura-cli --quiet -- \
                admin create-key --user-id "${user_id}" --label "datagen-${profile}")
            api_key=$(echo "$key_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")
            API_KEYS[$profile]="$api_key"

            info "  user_id: ${user_id}"
            info "  api_key: ${api_key:0:20}..."
            continue
        else
            error "Failed to create user ${email}: ${user_json}"
        fi
    }

    user_id=$(echo "$user_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['user_id'])")
    USER_IDS[$profile]="$user_id"

    info "Creating API key for ${profile}..."
    key_json=$(DATABASE_URL="${DB_URL}" cargo run -p kura-cli --quiet -- \
        admin create-key --user-id "${user_id}" --label "datagen-${profile}")
    api_key=$(echo "$key_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")
    API_KEYS[$profile]="$api_key"

    info "  user_id: ${user_id}"
    info "  api_key: ${api_key:0:20}..."
done

# ── Inject synthetic data ──────────────────────────────

echo ""
info "Generating and injecting ${DAYS} days of data per profile..."
echo ""

for profile in "${PROFILES[@]}"; do
    api_key="${API_KEYS[$profile]}"

    info "Injecting ${profile} (${DAYS} days${NOVEL_FLAGS:+, novel fields})..."
    (cd "${ROOT_DIR}/datagen" && uv run datagen generate \
        --profile "${profile}" \
        --days "${DAYS}" \
        --api "${API_URL}" \
        --api-key "${api_key}" \
        ${NOVEL_FLAGS})
    echo ""
done

# ── Summary ────────────────────────────────────────────

echo ""
info "═══════════════════════════════════════════════════"
info " Seed data injection complete!"
info "═══════════════════════════════════════════════════"
echo ""
info "Users created:"
for profile in "${PROFILES[@]}"; do
    echo "  ${profile}: ${USER_IDS[$profile]}"
done
echo ""
info "Next step: Start the worker to process events into projections:"
echo ""
echo "  cd ${ROOT_DIR}/workers"
echo "  DATABASE_URL='${DB_URL}' uv run python -m kura_workers.main"
echo ""
info "Then verify projections:"
echo ""
echo "  curl -s -H 'Authorization: Bearer ${API_KEYS[intermediate]:0:20}...' \\"
echo "    ${API_URL}/v1/projections/user_profile/me | python3 -m json.tool"
echo ""
