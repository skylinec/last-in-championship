from sqlalchemy import Column, Date, text
from datetime import datetime

def migrate(engine):
    try:
        # Create connection with transaction
        with engine.begin() as conn:
            # Add monitoring_start_date to settings table
            conn.execute(text("""
                ALTER TABLE settings 
                ADD COLUMN IF NOT EXISTS monitoring_start_date DATE 
                DEFAULT current_date;
            """))
            
            # Create missing_entries table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS missing_entries (
                    date DATE PRIMARY KEY,
                    checked_at TIMESTAMP NOT NULL
                );
            """))
            
            # Set default monitoring start date to beginning of current year
            conn.execute(text("""
                UPDATE settings 
                SET monitoring_start_date = date_trunc('year', current_date)
                WHERE monitoring_start_date IS NULL;
            """))
            
            # Transaction auto-commits at end of context

    except Exception as e:
        print(f"Migration failed: {str(e)}")
        raise

def downgrade(engine):
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE settings DROP COLUMN IF EXISTS monitoring_start_date;"))
            conn.execute(text("DROP TABLE IF EXISTS missing_entries;"))
            # Transaction auto-commits at end of context
            
    except Exception as e:
        print(f"Downgrade failed: {str(e)}")
        raise