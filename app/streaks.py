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
    db = SessionLocal()
    try:
        today = date.today()
        streak = 0
        day_offset = 0

        while True:
            check_date = today - timedelta(days=day_offset)
            if check_date.weekday() < 5:  # Only count Mon-Fri
                row = db.execute(text("""
                    SELECT status
                    FROM entries
                    WHERE name = :name
                      AND date = :check_date
                """), {"name": name, "check_date": check_date}).fetchone()

                if not row or row.status in ("sick", "leave"):
                    break
                streak += 1
            day_offset += 1
            # Optional cut-off (e.g., 100 days) to prevent infinite loop if needed

        return streak
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
        entries = db.execute(text("""
            WITH ordered_entries AS (
                SELECT 
                    date,
                    status,
                    LAG(date, 1) OVER (ORDER BY date) as prev_date,
                    LAG(status, 1) OVER (ORDER BY date) as prev_status,
                    LEAD(date, 1) OVER (ORDER BY date) as next_date,
                    LEAD(status, 1) OVER (ORDER BY date) as next_status
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
                
            # Get next expected working day after prev_date
            expected_date = get_next_working_day(prev_date)
            return current_date == expected_date
        
        for entry in entries:
            entry_date = entry.date if isinstance(entry.date, date) else datetime.strptime(entry.date, '%Y-%m-%d').date()
            status = entry.status
            
            # Skip non-working days
            if not is_working_day(entry_date):
                continue
            
            if status in ['in-office', 'remote']:
                if current_streak == 0:
                    streak_start = entry_date
                    current_streak = 1
                elif should_continue_streak(last_date, entry_date):
                    current_streak += 1
                else:
                    # Save previous streak if exists
                    if current_streak > 1:
                        streaks.append({
                            'start': streak_start,
                            'end': last_date,
                            'length': current_streak - 1,
                            'break_reason': "Missed working day(s)"
                        })
                    # Start new streak
                    streak_start = entry_date
                    current_streak = 1
            else:
                # Non-attendance status
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
