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

from .data import calculate_scores, load_data

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

class ConversationContext:
    def __init__(self):
        self.last_query = None
        self.last_results = None
        self.current_topic = None
        self.mentioned_users = []
        self.mentioned_dates = []
        self.follow_up_context = {}

class QueryIntent:
    def __init__(self, intent_type, confidence, parameters=None):
        self.type = intent_type
        self.confidence = confidence
        self.parameters = parameters or {}

class QueryProcessor:
    """Base class for query processing - this was missing"""
    def __init__(self):
        self.conversation_contexts = defaultdict(ConversationContext)
        self.intent_patterns = {
            'comparison': r'compare|versus|vs|difference|between',
            'trend': r'trend|over time|pattern|history',
            'ranking': r'rank|top|bottom|best|worst|leading',
            'status': r'status|where|doing|currently',
            'statistics': r'average|mean|total|count|summary|stats',
            'streak': r'streak|consecutive|row|sequence',
            'schedule': r'schedule|timing|when|arrival|departure'
        }

    def _classify_intent(self, text):
        # Add basic implementation
        for intent, pattern in self.intent_patterns.items():
            if re.search(pattern, text.lower()):
                return intent
        return 'status'  # Default intent

class EnhancedQueryProcessor(QueryProcessor):
    def __init__(self):
        super().__init__()
        self.chat_histories = defaultdict(ChatHistory)
        
        # Add more varied response templates
        self.response_templates = {
            'streak': [
                "🔥 {name} is on fire with a {streak} day streak!",
                "📈 {name}'s streak: {streak} days and counting",
                "⭐ {streak} consecutive days for {name}",
            ],
            'ranking': [
                "🏆 Current rankings:\n{rankings}",
                "🎯 Here's how everyone stands:\n{rankings}",
                "📊 Latest rankings:\n{rankings}",
            ],
            'status': [
                "👥 Current status:\n{status}",
                "📍 Here's where everyone is:\n{status}",
                "🎯 Status update:\n{status}",
            ],
            'suggestion': [
                "💡 You might also want to know: {suggestion}",
                "🤔 Related question: {suggestion}",
                "📝 Try asking: {suggestion}",
            ]
        }
        
    def process_query(self, query, user_id):
        history = self.chat_histories[user_id]
        history.add_message(query)
        
        # Analyze query with context
        context = history.get_context()
        intent, params = self.analyze_query(query, user_id)
        
        # Generate related suggestions
        suggestions = self.generate_suggestions(intent, params, context)
        history.suggestion_context = suggestions
        
        # Generate response
        response = self.format_response(intent, params, context)
        history.add_message(response, is_user=False)
        
        return {
            'response': response,
            'suggestions': suggestions[:2],  # Return top 2 suggestions
            'context': context
        }

    def format_response(self, intent, params, context):
        """Format response with emoji and better structure"""
        response = generate_response(intent, params, SessionLocal())
        
        # Add relevant emoji
        if 'streak' in response.lower():
            response = "🔥 " + response
        if 'ranking' in response.lower():
            response = "🏆 " + response
        if 'schedule' in response.lower():
            response = "📅 " + response
            
        # Add contextual follow-up
        if context and context['suggestions']:
            suggestion = random.choice(context['suggestions'])
            template = random.choice(self.response_templates['suggestion'])
            response += f"\n\n{template.format(suggestion=suggestion)}"
            
        return response

    def generate_suggestions(self, intent, params, context):
        """Generate contextual suggestions based on current query"""
        suggestions = []
        
        if intent.type == 'status':
            suggestions.extend([
                "What's the current ranking?",
                "Who has the longest streak?",
                "Show me attendance trends"
            ])
        elif intent.type == 'streak':
            suggestions.extend([
                "Who's in the office today?",
                "What's the average arrival time?",
                "Show me the top performers"
            ])
        elif intent.type == 'ranking':
            suggestions.extend([
                "How's everyone's attendance this week?",
                "Who's working remotely?",
                "Show me the streak leaderboard"
            ])
            
        # Add user-specific suggestions
        if params.get('users'):
            user = params['users'][0]
            suggestions.append(f"How often is {user} in the office?")
            suggestions.append(f"What's {user}'s average arrival time?")
            
        return suggestions

