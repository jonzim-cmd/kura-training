-- Durable scheduler state for recurring inference.nightly_refit execution.
-- Replaces fire-and-forget self-rescheduling with single-flight + recovery.

CREATE TABLE IF NOT EXISTS inference_scheduler_state (
    scheduler_key                        TEXT PRIMARY KEY,
    interval_hours                       INT NOT NULL CHECK (interval_hours > 0),
    next_run_at                          TIMESTAMPTZ NOT NULL,
    in_flight_job_id                     BIGINT REFERENCES background_jobs(id) ON DELETE SET NULL,
    in_flight_started_at                 TIMESTAMPTZ,
    last_run_started_at                  TIMESTAMPTZ,
    last_run_completed_at                TIMESTAMPTZ,
    last_run_status                      TEXT NOT NULL DEFAULT 'idle'
                                          CHECK (last_run_status IN ('idle', 'running', 'completed', 'failed')),
    last_error                           TEXT,
    last_enqueued_projection_updates     INT NOT NULL DEFAULT 0 CHECK (last_enqueued_projection_updates >= 0),
    last_missed_runs                     INT NOT NULL DEFAULT 0 CHECK (last_missed_runs >= 0),
    total_catch_up_runs                  BIGINT NOT NULL DEFAULT 0 CHECK (total_catch_up_runs >= 0),
    total_runs                           BIGINT NOT NULL DEFAULT 0 CHECK (total_runs >= 0),
    created_at                           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inference_scheduler_next_run
    ON inference_scheduler_state (next_run_at);

GRANT SELECT ON inference_scheduler_state TO app_reader;
GRANT SELECT ON inference_scheduler_state TO app_writer;
GRANT SELECT, INSERT, UPDATE ON inference_scheduler_state TO app_worker;
