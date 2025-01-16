from decimal import Decimal
from datetime import datetime, timedelta
from sqlalchemy import text
from flask import request
import logging

from .database import SessionLocal
from .utils import get_settings  # Use utils instead
from .streaks import calculate_streak_for_date, calculate_current_streak
from .helpers import in_period, calculate_average_time
from .app import app

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
        app.logger.error(f"Error evaluating rule: {str(e)}")
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
    """Calculate scores with proper handling of early-bird/last-in modes"""
    settings = get_settings()  # Use utils function instead of routes
    filtered_data = [entry for entry in data if in_period(entry, period, current_date)]
    mode = request.args.get('mode', 'last-in')
    
    # Group entries by date first
    daily_entries = {}
    daily_scores = {}
    
    for entry in filtered_data:
        date = entry["date"]
        if date not in daily_entries:
            daily_entries[date] = []
        daily_entries[date].append(entry)
    
    # Calculate scores for each day
    for date, entries in daily_entries.items():
        # Sort entries by time (always ascending)
        entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
        
        total_entries = len(entries)
        for position, entry in enumerate(entries, 1):
            name = entry["name"]
            if name not in daily_scores:
                daily_scores[name] = {
                    "early_bird_total": 0,
                    "last_in_total": 0,
                    "active_days": 0,
                    "base_points_total": 0,
                    "position_bonus_total": 0,
                    "streak_bonus_total": 0,
                    "stats": {
                        "in_office": 0,
                        "remote": 0,
                        "sick": 0,
                        "leave": 0,
                        "days": 0,
                        "latest_arrivals": 0,
                        "arrival_times": []
                    }
                }
            
            # Calculate scores for both modes
            scores = calculate_daily_score(entry, settings, position, total_entries, mode)
            
            status = entry["status"].replace("-", "_")
            daily_scores[name]["stats"][status] += 1
            daily_scores[name]["stats"]["days"] += 1
            
            if status in ["in_office", "remote"]:
                daily_scores[name]["active_days"] += 1
                daily_scores[name]["early_bird_total"] += scores["early_bird"]
                daily_scores[name]["last_in_total"] += scores["last_in"]
                daily_scores[name]["base_points_total"] += scores["base"]
                daily_scores[name]["position_bonus_total"] += scores["position_bonus"]
                daily_scores[name]["streak_bonus_total"] += scores["streak"]
                
                # Track achievements based on mode
                if (mode == 'last-in' and position == total_entries) or \
                   (mode == 'early-bird' and position == 1):
                    daily_scores[name]["stats"]["latest_arrivals"] += 1
                
                arrival_time = datetime.strptime(entry["time"], "%H:%M")
                daily_scores[name]["stats"]["arrival_times"].append(arrival_time)
    
    # Format rankings
    rankings = []
    for name, scores in daily_scores.items():
        if scores["active_days"] > 0:
            early_bird_avg = scores["early_bird_total"] / scores["active_days"]
            last_in_avg = scores["last_in_total"] / scores["active_days"]
            arrival_times = scores["stats"]["arrival_times"]
            
            rankings.append({
                "name": name,
                "score": round(last_in_avg if mode == 'last-in' else early_bird_avg, 2),
                "streak": calculate_current_streak(name),
                "stats": scores["stats"],
                "average_arrival_time": calculate_average_time(arrival_times) if arrival_times else "N/A",
                "base_points": scores["base_points_total"] / scores["active_days"] if scores["active_days"] > 0 else 0,
                "position_bonus": scores["position_bonus_total"] / scores["active_days"] if scores["active_days"] > 0 else 0,
                "streak_bonus": scores["streak_bonus_total"] / scores["active_days"] if scores["active_days"] > 0 else 0
            })
    
    # Always sort by descending score (scores are already mode-specific)
    rankings.sort(key=lambda x: x["score"], reverse=True)
    return rankings

def calculate_daily_score(entry, settings, position=None, total_entries=None, mode='last-in'):
    """Calculate score for a single day's entry with proper streak handling"""
    # Get day-specific start time for late calculations
    entry_date = datetime.strptime(entry["date"], '%Y-%m-%d')
    weekday = entry_date.strftime('%A').lower()
    day_shift = settings["points"].get("daily_shifts", {}).get(weekday, {
        "hours": settings["points"].get("shift_length", 9),
        "start": "09:00"
    })
    
    shift_start = datetime.strptime(day_shift["start"], "%H:%M").time()
    entry_time = datetime.strptime(entry["time"], "%H:%M").time()
    
    # Modify late arrival logic to use configured start time
    is_llate = entry_time > shift_start

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
        # Modify bonus calculation to make early-bird exactly inverse of last-in
        # Now first person (position=1) gets bonus equal to total_entries in early-bird mode
        # Last person (position=total_entries) gets bonus equal to total_entries in last-in mode
        position_bonus = position * settings["late_bonus"]
        last_in_bonus = position_bonus
        early_bird_bonus = (total_entries + 1 - position) * settings["late_bonus"]
    
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
