-- Allow GitHub as a first-class social identity provider.
ALTER TABLE user_identities
    DROP CONSTRAINT IF EXISTS user_identities_provider_check;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'user_identities_provider_check'
    ) THEN
        ALTER TABLE user_identities
            ADD CONSTRAINT user_identities_provider_check
            CHECK (provider IN ('email_password', 'google', 'apple', 'github'));
    END IF;
END $$;
