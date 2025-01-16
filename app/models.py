# models.py
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Float, Integer,
                        String, func, text)
from sqlalchemy.orm import relationship

from .database import Base, SessionLocal

# ...existing code...

def get_core_users():
    # Move this to a new utils.py to avoid circular imports
    from .utils import get_settings
    return get_settings().core_users if get_settings() else []

def migrate_database():
    # ...existing code for migrations if any...
    pass

class Entry(Base):
    __tablename__ = 'entries'
    id = Column(String, primary_key=True)
    date = Column(String, nullable=False)
    time = Column(String, nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.now)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False)
    password = Column(String, nullable=False)

class Settings(Base):
    __tablename__ = 'settings'
    id = Column(String, primary_key=True)
    points = Column(JSON, nullable=False)
    late_bonus = Column(Float, nullable=False)
    remote_days = Column(JSON, nullable=False)
    core_users = Column(JSON, nullable=False)
    enable_streaks = Column(Boolean, default=False)
    streak_multiplier = Column(Float, default=0.5)
    streaks_enabled = Column(Boolean, default=False)
    streak_bonus = Column(Float, default=0.5)
    monitoring_start_date = Column(Date, default=lambda: datetime.now().replace(month=1, day=1))
    enable_tiebreakers = Column(Boolean, default=False)
    tiebreaker_points = Column(Integer, default=5)
    tiebreaker_expiry = Column(Integer, default=24)
    auto_resolve_tiebreakers = Column(Boolean, default=False)
    tiebreaker_weekly = Column(Boolean, default=True)
    tiebreaker_monthly = Column(Boolean, default=True)

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.now)
    user = Column(String, nullable=False)
    action = Column(String, nullable=False)
    details = Column(String)
    changes = Column(JSON, nullable=True)

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

# ...existing code...