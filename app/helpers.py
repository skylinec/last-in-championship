from datetime import datetime, timedelta
from typing import Union, List, Dict, Any
from sqlalchemy import text
from functools import wraps
import re

from .database import SessionLocal

def format_date_range(start_date: datetime, end_date: datetime, period: str) -> str:
    """Format date range for display"""
    if period == 'day':
        return start_date.strftime('%d/%m/%Y')
    elif period == 'week':
        return f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"
    elif period == 'month':
        return start_date.strftime('%B %Y')
    return start_date.strftime('%d/%m/%Y')

def normalize_status(status: str) -> str:
    """Normalize status strings"""
    return status.replace("-", "_")

def calculate_average_time(times: List[datetime]) -> str:
    """Calculate average time from a list of datetime objects"""
    if not times:
        return "N/A"
    try:
        total_minutes = sum((t.hour * 60 + t.minute) for t in times)
        avg_minutes = total_minutes // len(times)
        return f"{avg_minutes//60:02d}:{avg_minutes%60:02d}"
    except (AttributeError, TypeError):
        return "N/A"

def track_response_time(route_name):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Implementation
            return f(*args, **kwargs)
        return wrapped
    return decorator

def in_period(entry, period, current_date):
    """Check if entry falls within the specified period"""
    try:
        entry_date = datetime.strptime(entry["date"], '%Y-%m-%d').date()
        current = current_date.date() if isinstance(current_date, datetime) else current_date
        
        if period == 'day':
            return entry_date == current
        elif period == 'week':
            # Get Monday of the current week
            week_start = current - timedelta(days=current.weekday())
            week_end = week_start + timedelta(days=6)  # Fix: Change days() to days
            return week_start <= entry_date <= week_end
        elif period == 'month':
            # Check if same year and month
            return entry_date.year == current.year and entry_date.month == current.month
        return True
    except (ValueError, AttributeError):
        return False

def normalize_settings(settings_dict):
    """Normalize settings dictionary for consistent comparison"""
    # Extract point values, handling nested dictionaries
    points = settings_dict.get("points", {})
    normalized_points = {
        "in_office": int(points.get("in_office", 0)),
        "remote": int(points.get("remote", 0)), 
        "sick": int(points.get("sick", 0)),
        "leave": int(points.get("leave", 0)),
        "shift_length": float(points.get("shift_length", 9)),
        "daily_shifts": points.get("daily_shifts", {}),
        "working_days": points.get("working_days", {}),
        "rules": points.get("rules", [])
    }

    # Explicitly convert boolean fields with default values
    bool_fields = {
        "enable_streaks": False,
        "enable_tiebreakers": False,
        "auto_resolve_tiebreakers": False,
        "tiebreaker_weekly": True,  # Add default for weekly generation
        "tiebreaker_monthly": True   # Add default for monthly generation
    }
    
    for field, default in bool_fields.items():
        # Handle both string and boolean inputs
        value = settings_dict.get(field, default)
        if isinstance(value, str):
            settings_dict[field] = value.lower() in ['true', '1', 'yes', 'on']
        else:
            settings_dict[field] = bool(value)

    return {
        "points": normalized_points,
        "late_bonus": float(settings_dict.get("late_bonus", 0)),
        "remote_days": settings_dict.get("remote_days", {}),
        "core_users": sorted(settings_dict.get("core_users", [])),
        "enable_streaks": settings_dict["enable_streaks"],
        "streak_multiplier": float(settings_dict.get("streak_multiplier", 0.5)),
        "enable_tiebreakers": settings_dict["enable_tiebreakers"],
        "tiebreaker_points": int(settings_dict.get("tiebreaker_points", 5)),
        "tiebreaker_expiry": int(settings_dict.get("tiebreaker_expiry", 24)),
        "auto_resolve_tiebreakers": settings_dict["auto_resolve_tiebreakers"],
        "tiebreaker_weekly": settings_dict["tiebreaker_weekly"],
        "tiebreaker_monthly": settings_dict["tiebreaker_monthly"]
    }

def parse_date_reference(text):
    """Parse natural language date references"""
    text = text.lower()
    today = datetime.now().date()
    
    if "yesterday" in text:
        return today - timedelta(days=1)
    elif "today" in text:
        return today
    elif "tomorrow" in text:
        return today + timedelta(days=1)
    elif "last week" in text:
        return today - timedelta(weeks=1)
    elif "next week" in text:
        return today + timedelta(weeks=1)
    elif "last month" in text:
        return today.replace(day=1) - timedelta(days=1)
    
    # Try to parse specific dates
    date_patterns = [
        r"(\d{1,2})(?:st|nd|rd|th)? (?:of )?([a-zA-Z]+)",  # "21st March", "3rd of April"
        r"([a-zA-Z]+) (\d{1,2})(?:st|nd|rd|th)?"          # "March 21", "April 3"
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                groups = match.groups()
                if groups[0].isdigit():
                    day, month = groups
                else:
                    month, day = groups
                date_str = f"{day} {month} {today.year}"
                return datetime.strptime(date_str, "%d %B %Y").date()
            except ValueError:
                continue
    
    return today

# Add other helper functions from main_old.py that don't belong in specific modules
