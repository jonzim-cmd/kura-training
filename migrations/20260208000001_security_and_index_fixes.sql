-- Security: Force RLS even for table owners / superusers
-- Without this, connections as the table owner silently bypass all RLS policies
ALTER TABLE events FORCE ROW LEVEL SECURITY;

-- Performance: Optimized index for cursor-based pagination
-- Cursor uses (timestamp DESC, id DESC) for stable ordering, needs matching index
-- Replaces idx_events_user_timestamp which lacked the id column for tie-breaking
DROP INDEX IF EXISTS idx_events_user_timestamp;
CREATE INDEX idx_events_user_timestamp_id ON events (user_id, timestamp DESC, id DESC);

-- Performance: Add id to type index for cursor pagination with event_type filter
DROP INDEX IF EXISTS idx_events_user_type;
CREATE INDEX idx_events_user_type ON events (user_id, event_type, timestamp DESC, id DESC);
