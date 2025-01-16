import uuid
from datetime import datetime

from .database import SessionLocal
from .models import Settings

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
    """Initialize default settings"""
    db = SessionLocal()
    try:
        if db.query(Settings).first() is None:
            default_settings = Settings(
                id=str(uuid.uuid4()),
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
                    },
                    "rules": []
                },
                late_bonus=1,
                remote_days={},
                core_users=["Matt", "Kushal", "Nathan", "Michael", "Ben"],
                enable_streaks=False,
                streak_multiplier=0.5,
                streaks_enabled=False,
                streak_bonus=0.5,
                enable_tiebreakers=False,
                tiebreaker_points=5,
                tiebreaker_expiry=24,
                auto_resolve_tiebreakers=False,
                tiebreaker_weekly=True,
                tiebreaker_monthly=True
            )
            db.add(default_settings)
            db.commit()
    finally:
        db.close()