#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
DEFAULT_ENV_FILE="${ROOT_DIR}/docker/.env.production"

ENV_FILE="${KURA_BACKUP_ENV_FILE:-$DEFAULT_ENV_FILE}"

log() {
  printf '%s [backup] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

require_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 || die "Missing required command: $cmd"
}

require_env() {
  local key="$1"
  local value="${!key:-}"
  if [[ -z "${value}" ]]; then
    die "Missing required environment variable: ${key}"
  fi
}

usage() {
  cat <<'EOF'
Usage:
  scripts/pgdump_s3_backup.sh [--env-file <path>]

Description:
  Creates a compressed PostgreSQL dump via pg_dump and uploads it to
  an S3-compatible object storage bucket.

Environment:
  KURA_BACKUP_ENV_FILE       Optional env file path (default: docker/.env.production)
  KURA_BACKUP_DATABASE_URL   Optional DB URL override
  KURA_API_DATABASE_URL      Fallback DB URL (prod)
  DATABASE_URL               Fallback DB URL (local/dev)
  BACKUP_S3_BUCKET           Target bucket
  BACKUP_S3_PREFIX           Target prefix (default: kura-training/postgres)
  BACKUP_S3_ENDPOINT_URL     Optional custom S3 endpoint (R2/B2/etc.)
  AWS_REGION / AWS_DEFAULT_REGION
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ $# -ge 2 ]] || die "--env-file requires a value"
      ENV_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  log "Env file not found at ${ENV_FILE}, using current process environment only."
fi

DB_URL="${KURA_BACKUP_DATABASE_URL:-${KURA_API_DATABASE_URL:-${DATABASE_URL:-}}}"
BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-}"
BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-kura-training/postgres}"
BACKUP_S3_ENDPOINT_URL="${BACKUP_S3_ENDPOINT_URL:-}"
BACKUP_AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"

require_cmd pg_dump
require_cmd gzip
require_cmd aws
require_env DB_URL
require_env BACKUP_S3_BUCKET
require_env AWS_ACCESS_KEY_ID
require_env AWS_SECRET_ACCESS_KEY

if command -v sha256sum >/dev/null 2>&1; then
  SHA_CMD=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
  SHA_CMD=(shasum -a 256)
else
  die "Missing checksum command: sha256sum or shasum"
fi

timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
date_path="$(date -u +"%Y/%m/%d")"
host_tag="$(hostname -s 2>/dev/null || hostname || echo unknown-host)"
db_name="${DB_URL##*/}"
db_name="${db_name%%\?*}"
if [[ -z "$db_name" ]]; then
  db_name="postgres"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

dump_name="${db_name}_${host_tag}_${timestamp}.sql.gz"
dump_path="${tmp_dir}/${dump_name}"
checksum_path="${dump_path}.sha256"

prefix="${BACKUP_S3_PREFIX#/}"
prefix="${prefix%/}"
remote_base="s3://${BACKUP_S3_BUCKET}/${prefix}/${date_path}"
remote_dump="${remote_base}/${dump_name}"
remote_checksum="${remote_base}/${dump_name}.sha256"

aws_args=()
if [[ -n "$BACKUP_S3_ENDPOINT_URL" ]]; then
  aws_args+=(--endpoint-url "$BACKUP_S3_ENDPOINT_URL")
fi
if [[ -n "$BACKUP_AWS_REGION" ]]; then
  aws_args+=(--region "$BACKUP_AWS_REGION")
fi

log "Creating compressed dump for database '${db_name}'"
PGOPTIONS='-c statement_timeout=0' pg_dump --no-owner --no-privileges --format=plain "$DB_URL" | gzip -9 > "$dump_path"
"${SHA_CMD[@]}" "$dump_path" > "$checksum_path"

dump_size_bytes="$(wc -c < "$dump_path" | tr -d ' ')"
log "Dump created (${dump_size_bytes} bytes): ${dump_name}"

log "Uploading dump to ${remote_dump}"
aws "${aws_args[@]}" s3 cp "$dump_path" "$remote_dump" --only-show-errors
aws "${aws_args[@]}" s3 cp "$checksum_path" "$remote_checksum" --only-show-errors

log "Backup upload finished successfully"
log "Remote objects:"
log "  - ${remote_dump}"
log "  - ${remote_checksum}"
