from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import text
from flask import request
import logging
from collections import defaultdict

from .models import Settings  # Add this import
from .database import SessionLocal
from .utils import get_settings  # Use utils instead
from .streaks import calculate_streak_for_date, calculate_current_streak
from .helpers import in_period, calculate_average_time

# Create a logger instance
logger = logging.getLogger(__name__)

def compare_times(time1, time2, operator):
    """Compare two time objects"""
    ops = {
        '<': lambda: time1 < time2,
        '>': lambda: time1 > time2,
        '=': lambda: time1 == time2,
        '>=': lambda: time1 >= time2,
        '<=': lambda: time1 <= time2
    }
    return ops.get(operator, lambda: False)()

def compare_values(val1, val2, operator):
    """Compare two numeric values"""
    ops = {
        '<': lambda: val1 < val2,
        '>': lambda: val1 > val2,
        '=': lambda: val1 == val2,
        '>=': lambda: val1 >= val2,
        '<=': lambda: val1 <= val2
    }
    return ops.get(operator, lambda: False)()

def evaluate_rule(rule, entry, context):
    """Evaluate a single scoring rule for an entry"""
    try:
        if rule['type'] == 'condition':
            if 'time' in rule:
                entry_time = datetime.strptime(entry['time'], '%H:%M').time()
                compare_time = datetime.strptime(rule['value'], '%H:%M').time()
                return compare_times(entry_time, compare_time, rule['operator'])
            elif 'status' in rule:
                return entry['status'] == rule['value']
            elif 'day' in rule:
                entry_date = datetime.strptime(entry['date'], '%Y-%m-%d')
                if rule['value'] == 'weekend':
                    return entry_date.weekday() >= 5
                elif rule['value'] == 'weekday':
                    return entry_date.weekday() < 5
                else:
                    return entry_date.strftime('%A').lower() == rule['value'].lower()
            elif 'streak' in rule:
                streak = context.get('streak', 0)
                return compare_values(streak, float(rule['value']), rule['operator'])
        elif rule['type'] == 'action':
            if 'award' in rule:
                return float(rule['points'])
            elif 'multiply' in rule:
                return context['current_points'] * float(rule['value'])
            elif 'streak_bonus' in rule:
                return context['streak'] * context.get('streak_multiplier', 0.5)
        return 0
    except (ValueError, KeyError, TypeError) as e:
        logger.error(f"Error evaluating rule: {str(e)}")
        return 0

def load_data():
    """Load entries from database"""
    from .models import Entry  # Import moved inside function
    db = SessionLocal()
    try:
        entries = db.query(Entry).all()
        return [{
            "id": entry.id,
            "date": entry.date,
            "time": entry.time,
            "name": entry.name,
            "status": entry.status,
            "timestamp": entry.timestamp.isoformat()
        } for entry in entries]
    finally:
        db.close()

def calculate_scores(data, period, current_date):
    """Calculate scores with proper date validation"""
    # Ensure current_date is not in the future
    now = datetime.now()
    if current_date > now:
        return []  # Return empty list for future dates
        
    # Convert current_date to datetime if it's a string
    if isinstance(current_date, str):
        current_date = datetime.strptime(current_date, '%Y-%m-%d')

    rankings = defaultdict(lambda: {
        "name": "",
        "points": 0,
        "days": 0,
        "remote_days": 0
    })
    
    # Process each entry
    for entry in data:
        name = entry["name"]
        rankings[name]["name"] = name
        
        # Calculate daily score
        daily_score = calculate_daily_score(entry, settings, mode=mode)
        rankings[name]["points"] += daily_score[mode]
        
        # Track attendance
        if entry["status"] == "remote":
            rankings[name]["remote_days"] += 1
        rankings[name]["days"] += 1
    
    # Convert to list and sort
    results = list(rankings.values())
    results.sort(key=lambda x: (-x["points"], x["name"]))
    
    return results

def get_settings():
    """Get settings with proper object conversion"""
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings:
            return {
                "points": {},
                "late_bonus": 2.0,
                "early_bonus": 2.0,
                "remote_days": {},
                "core_users": [],
                "enable_streaks": False,
                "streak_multiplier": 0.5,
                "enable_tiebreakers": False,
                "tiebreaker_points": 5
            }
        
        # Always return a dictionary, not the Settings object
        return {
            "points": dict(settings.points or {}),
            "late_bonus": float(settings.late_bonus or 2.0),
            "early_bonus": float(settings.early_bonus or 2.0),
            "remote_days": dict(settings.remote_days or {}),
            "core_users": list(settings.core_users or []),
            "enable_streaks": bool(settings.enable_streaks),
            "streak_multiplier": float(settings.streak_multiplier or 0.5),
            "enable_tiebreakers": bool(settings.enable_tiebreakers),
            "tiebreaker_points": int(settings.tiebreaker_points or 5),
            "tiebreaker_types": dict(settings.tiebreaker_types or {})
        }
    finally:
        db.close()

