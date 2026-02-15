-- Admin agent telemetry queries aggregate from api_access_log under app_worker role.
GRANT SELECT ON api_access_log TO app_worker;
