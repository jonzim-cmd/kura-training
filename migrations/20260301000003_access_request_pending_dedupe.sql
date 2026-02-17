-- Access-request dedupe hardening:
-- Keep only one pending request per normalized email and enforce this
-- invariant via a partial unique index.

WITH ranked_pending AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY lower(email)
            ORDER BY created_at ASC, id ASC
        ) AS row_num
    FROM access_requests
    WHERE status = 'pending'
)
UPDATE access_requests ar
SET
    status = 'rejected',
    reviewed_at = COALESCE(ar.reviewed_at, NOW())
FROM ranked_pending rp
WHERE ar.id = rp.id
  AND rp.row_num > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_access_requests_pending_email_norm
    ON access_requests ((lower(email)))
    WHERE status = 'pending';
