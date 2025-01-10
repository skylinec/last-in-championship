from sqlalchemy import Column, Date, text
from datetime import datetime

def migrate(engine):
    # Add monitoring_start_date to settings table
    with engine.connect() as conn:
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
        
        conn.commit()

def downgrade(engine):
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE settings DROP COLUMN IF EXISTS monitoring_start_date;"))
        conn.execute(text("DROP TABLE IF EXISTS missing_entries;"))
        conn.commit()
