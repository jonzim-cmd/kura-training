-- Grant INSERT on background_jobs to app_writer.
--
-- The trigger fn_enqueue_event_job() fires AFTER INSERT ON events.
-- When quality_health inserts compensating events as app_writer,
-- the trigger inherits that role and needs INSERT on background_jobs
-- (plus USAGE on the sequence for the serial PK).

GRANT INSERT ON background_jobs TO app_writer;
GRANT USAGE, SELECT ON SEQUENCE background_jobs_id_seq TO app_writer;
