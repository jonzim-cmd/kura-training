-- Kura Training: PostgreSQL initialization
-- Runs once on first container start

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Database roles with least-privilege principle
-- app_writer: INSERT on events (the hot path)
-- app_reader: SELECT on projections and events
-- app_migrator: DDL operations (used by migration tool only)
-- app_worker: background job processing (BYPASSRLS for cross-user access)

DO $$
BEGIN
    -- Writer role: can only INSERT events, SELECT for reads
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_writer') THEN
        CREATE ROLE app_writer;
    END IF;

    -- Reader role: SELECT only
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_reader') THEN
        CREATE ROLE app_reader;
    END IF;

    -- Migrator role: full DDL
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_migrator') THEN
        CREATE ROLE app_migrator;
    END IF;

    -- Worker role: processes background jobs across all users
    -- Needs DELETE on projections for alias consolidation (stale projection cleanup)
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_worker') THEN
        CREATE ROLE app_worker BYPASSRLS;
    END IF;
END
$$;

-- Grant roles to the kura user (dev convenience — production uses separate users)
GRANT app_writer TO kura;
GRANT app_reader TO kura;
GRANT app_migrator TO kura;
GRANT app_worker TO kura;
