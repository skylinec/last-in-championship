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

def calculate_streak_for_date(username, target_date, db):
    """Calculate streak up to a specific date"""
    try:
        if not username or not target_date:
            return 0
            
        target_date = (datetime.strptime(target_date, '%Y-%m-%d').date() 
                      if isinstance(target_date, str) 
                      else target_date.date() if isinstance(target_date, datetime) 
                      else target_date)
        
        # Don't start/end streaks on weekends
        if is_weekend(target_date):
            return 0

        working_days = get_working_days(db, username)
        
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
        last_date = None

        for entry in entries:
            entry_date = datetime.strptime(entry.date, '%Y-%m-%d').date()
            
            # Skip weekend entries when calculating streak
            if is_weekend(entry_date):
                continue
                
            if last_date is None:
                last_date = entry_date
                streak = 1
                continue

            days_between = (last_date - entry_date).days

            # Skip weekends when counting days between
            non_weekend_days = sum(1 for d in range(1, days_between)
                                 if not is_weekend(entry_date + timedelta(days=d)))

            if non_weekend_days == 0:
                streak += 1
            else:
                break

            last_date = entry_date

        return streak

    except Exception as e:
        logger.error(f"Error calculating streak: {str(e)}")
        return 0

def get_streak_history(username, db):
    """Get past notable streaks for a user"""
    try:
        working_days = get_working_days(db, username)
        today = datetime.now().date()
        
        entries = db.execute(
            Entry.select().where(
                Entry.c.name == username,
                Entry.c.status.in_(['in-office', 'remote'])
            ).order_by(Entry.c.date.asc())
        ).fetchall()

        if not entries:
            return []

        streaks = []
        current_streak = 0
        streak_start = None
        last_date = None
        last_working_day = None

        for entry in entries:
            entry_date = datetime.strptime(entry.date, '%Y-%m-%d').date()
            
            # Initialize streak start if this is the first entry
            if streak_start is None:
                streak_start = entry_date
                current_streak = 1
                last_date = entry_date
                continue

            days_between = (entry_date - last_date).days - 1
            
            # Check if streak is broken
            streak_broken = False
            break_reason = None
            
            if days_between > 0:
                # Check each day in between for working days
                check_date = last_date + timedelta(days=1)
                while check_date < entry_date:
                    if is_working_day(check_date, working_days):
                        streak_broken = True
                        break_reason = f"Missed working day on {check_date.strftime('%d/%m/%Y')}"
                        break
                    check_date += timedelta(days=1)
            
            if streak_broken:
                if current_streak >= 3:  # Only store significant streaks
                    next_entry = db.execute(
                        Entry.select().where(
                            Entry.c.name == username,
                            Entry.c.date > last_date.strftime('%Y-%m-%d'),
                            Entry.c.date < (last_date + timedelta(days=7)).strftime('%Y-%m-%d')
                        ).order_by(Entry.c.date.asc())
                    ).first()

                    if next_entry and next_entry.status not in ['in-office', 'remote']:
                        break_reason += f" ({next_entry.status})"

                    streaks.append({
                        'length': current_streak,
                        'start': streak_start,
                        'end': last_date,
                        'break_reason': break_reason
                    })
                
                # Reset streak
                streak_start = entry_date
                current_streak = 1
            else:
                current_streak += 1
            
            last_date = entry_date

        # Add final streak if significant
        if current_streak >= 3:
            is_active = (today - last_date).days <= 3  # Consider weekends
            # Only add to past streaks if it's not currently active
            if not is_active:
                streaks.append({
                    'length': current_streak,
                    'start': streak_start,
                    'end': last_date,
                    'break_reason': "End of records"
                })

        # Sort by length first, then by recency for same lengths
        streaks.sort(key=lambda x: (-x['length'], -x['end'].toordinal()))
        return streaks

    except Exception as e:
        logger.error(f"Error getting streak history: {str(e)}", exc_info=True)
        return []

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
