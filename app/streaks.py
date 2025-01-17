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
