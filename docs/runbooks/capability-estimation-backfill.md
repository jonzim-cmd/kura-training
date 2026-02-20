# Capability Estimation Backfill Runbook

Use this runbook after deploying changes that affect `capability_estimation` outputs (strength/sprint/jump/endurance) and you need deterministic, immediate recomputation instead of waiting for organic event traffic.

## Trigger one-shot backfill

```bash
scripts/backfill-capability-estimation.sh --database-url "$DATABASE_URL"
```

Optional source label for traceability/idempotency namespace:

```bash
scripts/backfill-capability-estimation.sh \
  --database-url "$DATABASE_URL" \
  --source "deploy.capability_estimation_v1"
```

The command enqueues a single `inference.capability_backfill` job (deduplicated by `source` while pending/processing). The worker fans out deduplicated `projection.update` jobs per user and capability-relevant event type.

## Verification queries

Queue progress for the backfill controller job:

```sql
SELECT status, COUNT(*) AS jobs
FROM background_jobs
WHERE job_type = 'inference.capability_backfill'
  AND payload->>'source' = '<your-source>'
GROUP BY status
ORDER BY status;
```

Queue progress for fan-out projection updates:

```sql
SELECT status, COUNT(*) AS jobs
FROM background_jobs
WHERE job_type = 'projection.update'
  AND payload->>'source' = '<your-source>'
GROUP BY status
ORDER BY status;
```

Coverage (users with capability signals vs users with full 4-key capability projection):

```sql
WITH target_users AS (
    SELECT DISTINCT user_id
    FROM events
    WHERE event_type IN ('set.logged', 'session.logged', 'set.corrected', 'external.activity_imported')
),
capability_projection AS (
    SELECT user_id,
           COUNT(DISTINCT key) FILTER (
               WHERE key IN ('strength_1rm', 'sprint_max_speed', 'jump_height', 'endurance_threshold')
           ) AS capability_key_count
    FROM projections
    WHERE projection_type = 'capability_estimation'
    GROUP BY user_id
)
SELECT
    (SELECT COUNT(*) FROM target_users) AS users_with_capability_events,
    (SELECT COUNT(*) FROM capability_projection WHERE capability_key_count = 4)
        AS users_with_all_capability_keys,
    (
        SELECT COUNT(*)
        FROM target_users t
        LEFT JOIN capability_projection c ON c.user_id = t.user_id
        WHERE c.user_id IS NULL
    ) AS users_missing_capability_projection;
```

Lag/freshness (projection update time relative to latest capability signal):

```sql
WITH latest_signal AS (
    SELECT user_id, MAX(timestamp) AS last_signal_at
    FROM events
    WHERE event_type IN ('set.logged', 'session.logged', 'set.corrected', 'external.activity_imported')
    GROUP BY user_id
),
latest_projection AS (
    SELECT user_id, MAX(updated_at) AS last_projection_at
    FROM projections
    WHERE projection_type = 'capability_estimation'
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
```
