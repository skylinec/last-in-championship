CREATE TABLE IF NOT EXISTS missing_entries (
    date DATE PRIMARY KEY,
    checked_at TIMESTAMP NOT NULL
);

ALTER TABLE settings 
ADD COLUMN IF NOT EXISTS monitoring_start_date DATE;
