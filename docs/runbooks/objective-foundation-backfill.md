# Objective Foundation Backfill Runbook

Use this runbook after deploying objective/advisory/modality foundation changes when existing users must be recalculated immediately instead of waiting for organic traffic.

## Trigger one-shot backfill

```bash
scripts/backfill-objective-foundation.sh --database-url "$DATABASE_URL"
```

Optional source label for traceability/idempotency namespace:

```bash
scripts/backfill-objective-foundation.sh \
  --database-url "$DATABASE_URL" \
  --source "deploy.objective_foundation_v1"
```

The command enqueues a single `inference.objective_backfill` job (deduplicated by `source` while pending/processing). The worker fans out deduplicated `projection.update` jobs per user and objective-relevant event type.

`include_all_users=true` is enabled and the script snapshots all `users.id` into `user_ids`, so users without prior events still receive seeded objective surfaces via synthetic `profile.updated` projection updates.

## Verification queries

Queue progress for the backfill controller job:

```sql
SELECT status, COUNT(*) AS jobs
FROM background_jobs
WHERE job_type = 'inference.objective_backfill'
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

Coverage (all users + signal users vs users with objective projections):

```sql
WITH all_users AS (
    SELECT id AS user_id
    FROM users
),
signal_users AS (
    SELECT DISTINCT user_id
    FROM events
    WHERE event_type IN (
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
    (SELECT COUNT(*) FROM all_users) AS users_total,
    (SELECT COUNT(*) FROM signal_users) AS users_with_objective_signals,
    (SELECT COUNT(*) FROM objective_state_projection) AS users_with_objective_state,
    (SELECT COUNT(*) FROM objective_advisory_projection) AS users_with_objective_advisory,
    (
        SELECT COUNT(*)
        FROM all_users t
        LEFT JOIN objective_state_projection s ON s.user_id = t.user_id
        LEFT JOIN objective_advisory_projection a ON a.user_id = t.user_id
        WHERE s.user_id IS NULL OR a.user_id IS NULL
    ) AS users_missing_objective_surfaces_all_users,
    (
        SELECT COUNT(*)
        FROM signal_users t
        LEFT JOIN objective_state_projection s ON s.user_id = t.user_id
        LEFT JOIN objective_advisory_projection a ON a.user_id = t.user_id
        WHERE s.user_id IS NULL OR a.user_id IS NULL
    ) AS users_missing_objective_surfaces_signal_users;
```

Lag/freshness (projection update time relative to latest objective signal):

```sql
WITH latest_signal AS (
    SELECT user_id, MAX(timestamp) AS last_signal_at
    FROM events
    WHERE event_type IN (
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
```
