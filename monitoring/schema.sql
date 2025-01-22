------------------------------------------
-- SECTION 1: MONITORING SYSTEM
------------------------------------------
CREATE TABLE IF NOT EXISTS monitoring_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_type VARCHAR(50) NOT NULL,
    details JSONB,
    status VARCHAR(20) DEFAULT 'success'
);

CREATE INDEX IF NOT EXISTS idx_monitoring_logs_timestamp 
ON monitoring_logs(timestamp DESC);

CREATE OR REPLACE FUNCTION cleanup_monitoring_logs() RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM monitoring_logs 
    WHERE id IN (
        SELECT id FROM monitoring_logs ORDER BY timestamp DESC OFFSET 5000
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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

------------------------------------------
-- SECTION 2: USER STREAKS
------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'user_streaks_username_key' 
        AND conrelid = 'user_streaks'::regclass
    ) THEN
        ALTER TABLE user_streaks 
        ADD CONSTRAINT user_streaks_username_key UNIQUE (username);
    END IF;
END $$;

ALTER TABLE user_streaks ADD COLUMN IF NOT EXISTS streak_start_date DATE;
ALTER TABLE user_streaks ADD COLUMN IF NOT EXISTS streak_history JSONB DEFAULT '[]'::jsonb;

------------------------------------------
-- SECTION 3: TIE BREAKER SYSTEM
------------------------------------------
DROP TABLE IF EXISTS tie_breaker_games CASCADE;
DROP TABLE IF EXISTS tie_breaker_participants CASCADE;
DROP TABLE IF EXISTS tie_breakers CASCADE;

CREATE TABLE tie_breakers (
    id SERIAL PRIMARY KEY,
    period VARCHAR(10) NOT NULL CHECK (period IN ('daily', 'weekly', 'monthly')),
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL, 
    points DECIMAL(10,2) NOT NULL,
    mode VARCHAR(20) NOT NULL CHECK (mode IN ('early-bird', 'last-in')),
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed')),
    points_applied BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    CONSTRAINT tie_breakers_unique_period UNIQUE (period, period_start, period_end, points, mode)
);

CREATE TABLE tie_breaker_participants (
    id SERIAL PRIMARY KEY,
    tie_breaker_id INTEGER REFERENCES tie_breakers(id) ON DELETE CASCADE,
    username VARCHAR(50) NOT NULL,
    game_choice VARCHAR(20),
    ready BOOLEAN DEFAULT false,
    winner BOOLEAN,
    UNIQUE(tie_breaker_id, username)
);

CREATE TABLE tie_breaker_games (
    id SERIAL PRIMARY KEY,
    tie_breaker_id INTEGER REFERENCES tie_breakers(id) ON DELETE CASCADE,
    game_type VARCHAR(20) NOT NULL,
    player1 VARCHAR(50) NOT NULL,
    player2 VARCHAR(50),
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'completed')),
    game_state JSONB DEFAULT '{"board":[], "moves":[], "current_player":null}'::jsonb,
    winner VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    final_tiebreaker BOOLEAN DEFAULT false
);

-- Tie breaker game constraints
CREATE OR REPLACE FUNCTION check_final_tiebreaker()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.final_tiebreaker = true AND EXISTS (
        SELECT 1 FROM tie_breaker_games 
        WHERE tie_breaker_id = NEW.tie_breaker_id 
        AND final_tiebreaker = true 
        AND id != NEW.id
    ) THEN
        RAISE EXCEPTION 'Only one final tie-breaker game allowed per tie breaker';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_check_final_tiebreaker
    BEFORE INSERT OR UPDATE ON tie_breaker_games
    FOR EACH ROW
    EXECUTE FUNCTION check_final_tiebreaker();

-- Add cascading constraint to tie breaker references
ALTER TABLE tie_breaker_participants
    DROP CONSTRAINT IF EXISTS tie_breaker_participants_tie_breaker_id_fkey,
    ADD CONSTRAINT tie_breaker_participants_tie_breaker_id_fkey
        FOREIGN KEY (tie_breaker_id)
        REFERENCES tie_breakers(id)
        ON DELETE CASCADE;