# Import for dynamic responses
def generate_response(intent, params, db):
    """Generate dynamic response based on intent and parameters"""
    if intent.type == 'comparison':
        return generate_comparison_response(params, db)
    elif intent.type == 'trend':
        return generate_trend_response(params, db)
    elif intent.type == 'ranking':
        return generate_ranking_response(params, db)
    elif intent.type == 'status':
        return generate_status_response(params, db)
    elif intent.type == 'statistics':
        return generate_stats_response(params, db)
    elif intent.type == 'streak':
        return generate_streak_response(params, db)
    elif intent.type == 'schedule':
        return generate_schedule_response(params, db)
    else:
        return "I'm not sure how to help with that. Try asking about attendance, rankings, or statistics."

# Add response generator functions
def generate_comparison_response(params, db):
    users = params.get('users', [])
    if len(users) < 2:
        return "Please specify two or more users to compare."
    
    date_range = params.get('date_range', 'month')
    metrics = params.get('metrics', ['attendance', 'points', 'streaks'])
    
    response = f"Comparing {', '.join(users)}:\n\n"
    
    for metric in metrics:
        if metric == 'attendance':
            # Query attendance patterns
            query = db.query(
                Entry.name,
                func.count(Entry.id).label('total_days'),
                func.avg(func.extract('hour', func.cast(Entry.time, time))).label('avg_arrival')
            ).filter(
                Entry.name.in_(users)
            ).group_by(Entry.name)
            
            results = query.all()
            response += "Attendance Patterns:\n"
            for result in results:
                response += f"- {result.name}: {result.total_days} days, avg arrival: {int(result.avg_arrival)}:00\n"
    
    return response

def generate_status_response(params, db):
    """Generate response for status queries"""
    users = params.get('users', [])
    date_range = params.get('date_range', 'today')
    
    # Build query
    query = db.query(Entry)
    
    # Apply date filter
    date = parse_date_reference(date_range or 'today')
    query = query.filter(Entry.date == date.isoformat())
    
    # Apply user filter if specified
    if users:
        query = query.filter(Entry.name.in_(users))
    
    # Get results
    entries = query.order_by(Entry.time).all()
    
    if not entries:
        if users:
            return f"No attendance records found for {', '.join(users)} on {date.strftime('%A, %d %B')}"
        return f"No attendance records found for {date.strftime('%A, %d %B')}"
    
    response = f"Status for {date.strftime('%A, %d %B')}:\n"
    for entry in entries:
        response += f"• {entry.name}: {entry.status} at {entry.time}\n"
    
    return response

def generate_trend_response(params, db):
    """Generate response for trend analysis queries"""
    users = params.get('users', [])
    metrics = params.get('metrics', ['attendance'])
    
    # Default to last 30 days if no date range specified
    date = datetime.now().date() - timedelta(days=30)
    query = db.query(Entry).filter(Entry.date >= date.isoformat())
    
    if users:
        query = query.filter(Entry.name.in_(users))
    
    entries = query.order_by(Entry.date).all()
    
    if not entries:
        return "No data available for trend analysis"
    
    response = "Trend Analysis:\n"
    
    # Group entries by user
    user_entries = defaultdict(list)
    for entry in entries:
        user_entries[entry.name].append(entry)
    
    for user, user_data in user_entries.items():
        response += f"\n{user}:\n"
        total_days = len(user_data)
        in_office = sum(1 for e in user_data if e.status == 'in-office')
        remote = sum(1 for e in user_data if e.status == 'remote')
        
        response += f"• Attendance Rate: {((in_office + remote) / total_days * 100):.1f}%\n"
        response += f"• Office Days: {in_office} ({(in_office/total_days * 100):.1f}%)\n"
        response += f"• Remote Days: {remote} ({(remote/total_days * 100):.1f}%)\n"
        
        # Calculate average arrival time
        times = [datetime.strptime(e.time, "%H:%M") for e in user_data if e.status in ['in-office', 'remote']]
        if times:
            avg_minutes = sum((t.hour * 60 + t.minute) for t in times) // len(times)
            avg_time = f"{avg_minutes//60:02d}:{avg_minutes%60:02d}"
            response += f"• Average Arrival: {avg_time}\n"
    
    return response

