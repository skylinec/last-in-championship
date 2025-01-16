from datetime import datetime
import time
import random
import re
from collections import defaultdict
from fuzzywuzzy import fuzz, process
import logging
from sqlalchemy import text, func
from typing import Dict, List, Optional, Union, Any
from datetime import timedelta

from .database import SessionLocal
from .models import Entry, UserStreak, Settings
from .helpers import parse_date_reference
from .data import calculate_scores, load_data

class ConversationContext:
    def __init__(self):
        self.last_query = None
        self.last_results = None
        self.current_topic = None
        self.mentioned_users = []
        self.mentioned_dates = []
        self.follow_up_context = {}

    def get_context(self):
        if not hasattr(self, 'messages'):
            return None
        return {
            'last_message': self.messages[-1]['content'] if self.messages else None,
            'topic': self.current_topic,
            'entities': self.last_entities if hasattr(self, 'last_entities') else {},
            'suggestions': self.suggestion_context if hasattr(self, 'suggestion_context') else {}
        }

class QueryIntent:
    def __init__(self, intent_type, confidence, parameters=None):
        self.type = intent_type
        self.confidence = confidence
        self.parameters = parameters or {}

class QueryProcessor:
    """Base class for query processing"""
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

    def analyze_query(self, query, user_id):
        context = self.conversation_contexts[user_id]
        tokens = self._tokenize(query.lower())
        
        if self._is_followup_question(query, context):
            return self._handle_followup(query, context)
        
        intent = self._classify_intent(query, tokens)
        params = self._extract_parameters(query, tokens, context)
        
        context.last_query = query
        context.current_topic = intent.type
        context.mentioned_users.extend(params.get('users', []))
        
        return intent, params

    def _tokenize(self, text):
        return text.split()

    def _is_followup_question(self, query, context):
        follow_up_indicators = [
            'what about', 'how about', 'and',
            'what else', 'who else', 'then', 'also'
        ]
        return any(indicator in query.lower() for indicator in follow_up_indicators)

    def _handle_followup(self, query, context):
        intent = self._classify_intent(query, self._tokenize(query.lower()))
        params = context.follow_up_context.copy() if hasattr(context, 'follow_up_context') else {}
        new_params = self._extract_parameters(query, self._tokenize(query.lower()), context)
        params.update(new_params)
        return intent, params

    def _classify_intent(self, query, tokens):
        scores = {}
        for intent, pattern in self.intent_patterns.items():
            if re.search(pattern, query.lower()):
                scores[intent] = fuzz.ratio(pattern, query.lower())
        
        if not scores:
            return QueryIntent('status', 0.5)
            
        best_intent = max(scores.items(), key=lambda x: x[1])
        return QueryIntent(best_intent[0], best_intent[1] / 100)

    def _extract_parameters(self, query, tokens, context):
        # ... existing parameter extraction code from models.py ...
        pass

class EnhancedQueryProcessor(QueryProcessor):
    def __init__(self):
        super().__init__()
        self.chat_histories = defaultdict(ChatHistory)
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
        
        context = history.get_context()
        intent, params = self.analyze_query(query, user_id)
        
        suggestions = self.generate_suggestions(intent, params, context)
        history.suggestion_context = suggestions
        
        response = self.format_response(intent, params, context)
        history.add_message(response, is_user=False)
        
        return {
            'response': response,
            'suggestions': suggestions[:2],
            'context': context
        }

    def format_response(self, intent, params, context):
        """Format response with emoji and better structure"""
        response = generate_response(intent, params, SessionLocal())
        
        if 'streak' in response.lower():
            response = "🔥 " + response
        if 'ranking' in response.lower():
            response = "🏆 " + response
        if 'schedule' in response.lower():
            response = "📅 " + response
            
        if context and context.get('suggestions'):
            suggestion = random.choice(context['suggestions'])
            template = random.choice(self.response_templates['suggestion'])
            response += f"\n\n{template.format(suggestion=suggestion)}"
            
        return response

    def generate_suggestions(self, intent, params, context):
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
            
        if params.get('users'):
            user = params['users'][0]
            suggestions.append(f"How often is {user} in the office?")
            suggestions.append(f"What's {user}'s average arrival time?")
            
        return suggestions

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

    def get_context(self):
        return {
            'last_message': self.messages[-1]['content'] if self.messages else None,
            'topic': self.current_context,
            'entities': {
                'users': self.mentioned_users,
                'dates': self.mentioned_dates
            },
            'suggestions': self.follow_up_context
        }

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