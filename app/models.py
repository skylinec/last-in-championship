# models.py
import json
import uuid
import re
import random
from time import time
from datetime import datetime, timedelta

from collections import defaultdict
from sqlalchemy import (
    Column, String, Integer, DateTime, Date, Float, JSON,
    Boolean, func, text
)
from sqlalchemy.orm import relationship
from .database import Base, SessionLocal
from .helpers import parse_date_reference

import os
import logging

# from .data import calculate_scores, load_data

def get_core_users():
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        return settings.core_users if settings else []
    finally:
        db.close()

def migrate_database():
    # ...existing code for migrations if any...
    pass

def init_settings():
    db = SessionLocal()
    try:
        existing = db.query(Settings).first()
        if not existing:
            # ...create default settings...
            default = Settings(
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
            db.add(default)
            db.commit()
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

from .chatbot import ChatHistory, ConversationContext, QueryIntent, QueryProcessor, EnhancedQueryProcessor, generate_response

# ...existing code...