def generate_ranking_response(params, db):
    """Generate response for ranking queries"""
    limit = params.get('limit', 3)
    date_range = params.get('date_range', 'today')
    
    # Load data and calculate rankings
    data = load_data()
    current_date = parse_date_reference(date_range)
    period = 'day' if date_range == 'today' else 'week' if 'week' in date_range else 'month'
    
    rankings = calculate_scores(data, period, current_date)
    
    if not rankings:
        return f"No ranking data available for {date_range}"
    
    response = f"Top {limit} Rankings for {date_range}:\n"
    for i, rank in enumerate(rankings[:limit], 1):
        response += f"{i}. {rank['name']}: {rank['score']} points"
        if rank.get('current_streak', 0) > 0:
            response += f" (streak: {rank['current_streak']})"
        response += f" - Avg. arrival: {rank.get('average_arrival_time', 'N/A')}\n"
    
    return response

def generate_streak_response(params, db):
    """Generate response for streak queries"""
    users = params.get('users', [])
    streaks = db.query(UserStreak)
    
    if users:
        streaks = streaks.filter(UserStreak.username.in_(users))
    
    streaks = streaks.order_by(UserStreak.current_streak.desc()).all()
    
    if not streaks:
        return "No active streaks found"
    
    response = "Current Streaks:\n"
    for streak in streaks:
        if streak.current_streak > 0:
            response += f"• {streak.username}: {streak.current_streak} days"
            if streak.current_streak == streak.max_streak:
                response += " (Personal Best! 🏆)"
            elif streak.max_streak > streak.current_streak:
                response += f" (Best: {streak.max_streak})"
            response += "\n"
    
    return response

def generate_schedule_response(params, db):
    """Generate response for schedule/timing queries"""
    users = params.get('users', [])
    date_range = params.get('date_range', 'today')
    date = parse_date_reference(date_range)
    
    query = db.query(Entry).filter(Entry.date == date.isoformat())
    
    if users:
        query = query.filter(Entry.name.in_(users))
    
    entries = query.order_by(Entry.time).all()
    
    if not entries:
        return f"No schedule information found for {date.strftime('%A, %d %B')}"
    
    response = f"Schedule for {date.strftime('%A, %d %B')}:\n"
    for entry in entries:
        response += f"• {entry.name}: Started at {entry.time} ({entry.status})\n"
    
    return response

def generate_stats_response(params, db):
    """Generate response for statistics queries"""
    users = params.get('users', [])
    metrics = params.get('metrics', ['attendance'])
    date_range = params.get('date_range')
    
    # Default to current month if no date range specified
    if not date_range:
        start_date = datetime.now().replace(day=1).date()
    else:
        start_date = parse_date_reference(date_range)
    
    query = db.query(Entry).filter(Entry.date >= start_date.isoformat())
    
    if users:
        query = query.filter(Entry.name.in_(users))
    
    entries = query.all()
    
    if not entries:
        return "No statistics available for the specified criteria"
    
    response = "Statistics Summary:\n"
    
    # Overall stats
    total_entries = len(entries)
    in_office = sum(1 for e in entries if e.status == 'in-office')
    remote = sum(1 for e in entries if e.status == 'remote')
    sick = sum(1 for e in entries if e.status == 'sick')
    leave = sum(1 for e in entries if e.status == 'leave')
    
    response += f"Total Records: {total_entries}\n"
    response += f"• In Office: {in_office} ({in_office/total_entries*100:.1f}%)\n"
    response += f"• Remote: {remote} ({remote/total_entries*100:.1f}%)\n"
    response += f"• Sick Days: {sick}\n"
    response += f"• Leave Days: {leave}\n"
    
    # Average arrival time
    times = [datetime.strptime(e.time, "%H:%M") for e in entries if e.status in ['in-office', 'remote']]
    if times:
        avg_minutes = sum((t.hour * 60 + t.minute) for t in times) // len(times)
        avg_time = f"{avg_minutes//60:02d}:{avg_minutes%60:02d}"
        response += f"Average Arrival Time: {avg_time}\n"
    
    return response

# ...existing code...

class ChatHistory:
    def __init__(self):
        self.messages = []
        self.current_context = None
        self.mentioned_users = []
        self.mentioned_dates = []
        self.follow_up_context = {}

    def add_message(self, message, is_user=True):
        self.messages.append({
            'content': message,
            'timestamp': datetime.now(),
            'is_user': is_user
        })
        if len(self.messages) > 10:  # Keep last 10 messages
            self.messages.pop(0)

    # ...existing code...