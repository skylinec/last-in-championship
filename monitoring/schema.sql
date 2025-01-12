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
