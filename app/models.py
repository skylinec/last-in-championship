# models.py
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import (Column, String, Integer, DateTime, Date, Float, JSON,
                       Boolean)
from sqlalchemy.orm import relationship

from .database import Base, SessionLocal

def get_core_users():
    """Get list of core users from settings"""
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        return settings.core_users if settings else []
    finally:
        db.close()

class Entry(Base):
    __tablename__ = 'entries'
    id = Column(String, primary_key=True)
    date = Column(String, nullable=False)
    time = Column(String, nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.now)

class User(Base):
    __tablename__ = "users"
    username = Column(String, primary_key=True)
    password = Column(String, nullable=False)

class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    points = Column(JSON)
    late_bonus = Column(Float, default=2.0)
    early_bonus = Column(Float, default=0.0)
    remote_days = Column(JSON)
    core_users = Column(JSON)
    enable_streaks = Column(Boolean, default=False)
    streak_multiplier = Column(Float, default=0.5)
    enable_tiebreakers = Column(Boolean, default=False)
    tiebreaker_points = Column(Integer, default=5)
    tiebreaker_expiry = Column(Integer, default=24)
    auto_resolve_tiebreakers = Column(Boolean, default=False)
    tiebreaker_weekly = Column(Boolean, default=True)
    tiebreaker_monthly = Column(Boolean, default=True)
    tiebreaker_types = Column(JSON)
    monitoring_start_date = Column(Date)

    def get(self, key, default=None):
        """Add get method to make Settings behave like a dict"""
        return getattr(self, key, default)

    def to_dict(self):
        """Improved dictionary conversion"""
        return {
            "points": dict(self.points or {}),
            "late_bonus": float(self.late_bonus or 0.0),
            "early_bonus": float(self.early_bonus or 0.0),
            "remote_days": dict(self.remote_days or {}),
            "core_users": list(self.core_users or []),
            "enable_streaks": bool(self.enable_streaks),
            "streak_multiplier": float(self.streak_multiplier or 0.5),
            "enable_tiebreakers": bool(self.enable_tiebreakers),
            "tiebreaker_points": int(self.tiebreaker_points or 5),
            "tiebreaker_expiry": int(self.tiebreaker_expiry or 24),
            "auto_resolve_tiebreakers": bool(self.auto_resolve_tiebreakers),
            "tiebreaker_weekly": bool(self.tiebreaker_weekly),
            "tiebreaker_monthly": bool(self.tiebreaker_monthly),
            "tiebreaker_types": dict(self.tiebreaker_types or {}),
            "monitoring_start_date": self.monitoring_start_date
        }

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    user = Column(String, nullable=False)
    action = Column(String, nullable=False)
    details = Column(String)
    changes = Column(JSON, nullable=True)  # Make sure nullable is True

    def __repr__(self):
        return f"<AuditLog {self.action} by {self.user} at {self.timestamp}>"

class UserStreak(Base):
    __tablename__ = 'user_streaks'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False)
    current_streak = Column(Integer, default=0)
    last_attendance = Column(DateTime, nullable=True)
    max_streak = Column(Integer, default=0)

class TieBreaker(Base):
    __tablename__ = 'tie_breakers'
    id = Column(Integer, primary_key=True, autoincrement=True)
    period = Column(String, nullable=False)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    points = Column(Float, nullable=False)
    mode = Column(String, nullable=False)
    status = Column(String, nullable=False, default='pending')
    created_at = Column(DateTime, default=datetime.now)
    resolved_at = Column(DateTime, nullable=True)
    # e.g. points_applied if you have it

class TieBreakerParticipant(Base):
    __tablename__ = 'tie_breaker_participants'
    id = Column(Integer, primary_key=True, autoincrement=True)
    tie_breaker_id = Column(Integer, nullable=False)
    username = Column(String, nullable=False)
    game_choice = Column(String, nullable=True)
    ready = Column(Boolean, default=False)
    winner = Column(Boolean, default=False)

class TieBreakerGame(Base):
    __tablename__ = 'tie_breaker_games'
    id = Column(Integer, primary_key=True, autoincrement=True)
    tie_breaker_id = Column(Integer, nullable=False)
    game_type = Column(String, nullable=False)
    player1 = Column(String, nullable=False)
    player2 = Column(String, nullable=False)
    status = Column(String, nullable=False, default='pending')
    game_state = Column(JSON, nullable=False)
    winner = Column(String, nullable=True)
    final_tiebreaker = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    completed_at = Column(DateTime, nullable=True)