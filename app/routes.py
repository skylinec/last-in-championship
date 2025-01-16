# routes.py
import json
import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps
from threading import Lock, Thread

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import inspect, text

from flask import current_app as app  # Use current_app instead of direct import
from .caching import HashableCacheWithMetrics
from .chatbot import EnhancedQueryProcessor  # Add this line
from .data import (calculate_daily_score, calculate_scores, decimal_to_float,
                   load_data)
from .database import SessionLocal
# from your local modules
from .game import (apply_move, check_connect4_winner, check_tictactoe_winner,
                   check_winner, create_test_games, is_valid_move)
from .helpers import (format_date_range, in_period, normalize_settings,
                      normalize_status, track_response_time)
from .metrics import (ATTENDANCE_COUNT, AUDIT_ACTIONS, IN_PROGRESS,
                      RANKING_CALLS, REQUEST_COUNT, REQUEST_TIME,
                      RESPONSE_TIME)
from .models import (AuditLog, Entry, Settings, TieBreaker, TieBreakerGame,
                     TieBreakerParticipant, User, UserStreak, get_core_users)
from .sockets import notify_game_update, socketio
from .tie_breakers import (check_tie_breaker_completion, create_game,
                           create_next_game, create_next_game_after_draw,
                           create_test_tie_breaker, determine_winner)
from .blueprints import bp  # Import bp from blueprints instead of creating it here
from .utils import init_settings

# If you need to call methods from your main app or from 'app.py' directly, 
# you typically do that through current_app from flask, or separate your code further.

# Remove this line since we moved it to blueprints.py:
# bp = Blueprint('bp', __name__)

rankings_lock = Lock()

# -------------
# AUTH HELPERS
# -------------

def verify_user(username, password):
    """Verify user credentials from database (placeholder, no hashing)."""
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        return user is not None and user.password == password
    except Exception as e:
        logging.error(f"Error verifying user: {str(e)}")
        return False
    finally:
        db.close()

def save_user(username, password):
    """Save a new user to the database."""
    db = SessionLocal()
    try:
        user = User(username=username, password=password)
        db.add(user)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logging.error(f"Error saving user: {str(e)}")
        return False
    finally:
        db.close()

