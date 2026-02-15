#!/usr/bin/env bash
# check-migration-drift.sh
# Blocks deployment when applied DB migrations drift from repository migrations.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

DATABASE_URL=""
MIGRATIONS_DIR=""

usage() {
    cat <<'USAGE'
Usage:
  scripts/check-migration-drift.sh --database-url <url> --migrations-dir <path>

Arguments:
  --database-url   Target database URL to validate against
  --migrations-dir Directory containing sqlx migration files (e.g. migrations/)
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --database-url)
            DATABASE_URL="${2:-}"
            shift 2
            ;;
        --migrations-dir)
            MIGRATIONS_DIR="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown argument: $1"
            ;;
    esac
done

[ -n "$DATABASE_URL" ] || error "--database-url is required"
[ -n "$MIGRATIONS_DIR" ] || error "--migrations-dir is required"
[ -d "$MIGRATIONS_DIR" ] || error "Migrations directory not found: $MIGRATIONS_DIR"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

REQUIRED_FILE="$TMP_DIR/required.txt"
APPLIED_FILE="$TMP_DIR/applied.txt"

find "$MIGRATIONS_DIR" -maxdepth 1 -type f -name '*.sql' -print \
    | sed -E 's#.*/([0-9]+)_.*\.sql#\1#' \
    | sort -u > "$REQUIRED_FILE"

if [ ! -s "$REQUIRED_FILE" ]; then
    error "No migration files found in: $MIGRATIONS_DIR"
fi

info "Checking migration drift against target database..."

SQL_APPLIED="
SELECT version::text
FROM _sqlx_migrations
WHERE success = TRUE
ORDER BY version;
"

if ! docker run --rm postgres:17 psql "$DATABASE_URL" -Atqc "$SQL_APPLIED" > "$APPLIED_FILE"; then
    error "Failed to query _sqlx_migrations. Check DATABASE_URL/network/auth."
fi

SQL_FAILED_COUNT="
SELECT COUNT(*)
FROM _sqlx_migrations
WHERE success = FALSE;
"

FAILED_COUNT="$(
    docker run --rm postgres:17 psql "$DATABASE_URL" -Atqc "$SQL_FAILED_COUNT" 2>/dev/null || echo "query_error"
)"
if [ "$FAILED_COUNT" = "query_error" ]; then
    error "Failed to query migration success status."
fi
if [ "$FAILED_COUNT" != "0" ]; then
    error "Database has $FAILED_COUNT failed migration entries in _sqlx_migrations."
fi

sort -u "$APPLIED_FILE" -o "$APPLIED_FILE"

MISSING_FILE="$TMP_DIR/missing.txt"
EXTRA_FILE="$TMP_DIR/extra.txt"

comm -23 "$REQUIRED_FILE" "$APPLIED_FILE" > "$MISSING_FILE"
comm -13 "$REQUIRED_FILE" "$APPLIED_FILE" > "$EXTRA_FILE"

if [ -s "$MISSING_FILE" ]; then
    echo "Missing migrations in database:"
    sed 's/^/  - /' "$MISSING_FILE"
    error "Schema drift detected (missing migrations)."
fi

if [ -s "$EXTRA_FILE" ]; then
    echo "Database has migrations not present in repository:"
    sed 's/^/  - /' "$EXTRA_FILE"
    error "Schema drift detected (unexpected applied migrations)."
fi

info "Migration drift check passed (required == applied)."
