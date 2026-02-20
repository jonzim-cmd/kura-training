#!/usr/bin/env bash
# One-shot objective foundation backfill trigger + verification.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

DATABASE_URL="${DATABASE_URL:-}"
SOURCE="manual.objective_backfill"
VERIFY=1

usage() {
    cat <<'USAGE'
Usage:
  scripts/backfill-objective-foundation.sh [--database-url <url>] [--source <label>] [--no-verify]

Arguments:
  --database-url   Target database URL (defaults to $DATABASE_URL)
  --source         Source label used for idempotency + observability (default: manual.objective_backfill)
  --no-verify      Skip post-enqueue verification queries
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --database-url)
            DATABASE_URL="${2:-}"
            shift 2
            ;;
        --source)
            SOURCE="${2:-}"
            shift 2
            ;;
        --no-verify)
            VERIFY=0
            shift
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

[ -n "$DATABASE_URL" ] || error "DATABASE_URL missing. Pass --database-url or export DATABASE_URL."
[ -n "$(printf '%s' "$SOURCE" | tr -d '[:space:]')" ] || error "--source must not be empty."

resolve_db_url_for_docker() {
    local url="$1"
    url="${url/@localhost:/@host.docker.internal:}"
    url="${url/@127.0.0.1:/@host.docker.internal:}"
    printf '%s' "$url"
}

run_query() {
    local sql="$1"
    if command -v psql >/dev/null 2>&1; then
        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -Atqc "$sql"
    else
        local docker_url
        docker_url="$(resolve_db_url_for_docker "$DATABASE_URL")"
        docker run --rm postgres:17 psql "$docker_url" -v ON_ERROR_STOP=1 -Atqc "$sql"
    fi
}

run_query_table() {
    local sql="$1"
    if command -v psql >/dev/null 2>&1; then
        psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "$sql"
    else
        local docker_url
        docker_url="$(resolve_db_url_for_docker "$DATABASE_URL")"
        docker run --rm postgres:17 psql "$docker_url" -v ON_ERROR_STOP=1 -c "$sql"
    fi
}

SOURCE_SQL="${SOURCE//\'/\'\'}"
EVENT_TYPES_SQL="'goal.set','objective.set','objective.updated','objective.archived','advisory.override.recorded','profile.updated','set.logged','session.logged','external.activity_imported'"

ENQUEUE_SQL="
WITH seed AS (
    SELECT user_id
    FROM events
    WHERE event_type IN (${EVENT_TYPES_SQL})
    ORDER BY timestamp DESC
    LIMIT 1
),
inflight AS (
    SELECT 1
    FROM background_jobs
    WHERE job_type = 'inference.objective_backfill'
      AND status IN ('pending', 'processing')
      AND payload->>'source' = '${SOURCE_SQL}'
    LIMIT 1
),
ins AS (
    INSERT INTO background_jobs (user_id, job_type, payload, priority, scheduled_for)
    SELECT seed.user_id,
           'inference.objective_backfill',
           jsonb_build_object(
               'source', '${SOURCE_SQL}',
               'event_types', jsonb_build_array(
                   'goal.set',
                   'objective.set',
                   'objective.updated',
                   'objective.archived',
                   'advisory.override.recorded',
                   'profile.updated',
                   'set.logged',
                   'session.logged',
                   'external.activity_imported'
               )
           ),
           4,
           NOW()
    FROM seed
    WHERE NOT EXISTS (SELECT 1 FROM inflight)
    RETURNING id
)
SELECT CASE
    WHEN NOT EXISTS (SELECT 1 FROM seed) THEN 'no_objective_seed_user'
    WHEN EXISTS (SELECT 1 FROM inflight) THEN 'already_inflight'
    WHEN EXISTS (SELECT 1 FROM ins) THEN 'enqueued:' || (SELECT id::text FROM ins LIMIT 1)
    ELSE 'skipped'
END;
"

info "Enqueueing inference.objective_backfill (source=${SOURCE})..."
ENQUEUE_STATUS="$(run_query "$ENQUEUE_SQL" 2>/dev/null || echo "failed")"

