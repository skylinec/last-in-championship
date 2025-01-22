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
    
    // Get working days configuration from settings
    const settingsResult = await client.query('SELECT points FROM settings LIMIT 1');
    const workingDays = settingsResult.rows[0]?.points?.working_days || {};

    // Get only valid attendance entries
    const entries = await client.query(`
      WITH grouped_entries AS (
        SELECT DISTINCT ON (name, date::date)
          name, 
          date::date,
          timestamp,
          EXTRACT(DOW FROM date::date) as day_of_week
        FROM entries 
        WHERE status IN ('in-office', 'remote')  -- Only valid attendance
        ORDER BY name, date::date DESC, timestamp DESC
      )
      SELECT * FROM grouped_entries
      ORDER BY name, date ASC
    `);

    const streaks = new Map();
    let currentUser = null;
    let currentStreak = 0;
    let maxStreak = 0;
    let lastDate = null;
    let lastTimestamp = null;
    let streakStart = null;

    function isWorkingDay(date, userWorkingDays, dayMap) {
      const dow = date.getDay();
      return dow > 0 && dow < 6 && userWorkingDays.includes(dayMap[dow]);
    }

    function getNextWorkingDay(date, userWorkingDays, dayMap) {
      let nextDate = new Date(date);
      nextDate.setDate(nextDate.getDate() + 1);
      while (!isWorkingDay(nextDate, userWorkingDays, dayMap)) {
        nextDate.setDate(nextDate.getDate() + 1);
      }
      return nextDate;
    }

    function shouldContinueStreak(lastDate, currentDate, userWorkingDays, dayMap) {
      if (!lastDate) return false;
      const expectedNext = getNextWorkingDay(lastDate, userWorkingDays, dayMap);
      return currentDate.getTime() === expectedNext.getTime();
    }

    for (const entry of entries.rows) {
      const entryDate = new Date(entry.date);
      const userWorkingDays = workingDays[entry.name] || ['mon', 'tue', 'wed', 'thu', 'fri'];
      const dayMap = {1: 'mon', 2: 'tue', 3: 'wed', 4: 'thu', 5: 'fri'};
      
      // Skip non-working days without breaking streak
      if (!isWorkingDay(entryDate, userWorkingDays, dayMap)) continue;

      // Handle user change
      if (entry.name !== currentUser) {
        if (currentUser && currentStreak > 0) {
          streaks.set(currentUser, {
            username: currentUser,
            currentStreak: Math.max(0, currentStreak - 1),
            maxStreak: Math.max(maxStreak, currentStreak - 1),
            lastAttendance: lastTimestamp,
            streakStartDate: streakStart
          });
        }
        
        currentUser = entry.name;
        currentStreak = 1;
        maxStreak = 1;
        streakStart = entryDate;
        lastDate = entryDate;
        lastTimestamp = entry.timestamp;
        continue;
      }

      if (lastDate?.getTime() === entryDate.getTime()) continue;

      if (shouldContinueStreak(lastDate, entryDate, userWorkingDays, dayMap)) {
        currentStreak++;
        maxStreak = Math.max(maxStreak, currentStreak);
      } else {
        maxStreak = Math.max(maxStreak, currentStreak);
        currentStreak = 1;
        streakStart = entryDate;
      }

      lastDate = entryDate;
      lastTimestamp = entry.timestamp;
    }

    // Handle last user
    if (currentUser && currentStreak > 0) {
      const finalStreak = lastDate?.getTime() === new Date().setHours(0,0,0,0) ? 
        currentStreak : Math.max(0, currentStreak - 1);
      maxStreak = Math.max(maxStreak, finalStreak);
      
      streaks.set(currentUser, {
        username: currentUser,
        currentStreak: finalStreak,
        maxStreak: maxStreak,
        lastAttendance: lastTimestamp,
        streakStartDate: streakStart
      });
    }

    // Update database with new streak values
    if (streaks.size > 0) {
      await client.query('BEGIN');
      try {
        for (const streak of streaks.values()) {
          await client.query(`
            INSERT INTO user_streaks (
              username, current_streak, last_attendance, max_streak, streak_start_date
            ) VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (username) 
            DO UPDATE SET 
              current_streak = $2,
              last_attendance = $3,
              max_streak = GREATEST(user_streaks.max_streak, $4),
              streak_start_date = CASE
                WHEN user_streaks.current_streak != $2 THEN $5
                ELSE user_streaks.streak_start_date
              END
          `, [
            streak.username,
            streak.currentStreak,
            streak.lastAttendance,
            streak.maxStreak,
            streak.streakStartDate
          ]);
        }
        await client.query('COMMIT');
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      }
    }

    await logMonitoringEvent('streak_generation', {
      duration: Date.now() - startTime,
      streaks_processed: streaks.size,
      streak_details: Array.from(streaks.values())
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

async function generateStreaks() {
  const client = await pool.connect();
  try {
    const startTime = Date.now();
    
    // Get streak history query
    const streakHistoryQuery = `
      WITH RECURSIVE dates AS (
        SELECT min(date::date) as date
        FROM entries
        UNION ALL
        SELECT date + 1
        FROM dates
        WHERE date < CURRENT_DATE
      ),
      valid_days AS (
        SELECT DISTINCT e.name, e.date::date
        FROM entries e
        WHERE e.status IN ('in-office', 'remote')
        ORDER BY e.name, e.date::date
      ),
      streak_calc AS (
        SELECT 
          v.name,
          v.date,
          CASE 
            WHEN LAG(v.date) OVER (PARTITION BY v.name ORDER BY v.date) IS NULL 
              OR date - LAG(v.date) OVER (PARTITION BY v.name ORDER BY v.date) <= 3
            THEN 0 ELSE 1 
          END as new_streak
        FROM valid_days v
      ),
      streak_groups AS (
        SELECT 
          name,
          date,
          SUM(new_streak) OVER (PARTITION BY name ORDER BY date) as streak_group
        FROM streak_calc
      )
      SELECT 
        name,
        MIN(date) as streak_start,
        MAX(date) as streak_end,
        COUNT(*) as streak_length
      FROM streak_groups
      GROUP BY name, streak_group
      ORDER BY name, streak_start DESC
    `;

    const streakHistory = await client.query(streakHistoryQuery);
    
    // Update user_streaks table with history
    for (const row of streakHistory.rows) {
      await client.query(`
        INSERT INTO user_streaks (
          username, 
          current_streak,
          streak_start_date,
          last_attendance,
          max_streak,
          streak_history
        ) VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (username) 
        DO UPDATE SET 
          current_streak = CASE 
            WHEN (CURRENT_DATE - $4::date) <= 3 THEN $2
            ELSE 0 
          END,
          streak_start_date = $3,
          last_attendance = $4,
          max_streak = GREATEST(user_streaks.max_streak, $5),
          streak_history = $6
      `, [
        row.name,
        row.streak_length,
        row.streak_start,
        row.streak_end,
        row.streak_length,
        JSON.stringify([{
          start: row.streak_start,
          end: row.streak_end,
          length: row.streak_length,
          is_current: (new Date() - new Date(row.streak_end)) / (1000 * 60 * 60 * 24) <= 3
        }])
      ]);
    }

    await logMonitoringEvent('streak_generation', {
      duration: Date.now() - startTime,
      streaks_processed: streakHistory.rows.length
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

// Initialize database on startup
initDb().catch(console.error);

async function checkForTieBreakers() {
  const client = await pool.connect();
  try {
    const startTime = Date.now();

    const settings = await client.query('SELECT enable_tiebreakers, tiebreaker_expiry, auto_resolve_tiebreakers, tiebreaker_weekly, tiebreaker_monthly FROM settings LIMIT 1');
    if (!settings.rows[0]?.enable_tiebreakers) {
      await logMonitoringEvent('tie_breaker_check', {
        duration: Date.now() - startTime,
        message: 'Tie breakers disabled in settings'
      });
      return;
    }

    // Get allowed periods based on settings
    let periodsToCheck = [];
    if (settings.rows[0].tiebreaker_weekly) periodsToCheck.push('weekly');
    if (settings.rows[0].tiebreaker_monthly) periodsToCheck.push('monthly');

    if (periodsToCheck.length === 0) {
      await logMonitoringEvent('tie_breaker_check', {
        message: 'No tie breaker periods enabled'
      });
      return;
    }

    const tieCheckQuery = `
      WITH period_bounds AS (
        SELECT DISTINCT
          r.period,
          r.period_start::date,
          r.period_end::date
        FROM rankings r
        WHERE period = ANY($1)
          AND period_end::date < CURRENT_DATE
      ),
      working_days AS (
        SELECT pb.period, pb.period_start, pb.period_end,
          array_agg(DISTINCT d.date) as required_dates
        FROM period_bounds pb
        CROSS JOIN LATERAL (
          SELECT date::date
          FROM generate_series(pb.period_start, pb.period_end, '1 day'::interval) date
          WHERE EXTRACT(DOW FROM date) BETWEEN 1 AND 5
        ) d
        GROUP BY pb.period, pb.period_start, pb.period_end
      ),
      core_user_attendance AS (
        SELECT 
          pb.period,
          pb.period_start,
          pb.period_end,
          e.name,
          COUNT(DISTINCT e.date) as days_present
        FROM period_bounds pb
        CROSS JOIN (SELECT UNNEST($1::text[]) as name) cu  -- Fixed parameter syntax
        LEFT JOIN entries e ON 
          e.date::date BETWEEN pb.period_start AND pb.period_end
          AND e.name = cu.name
          AND e.status IN ('in-office', 'remote')
        GROUP BY pb.period, pb.period_start, pb.period_end, e.name
      ),
      valid_periods AS (
        SELECT 
          ca.period,
          ca.period_start,
          ca.period_end
        FROM core_user_attendance ca
        JOIN working_days wd ON 
          ca.period = wd.period 
          AND ca.period_start = wd.period_start
        GROUP BY ca.period, ca.period_start, ca.period_end
        HAVING bool_and(ca.days_present >= array_length(wd.required_dates, 1))
      ),
      scored_users AS (
        -- Use the correct point columns based on mode
        SELECT
          r.period,
          r.period_start::date,
          r.period_end::date,
          r.username,
          ROUND(r.early_bird_points_rounded, 1) as early_bird_score,
          ROUND(r.last_in_points_rounded, 1) as last_in_score
        FROM rankings r
        INNER JOIN period_bounds pb ON 
          r.period = pb.period AND 
          r.period_end::date = pb.period_end::date
        WHERE EXISTS (
          SELECT 1 FROM entries e 
          WHERE e.name = r.username 
          AND e.date::date BETWEEN r.period_start::date AND r.period_end::date
          AND e.status IN ('in-office', 'remote')
        )
      ),
      potential_ties AS (
        SELECT 
          s.period,
          s.period_start,
          s.period_end,
          s.early_bird_score as points,
          'early_bird' as mode,
          array_agg(s.username) as usernames,
          COUNT(*) as tied_count,
          au.active_user_count
        FROM scored_users s
        JOIN active_users au ON 
          s.period = au.period AND 
          s.period_end::date = au.period_end::date
        WHERE s.early_bird_score > 0
        GROUP BY s.period, s.period_start, s.period_end, s.early_bird_score, au.active_user_count
        HAVING COUNT(*) > 1

        UNION ALL

        SELECT 
          s.period,
          s.period_start,
          s.period_end,
          s.last_in_score as points,
          'last-in' as mode,
          array_agg(s.username) as usernames,
          COUNT(*) as tied_count,
          au.active_user_count
        FROM scored_users s
        JOIN active_users au ON 
          s.period = au.period AND 
          s.period_end::date = au.period_end::date
        WHERE s.last_in_score > 0
        GROUP BY s.period, s.period_start, s.period_end, s.last_in_score, au.active_user_count
        HAVING COUNT(*) > 1
      )
      SELECT * FROM potential_ties t
      WHERE EXISTS (
        SELECT 1 FROM valid_periods vp
        WHERE t.period = vp.period
        AND t.period_end::date = vp.period_end
      )
      AND NOT EXISTS (
        SELECT 1 
        FROM tie_breakers tb
        WHERE tb.period = t.period
        AND tb.period_end::date = t.period_end
        AND tb.mode = t.mode
        AND ROUND(tb.points::numeric, 1) = t.points
        AND tb.status = 'completed'
      )
      ORDER BY period_end DESC, points DESC;
    `;

    // OR if using named parameters:
    const ties = await client.query({
      text: tieCheckQuery,
      values: [periodsToCheck]
    });

    console.log("Checking for tie breakers...");
    console.log(`Found ${ties.rows.length} potential tie breakers`);
    
    // Debug logging for each tie found
    ties.rows.forEach(tie => {
      console.log(`Found ${tie.mode} tie for ${tie.period} ending ${tie.period_end}:
        Points: ${tie.points}
        Users: ${tie.usernames.join(', ')}`);
    });

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
              mode,
              status
            ) VALUES ($1, $2, $3, $4, $5, 'pending')
            ON CONFLICT (period, period_start, period_end, points, mode) DO NOTHING
            RETURNING id
          `, [
            tie.period,
            tie.period_start,
            tie.period_end,
            tie.points,
            tie.mode
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
  const period = isWeekly ? 'weekly' : 'monthly';
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