-- Add date validation constraints
ALTER TABLE tie_breakers
    ADD CONSTRAINT tie_breakers_dates_check
        CHECK (period_start <= period_end),
    ADD CONSTRAINT tie_breakers_future_check
        CHECK (period_end <= CURRENT_TIMESTAMP);

-- Add auto-cleanup function for expired tie breakers
CREATE OR REPLACE FUNCTION cleanup_expired_tie_breakers() RETURNS void AS $$
BEGIN
    UPDATE tie_breakers
    SET status = 'completed',
        resolved_at = CURRENT_TIMESTAMP
    WHERE status = 'pending'
    AND created_at < CURRENT_TIMESTAMP - INTERVAL '24 hours';
END;
$$ LANGUAGE plpgsql;

-- Add additional performance indexes
CREATE INDEX IF NOT EXISTS idx_tie_breakers_created_at ON tie_breakers(created_at);
CREATE INDEX IF NOT EXISTS idx_tie_breakers_resolved_at ON tie_breakers(resolved_at);
CREATE INDEX IF NOT EXISTS idx_tie_breaker_participants_ready 
    ON tie_breaker_participants(ready) 
    WHERE ready = false;
CREATE INDEX IF NOT EXISTS idx_tie_breaker_games_active 
    ON tie_breaker_games(status, created_at) 
    WHERE status = 'active';

-- Add better error handling for tie breaker state changes
CREATE OR REPLACE FUNCTION validate_tie_breaker_state_change()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status = 'completed' AND NEW.status != 'completed' THEN
        RAISE EXCEPTION 'Cannot change status of completed tie breaker';
    END IF;

    IF OLD.status = 'pending' AND NEW.status = 'completed' AND 
       NOT EXISTS (
           SELECT 1 FROM tie_breaker_participants
           WHERE tie_breaker_id = NEW.id AND winner = true
       ) THEN
        RAISE EXCEPTION 'Cannot complete tie breaker without winner';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validate_tie_breaker_state
    BEFORE UPDATE ON tie_breakers
    FOR EACH ROW
    EXECUTE FUNCTION validate_tie_breaker_state_change();

------------------------------------------
-- SECTION 4: SETTINGS
------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                  WHERE table_name = 'settings' 
                  AND column_name = 'enable_tiebreakers') THEN
        ALTER TABLE settings 
        ADD COLUMN enable_tiebreakers BOOLEAN DEFAULT false,
        ADD COLUMN tiebreaker_points INTEGER DEFAULT 5,
        ADD COLUMN tiebreaker_expiry INTEGER DEFAULT 24,
        ADD COLUMN auto_resolve_tiebreakers BOOLEAN DEFAULT false,
        ADD COLUMN tiebreaker_types JSONB DEFAULT '{"daily":true,"weekly":true,"monthly":true}'::jsonb,
        DROP COLUMN IF EXISTS tiebreaker_weekly,
        DROP COLUMN IF EXISTS tiebreaker_monthly;
    END IF;
END $$;

DO $$
BEGIN
    -- Add new columns for tie breaker generation if they don't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                  WHERE table_name = 'settings' 
                  AND column_name = 'tiebreaker_weekly') THEN
        ALTER TABLE settings 
        ADD COLUMN tiebreaker_weekly BOOLEAN DEFAULT true,
        ADD COLUMN tiebreaker_monthly BOOLEAN DEFAULT true;
    END IF;

    -- Update settings initialization with default values if needed
    UPDATE settings 
    SET 
        tiebreaker_weekly = true,
        tiebreaker_monthly = true
    WHERE 
        tiebreaker_weekly IS NULL 
        OR tiebreaker_monthly IS NULL;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'settings'
        AND column_name = 'early_bonus'
    ) THEN
        ALTER TABLE settings 
        ADD COLUMN early_bonus NUMERIC(10,2) DEFAULT 2.0;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'settings'
        AND column_name = 'late_bonus'
    ) THEN
        ALTER TABLE settings
        ADD COLUMN late_bonus NUMERIC(10,2) DEFAULT 2.0;
    END IF;