def calculate_daily_score(entry, settings, position=None, total_entries=None, mode='last-in'):
    """Calculate score for a single day's entry with proper streak handling"""
    # Ensure settings is a dict
    if not isinstance(settings, dict):
        settings = get_settings()

    entry_date = datetime.strptime(entry["date"], '%Y-%m-%d')
    weekday = entry_date.strftime('%A').lower()
    
    # Fix settings access
    points_dict = settings.points if isinstance(settings, Settings) else settings.get("points", {})
    daily_shifts = points_dict.get("daily_shifts", {})
    
    # Get shift configuration for the current day
    day_shift = daily_shifts.get(weekday, {
        "hours": points_dict.get("shift_length", 9),
        "start": "09:00"
    })

    # Access settings properties safely
    if isinstance(settings, Settings):
        points = settings.points
        late_bonus = float(settings.late_bonus)
        early_bonus = float(settings.early_bonus)
        streak_multiplier = float(settings.streak_multiplier)
        streaks_enabled = settings.enable_streaks
        tiebreakers_enabled = settings.enable_tiebreakers
    else:
        points = settings.get("points", {})
        late_bonus = float(settings.get("late_bonus", 2.0))
        early_bonus = float(settings.get("early_bonus", 2.0))
        streak_multiplier = float(settings.get("streak_multiplier", 0.5))
        streaks_enabled = settings.get("enable_streaks", False)
        tiebreakers_enabled = settings.get("enable_tiebreakers", False)

    # Get base points based on status
    status = entry["status"].replace("-", "_")
    base_points = float(points.get(status, 0)) if isinstance(points, dict) else 0
    
    # Initialize context
    context = {
        'current_points': base_points,
        'position': position,
        'total_entries': total_entries,
        'streak_multiplier': streak_multiplier
    }

    # Modify late arrival logic to use configured start time
    shift_start = datetime.strptime(day_shift["start"], "%H:%M").time()
    entry_time = datetime.strptime(entry["time"], "%H:%M").time()
    
    is_late = entry_time > shift_start

    # Check if it's a working day for this user
    day_name = entry_date.strftime('%a').lower()
    user_working_days = settings.get("points", {}).get("working_days", {}).get(entry["name"], ['mon','tue','wed','thu','fri'])
    
    # If it's not a working day for this user, return zero points
    if day_name not in user_working_days:
        return {
            "early_bird": 0,
            "last_in": 0,
            "base": 0,
            "streak": 0,
            "position_bonus": 0,
            "breakdown": {
                "base_points": 0,
                "position_bonus": 0,
                "streak_bonus": 0
            }
        }

    # Continue with existing scoring logic
    status = entry["status"].replace("-", "_")
    base_points = settings["points"][status]
    
    # Initialize context for rule evaluation
    context = {
        'current_points': base_points,
        'position': position,
        'total_entries': total_entries,
        'streak_multiplier': settings.get('streak_multiplier', 0.5)
    }
    if 'streak' not in context:  # Provide a default streak
        context['streak'] = 0

    # Apply custom rules if they exist
    rules = settings["points"].get("rules", [])
    if rules:
        for rule in rules:
            if rule['type'] == 'condition':
                if evaluate_rule(rule, entry, context):
                    # Find matching action
                    action_rule = next((r for r in rules if r['type'] == 'action'), None)
                    if action_rule:
                        points_mod = evaluate_rule(action_rule, entry, context)
                        context['current_points'] += points_mod

    # Calculate standard bonuses
    early_bird_bonus = 0
    last_in_bonus = 0
    if position is not None and total_entries is not None and status in ["in_office", "remote"]:
        # Use early_bonus for early-bird mode and late_bonus for last-in mode
        position_bonus = position * late_bonus
        last_in_bonus = position_bonus
        early_bird_bonus = (total_entries + 1 - position) * early_bonus
    
    # Calculate streak bonus only if the entry isn't in the future
    streak_bonus = 0
    if settings.get("enable_streaks", False):
        entry_date = datetime.strptime(entry["date"], '%Y-%m-%d').date()
        current_date = datetime.now().date()
        
        if entry_date <= current_date:  # Only calculate streak for non-future dates
            db = SessionLocal()
            try:
                # Calculate streak up to the entry date
                streak = calculate_streak_for_date(entry["name"], entry_date, db)
                if streak > 0:
                    multiplier = settings.get("streak_multiplier", 0.5)
                    # Reverse logic: In last-in mode, streak subtracts points (penalizes consistent lateness)
                    # In early-bird mode, streak adds points (rewards consistent earliness)
                    streak_bonus = -streak * multiplier if mode == 'last-in' else streak * multiplier
            finally:
                db.close()

    # Apply tie breaker wins if enabled - Modified to use the exact date
    tie_breaker_points = 0
    if settings.get("enable_tiebreakers", False):
        db = SessionLocal()
        try:
            # Updated query to get wins specifically for ties that ended on this date
            wins = db.execute(text("""
                SELECT COUNT(*) FROM tie_breakers t
                JOIN tie_breaker_participants p ON t.id = p.tie_breaker_id
                WHERE p.username = :username
                AND t.period_end::date = :date
                AND p.winner = true
                AND t.status = 'completed'
            """), {
                "username": entry["name"],
                "date": entry["date"]
            }).scalar()
            
            if wins > 0:
                base_points = settings.get("tiebreaker_points", 5) * wins
                # In last-in mode, subtract points for winning (penalize)
                # In early-bird mode, add points for winning (reward)
                tie_breaker_points = -base_points if mode == 'last-in' else base_points
        finally:
            db.close()

    return {
        "early_bird": context['current_points'] + early_bird_bonus - streak_bonus + tie_breaker_points,
        "last_in": context['current_points'] + last_in_bonus + streak_bonus - tie_breaker_points,
        "base": context['current_points'],
        "streak": streak_bonus,
        "position_bonus": last_in_bonus if mode == 'last-in' else early_bird_bonus,
        "tie_breaker": tie_breaker_points,
        "breakdown": {
            "base_points": context['current_points'],
            "position_bonus": last_in_bonus if mode == 'last-in' else early_bird_bonus,
            "streak_bonus": streak_bonus,
            "tie_breaker_points": tie_breaker_points
        }
    }

def decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    return obj
