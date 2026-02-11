-- Extend population prior profile projection types to support causal_inference.

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    FOR constraint_name IN
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = 'population_prior_profiles'::regclass
          AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) ILIKE '%projection_type%'
    LOOP
        EXECUTE format('ALTER TABLE population_prior_profiles DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END
$$;

ALTER TABLE population_prior_profiles
    ADD CONSTRAINT population_prior_profiles_projection_type_check
    CHECK (
        projection_type IN (
            'strength_inference',
            'readiness_inference',
            'causal_inference'
        )
    );
