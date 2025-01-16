from sqlalchemy import create_engine, inspect, text
import os
import importlib
from ..database import SessionLocal, engine, Base
import logging

def check_table_exists(engine, table_name):
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()

def run_migrations():
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/championship')
    engine = create_engine(DATABASE_URL)
    
    db = SessionLocal()
    try:
        logging.info("Running database migrations...")
        
        # Add core_users column to settings if not exists
        db.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='settings' AND column_name='core_users'
                ) THEN
                    ALTER TABLE settings ADD COLUMN core_users JSON;
                END IF;
            END $$;
        """))
        
        # Add tie breaker related columns
        db.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='settings' AND column_name='enable_tiebreakers'
                ) THEN
                    ALTER TABLE settings 
                    ADD COLUMN enable_tiebreakers BOOLEAN DEFAULT false,
                    ADD COLUMN tiebreaker_points INTEGER DEFAULT 5,
                    ADD COLUMN tiebreaker_expiry INTEGER DEFAULT 24,
                    ADD COLUMN auto_resolve_tiebreakers BOOLEAN DEFAULT false,
                    ADD COLUMN tiebreaker_weekly BOOLEAN DEFAULT true,
                    ADD COLUMN tiebreaker_monthly BOOLEAN DEFAULT true;
                END IF;
            END $$;
        """))
        
        # Create indexes for performance
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);
            CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);
        """))
        
        db.commit()
        logging.info("Database migrations completed successfully")
        
    except Exception as e:
        db.rollback()
        logging.error(f"Migration error: {str(e)}")
        raise
    finally:
        db.close()
    
    # List all migration modules in order
    migrations = [
        'add_user_streaks',
        '20240124_add_monitoring_date'  # Add the new migration
    ]
    
    for migration in migrations:
        try:
            print(f"Running migration: {migration}")
            migration_module = importlib.import_module(f'migrations.{migration}')
            if hasattr(migration_module, 'should_run'):
                if migration_module.should_run(engine):
                    migration_module.migrate(engine)
                    print(f"Successfully completed migration: {migration}")
                else:
                    print(f"Skipping migration {migration} - already applied")
            else:
                migration_module.migrate(engine)
                print(f"Successfully completed migration: {migration}")
        except Exception as e:
            print(f"Error in migration {migration}: {str(e)}")
            raise

if __name__ == "__main__":
    run_migrations()
