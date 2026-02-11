-- Extend inference telemetry to include causal_inference runs.

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    FOR constraint_name IN
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = 'inference_runs'::regclass
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) ILIKE '%projection_type%'
    LOOP
        EXECUTE format('ALTER TABLE inference_runs DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END
$$;

ALTER TABLE inference_runs
    ADD CONSTRAINT inference_runs_projection_type_check
    CHECK (
        projection_type IN (
            'strength_inference',
            'readiness_inference',
            'causal_inference'
        )
    );
