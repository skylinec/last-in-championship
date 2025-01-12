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

-- Drop and recreate tie breakers table with correct schema
DROP TABLE IF EXISTS tie_breaker_games;
DROP TABLE IF EXISTS tie_breaker_participants;
DROP TABLE IF EXISTS tie_breakers;

CREATE TABLE tie_breakers (
    id SERIAL PRIMARY KEY,
    period VARCHAR(10) NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly')),
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    points DECIMAL NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tie_breaker_participants (
    id SERIAL PRIMARY KEY,
    tie_breaker_id INTEGER REFERENCES tie_breakers(id) ON DELETE CASCADE,
    username VARCHAR(50) NOT NULL,
    game_choice VARCHAR(20),
    ready BOOLEAN DEFAULT false,
    winner BOOLEAN,
    UNIQUE(tie_breaker_id, username)
);

CREATE TABLE IF NOT EXISTS tie_breaker_games (
    id SERIAL PRIMARY KEY,
    tie_breaker_id INTEGER REFERENCES tie_breakers(id) ON DELETE CASCADE,
    game_type VARCHAR(20) NOT NULL,
    player1 VARCHAR(50) NOT NULL,
    player2 VARCHAR(50) NOT NULL,
    game_state JSONB,
    winner VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Drop existing indices
DROP INDEX IF EXISTS idx_tiebreakers_date;
DROP INDEX IF EXISTS idx_tiebreakers_type;
DROP INDEX IF EXISTS idx_tiebreakers_status;
DROP INDEX IF EXISTS idx_tiebreakers_period;

-- Add correct indices for the new schema
CREATE INDEX idx_tiebreakers_period ON tie_breakers(period);
CREATE INDEX idx_tiebreakers_status ON tie_breakers(status);
CREATE INDEX idx_tiebreakers_period_range ON tie_breakers(period_start, period_end);
CREATE INDEX idx_tiebreakers_points ON tie_breakers(points);
CREATE INDEX idx_tiebreakers_composite ON tie_breakers(period_start, points, status);

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

-- Add tie breaker timing settings columns if they don't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                  WHERE table_name = 'settings' 
                  AND column_name = 'tiebreaker_weekly') THEN
        ALTER TABLE settings 
            DROP COLUMN IF EXISTS tiebreaker_weekly,
            DROP COLUMN IF EXISTS tiebreaker_monthly;

        ALTER TABLE settings ADD COLUMN IF NOT EXISTS tiebreaker_types JSONB DEFAULT '{"daily": true, "weekly": true, "monthly": true}'::jsonb;
    END IF;
END $$;

-- Drop and recreate the rankings view with period support
DROP MATERIALIZED VIEW IF EXISTS rankings;
CREATE MATERIALIZED VIEW rankings AS
WITH RECURSIVE periods AS (
    -- Generate daily, weekly, and monthly periods
    SELECT 
        date::date as date,
        'daily' as period,
        date::date as period_start,
        date::date as period_end
    FROM generate_series(
        (SELECT MIN(date::date) FROM entries),
        CURRENT_DATE,
        '1 day'::interval
    ) date
    UNION ALL
    SELECT 
        date::date,
        'weekly',
        date_trunc('week', date)::date,
        (date_trunc('week', date) + interval '6 days')::date
    FROM generate_series(
        (SELECT MIN(date::date) FROM entries),
        CURRENT_DATE,
        '1 day'::interval
    ) date
    UNION ALL
    SELECT 
        date::date,
        'monthly',
        date_trunc('month', date)::date,
        (date_trunc('month', date) + interval '1 month - 1 day')::date
    FROM generate_series(
        (SELECT MIN(date::date) FROM entries),
        CURRENT_DATE,
        '1 day'::interval
    ) date
),
daily_scores AS (
    SELECT 
        e.name as username,
        e.date::date,
        e.status,
        e.time,
        p.period,
        p.period_start,
        p.period_end,
        CASE 
            WHEN e.status = 'in-office' THEN 
                (SELECT points->>'in_office' FROM settings LIMIT 1)::numeric
            WHEN e.status = 'remote' THEN 
                (SELECT points->>'remote' FROM settings LIMIT 1)::numeric
            ELSE 0
        END as base_points,
        ROW_NUMBER() OVER (PARTITION BY e.date::date ORDER BY e.time) as position,
        COUNT(*) OVER (PARTITION BY e.date::date) as total_entries
    FROM entries e
    JOIN periods p ON e.date::date = p.date
    WHERE e.status IN ('in-office', 'remote')
)
SELECT 
    username,
    date,
    period,
    period_start,
    period_end,
    SUM(base_points + (
        CASE 
            WHEN position = total_entries THEN 
                position * (SELECT late_bonus FROM settings LIMIT 1)
            ELSE 0
        END
    )) OVER (
        PARTITION BY username, period, period_start
        ORDER BY date
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) as points
FROM daily_scores;

-- Update indices for the rankings view
DROP INDEX IF EXISTS idx_rankings_user_date;
CREATE UNIQUE INDEX idx_rankings_composite ON rankings(username, date, period);
CREATE INDEX idx_rankings_period ON rankings(period, period_end);

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
