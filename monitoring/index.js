const { Pool } = require('pg');
const cron = require('node-cron');

const pool = new Pool({
  connectionString: process.env.DATABASE_URL
});

async function initDb() {
  const client = await pool.connect();
  try {
    // Create monitoring_logs table if it doesn't exist
    await client.query(`
      CREATE TABLE IF NOT EXISTS monitoring_logs (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        event_type VARCHAR(50) NOT NULL,
        details JSONB,
        status VARCHAR(20) DEFAULT 'success'
      )
    `);
  } finally {
    client.release();
  }
}

async function logMonitoringEvent(eventType, details, status = 'success') {
  const client = await pool.connect();
  try {
    await client.query(
      'INSERT INTO monitoring_logs (event_type, details, status) VALUES ($1, $2, $3)',
      [eventType, JSON.stringify(details), status]
    );
  } finally {
    client.release();
  }
}

// Update existing functions to include logging
async function checkMissingEntries() {
  const client = await pool.connect();
  try {
    const startTime = Date.now();
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

    await logMonitoringEvent('missing_entries_check', {
      duration: Date.now() - startTime,
      entries_found: missingEntries.rows.length
    });
  } catch (error) {
    await logMonitoringEvent('missing_entries_check', {
      error: error.message
    }, 'error');
    throw error;
  } finally {
    client.release();
  }
}

async function generateStreaks() {
  const client = await pool.connect();
  try {
    const startTime = Date.now();
    await client.query('DELETE FROM user_streaks');

    // Get settings first to check if streaks are enabled
    const settings = await client.query('SELECT enable_streaks FROM settings LIMIT 1');
    if (!settings.rows[0]?.enable_streaks) {
      await logMonitoringEvent('streak_generation', {
        duration: Date.now() - startTime,
        streaks_generated: 0,
        message: 'Streaks disabled in settings'
      });
      return;
    }

    // First get working days for each user
    const settingsQuery = await client.query('SELECT points FROM settings LIMIT 1');
    const workingDays = settingsQuery.rows[0]?.points?.working_days || {};

    // Get all relevant entries ordered by user and date
    const entries = await client.query(`
      SELECT 
        e.name, 
        e.date::date, 
        e.timestamp,
        e.status,
        EXTRACT(DOW FROM e.date::date) as day_of_week
      FROM entries e
      WHERE e.status IN ('in-office', 'remote')
      ORDER BY e.name, e.date DESC
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
      
      // Get user's working days
      const userWorkingDays = workingDays[entry.name] || ['mon', 'tue', 'wed', 'thu', 'fri'];
      const dayMap = {0: 'sun', 1: 'mon', 2: 'tue', 3: 'wed', 4: 'thu', 5: 'fri', 6: 'sat'};
      
      // Check if this is a working day for the user
      const isWorkingDay = userWorkingDays.includes(dayMap[entry.day_of_week]);
      
      if (daysBetween <= 3) { // Within streak range
        if (daysBetween <= 1) {
          // Consecutive days
          if (isWorkingDay) {
            currentStreak++;
            maxStreak = Math.max(maxStreak, currentStreak);
          }
        } else {
          // Check if gap only includes non-working days
          let onlyNonWorkingDays = true;
          for (let d = 1; d < daysBetween; d++) {
            const checkDate = new Date(lastDate);
            checkDate.setDate(checkDate.getDate() - d);
            const checkDayOfWeek = checkDate.getDay();
            const isDayWorking = userWorkingDays.includes(dayMap[checkDayOfWeek]);
            if (isDayWorking) {
              onlyNonWorkingDays = false;
              break;
            }
          }
          
          if (onlyNonWorkingDays && isWorkingDay) {
            currentStreak++;
            maxStreak = Math.max(maxStreak, currentStreak);
          } else {
            currentStreak = 1;
          }
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

    await logMonitoringEvent('streak_generation', {
      duration: Date.now() - startTime,
      streaks_generated: streaks.length,
      message: 'Streaks generated successfully'
    });

  } catch (error) {
    await logMonitoringEvent('streak_generation', {
      error: error.message
    }, 'error');
    throw error;
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

// Initialize database on startup
initDb().catch(console.error);
