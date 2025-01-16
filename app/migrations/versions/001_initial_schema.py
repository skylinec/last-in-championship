from sqlalchemy import text

def should_run(engine):
    """Check if migration should run"""
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 
                FROM information_schema.indexes 
                WHERE indexname = 'idx_rankings_period_end'
            )
        """))
        return not result.scalar()

def migrate(engine):
    """Run initial schema migration"""
    with engine.begin() as conn:
        # Create indexes safely
        conn.execute(text("""
            DO $$ 
            BEGIN
                -- Create indexes if they don't exist
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes 
                    WHERE indexname = 'idx_rankings_period_end'
                ) THEN
                    CREATE INDEX idx_rankings_period_end ON tie_breakers (period_end);
                END IF;

                -- Drop and recreate trigger
                DROP TRIGGER IF EXISTS trg_refresh_rankings ON tie_breakers;
                
                -- Ensure trigger function exists
                CREATE OR REPLACE FUNCTION refresh_rankings()
                RETURNS TRIGGER AS $$
                BEGIN
                    -- Add your ranking refresh logic here
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                -- Create trigger
                CREATE TRIGGER trg_refresh_rankings
                    AFTER INSERT ON tie_breakers
                    FOR EACH ROW
                    EXECUTE PROCEDURE refresh_rankings();
            END $$;
        """))
