-- Fix: app_worker needs SELECT + DELETE for DELETE ... WHERE to work.
-- Previously only DELETE was granted; PostgreSQL requires SELECT to evaluate
-- the WHERE clause.

GRANT SELECT ON security_abuse_telemetry TO app_worker;
GRANT SELECT ON security_kill_switch_audit TO app_worker;
GRANT SELECT ON support_access_audit TO app_worker;
