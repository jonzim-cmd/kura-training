-- Extend eval harness artifacts to persist causal_inference rows.

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    FOR constraint_name IN
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = 'inference_eval_artifacts'::regclass
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) ILIKE '%projection_type%'
    LOOP
        EXECUTE format('ALTER TABLE inference_eval_artifacts DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END
$$;

ALTER TABLE inference_eval_artifacts
    ADD CONSTRAINT inference_eval_artifacts_projection_type_check
    CHECK (
        projection_type IN (
            'semantic_memory',
            'strength_inference',
            'readiness_inference',
            'causal_inference'
        )
    );
