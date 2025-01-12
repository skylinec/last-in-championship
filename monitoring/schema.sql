-- First ensure the table exists
CREATE TABLE IF NOT EXISTS monitoring_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_type VARCHAR(50) NOT NULL,
    details JSONB,
    status VARCHAR(20) DEFAULT 'success'
);

-- Add index for monitoring_logs if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes 
        WHERE tablename = 'monitoring_logs' 
        AND indexname = 'idx_monitoring_logs_timestamp'
    ) THEN
        CREATE INDEX idx_monitoring_logs_timestamp ON monitoring_logs(timestamp DESC);
    END IF;
END $$;

-- Add unique constraint to user_streaks if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'user_streaks_username_key' 
        AND conrelid = 'user_streaks'::regclass
    ) THEN
        ALTER TABLE user_streaks ADD CONSTRAINT user_streaks_username_key UNIQUE (username);
    END IF;
END $$;

-- Create cleanup function
CREATE OR REPLACE FUNCTION cleanup_monitoring_logs() RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM monitoring_logs 
    WHERE id IN (
        SELECT id 
        FROM monitoring_logs 
        ORDER BY timestamp DESC 
        OFFSET 5000
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger only if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger 
        WHERE tgname = 'trg_cleanup_monitoring_logs' 
        AND tgrelid = 'monitoring_logs'::regclass
    ) THEN
        CREATE TRIGGER trg_cleanup_monitoring_logs
        AFTER INSERT ON monitoring_logs
        FOR EACH STATEMENT
        EXECUTE FUNCTION cleanup_monitoring_logs();
    END IF;
END $$;

-- Add tie breaker tables
CREATE TABLE IF NOT EXISTS tie_breakers (
    id SERIAL PRIMARY KEY,
    period VARCHAR(10) NOT NULL, -- 'week' or 'month'
    period_end DATE NOT NULL,
    points DECIMAL NOT NULL,
    status VARCHAR(20) DEFAULT 'pending', -- pending, in_progress, completed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tie_breaker_participants (
    id SERIAL PRIMARY KEY,
    tie_breaker_id INTEGER REFERENCES tie_breakers(id),
    username VARCHAR(50) NOT NULL,
    game_choice VARCHAR(20), -- 'tictactoe' or 'connect4'
    ready BOOLEAN DEFAULT false,
    winner BOOLEAN,
    UNIQUE(tie_breaker_id, username)
);

CREATE TABLE IF NOT EXISTS tie_breaker_games (
    id SERIAL PRIMARY KEY,
    tie_breaker_id INTEGER REFERENCES tie_breakers(id),
    game_type VARCHAR(20) NOT NULL,
    player1 VARCHAR(50) NOT NULL,
    player2 VARCHAR(50) NOT NULL,
    game_state JSONB,
    winner VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Add indices
CREATE INDEX IF NOT EXISTS idx_tiebreakers_status ON tie_breakers(status);
CREATE INDEX IF NOT EXISTS idx_tiebreakers_period_end ON tie_breakers(period_end);

-- Add tie breaker settings columns if they don't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                  WHERE table_name = 'settings' 
                  AND column_name = 'enable_tiebreakers') THEN
        ALTER TABLE settings 
        ADD COLUMN enable_tiebreakers BOOLEAN DEFAULT false,
        ADD COLUMN tiebreaker_points INTEGER DEFAULT 5,
        ADD COLUMN tiebreaker_expiry INTEGER DEFAULT 24,
        ADD COLUMN auto_resolve_tiebreakers BOOLEAN DEFAULT false;
    END IF;
END $$;

-- Create rankings materialized view
CREATE MATERIALIZED VIEW IF NOT EXISTS rankings AS
WITH daily_scores AS (
    SELECT 
        e.name as username,
        e.date::date,
        e.status,
        e.time,
        CASE 
            WHEN e.status = 'in-office' THEN 
                (SELECT points->>'in_office' FROM settings LIMIT 1)::numeric
            WHEN e.status = 'remote' THEN 
                (SELECT points->>'remote' FROM settings LIMIT 1)::numeric
            ELSE 0
        END as base_points,
        ROW_NUMBER() OVER (PARTITION BY e.date ORDER BY e.time) as position,
        COUNT(*) OVER (PARTITION BY e.date) as total_entries
    FROM entries e
    WHERE e.status IN ('in-office', 'remote')
)
SELECT 
    username,
    date,
    base_points + (
        CASE 
            WHEN position = total_entries THEN 
                position * (SELECT late_bonus FROM settings LIMIT 1)
            ELSE 0
        END
    ) as points
FROM daily_scores;

-- Add index on the materialized view
CREATE UNIQUE INDEX IF NOT EXISTS idx_rankings_user_date 
ON rankings(username, date);

-- Create refresh function
CREATE OR REPLACE FUNCTION refresh_rankings()
RETURNS TRIGGER AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY rankings;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for rankings refresh
DROP TRIGGER IF EXISTS trg_refresh_rankings ON entries;
CREATE TRIGGER trg_refresh_rankings
    AFTER INSERT OR UPDATE OR DELETE ON entries
    FOR EACH STATEMENT
    EXECUTE FUNCTION refresh_rankings();

-- Initial refresh of the rankings view
REFRESH MATERIALIZED VIEW rankings;
