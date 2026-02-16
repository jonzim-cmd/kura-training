#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

DEFAULT_ENV_FILE="${ROOT_DIR}/docker/.env.production"
DEFAULT_LOG_FILE="${ROOT_DIR}/logs/pgdump-backup.log"
DEFAULT_SCHEDULE="15 3 * * *"
CRON_TAG="# kura-training-daily-pgdump-backup"

ENV_FILE="${KURA_BACKUP_ENV_FILE:-$DEFAULT_ENV_FILE}"
LOG_FILE="${KURA_BACKUP_LOG_FILE:-$DEFAULT_LOG_FILE}"
SCHEDULE="${KURA_BACKUP_CRON_SCHEDULE:-$DEFAULT_SCHEDULE}"
DRY_RUN="0"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  scripts/install_backup_cron.sh [options]

Options:
  --schedule "<cron expr>"   Cron schedule (default: "15 3 * * *")
  --env-file <path>          Env file for backup script (default: docker/.env.production)
  --log-file <path>          Log file path (default: logs/pgdump-backup.log)
  --dry-run                  Print resulting crontab without installing it
  -h, --help                 Show this help

Notes:
  - Installs one idempotent cron entry tagged with:
      # kura-training-daily-pgdump-backup
  - Requires the backup script:
      scripts/pgdump_s3_backup.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --schedule)
      [[ $# -ge 2 ]] || die "--schedule requires a value"
      SCHEDULE="$2"
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || die "--env-file requires a value"
      ENV_FILE="$2"
      shift 2
      ;;
    --log-file)
      [[ $# -ge 2 ]] || die "--log-file requires a value"
      LOG_FILE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="1"
      shift
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

mkdir -p "$(dirname "$LOG_FILE")"

BACKUP_SCRIPT="${ROOT_DIR}/scripts/pgdump_s3_backup.sh"
[[ -x "$BACKUP_SCRIPT" ]] || die "Backup script not executable: ${BACKUP_SCRIPT}"

cron_cmd="${SCHEDULE} [ -f ${ENV_FILE} ] && KURA_BACKUP_ENV_FILE=${ENV_FILE} /usr/bin/env bash ${BACKUP_SCRIPT} >> ${LOG_FILE} 2>&1 ${CRON_TAG}"
existing_crontab="$(crontab -l 2>/dev/null || true)"

filtered_crontab="$(printf '%s\n' "$existing_crontab" | grep -v "kura-training-daily-pgdump-backup" || true)"
if [[ -n "$filtered_crontab" ]]; then
  new_crontab="${filtered_crontab}"$'\n'"${cron_cmd}"$'\n'
else
  new_crontab="${cron_cmd}"$'\n'
fi

if [[ "$DRY_RUN" == "1" ]]; then
  printf '%s' "$new_crontab"
  exit 0
fi

printf '%s' "$new_crontab" | crontab -

printf 'Installed cron job:\n%s\n' "$cron_cmd"
printf 'Log file: %s\n' "$LOG_FILE"
