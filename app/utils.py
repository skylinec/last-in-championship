import uuid
from datetime import datetime

from .database import SessionLocal
from .models import Settings
from .caching import HashableCacheWithMetrics

def get_settings():
    """Get application settings"""
    db = SessionLocal()
    try:
        return db.query(Settings).first()
    finally:
        db.close()

def get_core_users():
    """Get list of core users"""
    settings = get_settings()
    return settings.core_users if settings else []

def init_settings():
    """Initialize settings if not exists"""
    db = SessionLocal()
    if not db.query(Settings).first():
        default_settings = Settings(
            points={
                "in_office": 10,
                "remote": 8,
                "sick": 5,
                "leave": 5,
                "shift_length": 9,
                "daily_shifts": {
                    day: {"hours": 9, "start": "09:00"}
                    for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]
                },
                "working_days": {
                    user: ['mon','tue','wed','thu','fri'] 
                    for user in ["Matt", "Kushal", "Nathan", "Michael", "Ben"]
                }
            },
            late_bonus=2.0,  # Ensure late_bonus is positive for last-in mode
            early_bonus=0.0,  # Set early_bonus to 0 to enforce last-in mode
            remote_days={},
            core_users=["Matt", "Kushal", "Nathan", "Michael", "Ben"],
            enable_streaks=False,
            streak_multiplier=0.5,
            enable_tiebreakers=False,
            tiebreaker_points=5,
            tiebreaker_expiry=24,
            auto_resolve_tiebreakers=False,
            tiebreaker_weekly=True,
            tiebreaker_monthly=True
        )
        db.add(default_settings)
        db.commit()
    db.close()

@HashableCacheWithMetrics
def load_settings():
    """Load settings with proper type conversion and defaults"""
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings:
            init_settings()
            settings = db.query(Settings).first()
        
        return {
            "points": settings.points if isinstance(settings.points, dict) else {},
            "late_bonus": settings.late_bonus,
            "remote_days": settings.remote_days,
            "core_users": settings.core_users,
            "enable_streaks": settings.enable_streaks,
            "streak_multiplier": settings.streak_multiplier,
            "enable_tiebreakers": settings.enable_tiebreakers,
            "tiebreaker_points": settings.tiebreaker_points,
            "tiebreaker_expiry": settings.tiebreaker_expiry,
            "auto_resolve_tiebreakers": settings.auto_resolve_tiebreakers,
            "tiebreaker_weekly": settings.tiebreaker_weekly,
            "tiebreaker_monthly": settings.tiebreaker_monthly
        }
    finally:
        db.close()