const { Pool } = require('pg');
const cron = require('node-cron');

const pool = new Pool({
  connectionString: process.env.DATABASE_URL
});

async function checkMissingEntries() {
  const client = await pool.connect();
  try {
    const settingsResult = await client.query('SELECT monitoring_start_date FROM settings LIMIT 1');
    const startDate = settingsResult.rows[0]?.monitoring_start_date || new Date();
    
    const missingEntries = await client.query(`
      WITH dates AS (
        SELECT generate_series(
          $1::timestamp,
          CURRENT_DATE,
          '1 day'::interval
        )::date AS date
      )
      SELECT dates.date
      FROM dates
      LEFT JOIN entries ON dates.date = entries.date
      WHERE entries.id IS NULL
      AND EXTRACT(DOW FROM dates.date) BETWEEN 1 AND 5
    `, [startDate]);
    
    await client.query('INSERT INTO missing_entries (date, checked_at) VALUES ($1, NOW()) ON CONFLICT (date) DO UPDATE SET checked_at = NOW()', 
      [missingEntries.rows.map(r => r.date)]
    );
  } finally {
    client.release();
  }
}

// Run check every 5 minutes
setInterval(checkMissingEntries, process.env.MONITORING_INTERVAL || 300000);
checkMissingEntries();
