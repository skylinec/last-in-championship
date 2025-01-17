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
                Entry.c.date >= start_date.strftime('%Y-%m-%d'),
                Entry.c.date <= end_date.strftime('%Y-%m-%d')
            ).order_by(Entry.c.date.asc())
        ).fetchall()
        
        return {entry.date: entry.status for entry in entries}
    except Exception as e:
        logger.error(f"Error getting attendance data: {str(e)}")
        return {}

def calculate_current_streak(name):
    """Get current streak for a user from the database"""
    db = SessionLocal()
    try:
        streak = db.execute(text("""
            SELECT current_streak FROM user_streaks WHERE username = :name
        """), {"name": name}).scalar()
        return streak or 0
    except Exception as e:
        logger.error(f"Error getting current streak: {str(e)}")
        return 0
    finally:
        db.close()

def get_streak_data(username, db):
    """Get complete streak data for a user"""
    try:
        streak = db.execute(text("""
            SELECT * FROM user_streaks WHERE username = :username
        """), {"username": username}).first()

        return {
            'current_streak': streak.current_streak if streak else 0,
            'max_streak': streak.max_streak if streak else 0,
            'last_attendance': streak.last_attendance.isoformat() if streak and streak.last_attendance else None
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
        # Get all entries regardless of status to properly track breaks
        entries = db.execute(text("""
            WITH ordered_entries AS (
                SELECT 
                    date,
                    status,
                    LAG(date) OVER (ORDER BY date) as prev_date,
                    LAG(status) OVER (ORDER BY date) as prev_status
                FROM entries 
                WHERE name = :username
                ORDER BY date ASC
            )
            SELECT * FROM ordered_entries
        """), {"username": username}).fetchall()
        
        if not entries:
            return []

        streaks = []
        current_streak = 0
        streak_start = None
        last_date = None
        working_days = get_working_days(db, username)

        def is_working_day(check_date):
            """Helper to check if a date is a working day"""
            return (check_date.weekday() < 5 and 
                   check_date.strftime('%a').lower() in working_days)

        def should_start_new_streak(entry_date, last_date, last_status):
            """Determine if a new streak should start on this date"""
            # Always start a streak if there's no previous date
            if not last_date:
                return True
                
            # If last status was invalid (sick/leave), start new streak
            if last_status not in ['in-office', 'remote']:
                return True
                
            # Get working days between last date and current date
            days_between = []
            current = last_date + timedelta(days=1)
            while current < entry_date:
                if is_working_day(current):
                    days_between.append(current)
                current += timedelta(days=1)
            
            # Start new streak if there are missed working days
            return bool(days_between)
        
        last_status = None
        
        for entry in entries:
            entry_date = entry.date if isinstance(entry.date, date) else datetime.strptime(entry.date, '%Y-%m-%d').date()
            status = entry.status
            
            # Skip non-working days without breaking streak
            if not is_working_day(entry_date):
                continue
            
            # Valid attendance status
            if status in ['in-office', 'remote']:
                # Determine if we should start a new streak
                if current_streak == 0 or should_start_new_streak(entry_date, last_date, last_status):
                    # If there was a previous streak, save it
                    if current_streak > 1:
                        streaks.append({
                            'start': streak_start,
                            'end': last_date,
                            'length': current_streak - 1,
                            'break_reason': "Status changed to " + last_status if last_status not in ['in-office', 'remote'] else "Missed working days"
                        })
                    # Start new streak
                    streak_start = entry_date
                    current_streak = 1
                else:
                    # Continue existing streak
                    current_streak += 1
            else:
                # Invalid status breaks the streak
                if current_streak > 1:
                    streaks.append({
                        'start': streak_start,
                        'end': last_date,
                        'length': current_streak - 1,
                        'break_reason': f"Status changed to {status}"
                    })
                current_streak = 0
                streak_start = None
            
            last_date = entry_date
            last_status = status

        # Handle final active streak
        if current_streak > 1:
            today = datetime.now().date()
            is_active = last_date == today
            streaks.append({
                'start': streak_start,
                'end': last_date,
                'length': current_streak if is_active else current_streak - 1,
                'break_reason': "Current active streak" if is_active else "End of records"
            })

        # Filter out zero-length streaks
        streaks = [s for s in streaks if s['length'] > 0]
        return sorted(streaks, key=lambda x: (-x['length'], -x['end'].toordinal()))

    except Exception as e:
        logger.error(f"Error getting streak history: {str(e)}")
        return []
