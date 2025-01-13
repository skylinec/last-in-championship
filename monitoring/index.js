const { Pool } = require('pg');
const cron = require('node-cron');

const pool = new Pool({
  connectionString: process.env.DATABASE_URL
});

const MAX_LOG_ENTRIES = 5000;

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
    
    // Add periodic cleanup
    setInterval(cleanupOldLogs, 1800000); // Run every 30 minutes
    await cleanupOldLogs(); // Initial cleanup
    
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

async function cleanupOldLogs() {
  const client = await pool.connect();
  try {
    // Delete oldest logs keeping only MAX_LOG_ENTRIES most recent
    await client.query(`
      DELETE FROM monitoring_logs 
      WHERE id IN (
        SELECT id 
        FROM monitoring_logs 
        ORDER BY timestamp DESC 
        OFFSET $1
      )
    `, [MAX_LOG_ENTRIES]);
  } catch (error) {
    console.error('Error cleaning up old logs:', error);
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

    // Get existing streaks for comparison
    const existingStreaks = await client.query('SELECT username, current_streak, max_streak FROM user_streaks');
    const existingStreakMap = new Map(existingStreaks.rows.map(s => [s.username, s]));

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

    // Compare and update streaks
    if (streaks.length > 0) {
      // Use a transaction for bulk updates
      await client.query('BEGIN');
      
      try {
        for (const streak of streaks) {
          const existing = existingStreakMap.get(streak.username);
          
          if (existing) {
            // Update existing streak
            await client.query(`
              UPDATE user_streaks 
              SET 
                current_streak = $1,
                last_attendance = $2,
                max_streak = GREATEST(max_streak, $1)
              WHERE username = $3
            `, [streak.currentStreak, streak.lastAttendance, streak.username]);
          } else {
            // Insert new streak
            await client.query(`
              INSERT INTO user_streaks (username, current_streak, last_attendance, max_streak)
              VALUES ($1, $2, $3, $2)
            `, [streak.username, streak.currentStreak, streak.lastAttendance]);
          }
        }
        
        await client.query('COMMIT');
        
        await logMonitoringEvent('streak_generation', {
          duration: Date.now() - startTime,
          streaks_generated: streaks.length,
          streaks_updated: streaks.filter(s => {
            const existing = existingStreakMap.get(s.username);
            return !existing || existing.current_streak !== s.currentStreak;
          }).length,
          message: 'Streaks processed successfully'
        });
        
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      }
    }

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

// Initialize database on startup
initDb().catch(console.error);

async function checkForTieBreakers() {
  const client = await pool.connect();
  try {
    const startTime = Date.now();

    // Get settings first
    const settings = await client.query('SELECT enable_tiebreakers, tiebreaker_expiry, auto_resolve_tiebreakers FROM settings LIMIT 1');
    if (!settings.rows[0]?.enable_tiebreakers) {
      await logMonitoringEvent('tie_breaker_check', {
        duration: Date.now() - startTime,
        message: 'Tie breakers disabled in settings'
      });
      return;
    }

    const autoResolve = settings.rows[0]?.auto_resolve_tiebreakers;
    
    // Modified query to handle historical periods when auto-resolve is disabled
    const tieCheckQuery = `
      WITH period_entries AS (
        SELECT DISTINCT
          e.date,
          CASE 
            WHEN extract(dow from e.date::date) = 0 THEN e.date::date - interval '6 days'
            ELSE e.date::date - (extract(dow from e.date::date) - 1 || ' days')::interval
          END as week_start,
          date_trunc('month', e.date::date)::date as month_start
        FROM entries e
        WHERE e.date >= CASE 
          WHEN $1 = true THEN CURRENT_DATE - INTERVAL '7 days'
          ELSE (SELECT monitoring_start_date FROM settings LIMIT 1)
        END
      ),
      tied_rankings AS (
        SELECT 
          period,
          period_start,
          period_end,
          points,
          array_agg(username) as usernames,
          COUNT(*) as tied_count
        FROM rankings r
        WHERE EXISTS (
          SELECT 1 FROM period_entries pe
          WHERE (r.period = 'weekly' AND r.period_start = pe.week_start)
          OR (r.period = 'monthly' AND r.period_start = pe.month_start)
        )
        AND period IN ('weekly', 'monthly')
        AND period_end < CURRENT_DATE
        GROUP BY period, period_start, period_end, points
        HAVING COUNT(*) > 1
      )
      SELECT *
      FROM tied_rankings tr
      WHERE NOT EXISTS (
        SELECT 1 
        FROM tie_breakers tb
        WHERE tb.period = tr.period
        AND tb.period_end = tr.period_end
        AND tb.points = tr.points
      )`;

    // Initialize new tie breakers
    const ties = await client.query(tieCheckQuery, [autoResolve]);
    let tieBreakersCreated = 0;

    if (ties.rows.length > 0) {
      await client.query('BEGIN');
      
      try {
        for (const tie of ties.rows) {
          const result = await client.query(`
            INSERT INTO tie_breakers (
              period,
              period_start,
              period_end,
              points,
              status
            ) VALUES ($1, $2, $3, $4, 'pending')
            ON CONFLICT (period, period_start, period_end, points) DO NOTHING
            RETURNING id
          `, [
            tie.period,
            tie.period_start,
            tie.period_end,
            tie.points
          ]);
          
          if (result.rows[0]) {
            const tieBreakerId = result.rows[0].id;
            
            await client.query(`
              INSERT INTO tie_breaker_participants (tie_breaker_id, username)
              SELECT $1, unnest($2::text[])
            `, [tieBreakerId, tie.usernames]);
            
            tieBreakersCreated++;
          }
        }
        
        await client.query('COMMIT');
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      }
    }

    await logMonitoringEvent('tie_breaker_check', {
      duration: Date.now() - startTime,
      ties_found: ties.rows.length,
      tie_breakers_created: tieBreakersCreated
    });

  } catch (error) {
    await logMonitoringEvent('tie_breaker_check', {
      error: error.message
    }, 'error');
    throw error;
  } finally {
    client.release();
  }
}

// ...existing code...

async function createPeriodTieBreakers(client, { isWeekly, isMonthly }) {
  const period = isWeekly ? 'week' : 'month';
  const startDate = new Date();
  
  // Set date to start of period
  if (isWeekly) {
    startDate.setDate(startDate.getDate() - startDate.getDay()); // Start of week
  } else {
    startDate.setDate(1); // Start of month
  }

  await client.query(`
    INSERT INTO tie_breakers (period, period_end, points, status)
    SELECT $1, $2, total_points, 'pending'
    FROM (
      SELECT period_end, total_points, COUNT(*) as tied_count
      FROM rankings
      WHERE period = $1 AND period_end = $2
      GROUP BY period_end, total_points
      HAVING COUNT(*) > 1
    ) ties
    ON CONFLICT DO NOTHING
  `, [period, startDate.toISOString()]);
}

async function resolveExpiredTieBreakers(client, expiryHours) {
  const expiredQuery = `
    UPDATE tie_breakers
    SET 
      status = 'completed',
      resolved_at = NOW()
    WHERE 
      status = 'pending'
      AND created_at < NOW() - INTERVAL '${expiryHours} hours'
    RETURNING id`;

  const expired = await client.query(expiredQuery);
  
  // Randomly select winners for expired tie breakers
  for (const row of expired.rows) {
    await client.query(`
      UPDATE tie_breaker_participants
      SET winner = (
        CASE WHEN username = (
          SELECT username 
          FROM tie_breaker_participants 
          WHERE tie_breaker_id = $1 
          ORDER BY RANDOM() 
          LIMIT 1
        ) THEN true ELSE false END
      )
      WHERE tie_breaker_id = $1
    `, [row.id]);
  }

  if (expired.rows.length > 0) {
    await logMonitoringEvent('tie_breaker_auto_resolve', {
      resolved_count: expired.rows.length,
      expiry_hours: expiryHours
    });
  }
}

// Update intervals section at bottom of file
const INTERVAL = parseInt(process.env.MONITORING_INTERVAL) || 300000;

// Run all checks every 5 minutes
setInterval(checkMissingEntries, INTERVAL);
setInterval(generateStreaks, INTERVAL);
setInterval(checkForTieBreakers, INTERVAL);

// Initial runs on startup
checkMissingEntries().catch(console.error);
generateStreaks().catch(console.error);
checkForTieBreakers().catch(console.error);

// Remove the cron schedule for tie breakers since we're using interval now
