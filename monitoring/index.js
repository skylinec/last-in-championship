const { Pool } = require('pg');
const cron = require('node-cron');

const pool = new Pool({
  connectionString: process.env.DATABASE_URL
});

async function checkMissingEntries() {
  const client = await pool.connect();
  try {
    const settingsResult = await client.query('SELECT monitoring_start_date::date FROM settings LIMIT 1');
    const startDate = settingsResult.rows[0]?.monitoring_start_date || new Date().toISOString().split('T')[0];
    
    // Get all weekdays between start date and today
    const missingEntries = await client.query(`
      WITH RECURSIVE dates AS (
        SELECT 
          $1::date as date
        UNION ALL
        SELECT 
          date + 1
        FROM 
          dates
        WHERE 
          date < CURRENT_DATE
      ),
      weekdays AS (
        SELECT date::date
        FROM dates
        WHERE EXTRACT(DOW FROM date) BETWEEN 1 AND 5  -- 1=Monday, 5=Friday
      ),
      entries_by_date AS (
        SELECT DISTINCT date::date
        FROM entries
      )
      SELECT w.date
      FROM weekdays w
      LEFT JOIN entries_by_date e ON w.date = e.date
      WHERE e.date IS NULL
      ORDER BY w.date;
    `, [startDate]);
    
    // Insert or update missing entries
    if (missingEntries.rows.length > 0) {
      const values = missingEntries.rows.map(r => `('${r.date.toISOString().split('T')[0]}'::date, NOW())`).join(',');
      if (values) {
        await client.query(`
          INSERT INTO missing_entries (date, checked_at)
          VALUES ${values}
          ON CONFLICT (date) 
          DO UPDATE SET checked_at = NOW()
        `);
      }
    }
  } finally {
    client.release();
  }
}

async function generateStreaks() {
  const client = await pool.connect();
  try {
    // Clear existing streaks
    await client.query('DELETE FROM user_streaks');

    // Get all relevant entries ordered by user and date
    const entries = await client.query(`
      SELECT name, date::date, timestamp
      FROM entries
      WHERE status IN ('in-office', 'remote')
      ORDER BY name, date DESC
    `);

    if (!entries.rows.length) return;

    const streaks = [];
    let currentUser = null;
    let currentStreak = 0;
    let maxStreak = 0;
    let lastDate = null;
    let lastTimestamp = null;

    for (const entry of entries.rows) {
      if (entry.name !== currentUser) {
        // Save previous user's streak
        if (currentUser && currentStreak > 0) {
          streaks.push({
            username: currentUser,
            currentStreak: currentStreak,
            maxStreak: maxStreak,
            lastAttendance: lastTimestamp
          });
        }
        // Reset for new user
        currentUser = entry.name;
        currentStreak = 1;
        maxStreak = 1;
        lastDate = entry.date;
        lastTimestamp = entry.timestamp;
        continue;
      }

      const daysBetween = Math.floor((lastDate - entry.date) / (1000 * 60 * 60 * 24));

      if (daysBetween <= 3) { // Within streak range
        if (daysBetween <= 1 || isWeekendGap(lastDate, entry.date)) {
          currentStreak++;
          maxStreak = Math.max(maxStreak, currentStreak);
        } else {
          currentStreak = 1;
        }
      } else {
        currentStreak = 1;
      }

      lastDate = entry.date;
      lastTimestamp = entry.timestamp;
    }

    // Don't forget the last user
    if (currentUser && currentStreak > 0) {
      streaks.push({
        username: currentUser,
        currentStreak: currentStreak,
        maxStreak: maxStreak,
        lastAttendance: lastTimestamp
      });
    }

    // Bulk insert all streaks
    if (streaks.length > 0) {
      const values = streaks.map(streak => 
        `('${streak.username}', ${streak.currentStreak}, '${streak.lastAttendance.toISOString()}', ${streak.maxStreak})`
      ).join(',');

      await client.query(`
        INSERT INTO user_streaks (username, current_streak, last_attendance, max_streak)
        VALUES ${values}
      `);
    }

  } finally {
    client.release();
  }
}

function isWeekendGap(date1, date2) {
  // Check if the gap between dates only includes weekend days
  const d1 = new Date(date1);
  const d2 = new Date(date2);
  for (let d = new Date(date2); d < date1; d.setDate(d.getDate() + 1)) {
    if (d.getDay() !== 0 && d.getDay() !== 6) {
      return false;
    }
  }
  return true;
}

// Schedule both tasks
const INTERVAL = parseInt(process.env.MONITORING_INTERVAL) || 300000;

// Run missing entries check every 5 minutes
setInterval(checkMissingEntries, INTERVAL);

// Run streak generation every 5 minutes
setInterval(generateStreaks, INTERVAL);

// Initial runs on startup
checkMissingEntries().catch(console.error);
generateStreaks().catch(console.error);