END $$;

------------------------------------------
-- SECTION 5: RANKINGS SYSTEM
------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS rankings CASCADE;
DROP INDEX IF EXISTS idx_rankings_unique_refresh;

CREATE MATERIALIZED VIEW rankings AS
WITH RECURSIVE periods AS (
    -- Daily periods
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
    
    -- Weekly periods
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
    
    -- Monthly periods
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
        ROW_NUMBER() OVER (PARTITION BY e.date::date ORDER BY e.time) as early_position,
        ROW_NUMBER() OVER (PARTITION BY e.date::date ORDER BY e.time DESC) as late_position,
        COUNT(*) OVER (PARTITION BY e.date::date) as total_entries
    FROM entries e
    JOIN periods p ON e.date::date = p.date
    WHERE e.status IN ('in-office', 'remote')
),
scored_entries AS (
    SELECT 
        username,
        date,
        period,
        period_start,
        period_end,
        -- Single bonus calculation using position
        -- For last-in mode: higher position = more points
        -- For early-bird mode: position is reversed (total_entries - position + 1)
        -- This ensures exact inverse relationship
        base_points +
        CASE WHEN late_position = 1 THEN  -- Last person gets full bonus
            total_entries * (SELECT late_bonus FROM settings LIMIT 1)
        ELSE 0 END 
        as last_in_bonus,
        
        base_points +
        CASE WHEN early_position = 1 THEN  -- First person gets full bonus
            total_entries * (SELECT early_bonus FROM settings LIMIT 1)
        ELSE 0 END
        as early_bird_bonus,
        
        -- Calculate cumulative points for each mode
        SUM(base_points +
            CASE WHEN late_position = 1 THEN
                total_entries * (SELECT late_bonus FROM settings LIMIT 1)
            ELSE 0 END)
        OVER (
            PARTITION BY username, period, period_start
            ORDER BY date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS last_in_points,

        SUM(base_points +
            CASE WHEN early_position = 1 THEN
                total_entries * (SELECT early_bonus FROM settings LIMIT 1)
            ELSE 0 END)
        OVER (
            PARTITION BY username, period, period_start
            ORDER BY date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS early_bird_points,
        
        early_position,
        late_position,
        total_entries,
        base_points
    FROM daily_scores
)
SELECT 
    username,
    date,
    period,
    period_start,
    period_end,
    early_bird_points,
    last_in_points,
    early_position,
    late_position,
    total_entries,
    base_points,
    ROUND(early_bird_points::numeric, 1) as early_bird_points_rounded,
    ROUND(last_in_points::numeric, 1) as last_in_points_rounded
FROM scored_entries;

-- Replace the problematic unique index with a more specific one
CREATE UNIQUE INDEX idx_rankings_unique_refresh 
ON rankings(username, date, period, period_start, period_end, early_bird_points);

-- Other indices can remain but should come after
CREATE INDEX idx_rankings_period ON rankings(period, period_end);
CREATE INDEX idx_rankings_period_points ON rankings(period, base_points);
CREATE INDEX idx_rankings_period_end ON rankings(period_end);

-- Make sure to refresh the rankings
REFRESH MATERIALIZED VIEW rankings;

------------------------------------------
-- SECTION 6: INDICES
------------------------------------------
-- Drop conflicting indices before recreation
DROP INDEX IF EXISTS idx_rankings_composite;
DROP INDEX IF EXISTS idx_tie_breakers_unique_period;

-- Recreate indices with updated constraints
CREATE UNIQUE INDEX idx_tie_breakers_unique_period ON tie_breakers(period, period_end, points, mode) 
WHERE status != 'completed';

-- Keep other indices
CREATE INDEX IF NOT EXISTS idx_rankings_period ON rankings(period, period_end);
CREATE INDEX IF NOT EXISTS idx_rankings_period_points ON rankings(period, base_points);
CREATE INDEX idx_rankings_period_end ON rankings(period_end);

CREATE INDEX idx_tie_breakers_mode ON tie_breakers(mode);
CREATE INDEX idx_tie_breakers_period_end ON tie_breakers(period_end);
CREATE INDEX idx_tie_breakers_points ON tie_breakers(points);
CREATE INDEX idx_tie_breakers_status ON tie_breakers(status);
CREATE INDEX idx_tie_breakers_period_range ON tie_breakers(period_start, period_end);

CREATE INDEX IF NOT EXISTS idx_tie_breakers_created_at ON tie_breakers(created_at);
CREATE INDEX IF NOT EXISTS idx_tie_breakers_resolved_at ON tie_breakers(resolved_at);
CREATE INDEX IF NOT EXISTS idx_tie_breaker_participants_ready 
    ON tie_breaker_participants(ready) 
    WHERE ready = false;
CREATE INDEX IF NOT EXISTS idx_tie_breaker_games_active 
    ON tie_breaker_games(status, created_at) 
    WHERE status = 'active';

------------------------------------------
-- SECTION 7: VIEWS AND FUNCTIONS
------------------------------------------
CREATE OR REPLACE FUNCTION refresh_rankings()
RETURNS TRIGGER AS $$
BEGIN
    -- Attempt concurrent refresh first
    BEGIN
        REFRESH MATERIALIZED VIEW CONCURRENTLY rankings;
    EXCEPTION 
        WHEN OTHERS THEN
            -- Log the error and fall back to regular refresh
            RAISE NOTICE 'Concurrent refresh failed, falling back to regular refresh';
            REFRESH MATERIALIZED VIEW rankings;
    END;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_refresh_rankings
    AFTER INSERT OR UPDATE OR DELETE ON entries
    FOR EACH STATEMENT
    EXECUTE FUNCTION refresh_rankings();

CREATE OR REPLACE VIEW active_users AS
WITH period_bounds AS (
    SELECT DISTINCT
        r.period,
        r.period_end::date,
        COUNT(DISTINCT e.name) as active_user_count
    FROM rankings r
    JOIN entries e ON e.date::date <= r.period_end::date 
        AND e.date::date >= r.period_start::date
    WHERE e.status IN ('in-office', 'remote')
    GROUP BY r.period, r.period_end::date
)
SELECT * FROM period_bounds;

-- Initial rankings refresh
REFRESH MATERIALIZED VIEW rankings;

------------------------------------------
-- SECTION 8: EXTERNAL SYSTEMS
------------------------------------------
\echo 'Creating Mattermost database if it does not exist'
SELECT 'CREATE DATABASE mattermost WITH ENCODING = ''UTF8'' CONNECTION LIMIT = -1'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mattermost')\gexec

\echo 'Creating Matomo database if it does not exist'
SELECT 'CREATE DATABASE matomo WITH ENCODING = ''UTF8'' CONNECTION LIMIT = -1'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'matomo')\gexec

------------------------------------------
-- SECTION 9: USERS
------------------------------------------
-- Add API token column to users table if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'users' 
        AND column_name = 'api_token'
    ) THEN
        ALTER TABLE users ADD COLUMN api_token VARCHAR(255) UNIQUE;
    END IF;
END $$;

-- Create index on api_token
CREATE INDEX IF NOT EXISTS idx_users_api_token ON users(api_token);

-- Add not null constraint to username and password if not already present
ALTER TABLE users 
    ALTER COLUMN username SET NOT NULL,
    ALTER COLUMN password SET NOT NULL;

-- Ensure unique constraint on username
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_username_key;
ALTER TABLE users ADD CONSTRAINT users_username_key UNIQUE (username);