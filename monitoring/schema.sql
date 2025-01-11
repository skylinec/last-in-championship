-- Add index for better cleanup performance
CREATE INDEX IF NOT EXISTS idx_monitoring_logs_timestamp 
ON monitoring_logs(timestamp DESC);

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

-- Create trigger for automatic cleanup
DROP TRIGGER IF EXISTS trg_cleanup_monitoring_logs ON monitoring_logs;
CREATE TRIGGER trg_cleanup_monitoring_logs
  AFTER INSERT ON monitoring_logs
  FOR EACH STATEMENT
  EXECUTE FUNCTION cleanup_monitoring_logs();