def login_required(f):
    """Decorator to require login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('bp.login'))  # route name is 'bp.login'
        return f(*args, **kwargs)
    return decorated_function

# -------------
# AUDIT LOGGING
# -------------

def log_audit(action, user, details, old_data=None, new_data=None):
    """Log an audit entry to the database with old/new data comparison."""
    db = SessionLocal()
    try:
        logging.info(f"Starting audit log for {action} by {user}")

        # Clean values for JSON
        def clean_value(v):
            if v is None:
                return "None"
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: clean_value(v) for k, v in v.items() if not k.startswith('_')}
            if isinstance(v, (list, tuple)):
                return [clean_value(x) for x in v]
            return str(v)

        if old_data:
            old_data = {k: clean_value(v) for k, v in old_data.items() if not k.startswith('_')}
        if new_data:
            new_data = {k: clean_value(v) for k, v in new_data.items() if not k.startswith('_')}

        changes = []
        if action == "update_settings" and old_data and new_data:
            # Compare specific fields
            fields_to_compare = [
                'points','late_bonus','remote_days','core_users','enable_streaks',
                'streak_multiplier','enable_tiebreakers','tiebreaker_points',
                'tiebreaker_expiry','auto_resolve_tiebreakers','tiebreaker_weekly',
                'tiebreaker_monthly'
            ]
            for field in fields_to_compare:
                old_val = old_data.get(field)
                new_val = new_data.get(field)
                if old_val != new_val:
                    changes.append({
                        "field": field,
                        "old": old_val,
                        "new": new_val,
                        "type": "modified"
                    })
        elif action == "delete_entry" and old_data:
            changes = [{
                "field": k, "old": v, "new": "None", "type": "deleted"
            } for k, v in old_data.items()]
        elif action == "log_attendance" and new_data:
            changes = [{
                "field": k, "old": "None", "new": v, "type": "added"
            } for k, v in new_data.items()]
        elif old_data and new_data:
            # Generic modification
            all_keys = set(old_data.keys()) | set(new_data.keys())
            for key in all_keys:
                old_value = old_data.get(key, "None")
                new_value = new_data.get(key, "None")
                if old_value != new_value:
                    changes.append({
                        "field": key,
                        "old": old_value,
                        "new": new_value,
                        "type": "modified"
                    })

        # Only create an AuditLog if changes exist or if it's a non-modification action
        if changes or not (old_data and new_data):
            audit_entry = AuditLog(
                user=user,
                action=action,
                details=details,
                changes=changes
            )
            db.add(audit_entry)
            db.commit()
    except Exception as e:
        db.rollback()
        logging.error(f"Error logging audit: {str(e)}")
        raise
    finally:
        db.close()

# -------------
# ROUTES
# -------------

@bp.route("/")
@login_required
def index():
    return render_template("index.html", core_users=get_core_users())

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        if username and password and verify_user(username, password):
            session['user'] = username
            log_audit("login", username, "Successful login")
            return redirect(url_for('bp.index'))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@bp.route("/logout")
def logout():
    if 'user' in session:
        log_audit("logout", session['user'], "User logged out")
        session.pop('user', None)
    return redirect(url_for('bp.login'))  # Fix: use bp.login

@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        if not username or not password:
            return render_template("error.html", 
                                error="Username and password are required",
                                back_link=url_for('register'))
        
        db = SessionLocal()
        try:
            existing_user = db.query(User).filter_by(username=username).first()
            if (existing_user):
                return render_template("error.html", 
                                    error="Username already exists",
                                    back_link=url_for('register'))
            
            user = User(username=username, password=password)
            db.add(user)
            db.commit()
            
            session['user'] = username
            log_audit("register", username, "New user registration")
            return redirect(url_for('index'))
        
        except Exception as e:
            db.rollback()
            app.logger.error(f"Error registering user: {str(e)}")
            return render_template("error.html", 
                                error="Registration system error",
                                details=str(e),
                                back_link=url_for('register'))
        finally:
            db.close()
    return render_template("register.html")

@bp.route("/check_attendance")
def check_attendance():
    db = SessionLocal()
    try:
        # Check if current day is a weekday (0-4 = Monday-Friday)
        if datetime.now().weekday() >= 5:  # Weekend
            return jsonify([])
            
        today = datetime.now().date().isoformat()
        today_entries = db.query(Entry).filter_by(date=today).all()
        present_users = [entry.name for entry in today_entries]
        missing_users = [user for user in get_core_users() if user not in present_users]
        return jsonify(missing_users)
    finally:
        db.close()

@bp.route("/today-entries")
@login_required
def get_today_entries():
    db = SessionLocal()
    try:
        today = datetime.now().date().isoformat()
        entries = db.query(Entry).filter_by(date=today).all()
        return jsonify([{
            "id": e.id,
            "date": e.date,
            "time": e.time,
            "name": e.name,
            "status": e.status
        } for e in entries])
    finally:
        db.close()

@bp.route("/log", methods=["POST"])
@login_required
def log_attendance():
    db = SessionLocal()
    try:
        # Check for existing entry
        existing = db.query(Entry).filter_by(
            date=request.json["date"],
            name=request.json["name"]
        ).first()
        
        if existing:
            return jsonify({
                "message": "Error: Already logged attendance for this person today.",
                "type": "error"
            }), 400
        
        entry = Entry(
            id=str(uuid.uuid4()),
            date=request.json["date"],
            name=request.json["name"],
            status=request.json["status"],
            time=request.json["time"]
        )
        
        db.add(entry)
        db.commit()
        
        log_audit(
            "log_attendance",
            session['user'],
            f"Logged attendance for {entry.name}",
            new_data={
                "date": entry.date,
                "name": entry.name,
                "status": entry.status,
                "time": entry.time
            }
        )
        
        if entry.status in ['in-office', 'remote']:
            settings = db.query(Settings).first()
            if settings and settings.enable_streaks:
                # Streak updates are now handled by monitoring container
                pass
        
        return jsonify({
            "message": "Attendance logged successfully.",
            "type": "success"
        }), 200
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error logging attendance: {str(e)}")
        return jsonify({
            "message": f"Error logging attendance: {str(e)}",
            "type": "error"
        }), 500
    finally:
        db.close()

# -------------
# SETTINGS
# -------------

@HashableCacheWithMetrics
def load_settings():
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings:
            init_settings()
            settings = db.query(Settings).first()
        result = {
            "points": settings.points,
            "late_bonus": settings.late_bonus,
            "remote_days": settings.remote_days,
            "core_users": settings.core_users,
            "enable_streaks": settings.enable_streaks,
            "streak_multiplier": settings.streak_multiplier,
            "streaks_enabled": settings.streaks_enabled,
            "streak_bonus": settings.streak_bonus,
            "rules": settings.points.get('rules', []),  # Add rules to response
            "enable_tiebreakers": settings.enable_tiebreakers,
            "tiebreaker_points": settings.tiebreaker_points,
            "tiebreaker_expiry": settings.tiebreaker_expiry,
            "auto_resolve_tiebreakers": settings.auto_resolve_tiebreakers,
            "tiebreaker_weekly": settings.tiebreaker_weekly,  # Add this
            "tiebreaker_monthly": settings.tiebreaker_monthly,  # Add this
        }
        return result
    finally:
        db.close()

# Add cache invalidation on settings update
def save_settings(settings_data):
    """Update settings with cache invalidation"""
    load_settings.cache_clear()  # Clear the cached settings
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if settings:
            # Save daily shifts properly
            points_data = settings_data.get("points", {})
            if isinstance(points_data, dict):
                # Extract daily shifts from form data
                daily_shifts = {}
                for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
                    daily_shifts[day] = {
                        "hours": float(points_data.get(f"daily_shifts.{day}.hours", 9)),
                        "start": points_data.get(f"daily_shifts.{day}.start", "09:00")
                    }
                points_data["daily_shifts"] = daily_shifts
                
                # Preserve existing rules if not being updated
                if 'rules' not in points_data and hasattr(settings, 'points'):
                    points_data['rules'] = settings.points.get('rules', [])
            
            settings.points = points_data
            settings.late_bonus = settings_data["late_bonus"]
            settings.remote_days = settings_data["remote_days"]
            settings.core_users = settings_data.get("core_users", [])
            settings.enable_streaks = settings_data.get("enable_streaks", False)
            settings.streak_multiplier = settings_data.get("streak_multiplier", 0.5)
            # Add explicit tie breaker settings including timing
            settings.enable_tiebreakers = settings_data.get("enable_tiebreakers", False)
            settings.tiebreaker_points = settings_data.get("tiebreaker_points", 5)
            settings.tiebreaker_expiry = settings_data.get("tiebreaker_expiry", 24)
            settings.auto_resolve_tiebreakers = settings_data.get("auto_resolve_tiebreakers", False)
            settings.tiebreaker_weekly = settings_data.get("tiebreaker_weekly", True)  # Add this
            settings.tiebreaker_monthly = settings_data.get("tiebreaker_monthly", True)  # Add this
            
        else:
            settings = Settings(**settings_data)
            db.add(settings)
        db.commit()
    finally:
        db.close()

@bp.route("/settings", methods=["GET", "POST"])
@login_required
def manage_settings():
    db = SessionLocal()
    try:
        if request.method == "GET":
            # Clear any stale cache before loading
            load_settings.cache_clear()
            settings_data = load_settings()
            
            if not settings_data.get('monitoring_start_date'):
                settings_data['monitoring_start_date'] = datetime.now().replace(month=1, day=1).date()
            
            registered_users = [user[0] for user in db.query(User.username).all()]
            core_users = settings_data.get("core_users", [])
            
            # Ensure tie breaker generation settings are present
            settings_data.setdefault('tiebreaker_weekly', True)
            settings_data.setdefault('tiebreaker_monthly', True)
            
            return render_template(
                "settings.html",
                settings=settings_data,
                settings_data=settings_data,
                registered_users=registered_users,
                core_users=core_users,
                rules=settings_data.get("points", {}).get("rules", [])
            )
        else:
            try:
                # Clear the settings cache immediately
                load_settings.cache_clear()
                
                # Get current settings for comparison
                old_settings = db.query(Settings).first()
                old_settings_dict = {
                    "points": old_settings.points,
                    "late_bonus": old_settings.late_bonus,
                    "remote_days": old_settings.remote_days,
                    "core_users": old_settings.core_users,
                    "enable_streaks": old_settings.enable_streaks,
                    "streak_multiplier": old_settings.streak_multiplier,
                    "enable_tiebreakers": old_settings.enable_tiebreakers,
                    "tiebreaker_points": old_settings.tiebreaker_points,
                    "tiebreaker_expiry": old_settings.tiebreaker_expiry,
                    "auto_resolve_tiebreakers": old_settings.auto_resolve_tiebreakers,
                    "tiebreaker_weekly": old_settings.tiebreaker_weekly,
                    "tiebreaker_monthly": old_settings.tiebreaker_monthly
                }

                # Normalize new settings
                new_settings = request.json
                app.logger.debug(f"Received settings: {new_settings}")
                normalized_settings = normalize_settings(new_settings)
                app.logger.debug(f"Normalized settings: {normalized_settings}")

                if old_settings:
                    # Update existing settings, explicitly setting each field
                    for key, value in normalized_settings.items():
                        setattr(old_settings, key, value)
                else:
                    # Create new settings
                    old_settings = Settings(**normalized_settings)
                    db.add(old_settings)

                # Log the changes before commit
                log_audit(
                    "update_settings",
                    session['user'],
                    "Updated settings",
                    old_data=old_settings_dict,
                    new_data=normalized_settings
                )

                db.commit()
                # Clear cache again after commit to ensure fresh data on next load
                load_settings.cache_clear()

                return jsonify({"message": "Settings updated successfully"})
                
            except Exception as e:
                db.rollback()
                app.logger.error(f"Error managing settings: {str(e)}")
                return jsonify({"error": str(e)}), 500
    finally:
        db.close()

# -------------
# AUDIT
# -------------

@bp.route("/audit")
@login_required
def view_audit():
    db = SessionLocal()
    try:
        app.logger.info("Fetching audit logs...")
        
        # Get filter parameters with new per_page parameter
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        # Ensure per_page is within limits
        per_page = min(max(per_page, 50), 500)
        
        action_filter = request.args.get('action', 'all')
        user_filter = request.args.get('user', 'all')
        date_from = request.args.get('from')
        date_to = request.args.get('to')
        
        # Build query
        query = db.query(AuditLog)
        
        # Apply filters
        if action_filter != 'all':
            query = query.filter(AuditLog.action == action_filter)
        if user_filter != 'all':
            query = query.filter(AuditLog.user == user_filter)
        if date_from:
            query = query.filter(AuditLog.timestamp >= date_from)
        if date_to:
            query = query.filter(AuditLog.timestamp <= date_to + " 23:59:59")
            
        # Get total count for pagination
        total_entries = query.count()
        total_pages = (total_entries + per_page - 1) // per_page
        
        # Get paginated results
        audit_entries = query.order_by(AuditLog.timestamp.desc())\
                           .offset((page - 1) * per_page)\
                           .limit(per_page)\
                           .all()
        
        # Get unique users and actions for filters
        unique_users = db.query(AuditLog.user).distinct().all()
        unique_actions = db.query(AuditLog.action).distinct().all()
        
        entries = []
        for entry in audit_entries:
            audit_data = {
                "timestamp": entry.timestamp.isoformat(),
                "user": entry.user,
                "action": entry.action,
                "details": entry.details,
                "changes": entry.changes if entry.changes else []
            }
            entries.append(audit_data)
            
        return render_template("audit.html",
                             entries=entries,
                             current_page=page,
                             total_pages=total_pages,
                             users=sorted([u[0] for u in unique_users]),
                             actions=sorted([a[0] for a in unique_actions]),
                             selected_action=action_filter,
                             selected_user=user_filter,
                             date_from=date_from,
                             date_to=date_to)
    except Exception as e:
        app.logger.error(f"Error viewing audit log: {str(e)}")
        return render_template("error.html", message="Failed to load audit trail")
    finally:
        db.close()

# -------------
# RANKINGS
# -------------

@bp.route("/rankings/<period>")
@bp.route("/rankings/<period>/<date_str>")
@login_required
@track_response_time('rankings')
def view_rankings(period, date_str=None):
    RANKING_CALLS.inc()
    db = SessionLocal()
    try:
        mode = request.args.get('mode', 'last-in')
        app.logger.debug(f"Rankings request - Period: {period}, Date: {date_str}, Mode: {mode}")
        
        # Get current date (either from URL or today)
        try:
            if (date_str):
                current_date = datetime.strptime(date_str, '%Y-%m-%d')
            else:
                current_date = datetime.now()
            
            # For weekly view, always snap to Monday
            if (period == 'week'):
                current_date = current_date - timedelta(days=current_date.weekday())
            
            # Calculate period end date
            if (period == 'week'):
                period_end = current_date + timedelta(days=6)
            elif (period == 'month'):
                next_month = current_date.replace(day=28) + timedelta(days=4)
                period_end = next_month - timedelta(days=next_month.day)
            else:
                period_end = current_date
            
            with rankings_lock:
                # Ensure thread-safe access to data
                data = load_data()
                if not data:
                    app.logger.warning("No data found for rankings")
                    return render_template("rankings.html", 
                                        rankings=[],
                                        period=period,
                                        current_date=current_date.strftime('%Y-%m-%d'),
                                        current_display="No data available",
                                        current_month_value=current_date.strftime('%Y-%m'),
                                        mode=mode,
                                        streaks_enabled=False)
                
                rankings = calculate_scores(data, period, current_date)
                
                # Process each ranking entry to add time data
                for rank in rankings:
                    user_entries = [e for e in data if e["name"] == rank["name"] and 
                                in_period(e, period, current_date) and
                                normalize_status(e["status"]) in ["in_office", "remote"]]
                    
                    # Set default values first
                    rank.update({
                        "time": "N/A",
                        "time_obj": datetime.strptime("09:00", "%H:%M"),
                        "shift_length": 540,
                        "end_time": "18:00",
                        "current_streak": 0,
                        "max_streak": 0
                    })
                    
                    if user_entries:
                        # Calculate average time from entries
                        times = [datetime.strptime(e["time"], "%H:%M") for e in user_entries]
                        avg_time = sum((t.hour * 60 + t.minute) for t in times) // len(times)
                        avg_hour = avg_time // 60
                        avg_minute = avg_time % 60
                        
                        rank["time"] = f"{avg_hour:02d}:{avg_minute:02d}"
                        rank["time_obj"] = datetime.strptime(rank["time"], "%H:%M")
                        end_time = rank["time_obj"] + timedelta(minutes=rank["shift_length"])
                        rank["end_time"] = end_time.strftime('%H:%M')
                    
                    # Add streak information
                    streak = db.query(UserStreak).filter_by(username=rank["name"]).first()
                    if streak:
                        rank["current_streak"] = streak.current_streak
                        rank["max_streak"] = streak.max_streak
                
                settings = load_settings()
                
                # Calculate earliest and latest hours from actual data
                all_times = []
                for rank in rankings:
                    if rank.get('time') and rank['time'] != 'N/A':
                        time_obj = datetime.strptime(rank['time'], '%H:%M')
                        all_times.append(time_obj)
                        if rank.get('end_time') and rank['end_time'] != 'N/A':
                            end_obj = datetime.strptime(rank['end_time'], '%H:%M')
                            all_times.append(end_obj)

                earliest_hour = 7  # Default earliest
                latest_hour = 19  # Default latest
                
                if all_times:
                    earliest_time = min(all_times)
                    latest_time = max(all_times)
                    # Round down/up to nearest hour
                    earliest_hour = max(7, earliest_time.hour)  # Don't go earlier than 7am
                    latest_hour = min(19, latest_time.hour + 1)  # Don't go later than 7pm
                
                template_data = {
                    'rankings': rankings,
                    'period': period,
                    'current_date': current_date.strftime('%Y-%m-%d'),
                    'current_display': format_date_range(current_date, period_end, period),
                    'current_month_value': current_date.strftime('%Y-%m'),
                    'mode': mode,
                    'streaks_enabled': settings.get("enable_streaks", False),
                    'earliest_hour': earliest_hour,
                    'latest_hour': latest_hour
                }
                
                app.logger.debug(f"Rankings template data: {template_data}")
                return render_template("rankings.html", **template_data)
            
        except ValueError as e:
            app.logger.error(f"Date parsing error: {str(e)}")
            return render_template("error.html", 
                                error=f"Invalid date format: {str(e)}")
            
    except Exception as e:
        app.logger.error(f"Rankings error: {str(e)}")
        return render_template("error.html", 
                            error=f"Failed to load rankings: {str(e)}")
    finally:
        db.close()

@bp.route("/rankings/today")
@login_required
def daily_rankings():
    data = load_data()
    settings = load_settings()
    today = datetime.now().date().isoformat()
    
    today_entries = [e for e in data if e["date"] == today]
    today_entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
    
    rankings = []
    total_entries = len(today_entries)
    for position, entry in enumerate(today_entries, 1):
        scores = calculate_daily_score(entry, settings, position, total_entries)
        # Fix: Use the correct score based on mode
        mode = request.args.get('mode', 'last-in')
        points = scores["last_in"] if mode == 'last-in' else scores["early_bird"]
        
        rankings.append({
            "name": entry["name"],
            "time": entry["time"],
            "status": entry["status"],
            "points": points  # Now points is a number, not a dict
        })
    
    # Sort by points descending
    rankings.sort(key=lambda x: x["points"], reverse=True)
    return jsonify(rankings)

# -------------
# TIE-BREAKERS
# -------------

@bp.route("/tie-breakers")
@login_required
def tie_breakers():
    db = SessionLocal()
    try:
        mode = request.args.get('mode', 'last-in')
        show_completed = request.args.get('show_completed', 'true').lower() == 'true'

        base_query = """
            WITH tie_breakers_cte AS (
                SELECT 
                    t.id,
                    t.period,
                    t.period_start,
                    t.period_end,
                    t.points::float as points,
                    t.mode,
                    t.status,
                    t.created_at,
                    t.resolved_at,
                    jsonb_agg(DISTINCT jsonb_build_object(
                        'username', tp.username,
                        'game_choice', tp.game_choice,
                        'ready', tp.ready,
                        'winner', tp.winner
                    )) FILTER (WHERE tp.username IS NOT NULL) as participants,
                    jsonb_agg(DISTINCT jsonb_build_object(
                        'id', g.id,
                        'game_type', g.game_type,
                        'player1', g.player1,
                        'player2', g.player2,
                        'status', g.status,
                        'game_state', g.game_state,
                        'final_tiebreaker', g.final_tiebreaker
                    )) FILTER (WHERE g.id IS NOT NULL) as games
                FROM tie_breakers t
                LEFT JOIN tie_breaker_participants tp ON t.id = tp.tie_breaker_id
                LEFT JOIN tie_breaker_games g ON t.id = g.tie_breaker_id
                WHERE t.mode = :mode
                GROUP BY t.id, t.period, t.period_start, t.period_end, t.points,
                         t.mode, t.status, t.created_at, t.resolved_at
            )
            SELECT * FROM tie_breakers_cte
            ORDER BY 
                CASE status 
                    WHEN 'in_progress' THEN 1
                    WHEN 'pending' THEN 2
                    WHEN 'completed' THEN 3
                    ELSE 4
                END,
                created_at DESC,
                period_end DESC
        """

        tie_breakers = db.execute(text(base_query), {
            "mode": mode
        }).fetchall()

        formatted_tie_breakers = []
        for tb in tie_breakers:
            if not show_completed and tb.status == 'completed':
                continue

            tie_breaker_dict = {
                k: decimal_to_float(v) if isinstance(v, Decimal) else v
                for k, v in dict(tb).items()
            }
            
            # Parse JSON fields
            for field in ['games', 'participants']:
                if tie_breaker_dict[field] is None:
                    tie_breaker_dict[field] = []
                elif isinstance(tie_breaker_dict[field], str):
                    tie_breaker_dict[field] = json.loads(tie_breaker_dict[field])
            
            formatted_tie_breakers.append(tie_breaker_dict)

        return render_template(
            "tie_breakers.html",
            tie_breakers=formatted_tie_breakers,
            current_user=session['user'],
            mode=mode,
            show_completed=show_completed
        )
        
    except Exception as e:
        app.logger.error(f"Error fetching tie breakers: {str(e)}")
        return render_template("error.html", 
                             error="Failed to load tie breakers",
                             details=str(e),
                             back_link=url_for('index'))
    finally:
        db.close()

# ...existing code...

@bp.route("/tie-breaker/<int:tie_id>/choose-game", methods=["POST"])
@login_required
def choose_game(tie_id):
    # Renamed from /tie-breakers/ to /tie-breaker/ to fix routing issue
    db = SessionLocal()
    try:
        game_choice = request.form.get('game_choice')
        if game_choice not in ['tictactoe', 'connect4']:
            return jsonify({"error": "Invalid game choice"}), 400

        # Update participant's choice and ready status
        db.execute(text("""
            UPDATE tie_breaker_participants
            SET game_choice = :choice, ready = true
            WHERE tie_breaker_id = :tie_id
            AND username = :username
        """), {
            "choice": game_choice,
            "tie_id": tie_id,
            "username": session['user']
        })

        # Check if all participants are ready
        all_ready = db.execute(text("""
            SELECT bool_and(ready) 
            FROM tie_breaker_participants
            WHERE tie_breaker_id = :tie_id
        """), {"tie_id": tie_id}).scalar()

        if all_ready:
            # Create initial games
            create_next_game(db, tie_id)
            
            # Update tie breaker status to in_progress only after games are created
            db.execute(text("""
                UPDATE tie_breakers
                SET status = 'in_progress'
                WHERE id = :tie_id
                AND status = 'pending'
            """), {"tie_id": tie_id})

        db.commit()
        return redirect(url_for('tie_breakers'))
    finally:
        db.close()

# -------------
# CHATBOT
# -------------

@bp.route("/chatbot", methods=["POST"])
@login_required
def chatbot():
    try:
        message = request.json.get("message", "").strip()
        if not message:
            return jsonify({"response": "Please ask me a question!"})
            
        user_id = session.get('user')
        if not user_id:
            return jsonify({"response": "Please log in to use the chatbot."})
        
        # Use the EnhancedQueryProcessor for better NLP handling
        processor = EnhancedQueryProcessor()
        result = processor.process_query(message, user_id)
        
        return jsonify({
            "response": result['response'],
            "suggestions": result['suggestions'],
            "context": result['context']
        })
            
    except Exception as e:
        app.logger.error(f"Chatbot error: {str(e)}")
        return jsonify({
            "response": "Sorry, something went wrong. Please try again.",
            "suggestions": ["Try asking something else", "Check the current status"],
            "context": None
        })

# -------------
# MAINTENANCE
# -------------

@bp.route("/maintenance")
@login_required
def maintenance():
    db = SessionLocal()
    try:
        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        # Ensure per_page is within limits
        per_page = min(max(per_page, 50), 500)

        # Calculate offset
        offset = (page - 1) * per_page
        
        # Fetch monitoring logs with pagination
        monitoring_logs = db.execute(
            text("""
                SELECT 
                    timestamp,
                    event_type,
                    details,
                    status
                FROM monitoring_logs
                ORDER BY timestamp DESC
                LIMIT :limit OFFSET :offset
            """),
            {
                "limit": per_page,
                "offset": offset
            }
        ).fetchall()

        # Get total count for pagination
        total_logs = db.scalar(text("SELECT COUNT(*) FROM monitoring_logs"))
        total_pages = (total_logs + per_page - 1) // per_page
        
        # Get core users for test data selection
        settings = db.query(Settings).first()
        core_users = settings.core_users if settings else []
        
        return render_template(
            "maintenance.html",
            monitoring_logs=monitoring_logs,
            current_page=page,
            total_pages=total_pages,
            per_page=per_page,
            core_users=core_users  # Pass core users to template
        )
    finally:
        db.close()

@bp.route("/maintenance/reset-tiebreakers", methods=["POST"])
@login_required
def reset_tiebreakers():
    db = SessionLocal()
    try:
        # Log the action first
        log_audit(
            "reset_tiebreakers",
            session['user'],
            "Manual reset of tie breakers"
        )
        
        # Delete all tie breakers (cascades to games and participants)
        db.execute(text("DELETE FROM tie_breakers"))
        db.commit()
        
        return jsonify({
            "message": "Tie breakers reset successfully",
            "type": "success"
        })
    except Exception as e:
        db.rollback()
        return jsonify({
            "message": f"Error resetting tie breakers: {str(e)}",
            "type": "error"
        }), 500
    finally:
        db.close()

@bp.route("/maintenance/reset-streaks", methods=["POST"])
@login_required
def reset_streaks():
    db = SessionLocal()
    try:
        # Log the action first
        log_audit(
            "reset_streaks",
            session['user'],
            "Manual reset of streaks"
        )
        
        # Delete all streak data
        db.execute(text("DELETE FROM user_streaks"))
        db.commit()
        
        return jsonify({
            "message": "Streaks reset successfully",
            "type": "success"
        })
    except Exception as e:
        db.rollback()
        return jsonify({
            "message": f"Error resetting streaks: {str(e)}",
            "type": "error"
        }), 500
    finally:
        db.close()

@bp.route("/maintenance/reset-tiebreaker-effects", methods=["POST"])
@login_required
def reset_tiebreaker_effects():
    db = SessionLocal()
    try:
        # Log the action first
        log_audit(
            "reset_tiebreaker_effects",
            session['user'],
            "Manual reset of tie breaker effects"
        )
        
        # Update all completed tie breakers
        db.execute(text("""
            UPDATE tie_breakers 
            SET status = 'pending',
                points_applied = false,
                resolved_at = NULL
            WHERE status = 'completed'
        """))
        
        # Reset all participants
        db.execute(text("""
            UPDATE tie_breaker_participants
            SET winner = NULL,
                ready = false,
                game_choice = NULL
            WHERE tie_breaker_id IN (
                SELECT id FROM tie_breakers
                WHERE status = 'pending'
            )
        """))
        
        # Delete all games for reset tie breakers
        db.execute(text("""
            DELETE FROM tie_breaker_games
            WHERE tie_breaker_id IN (
                SELECT id FROM tie_breakers
                WHERE status = 'pending'
            )
        """))
        
        # Force rankings refresh
        db.execute(text("REFRESH MATERIALIZED VIEW rankings"))
        
        db.commit()
        
        return jsonify({
            "message": "Tie breaker effects reset successfully. Rankings have been recalculated.",
            "type": "success"
        })
    except Exception as e:
        db.rollback()
        return jsonify({
            "message": f"Error resetting tie breaker effects: {str(e)}",
            "type": "error"
        }), 500
    finally:
        db.close()

@bp.route("/maintenance/seed-test-data", methods=["POST"])
@login_required
def seed_test_data():
    db = SessionLocal()
    try:
        app.logger.info("Starting test data seeding...")

        # Get selected users from request, fallback to first two core users
        selected_users = request.json.get('users', []) if request.is_json else []
        settings = db.query(Settings).first()
        core_users = settings.core_users if settings else ["Matt", "Nathan"]
        
        # Use selected users if valid, otherwise use first two core users
        test_users = []
        if len(selected_users) >= 2:
            # Validate selected users are core users
            valid_users = [u for u in selected_users if u in core_users]
            if len(valid_users) >= 2:
                test_users = valid_users[:2]
            
        if not test_users:
            test_users = core_users[:2]
            
        app.logger.info(f"Using users for test: {test_users}")

        created_ties = []

        # Create weekly tie breaker for last week
        last_week = datetime.now() - timedelta(days=7)
        week_end = last_week + timedelta(days=(6 - last_week.weekday()))  # Move to Sunday
        app.logger.info(f"Creating weekly tie breaker ending {week_end}")
        
        # Create test entries for the period to ensure valid tie breaker
        for user in test_users:
            # Add a test entry for each user
            entry = Entry(
                id=str(uuid.uuid4()),
                date=last_week.strftime('%Y-%m-%d'),
                time='09:00',
                name=user,
                status='in-office'
            )
            db.add(entry)
        
        tie_id = create_test_tie_breaker(db, 'weekly', week_end, 10.0, 'last-in', test_users)
        if tie_id:
            app.logger.info(f"Created weekly tie breaker with ID: {tie_id}")
            created_ties.append(tie_id)
            create_test_games(db, tie_id, test_users)

        # Create monthly tie breaker
        month_end = datetime.now().replace(day=1) - timedelta(days=1)
        app.logger.info(f"Creating monthly tie breaker ending {month_end}")
        tie_id = create_test_tie_breaker(db, 'monthly', month_end, 15.0, 'early-bird', test_users)
        if tie_id:
            app.logger.info(f"Created monthly tie breaker with ID: {tie_id}")
            created_ties.append(tie_id)
            create_test_games(db, tie_id, test_users)

        db.commit()
        
        app.logger.info(f"Successfully created {len(created_ties)} test tie breakers")
        
        return jsonify({
            "message": f"Created {len(created_ties)} test tie breakers",
            "type": "success",
            "tie_ids": created_ties
        })

    except Exception as e:
        db.rollback()
        app.logger.error(f"Error seeding test data: {str(e)}", exc_info=True)
        return jsonify({
            "message": f"Error seeding test data: {str(e)}",
            "type": "error"
        }), 500
    finally:
        db.close()

# -------------
# ETC.
# -------------

@bp.route("/health")
def health_check():
    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        settings = load_settings()
        metrics = {
            "database": "healthy",
            "settings": "loaded" if settings else "missing",
            "cache_stats": {
                "settings": load_settings.cache_info(),
                "scores": calculate_daily_score.cache_info()
            }
        }
        return jsonify({"status": "healthy", "metrics": metrics})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500
    finally:
        db.close()

@bp.route("/export-data")
@login_required
def export_data():
    db = SessionLocal()
    try:
        # Export all tables
        entries = db.query(Entry).all()
        settings = db.query(Settings).first()
        audit_logs = db.query(AuditLog).all()

        data = {
            "entries": [{
                "id": e.id,
                "date": e.date,
                "time": e.time,
                "name": e.name,
                "status": e.status,
                "timestamp": e.timestamp.isoformat()
            } for e in entries],
            "settings": {
                "points": settings.points,
                "late_bonus": settings.late_bonus,
                "remote_days": settings.remote_days
            } if settings else None,
            "audit_logs": [{
                "timestamp": log.timestamp.isoformat(),
                "user": log.user,
                "action": log.action,
                "details": log.details,
                "changes": log.changes
            } for log in audit_logs]
        }
        
        return jsonify(data)
    finally:
        db.close()

@bp.route("/import-data", methods=["POST"])
@login_required
def import_data():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    data = request.json
    db = SessionLocal()
    try:
        # Clear existing data
        db.query(Entry).delete()
        db.query(Settings).delete()
        db.query(AuditLog).delete()

        # Import entries
        for entry_data in data.get("entries", []):
            entry = Entry(
                id=entry_data["id"],
                date=entry_data["date"],
                time=entry_data["time"],
                name=entry_data["name"],
                status=entry_data["status"],
                timestamp=datetime.fromisoformat(entry_data["timestamp"])
            )
            db.add(entry)

        # Import settings
        if data.get("settings"):
            settings = Settings(
                points=data["settings"]["points"],
                late_bonus=data["settings"]["late_bonus"],
                remote_days=data["settings"]["remote_days"]
            )
            db.add(settings)

        # Import audit logs
        for log_data in data.get("audit_logs", []):
            log = AuditLog(
                timestamp=datetime.fromisoformat(log_data["timestamp"]),
                user=log_data["user"],
                action=log_data["action"],
                details=log_data["details"],
                changes=log_data["changes"]
            )
            db.add(log)

        db.commit()
        return jsonify({"message": "Data imported successfully"})
    
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@bp.route("/clear-database", methods=["POST"])
@login_required
def clear_database():
    db = SessionLocal()
    try:
        # Clear all tables
        db.query(Entry).delete()
        db.query(Settings).delete()
        db.query(AuditLog).delete()
        db.commit()

        log_audit(
            "clear_database",
            session['user'],
            "Cleared all database tables",
            old_data={"action": "database_clear"},
            new_data={"action": "database_clear"}
        )
        
        return jsonify({"message": "Database cleared successfully"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@bp.route("/games/<int:game_id>/move", methods=["POST"])
@login_required
def make_move(game_id):
    db = SessionLocal()
    try:
        # Get request data with better error handling
        try:
            data = request.get_json()
            if not data or 'move' not in data:
                return jsonify({
                    "success": False,
                    "message": "Missing move data"
                }), 400
            move = int(data.get('move'))
        except (ValueError, TypeError) as e:
            return jsonify({
                "success": False,
                "message": f"Invalid move data: {str(e)}"
            }), 400

        # Get game with explicit locking and validation
        game = db.execute(text("""
            SELECT g.*, t.status as tie_breaker_status 
            FROM tie_breaker_games g
            JOIN tie_breakers t ON g.tie_breaker_id = t.id
            WHERE g.id = :game_id
            FOR UPDATE
        """), {"game_id": game_id}).fetchone()

        if not game:
            return jsonify({
                "success": False,
                "message": "Game not found"
            }), 404

        # Ensure game state is valid JSON
        try:
            game_state = game.game_state
            if not isinstance(game_state, dict):
                return jsonify({
                    "success": False,
                    "message": "Invalid game state"
                }), 400
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Error parsing game state: {str(e)}"
            }), 400

        # Validate game status
        if game.status != 'active':
            return jsonify({
                "success": False,
                "message": f"Game is not active (status: {game.status})"
            }), 400

        # Verify it's the player's turn
        current_user = session.get('user')
        if current_user != game_state.get('current_player'):
            return jsonify({
                "success": False,
                "message": "Not your turn"
            }), 400

        # Validate move
        board = game_state.get('board', [])
        if not is_valid_move({"board": board, "game_type": game.game_type}, move):
            return jsonify({
                "success": False,
                "message": "Invalid move position"
            }), 400

        # Apply move
        try:
            updated_state = apply_move(game_state, move, current_user)
        except ValueError as e:
            return jsonify({
                "success": False,
                "message": str(e)
            }), 400

        # Check for winner
        winner = check_winner(updated_state, game.game_type)
        
        # Update game state
        db.execute(text("""
            UPDATE tie_breaker_games 
            SET game_state = :game_state,
                status = CASE WHEN :winner IS NOT NULL THEN 'completed' ELSE status END,
                winner = :winner,
                completed_at = CASE WHEN :winner IS NOT NULL THEN NOW() ELSE completed_at END
            WHERE id = :game_id
        """), {
            "game_id": game_id,
            "game_state": json.dumps(updated_state),
            "winner": winner if winner and winner != 'draw' else None
        })

        # Handle game completion
        if winner:
            if winner == 'draw':
                create_next_game_after_draw(
                    db, 
                    game.tie_breaker_id,
                    game.game_type,
                    game.player1,
                    game.player2
                )
            else:
                check_tie_breaker_completion(db, game.tie_breaker_id)

        db.commit()

        # Prepare response
        response = {
            "success": True,
            "state": updated_state,
            "winner": winner,
            "gameStatus": 'completed' if winner else 'active'
        }

        # Notify other players
        socketio.emit('game_update', response, room=f"game_{game_id}")

        return jsonify(response)

    except Exception as e:
        db.rollback()
        app.logger.error(f"Error making move: {str(e)}", exc_info=True)
        return jsonify({
            "success": False,
            "message": f"Server error: {str(e)}"
        }), 500
    finally:
        db.close()

@bp.route('/games/<int:game_id>')
@login_required
def play_game(game_id):
    db = SessionLocal()
    try:
        # Get game details with proper type info
        game = db.execute(text("""
            SELECT g.*, tb.status as tie_breaker_status
            FROM tie_breaker_games g
            JOIN tie_breakers tb ON g.tie_breaker_id = tb.id
            WHERE g.id = :game_id
        """), {"game_id": game_id}).fetchone()

        if not game:
            return redirect(url_for('tie_breakers'))

        # Check if user can play (is one of the players)
        current_user = session.get('user')
        can_play = current_user in [game.player1, game.player2]

        # Route to correct game template based on game type
        template_name = f"games/{game.game_type}.html"
        return render_template(
            template_name,
            game=game,
            can_play=can_play,
        )

    except Exception as e:
        app.logger.error(f"Error loading game: {str(e)}")
        return redirect(url_for('tie_breakers'))
    finally:
        db.close()

@bp.route('/games/<int:game_id>/join', methods=['POST'])
@login_required
def join_game(game_id):
    db = SessionLocal()
    try:
        # Get game with proper locking
        game = db.execute(text("""
            SELECT g.*, t.status as tie_breaker_status 
            FROM tie_breaker_games g
            JOIN tie_breakers t ON g.tie_breaker_id = t.id 
            WHERE g.id = :game_id
            FOR UPDATE
        """), {"game_id": game_id}).fetchone()

        if not game:
            app.logger.warning(f"Cannot join game {game_id}: Game not found")
            return jsonify({"error": "Game not found"}), 404

        current_user = session.get('user')
        if not current_user or current_user == game.player1:
            app.logger.warning(f"Cannot join game {game_id}: Invalid user")
            return jsonify({"error": "Invalid user"}), 400

        if game.status != 'pending':
            app.logger.warning(f"Cannot join game {game_id}: Game not in pending state")
            return jsonify({"error": "Game already started"}), 400

        if game.tie_breaker_status != 'in_progress':
            app.logger.warning(f"Cannot join game {game_id}: Tie breaker not in progress")
            return jsonify({"error": "Tie breaker not active"}), 400

        # Initialize game state
        board_size = 9 if game.game_type == 'tictactoe' else 42
        game_state = {
            'board': [None] * board_size,
            'moves': [],
            'current_player': game.player1,
            'player1': game.player1,
            'player2': current_user
        }

        # Update game
        db.execute(text("""
            UPDATE tie_breaker_games 
            SET player2 = :player2,
                status = 'active',
                game_state = :game_state
            WHERE id = :game_id
        """), {
            "player2": current_user,
            "game_state": json.dumps(game_state),
            "game_id": game_id
        })

        # Log the action
        log_audit(
            "join_game",
            current_user,
            f"Joined game {game_id}",
            new_data={"game_id": game_id, "game_type": game.game_type}
        )

        db.commit()

        # Redirect to game page
        return redirect(url_for('play_game', game_id=game_id))

    except Exception as e:
        db.rollback()
        app.logger.error(f"Error joining game: {str(e)}")
        return jsonify({"error": "Server error"}), 500
    finally:
        db.close()

@bp.route("/api/rules", methods=["GET", "POST"])
@login_required
def handle_rules():
    """Handle loading and saving scoring rules"""
    db = SessionLocal()
    try:
        if request.method == "GET":
            settings = db.query(Settings).first()
            return jsonify(settings.points.get("rules", []))
        
        new_rules = request.json.get("rules", [])
        settings = db.query(Settings).first()
        points = settings.points
        points["rules"] = new_rules
        settings.points = points
        db.commit()
        return jsonify({"status": "ok"})
    finally:
        db.close()

# ...existing code...

@bp.route("/rankings/day")
@bp.route("/rankings/day/<date>")
@login_required
def day_rankings(date=None):
    if date is None:
        date = datetime.now().date().isoformat()
    
    data = load_data()
    settings = load_settings()
    mode = request.args.get('mode', 'last-in')
    
    # Get entries for the specified date and sort by time
    day_entries = [e for e in data if e["date"] == date]
    day_entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
    
    # Get shift length based on the day
    weekday = datetime.strptime(date, '%Y-%m-%d').strftime('%A').lower()
    day_shift = settings["points"].get("daily_shifts", {}).get(weekday, {
        "hours": settings["points"].get("shift_length", 9),
        "start": "09:00"
    })
    
    shift_length_hours = float(day_shift["hours"])
    shift_length_minutes = int(shift_length_hours * 60)
    shift_start = datetime.strptime(day_shift["start"], "%H:%M")
    start_hour = shift_start.hour
    start_minute = shift_start.minute
    
    # Calculate points and prepare rankings
    rankings = []
    total_entries = len(day_entries)
    for position, entry in enumerate(day_entries, 1):
        scores = calculate_daily_score(entry, settings, position, total_entries, mode)
        entry_time = datetime.strptime(entry["time"], "%H:%M")
        entry_date = datetime.strptime(entry["date"], "%Y-%m-%d")
        
        # Use day-specific shift length
        shift_length = shift_length_minutes
        end_time = entry_time + timedelta(minutes=shift_length)
        
        rankings.append({
            "name": entry["name"],
            "time": entry["time"],
            "time_obj": entry_time,
            "shift_length": shift_length,
            "shift_hours": shift_length_hours,
            "end_time": end_time.strftime('%H:%M'),
            "status": entry["status"],
            "points": scores["last_in"] if mode == 'last-in' else scores["early_bird"]
        })
    
    # Sort by points descending
    rankings.sort(key=lambda x: x["points"], reverse=True)
    
    # Calculate earliest and latest hours from the actual data
    all_times = []
    for rank in rankings:
        if rank.get('time'):
            time_obj = datetime.strptime(rank['time'], "%H:%M")
            all_times.append(time_obj)
            if rank.get('end_time'):
                end_obj = datetime.strptime(rank['end_time'], "%H:%M")
                all_times.append(end_obj)

    earliest_hour = 7  # Default earliest
    latest_hour = 19  # Default latest
    
    if all_times:
        earliest_time = min(all_times)
        latest_time = max(all_times)
        earliest_hour = max(7, earliest_time.hour)  # Don't go earlier than 7am
        latest_hour = min(19, latest_time.hour + 1)  # Don't go later than 7pm

    return render_template("day_rankings.html", 
                         rankings=rankings,
                         date=date,
                         mode=mode,
                         start_hour=start_hour,
                         start_minute=start_minute,
                         earliest_hour=earliest_hour,
                         latest_hour=latest_hour)

# ...existing code...
