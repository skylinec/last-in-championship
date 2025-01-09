from sqlalchemy import create_engine, text
import os

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/championship')
engine = create_engine(DATABASE_URL)

MIGRATION_VERSION = "1.0.0"

def migrate():
    """Add streak columns to settings table"""
    with engine.connect() as conn:
        # Start transaction
        trans = conn.begin()
        try:
            # Create migrations table first
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS migrations (
                    version VARCHAR(50) PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

            # Check if migration already applied
            result = conn.execute(text(
                "SELECT version FROM migrations WHERE version = :version"
            ), {"version": MIGRATION_VERSION})
            
            if result.first():
                print(f"Migration {MIGRATION_VERSION} already applied")
                trans.commit()
                return

            # Add streak columns
            conn.execute(text("""
                ALTER TABLE settings 
                ADD COLUMN IF NOT EXISTS enable_streaks BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS streak_multiplier FLOAT DEFAULT 0.5,
                ADD COLUMN IF NOT EXISTS streaks_enabled BOOLEAN DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS streak_bonus FLOAT DEFAULT 0.5
            """))
            
            # Record migration
            conn.execute(text(
                "INSERT INTO migrations (version) VALUES (:version)"
            ), {"version": MIGRATION_VERSION})
            
            # Commit transaction
            trans.commit()
            print(f"Successfully applied migration {MIGRATION_VERSION}")
            
        except Exception as e:
            trans.rollback()
            print(f"Error during migration: {str(e)}")
            raise

if __name__ == "__main__":
    migrate()
