import logging
import os
import importlib
from sqlalchemy import create_engine, inspect, text
from ..database import SessionLocal

logger = logging.getLogger(__name__)

def get_migration_versions():
    """Get list of migration versions"""
    versions_dir = os.path.join(os.path.dirname(__file__), 'versions')
    migrations = []
    
    if os.path.exists(versions_dir):
        for filename in sorted(os.listdir(versions_dir)):
            if filename.endswith('.py') and filename != '__init__.py':
                migrations.append(filename[:-3])
    
    return migrations

def run_migrations():
    """Run all database migrations in order"""
    db = SessionLocal()
    try:
        logger.info("Running database migrations...")
        
        # Get all migration versions
        migrations = get_migration_versions()
        
        for migration_name in migrations:
            try:
                migration = importlib.import_module(f'app.migrations.versions.{migration_name}')
                if hasattr(migration, 'should_run'):
                    if migration.should_run(db.get_bind()):
                        logger.info(f"Running migration: {migration_name}")
                        migration.migrate(db.get_bind())
                        logger.info(f"Completed migration: {migration_name}")
                    else:
                        logger.info(f"Skipping migration {migration_name} - already applied")
                else:
                    logger.warning(f"Migration {migration_name} has no should_run check")
                    migration.migrate(db.get_bind())
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
