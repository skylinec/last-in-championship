import logging
from sqlalchemy import create_engine, inspect, text
from ..database import SessionLocal

logger = logging.getLogger(__name__)

def run_migrations():
    """Run all database migrations in order"""
    db = SessionLocal()
    try:
        logger.info("Running database migrations...")
        
        # Basic schema migrations
        db.execute(text("""
            DO $$ 
            BEGIN
                -- Add core_users to settings if not exists
                IF NOT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='settings' AND column_name='core_users'
                ) THEN
                    ALTER TABLE settings ADD COLUMN core_users JSON;
                END IF;

                -- Add monitoring_start_date if not exists
                IF NOT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='settings' AND column_name='monitoring_start_date'
                ) THEN
                    ALTER TABLE settings ADD COLUMN monitoring_start_date DATE;
                END IF;
            END $$;
        """))
        
        # Create indexes
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);
            CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);
        """))
        
        db.commit()
        logger.info("Basic schema migrations completed")
        
        # Import and run structured migrations
        migrations = [
            'add_user_streaks',
            '20240124_add_monitoring_date'
        ]
        
        for migration_name in migrations:
            try:
                migration = __import__(f'app.migrations.{migration_name}', fromlist=['migrate'])
                if hasattr(migration, 'should_run'):
                    if migration.should_run(db.get_bind()):
                        logger.info(f"Running migration: {migration_name}")
                        migration.migrate(db.get_bind())
                        logger.info(f"Completed migration: {migration_name}")
                    else:
                        logger.info(f"Skipping migration {migration_name} - already applied")
            except ImportError as e:
                logger.warning(f"Migration {migration_name} not found: {e}")
                continue
            except Exception as e:
                logger.error(f"Error in migration {migration_name}: {e}")
                raise
        
        logger.info("All migrations completed successfully")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Migration error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run_migrations()
