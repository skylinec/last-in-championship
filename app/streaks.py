from datetime import datetime, timedelta
from sqlalchemy import text
import logging

from .database import SessionLocal, Base

logger = logging.getLogger(__name__)

# Import model classes directly from Base
Entry = Base.metadata.tables['entries']
Settings = Base.metadata.tables['settings']
UserStreak = Base.metadata.tables['user_streaks']

def calculate_streak_for_date(username, target_date, db):
    """Calculate streak up to a specific date"""
    try:
        if not username or not target_date:
            return 0
            
        # Ensure target_date is a date object, not datetime
        target_date = (datetime.strptime(target_date, '%Y-%m-%d').date() 
                      if isinstance(target_date, str) 
                      else target_date.date() if isinstance(target_date, datetime) 
                      else target_date)

        # Get settings using raw table
        settings = db.execute(Settings.select()).first()
        if not settings:
            return 0
            
        # Get user's working days from settings JSON
        working_days = settings.points.get('working_days', {}).get(username, ['mon', 'tue', 'wed', 'thu', 'fri'])

        # Query entries in reverse chronological order, including up to target date
        entries = db.execute(
            Entry.select().where(
                Entry.c.name == username,
                Entry.c.status.in_(['in-office', 'remote']),
                Entry.c.date <= target_date.isoformat()
            ).order_by(Entry.c.date.desc())
        ).fetchall()

        if not entries:
            return 0

        # Start counting streak
        streak = 1  # Start with 1 since we have at least one entry
        last_date = datetime.strptime(entries[0].date, '%Y-%m-%d').date()
        
        # Process subsequent entries
        for entry in entries[1:]:
            entry_date = datetime.strptime(entry.date, '%Y-%m-%d').date()
            days_diff = (last_date - entry_date).days

            # Check if gap only contains non-working days
            only_non_working_days = True
            check_date = entry_date
            while check_date < last_date:
                check_date += timedelta(days=1)
                if check_date.strftime('%a').lower()[:3] in working_days:
                    only_non_working_days = False
                    break

            if days_diff <= 3 and only_non_working_days:
                streak += 1
                last_date = entry_date
            else:
                break

        return streak

    except Exception as e:
        logger.error(f"Error calculating streak: {str(e)}")
        return 0

def update_user_streak(username, attendance_date):
    """Update streak for a user based on new attendance"""
    db = SessionLocal()
    try:
        streak = db.execute(
            UserStreak.select().where(UserStreak.c.username == username)
        ).first()

        if not streak:
            db.execute(
                UserStreak.insert().values(
                    username=username,
                    current_streak=1,
                    max_streak=1,
                    last_attendance=datetime.combine(attendance_date, datetime.min.time())
                )
            )
        else:
            # Convert attendance_date to datetime if it's a string
            if isinstance(attendance_date, str):
                attendance_date = datetime.strptime(attendance_date, '%Y-%m-%d')
            
            # Ensure we're comparing dates, not mixing date and datetime
            attendance_date = attendance_date.date() if isinstance(attendance_date, datetime) else attendance_date

            if streak.last_attendance:
                # Convert last_attendance to date for comparison
                last_date = streak.last_attendance.date()
                days_diff = (attendance_date - last_date).days
                if days_diff <= 3:  # Allow for weekends
                    streak.current_streak += 1
                else:
                    streak.current_streak = 1
            else:
                streak.current_streak = 1

            # Store the full datetime
            streak.last_attendance = datetime.combine(attendance_date, datetime.min.time())
            streak.max_streak = max(streak.max_streak, streak.current_streak)
            db.commit()
    finally:
        db.close()

def generate_streaks():
    """Generate user streaks properly"""
    logger.info("Generating streaks...")
    db = SessionLocal()
    try:
        # Get settings
        settings = db.query(Settings).first()
        if not settings or not settings.enable_streaks:
            return
            
        today = datetime.now().date()
        
        # Get all users with recent activity
        active_users = db.query(Entry.name).distinct().filter(
            Entry.date >= (today - timedelta(days=30)).isoformat()
        ).all()
        
        for user_tuple in active_users:
            username = user_tuple[0]
            current_streak = calculate_streak_for_date(username, today)
            
            # Update or create streak record
            streak = db.query(UserStreak).filter_by(username=username).first()
            if streak:
                if current_streak > 0:  # Only update if there's an active streak
                    streak.current_streak = current_streak
                    streak.last_attendance = datetime.now()
                    streak.max_streak = max(streak.max_streak, current_streak)
                else:
                    streak.current_streak = 0  # Reset expired streak
            else:
                streak = UserStreak(
                    username=username,
                    current_streak=current_streak,
                    max_streak=current_streak,
                    last_attendance=datetime.now() if current_streak > 0 else None
                )
                db.add(streak)
        
        db.commit()
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error generating streaks: {str(e)}")
    finally:
        db.close()

def calculate_current_streak(name):
    """Calculate current streak for a user"""
    db = SessionLocal()
    try:
        current_date = datetime.now().date()
        return calculate_streak_for_date(name, current_date, db)
    except Exception as e:
        logging.error(f"Error calculating current streak: {str(e)}")
        return 0
    finally:
        db.close()

def get_streak_history(username, db):
    """Get complete streak history for a user using same logic as current streak"""
    try:
        entries = db.query(Entry).filter(
            Entry.name == username,
            Entry.status.in_(['in-office', 'remote'])
        ).order_by(Entry.date.asc()).all()

        settings = db.query(Settings).first()
        working_days = settings.points.get('working_days', {}).get(username, ['mon', 'tue', 'wed', 'thu', 'fri'])
        
        streaks = []
        current_run = 0
        streak_start = None
        prev_date = None

        for entry in entries:
            entry_date = datetime.strptime(entry.date, '%Y-%m-%d').date()

            if prev_date is None:
                current_run = 1
                streak_start = entry_date
                prev_date = entry_date
                continue

            days_diff = (entry_date - prev_date).days

            # Use same logic as calculate_streak_for_date
            only_non_working_days = True
            check_date = prev_date
            while check_date < entry_date:
                check_date += timedelta(days=1)
                if check_date.strftime('%a').lower()[:3] in working_days:
                    only_non_working_days = False
                    break

            if days_diff <= 3 and only_non_working_days:
                current_run += 1
            else:
                if current_run >= 3:  # Only record significant streaks
                    streaks.append({
                        'length': current_run,
                        'start': streak_start,
                        'end': prev_date
                    })
                current_run = 1
                streak_start = entry_date

            prev_date = entry_date

        # Add final streak if significant
        if current_run >= 3:
            streaks.append({
                'length': current_run,
                'start': streak_start,
                'end': prev_date
            })

        return sorted(streaks, key=lambda x: (-x['length'], -x['end'].toordinal()))

    except Exception as e:
        logger.error(f"Error getting streak history: {str(e)}")
        return []
