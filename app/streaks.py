from datetime import datetime, timedelta, date
from sqlalchemy import text
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

def get_attendance_for_period(username, start_date, end_date, db):
    """Get attendance data for a date range"""
    try:
        entries = db.execute(
            Entry.select().where(
                Entry.c.name == username,
                (Entry.c.date.cast(Date)) >= start_date.strftime('%Y-%m-%d'),
                (Entry.c.date.cast(Date)) <= end_date.strftime('%Y-%m-%d')
            ).order_by(Entry.c.date.asc())
        ).fetchall()
        
        return {entry.date: entry.status for entry in entries}
    except Exception as e:
        logger.error(f"Error getting attendance data: {str(e)}")
        return {}

def calculate_current_streak(name):
    """Get the current (most recent) streak length"""
    db = SessionLocal()
    try:
        # Get streak history
        streaks = get_streak_history(name, db)
        
        # Return the most recent streak's length
        if streaks:
            return streaks[0]['length']
        return 0
    finally:
        db.close()

def get_streak_data(username, db):
    """Get complete streak data for a user"""
    try:
        # Get all streaks
        streaks = get_streak_history(username, db)
        
        # Get current streak from most recent streak
        current_streak = streaks[0]['length'] if streaks else 0
        
        # Calculate max streak
        max_streak = max((s['length'] for s in streaks), default=0)
        
        # Get last attendance date
        last_attendance = streaks[0]['end'].isoformat() if streaks else None

        return {
            'current_streak': current_streak,
            'max_streak': max_streak,
            'last_attendance': last_attendance
        }
    except Exception as e:
        logger.error(f"Error getting streak data: {str(e)}")
        return {
            'current_streak': 0,
            'max_streak': 0,
            'last_attendance': None
        }

def get_streak_history(username, db):
    """Get historical streak data for a user"""
    try:
        entries = db.execute(text("""
            WITH sorted_entries AS (
                SELECT date, status
                FROM entries 
                WHERE name = :username
                  AND status IN ('in-office', 'remote')
                ORDER BY date ASC
            )
            SELECT * FROM sorted_entries
        """), {"username": username}).fetchall()
        
        if not entries:
            return []

        streaks = []
        current_streak = 0
        streak_start = None
        last_date = None
        working_days = get_working_days(db, username)
        today = datetime.now().date()

        def is_working_day(check_date):
            """Helper to check if a date is a working day"""
            return (check_date.weekday() < 5 and 
                   check_date.strftime('%a').lower() in working_days)

        def get_next_working_day(from_date):
            """Get the next working day after the given date"""
            next_date = from_date + timedelta(days=1)
            while not is_working_day(next_date):
                next_date += timedelta(days=1)
            return next_date

        def should_continue_streak(prev_date, current_date):
            """Check if current_date should continue streak from prev_date"""
            if not prev_date:
                return False
            expected_date = get_next_working_day(prev_date)
            return current_date == expected_date

        for entry in entries:
            entry_date = entry.date if isinstance(entry.date, date) else datetime.strptime(entry.date, '%Y-%m-%d').date()
            
            # Skip non-working days without breaking streak
            if not is_working_day(entry_date):
                continue
            
            if current_streak == 0:
                streak_start = entry_date
                current_streak = 1
            elif should_continue_streak(last_date, entry_date):
                current_streak += 1
            else:
                # Save completed streak
                if current_streak > 0:
                    streaks.append({
                        'start': streak_start,
                        'end': last_date,
                        'length': current_streak,
                        'break_reason': "Missed working day(s)",
                        'is_current': False
                    })
                streak_start = entry_date
                current_streak = 1
            
            last_date = entry_date

        # Handle final streak
        if current_streak > 0:
            is_active = last_date == today
            streaks.append({
                'start': streak_start,
                'end': last_date,
                'length': current_streak,
                'break_reason': "Current active streak" if is_active else "End of records",
                'is_current': is_active
            })

        return sorted(streaks, key=lambda x: x['end'].toordinal(), reverse=True)

    except Exception as e:
        logger.error(f"Error getting streak history: {str(e)}")
        return []

def get_current_streak_info(username, db=None):
    """Get complete info about user's current streak"""
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False

    try:
        streaks = get_streak_history(username, db)
        if not streaks:
            return {
                'length': 0,
                'start': None,
                'end': None,
                'is_current': False
            }

        current = streaks[0]
        return {
            'length': current['length'],
            'start': current['start'],
            'end': current['end'],
            'is_current': current['is_current']
        }
    finally:
        if should_close:
            db.close()
