-- ============================================================================
-- Lean4-Automata Forum Database Schema
-- ============================================================================
-- SQLite 3.35+ with WAL (Write-Ahead Logging) mode
-- Path: ~/.lean4a/forum.db (configurable via LEAN4A_FORUM_DB env var)
-- ============================================================================

-- Enable WAL mode for concurrent reads
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA cache_size=-64000;  -- 64MB cache
PRAGMA temp_store=MEMORY;

-- ============================================================================
-- Tickets: Stored findings from flow executions
-- ============================================================================
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,  -- tk_<ulid> format
    flow_name TEXT NOT NULL,
    input_json TEXT NOT NULL,  -- JSON string of input parameters
    output_json TEXT NOT NULL,  -- JSON string of flow output
    status TEXT NOT NULL CHECK(status IN ('success', 'error', 'timeout')),
    error_message TEXT,  -- Populated if status = 'error' or 'timeout'
    created_at INTEGER NOT NULL,  -- Unix timestamp (seconds since epoch)
    duration_ms INTEGER,  -- Execution duration in milliseconds
    tags TEXT  -- JSON array: ["lean", "sorry", "tph"]
);

CREATE INDEX IF NOT EXISTS idx_tickets_flow ON tickets(flow_name);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at DESC);

-- Example query: Find all failed tickets from last 24 hours
-- SELECT * FROM tickets 
-- WHERE status IN ('error', 'timeout') 
--   AND created_at > unixepoch() - 86400
-- ORDER BY created_at DESC;

-- ============================================================================
-- Sessions: Group tickets together
-- ============================================================================
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,  -- sess_<ulid> format
    name TEXT NOT NULL,  -- User-defined session name
    created_at INTEGER NOT NULL,  -- Unix timestamp
    ticket_ids_json TEXT  -- JSON array of ticket IDs: ["tk_abc", "tk_def"]
);

CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);

-- Example query: Get all tickets in a session
-- SELECT t.* FROM tickets t
-- JOIN json_each((SELECT ticket_ids_json FROM sessions WHERE id = ?)) AS je
-- ON t.id = je.value;

-- ============================================================================
-- Flow Definitions: Metadata about available flows
-- ============================================================================
CREATE TABLE IF NOT EXISTS flow_definitions (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    params_schema TEXT NOT NULL,  -- JSON schema for input parameters
    handler_type TEXT NOT NULL,  -- 'builtin' or 'proxy'
    target_url TEXT,  -- Target URL for proxy flows
    enabled BOOLEAN NOT NULL DEFAULT 1,
    tags TEXT  -- JSON array: ["lean", "sorry"]
);

-- Example insert:
-- INSERT INTO flow_definitions (name, description, params_schema, handler_type, target_url, enabled, tags)
-- VALUES (
--     'lean_sorry_scan',
--     'Scan Lean4 codebase for sorry statements',
--     '{"type":"object","properties":{"file_pattern":{"type":"string","default":"*.lean"}}}',
--     'builtin',
--     NULL,
--     1,
--     '["lean", "sorry"]'
-- );

-- ============================================================================
-- Flow Logs: Circular buffer of recent flow execution logs
-- ============================================================================
CREATE TABLE IF NOT EXISTS flow_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flow_name TEXT NOT NULL,
    ticket_id TEXT,  -- Associated ticket (if applicable)
    level TEXT NOT NULL,  -- 'info', 'error', 'stdout', 'stderr'
    message TEXT NOT NULL,
    timestamp INTEGER NOT NULL  -- Unix timestamp
);

CREATE INDEX IF NOT EXISTS idx_flow_logs_ticket ON flow_logs(ticket_id);
CREATE INDEX IF NOT EXISTS idx_flow_logs_timestamp ON flow_logs(timestamp DESC);

-- Circular buffer: Keep only last 1000 logs
CREATE TRIGGER IF NOT EXISTS trim_flow_logs
AFTER INSERT ON flow_logs
WHEN (SELECT COUNT(*) FROM flow_logs) > 1000
BEGIN
    DELETE FROM flow_logs
    WHERE id IN (
        SELECT id FROM flow_logs
        ORDER BY timestamp ASC
        LIMIT (SELECT COUNT(*) FROM flow_logs) - 1000
    );
END;

-- ============================================================================
-- Useful Queries
-- ============================================================================

-- Find tickets by tag:
-- SELECT * FROM tickets
-- WHERE EXISTS (
--     SELECT 1 FROM json_each(tags) WHERE value = 'sorry'
-- )
-- ORDER BY created_at DESC;

-- Session with ticket summaries:
-- SELECT 
--     s.id,
--     s.name,
--     COUNT(t.id) AS ticket_count,
--     SUM(CASE WHEN t.status = 'success' THEN 1 ELSE 0 END) AS success_count
-- FROM sessions s
-- LEFT JOIN json_each(s.ticket_ids_json) AS je
-- LEFT JOIN tickets t ON t.id = je.value
-- WHERE s.id = ?
-- GROUP BY s.id;

-- Flow execution statistics:
-- SELECT 
--     flow_name,
--     COUNT(*) AS total_runs,
--     SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
--     ROUND(AVG(duration_ms), 2) AS avg_duration_ms,
--     MAX(duration_ms) AS max_duration_ms
-- FROM tickets
-- GROUP BY flow_name
-- ORDER BY total_runs DESC;

-- Recent activity:
-- SELECT 
--     datetime(created_at, 'unixepoch', 'localtime') AS timestamp,
--     flow_name,
--     status,
--     duration_ms
-- FROM tickets
-- ORDER BY created_at DESC
-- LIMIT 20;

-- ============================================================================
-- Data Retention Policy (Optional)
-- ============================================================================

-- Delete tickets older than 90 days:
-- DELETE FROM tickets 
-- WHERE created_at < unixepoch() - (90 * 86400);

-- Delete orphaned sessions (no tickets):
-- DELETE FROM sessions
-- WHERE NOT EXISTS (
--     SELECT 1 FROM json_each(ticket_ids_json) AS je
--     WHERE EXISTS (SELECT 1 FROM tickets WHERE id = je.value)
-- );

-- ============================================================================
-- Vacuum and Optimization (Run periodically)
-- ============================================================================

-- VACUUM;  -- Reclaim space from deleted rows
-- ANALYZE; -- Update query planner statistics

-- ============================================================================
-- Notes
-- ============================================================================
-- 1. All timestamps are Unix timestamps (seconds since 1970-01-01 UTC)
-- 2. JSON fields use SQLite's json_each() for querying
-- 3. ULID format ensures chronological sorting by ID
-- 4. WAL mode allows concurrent reads during writes
-- 5. Circular buffer on flow_logs prevents unbounded growth
-- ============================================================================
