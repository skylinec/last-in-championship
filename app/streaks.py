from datetime import datetime, timedelta
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

def is_working_day(date, working_days):
    """Check if a given date is a working day"""
    return date.strftime('%a').lower() in working_days

def is_weekend(date):
    """Check if a date is a weekend (Saturday=5 or Sunday=6)"""
    return date.weekday() >= 5

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

def get_streak_history(username, db):
    """Get past notable streaks for a user"""
    try:
        entries = db.execute(
            Entry.select().where(
                Entry.c.name == username,
                Entry.c.status.in_(['in-office', 'remote'])
            ).order_by(Entry.c.date.asc())
        ).fetchall()

        if not entries:
            return []

        today = datetime.now().date()
        streaks = []
        # Get streak data from user_streaks table
        streak_data = db.execute(
            UserStreak.select().where(UserStreak.c.username == username)
        ).first()

        current_streak = streak_data.current_streak if streak_data else 0

        # Only return non-active historical streaks
        return sorted(streaks, key=lambda x: (-x['length'], -x['end'].toordinal()))

    except Exception as e:
        logger.error(f"Error getting streak history: {str(e)}", exc_info=True)
        return []

def calculate_current_streak(name):
    """Get current streak for a user from the database"""
    db = SessionLocal()
    try:
        streak = db.execute(
            UserStreak.select().where(UserStreak.c.username == name)
        ).first()
        return streak.current_streak if streak else 0
    except Exception as e:
        logging.error(f"Error getting current streak: {str(e)}")
        return 0
    finally:
        db.close()

# Remove other streak calculation functions since they're handled by the monitoring service
