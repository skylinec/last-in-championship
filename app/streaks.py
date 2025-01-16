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
    db = SessionLocal()
    
    try:
        if not username or not target_date:
            return 0
            
        target_date = (datetime.strptime(target_date, '%Y-%m-%d').date() 
                      if isinstance(target_date, str) else target_date)
        
        # Get settings using raw table
        settings = db.execute(Settings.select()).first()
        if not settings:
            return 0
            
        # Get user's working days from settings JSON
        working_days = settings.points.get('working_days', {}).get(username, ['mon', 'tue', 'wed', 'thu', 'fri'])
        
        # Query entries using raw table
        entries = db.execute(
            Entry.select().where(
                Entry.c.name == username,
                Entry.c.status.in_(['in-office', 'remote']),
                Entry.c.date <= target_date.isoformat()
            ).order_by(Entry.c.date.desc())
        ).fetchall()
        
        if not entries:
            return 0
        
        streak = 0
        last_date = datetime.strptime(entries[0].date, '%Y-%m-%d').date()
        
        # Check if the first entry is the target date
        if last_date == target_date:
            streak = 1
        else:
            return 0  # If most recent entry isn't target date, no active streak
        
        # Process remaining entries
        for entry in entries[1:]:
            entry_date = datetime.strptime(entry.date, '%Y-%m-%d').date()
            days_between = (last_date - entry_date).days
            
            # Check if dates are consecutive working days
            current_date = last_date
            working_day_count = 0
            non_working_day_count = 0
            
            for _ in range(days_between):
                current_date -= timedelta(days=1)
                day_name = current_date.strftime('%a').lower()
                
                if day_name in working_days:
                    working_day_count += 1
                else:
                    non_working_day_count += 1
                    
            # Break streak if there are missed working days
            if working_day_count > 1:  # More than one working day gap
                break
                
            if working_day_count == 1:
                # Found next working day, continue streak
                streak += 1
                last_date = entry_date
            elif working_day_count == 0 and non_working_day_count <= 3:
                # Only weekend/non-working days between, continue streak
                streak += 1
                last_date = entry_date
            else:
                # Gap too large, break streak
                break
        
        return streak
        
    except Exception as e:
        logger.error(f"Error calculating streak: {str(e)}")
        return 0
    finally:
        db.close()

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

def calculate_current_streak(username):
    """Calculate current active streak"""
    db = SessionLocal()
    try:
        today = datetime.now().date()
        
        # Get most recent entries, ordered by date descending
        entries = db.query(Entry).filter(
            Entry.name == username,
            Entry.status.in_(['in-office', 'remote'])
        ).order_by(Entry.date.desc()).all()
        
        if not entries:
            return 0
            
        streak = 1
        last_date = datetime.strptime(entries[0].date, "%Y-%m-%d").date()
        
        # Skip first entry as it's counted above
        for entry in entries[1:]:
            entry_date = datetime.strptime(entry.date, "%Y-%m-%d").date()
            days_between = (last_date - entry_date).days
            
            if days_between > 3:  # More than a weekend
                break
            elif days_between > 1:
                # Check if gap only includes weekend
                weekend_only = True
                for d in range(1, days_between):
                    check_date = last_date - timedelta(days=d)
                    if check_date.weekday() < 5:  # Not weekend
                        weekend_only = False
                        break
                if not weekend_only:
                    break
            
            streak += 1
            last_date = entry_date
            
        return streak
        
    finally:
        db.close()