case "$ENQUEUE_STATUS" in
    enqueued:*)
        info "Backfill job created (${ENQUEUE_STATUS#enqueued:})."
        ;;
    already_inflight)
        info "Skipped enqueue (matching backfill job already pending/processing)."
        ;;
    no_objective_seed_user)
        warn "No users with objective-related event types yet; nothing enqueued."
        ;;
    *)
        error "Backfill enqueue failed or returned unexpected status: ${ENQUEUE_STATUS}"
        ;;
esac

if [ "$VERIFY" -ne 1 ]; then
    exit 0
fi

info "Queue status (backfill jobs)..."
run_query_table "
SELECT status, COUNT(*) AS jobs
FROM background_jobs
WHERE job_type = 'inference.objective_backfill'
  AND payload->>'source' = '${SOURCE_SQL}'
GROUP BY status
ORDER BY status;
"

info "Queue status (projection.update fan-out for this source)..."
run_query_table "
SELECT status, COUNT(*) AS jobs
FROM background_jobs
WHERE job_type = 'projection.update'
  AND payload->>'source' = '${SOURCE_SQL}'
GROUP BY status
ORDER BY status;
"

info "Coverage check (target users vs objective projections)..."
run_query_table "
WITH target_users AS (
    SELECT DISTINCT user_id
    FROM events
    WHERE event_type IN (${EVENT_TYPES_SQL})
),
objective_state_projection AS (
    SELECT DISTINCT user_id
    FROM projections
    WHERE projection_type = 'objective_state'
      AND key = 'active'
),
objective_advisory_projection AS (
    SELECT DISTINCT user_id
    FROM projections
    WHERE projection_type = 'objective_advisory'
      AND key = 'overview'
)
SELECT
    (SELECT COUNT(*) FROM target_users) AS users_with_objective_signals,
    (SELECT COUNT(*) FROM objective_state_projection) AS users_with_objective_state,
    (SELECT COUNT(*) FROM objective_advisory_projection) AS users_with_objective_advisory,
    (
        SELECT COUNT(*)
        FROM target_users t
        LEFT JOIN objective_state_projection s ON s.user_id = t.user_id
        LEFT JOIN objective_advisory_projection a ON a.user_id = t.user_id
        WHERE s.user_id IS NULL OR a.user_id IS NULL
    ) AS users_missing_objective_surfaces;
"

info "Lag check (projection freshness against latest objective signal)..."
run_query_table "
WITH latest_signal AS (
    SELECT user_id, MAX(timestamp) AS last_signal_at
    FROM events
    WHERE event_type IN (${EVENT_TYPES_SQL})
    GROUP BY user_id
),
latest_projection AS (
    SELECT user_id, MAX(updated_at) AS last_projection_at
    FROM projections
    WHERE (projection_type = 'objective_state' AND key = 'active')
       OR (projection_type = 'objective_advisory' AND key = 'overview')
    GROUP BY user_id
),
lag AS (
    SELECT
        s.user_id,
        s.last_signal_at,
        p.last_projection_at,
        CASE
            WHEN p.last_projection_at IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (p.last_projection_at - s.last_signal_at)) / 60.0
        END AS projection_minus_signal_minutes,
        CASE
            WHEN p.last_projection_at IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (NOW() - p.last_projection_at)) / 60.0
        END AS projection_age_minutes
    FROM latest_signal s
    LEFT JOIN latest_projection p ON p.user_id = s.user_id
)
SELECT
    COUNT(*) AS users_considered,
    COUNT(*) FILTER (WHERE last_projection_at IS NULL) AS users_without_projection,
    ROUND(AVG(projection_minus_signal_minutes)::numeric, 2) AS avg_projection_minus_signal_minutes,
    ROUND(AVG(projection_age_minutes)::numeric, 2) AS avg_projection_age_minutes,
    ROUND(MAX(projection_age_minutes)::numeric, 2) AS max_projection_age_minutes
FROM lag;
"

info "Objective foundation backfill command completed."
