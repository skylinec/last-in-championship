from datetime import datetime, timedelta, date
from sqlalchemy import text, Date
import logging

from .database import SessionLocal, Base

logger = logging.getLogger(__name__)

# Import model classes directly from Base
Entry = Base.metadata.tables['entries']
Settings = Base.metadata.tables['settings']
UserStreak = Base.metadata.tables['user_streaks']

def get_working_days(db, username):
    """Get working days for a user from settings"""
    settings = db.execute(Settings.select()).first()
    if not settings or not settings.points:
        return ['mon', 'tue', 'wed', 'thu', 'fri']  # Default working days
    return settings.points.get('working_days', {}).get(username, ['mon', 'tue', 'wed', 'thu', 'fri'])

def get_streak_history(username, db):
    """Get historical streak data for a user"""
    try:
        entries = db.execute(text("""
            WITH valid_entries AS (
                SELECT DISTINCT ON (date::date)
                    date::date as entry_date,
                    status,
                    timestamp
                FROM entries 
                WHERE name = :username
                    AND status IN ('in-office', 'remote')
                ORDER BY date::date DESC, timestamp DESC
            ),
            streak_bounds AS (
                SELECT 
                    entry_date,
                    status,
                    CASE 
                        WHEN entry_date > CURRENT_DATE THEN 1
                        WHEN date_trunc('day', lag(entry_date::timestamp) over (order by entry_date))
                             - date_trunc('day', entry_date::timestamp) > interval '3 days'
                        THEN 1
                        ELSE 0
                    END as is_break
                FROM valid_entries
            ),
            streak_groups AS (
                SELECT
                    entry_date,
                    status,
                    sum(is_break) over (order by entry_date) as streak_group
                FROM streak_bounds
            )
            SELECT 
                min(entry_date) as start_date,
                max(entry_date) as end_date,
                count(*) as length,
                max(entry_date) >= CURRENT_DATE - interval '3 days' as is_current
            FROM streak_groups
            GROUP BY streak_group
            ORDER BY max(entry_date) DESC
        """), {"username": username}).fetchall()

        if not entries:
            return []

        working_days = get_working_days(db, username)
        today = datetime.now().date()
        streaks = []

        prev_start = None
        prev_end = None

        for entry in entries:
            start_date = entry.start_date
            end_date = entry.end_date
            length = entry.length
            is_current = entry.is_current

            # Calculate break reason
            break_reason = "Current active streak" if is_current else "Streak ended"
            if prev_end and start_date:
                days_between = (prev_start - end_date).days - 1
                if days_between > 0:
                    if days_between <= 2 and all(d in ['sat', 'sun'] for d in [prev_end.strftime('%a').lower()]):
                        break_reason = "Weekend break"
                    else:
                        break_reason = f"Missed {days_between} day{'s' if days_between > 1 else ''}"

            streaks.append({
                'start': start_date,
                'end': end_date,
                'length': length,
                'is_current': is_current,
                'break_reason': break_reason
            })

            prev_start = start_date
            prev_end = end_date

        return streaks

    except Exception as e:
        logger.error(f"Error getting streak history: {str(e)}")
        return []

def get_attendance_for_period(username, start_date, end_date, db):
    """Get attendance records for a date range"""
    try:
        attendance = {}
        entries = db.execute(text("""
            SELECT DISTINCT ON (date::date)
                date::date as entry_date,
                status
            FROM entries 
            WHERE name = :username 
                AND date::date BETWEEN :start_date AND :end_date
                AND status IN ('in-office', 'remote', 'sick', 'leave')
            ORDER BY date::date, timestamp DESC
        """), {
            "username": username,
            "start_date": start_date.strftime('%Y-%m-%d'),
            "end_date": end_date.strftime('%Y-%m-%d')
        }).fetchall()

        for entry in entries:
            attendance[entry.entry_date.isoformat()] = entry.status
            
        return attendance
    except Exception as e:
        logger.error(f"Error getting attendance: {str(e)}")
        return {}

def calculate_current_streak(username):
    """Calculate current streak for a user"""
    db = SessionLocal()
    try:
        streaks = get_streak_history(username, db)
        if not streaks:
            return 0
            
        current = streaks[0]
        return current['length'] if current['is_current'] else 0
        
    except Exception as e:
        logger.error(f"Error calculating current streak: {str(e)}")
        return 0
    finally:
        db.close()

def get_current_streak_info(username, db=None):
    """Get current streak details"""
    should_close = db is None
    if should_close:
        db = SessionLocal()
    
    try:
        streaks = get_streak_history(username, db)
        if not streaks:
            return {'length': 0, 'start': None, 'is_current': False}
            
        current = streaks[0]
        return {
            'length': current['length'],
            'start': current['start'],
            'is_current': current['is_current']
        }
    finally:
        if should_close:
            db.close()
