from sqlalchemy import create_engine, text, Table, Column, String, MetaData
import os

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/championship')
engine = create_engine(DATABASE_URL)

MIGRATION_VERSION = "1.0.0"

def check_migration_status():
    """Check if this migration has already been applied"""
    with engine.connect() as conn:
        # Create migrations table if it doesn't exist
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS migrations (
                version VARCHAR(50) PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        
        # Check if this migration version exists
        result = conn.execute(text(
            "SELECT version FROM migrations WHERE version = :version"
        ), {"version": MIGRATION_VERSION})
        
        return bool(result.first())

def migrate():
    """Add streak columns to settings table if not already migrated"""
    if check_migration_status():
        print(f"Migration {MIGRATION_VERSION} already applied")
        return

    with engine.connect() as conn:
        try:
            # Add streaks_enabled column
            conn.execute(text("""
                ALTER TABLE settings 
                ADD COLUMN IF NOT EXISTS streaks_enabled BOOLEAN DEFAULT FALSE
            """))
            
            # Add streak_bonus column
            conn.execute(text("""
                ALTER TABLE settings 
                ADD COLUMN IF NOT EXISTS streak_bonus FLOAT DEFAULT 0.5
            """))
            
            # Record migration
            conn.execute(text(
                "INSERT INTO migrations (version) VALUES (:version)"
            ), {"version": MIGRATION_VERSION})
            
            # Commit all changes
            conn.execute(text("COMMIT"))
            print(f"Successfully applied migration {MIGRATION_VERSION}")
            
        except Exception as e:
            print(f"Error during migration: {str(e)}")
            conn.execute(text("ROLLBACK"))
            raise

if __name__ == "__main__":
    migrate()
