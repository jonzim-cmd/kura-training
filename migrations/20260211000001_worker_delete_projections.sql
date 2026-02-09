-- Grant DELETE on projections to app_worker.
-- Needed for alias consolidation: exercise_progression DELETEs stale
-- alias-named projections when an alias maps them to a canonical exercise.
GRANT DELETE ON projections TO app_worker;
