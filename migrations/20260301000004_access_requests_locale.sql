-- Add locale column to access_requests for invite email localization
ALTER TABLE access_requests ADD COLUMN locale TEXT NOT NULL DEFAULT 'en';
