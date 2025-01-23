import secrets
from functools import wraps
import os
from werkzeug.utils import safe_join

def init_app(app):
    """Initialize Flask app with filters and context processors"""
    
    @app.template_filter('time_to_minutes')
    def time_to_minutes(time_str):
        """Convert time string (HH:MM) to minutes since midnight"""
        if not time_str:
            return 0
        try:
            hours, minutes = map(int, time_str.split(':'))
            return hours * 60 + minutes
        except (ValueError, AttributeError):
            return 0
    
    @app.template_filter('minutes_to_time')
    def minutes_to_time(minutes):
        """Convert minutes since midnight to HH:MM format"""
        if not minutes:
            return "00:00"
        try:
            hours = int(minutes) // 60
            mins = int(minutes) % 60
            return f"{hours:02d}:{mins:02d}"
        except (ValueError, TypeError):
            return "00:00"
    
    @app.template_filter('format_date')
    def format_date(value):
        """Format date for template display"""
        if isinstance(value, str):
            try:
                value = datetime.strptime(value, '%Y-%m-%d').date()
            except ValueError:
                return value
        return value.strftime('%d/%m/%Y') if value else ''

    @app.template_filter('format_time')
    def format_time(value):
        """Format time for template display"""
        if not value:
            return ''
        try:
            if isinstance(value, str):
                time = datetime.strptime(value, '%H:%M').time()
            else:
                time = value
            return time.strftime('%H:%M')
        except ValueError:
            return value

    @app.context_processor
    def utility_processor():
        """Make utility functions available to all templates"""
        def today():
            """Return today's date for template use"""
            return datetime.now().date()
            
        def get_setting(key, default=None):
            """Get a setting value with fallback"""
            settings = load_settings()
            return settings.get(key, default)
            
        return {
            'today': today,
            'get_setting': get_setting,
            'core_users': get_core_users()
        }

import json
import logging
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import Lock, Thread

from flask import Blueprint
from flask import \
    current_app as app  # Use current_app instead of direct import
from flask import jsonify, redirect, render_template, request, session, url_for, send_from_directory
from sqlalchemy import inspect, text

from .blueprints import \
    bp  # Import bp from blueprints instead of creating it here
from .caching import HashableCacheWithMetrics
from .chatbot import EnhancedQueryProcessor  # Add this line
from .data import (calculate_daily_score, calculate_scores, decimal_to_float,
                   load_data, get_settings)  # Add get_settings here
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
from .utils import init_settings, load_settings
from .visualisation import (calculate_arrival_patterns, calculate_average_time,
                            calculate_daily_activity, calculate_daily_score,
                            calculate_points_progression,
                            calculate_status_counts, calculate_user_comparison,
                            calculate_weekly_patterns, analyze_early_arrivals,
                            analyze_late_arrivals)
from .streaks import calculate_current_streak, get_streak_history, get_attendance_for_period, get_current_streak_info

# If you need to call methods from your main app or from 'app.py' directly, 
# you typically do that through current_app from flask, or separate your code further.

# Remove this line since we moved it to blueprints.py:
# bp = Blueprint('bp', __name__)

def register_template_filters(app):
    """Register custom template filters"""
    
    @app.template_filter('time_to_minutes')
    def time_to_minutes(time_str):
        """Convert time string (HH:MM) to minutes since midnight"""
        if not time_str:
            return 0
        try:
            hours, minutes = map(int, time_str.split(':'))
            return hours * 60 + minutes
        except (ValueError, AttributeError):
            return 0
    
    @app.template_filter('minutes_to_time')
    def minutes_to_time(minutes):
        """Convert minutes since midnight to HH:MM format"""
        if not minutes:
            return "00:00"
        try:
            hours = int(minutes) // 60
            mins = int(minutes) % 60
            return f"{hours:02d}:{mins:02d}"
        except (ValueError, TypeError):
            return "00:00"
    
    @app.template_filter('format_date')
    def format_date(value):
        """Format date for template display"""
        if isinstance(value, str):
            try:
                value = datetime.strptime(value, '%Y-%m-%d').date()
            except ValueError:
                return value
        return value.strftime('%d/%m/%Y') if value else ''

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
    # Get core users and calculate current streaks
    core_users = get_core_users()
    
    # Get CLI download info
    cli_dir = os.path.join(app.static_folder, 'cli')
    cli_downloads = []
    if os.path.exists(cli_dir):
        for filename in os.listdir(cli_dir):
            if filename.startswith("lic-cli"):
                if filename.endswith(".exe"):
                    platform = "windows"
                    display_platform = "Windows"
                elif "macos-arm64" in filename:
                    platform = "macos-arm64"
                    display_platform = "macOS"
                elif "macos-x64" in filename:
                    platform = "macos-x64"
                    display_platform = "macOS"
                else:
                    platform = "linux"
                    display_platform = "Linux"
                
                arch = "ARM64" if "arm64" in filename else "x64"
                cli_downloads.append({
                    "filename": filename,
                    "platform": display_platform,
                    "arch": arch,
                    "url": url_for('bp.download_cli', platform=platform),
                    "size": os.path.getsize(os.path.join(cli_dir, filename)) // 1024
                })
    
    # Return template with core users and CLI downloads
    return render_template("index.html", 
                         core_users=core_users,
                         cli_downloads=cli_downloads)

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
                                back_link=url_for('bp.register'))  # Fix: add bp. prefix
        
        db = SessionLocal()
        try:
            existing_user = db.query(User).filter_by(username=username).first()
            if (existing_user):
                return render_template("error.html", 
                                    error="Username already exists",
                                    back_link=url_for('bp.register'))  # Fix: add bp. prefix
            
            user = User(username=username, password=password)
            db.add(user)
            db.commit()
            
            session['user'] = username
            log_audit("register", username, "New user registration")
            return redirect(url_for('bp.index'))  # Fix: add bp. prefix
        
        except Exception as e:
            db.rollback()
            app.logger.error(f"Error registering user: {str(e)}")
            return render_template("error.html", 
                                error="Registration system error",
                                details=str(e),
                                back_link=url_for('bp.register'))  # Fix: add bp. prefix
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
            
            # Ensure working_days exists in points
            if 'points' not in settings_data:
                settings_data['points'] = {}
            if 'working_days' not in settings_data['points']:
                settings_data['points']['working_days'] = {}
            
            # Initialize default working days for users without settings
            core_users = settings_data.get("core_users", [])
            for user in core_users:
                if user not in settings_data['points']['working_days']:
                    settings_data['points']['working_days'][user] = ['mon', 'tue', 'wed', 'thu', 'fri']

            # Get list of registered users for core users selection
            registered_users = [user[0] for user in db.query(User.username).all()]

            # Get today's date for template
            today = datetime.now().date()

            # Return the template with all required data
            return render_template(
                "settings.html",
                settings=settings_data,
                settings_data=settings_data,
                registered_users=registered_users,
                core_users=core_users,
                rules=settings_data.get("points", {}).get("rules", []),
                today=today
            )

        else:  # POST
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

                # Ensure working_days are properly nested in points
                if 'points' in normalized_settings and 'working_days' in normalized_settings:
                    working_days = normalized_settings.pop('working_days')
                    normalized_settings['points']['working_days'] = working_days

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
        
        # Apply filters with proper SQL syntax
        if action_filter != 'all':
            query = query.filter(AuditLog.action == action_filter)
        if user_filter != 'all':
            query = query.filter(AuditLog.user == user_filter)
        if date_from:
            date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(AuditLog.timestamp >= date_from_dt)
        if date_to:
            date_to_dt = datetime.strptime(date_to, '%Y-%m-%d')
            query = query.filter(AuditLog.timestamp <= date_to_dt)
            
        # Ensure proper ordering
        query = query.order_by(AuditLog.timestamp.desc())

        # Get total count first
        total_entries = query.count()
        total_pages = (total_entries + per_page - 1) // per_page

        # Then get paginated results
        audit_entries = query.offset((page - 1) * per_page)\
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

def calculate_period_averages(rankings, period):
    """Calculate average arrival times for weekly/monthly views"""
    for rank in rankings:
        arrival_times = rank.get('stats', {}).get('arrival_times', [])
        if arrival_times:
            avg_time = calculate_average_time(arrival_times)
            if avg_time != "N/A":
                avg_datetime = datetime.strptime(avg_time, '%H:%M')
                
                # Calculate average shift length from points settings
                settings = load_settings()
                shift_length = float(settings.get('points', {}).get('shift_length', 9))
                
                # Calculate end time
                end_datetime = avg_datetime + timedelta(hours=shift_length)
                
                rank['time'] = avg_time
                rank['time_obj'] = avg_datetime.time()
                rank['end_time'] = end_datetime.strftime('%H:%M')
                rank['shift_length'] = shift_length * 60  # Convert hours to minutes
        else:
            # Set default values if no arrival times
            rank['time'] = "N/A"
            rank['time_obj'] = datetime.strptime("00:00", '%H:%M').time()
            rank['end_time'] = "N/A"
            rank['shift_length'] = 540  # Default 9 hours in minutes

@bp.route("/rankings/<period>")
@bp.route("/rankings/<period>/<date_str>")
@login_required
@track_response_time('rankings')
def view_rankings(period, date_str=None):
    RANKING_CALLS.inc()
    db = SessionLocal()
    try:
        # Change default mode to 'last_in'
        mode = request.args.get('mode', 'last_in')
        if mode not in ['last_in', 'early_bird']:
            app.logger.warning(f"Invalid mode provided: {mode}, defaulting to last-in")
            mode = 'last_in'
            
        app.logger.debug(f"Rankings request - Period: {period}, Date: {date_str}, Mode: {mode}")
        
        # Get current date (either from URL or today)
        try:
            if date_str:
                current_date = datetime.strptime(date_str, '%Y-%m-%d')
                # Validate date is not in future
                if current_date.date() > datetime.now().date():
                    return render_template("error.html", 
                                        error="Invalid Date",
                                        details="Cannot view rankings for future dates.",
                                        back_link=url_for('bp.index'))
            else:
                current_date = datetime.now()
            
            # For weekly view, always snap to Monday
            if period == 'week':
                current_date = current_date - timedelta(days=current_date.weekday())
            
            # Calculate period end date
            if period == 'week':
                period_end = current_date + timedelta(days=6)
            elif period == 'month':
                next_month = current_date.replace(day=28) + timedelta(days=4)
                period_end = next_month - timedelta(days=next_month.day)
            else:
                period_end = current_date
            
            with rankings_lock:
                # Ensure thread-safe access to data
                data = load_data()
                settings = get_settings()  # Get settings here to pass to calculate_scores
                
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
                
                rankings = calculate_scores(data, period, current_date, mode=mode)
                
                # Add this after rankings calculation
                if period in ['week', 'month']:
                    calculate_period_averages(rankings, period)
                
                # Calculate earliest and latest hours from actual data
                all_times = []
                for rank in rankings:
                    if rank.get('time') and rank['time'] != "N/A":
                        time_obj = datetime.strptime(rank['time'], '%H:%M')
                        all_times.append(time_obj)
                        if rank.get('end_time') and rank['end_time'] != "N/A":
                            end_obj = datetime.strptime(rank['end_time'], '%H:%M')
                            all_times.append(end_obj)
                
                earliest_hour = 7  # Default earliest
                latest_hour = 19  # Default latest
                
                if all_times:
                    earliest_time = min(all_times)
                    latest_time = max(all_times)
                    earliest_hour = max(7, earliest_time.hour)  # Don't go earlier than 7am
                    latest_hour = min(19, latest_time.hour + 1)  # Don't go later than 7pm

                # Get points mode from request
                points_mode = request.args.get('points_mode', 'average')  # default to average
                
                for rank in rankings:
                    if period in ['week', 'month'] and points_mode == 'cumulative':
                        # Use the total score instead of calculating from average
                        rank['score'] = round(rank['total_score'], 2)

                # Sort rankings again if using cumulative mode
                if points_mode == 'cumulative':
                    rankings.sort(key=lambda x: (-x['score'], x['name']))

                # Add current streak for each ranked user
                for rank in rankings:
                    streak = calculate_current_streak(rank["name"])
                    rank["current_streak"] = streak  # Add this line

                # Add this before returning template
                for rank in rankings:
                    streak_info = get_current_streak_info(rank['name'], db)
                    rank.update({
                        'streak': streak_info['length'],
                        'streak_start': streak_info['start'],
                        'is_current_streak': streak_info['is_current']
                    })

                template_data = {
                    'rankings': rankings,
                    'period': period,
                    'current_date': current_date.strftime('%Y-%m-%d'),
                    'current_display': format_date_range(current_date, period_end, period),
                    'current_month_value': current_date.strftime('%Y-%m'),
                    'mode': mode,
                    'points_mode': points_mode,  # Add points_mode to template data
                    'streaks_enabled': settings.get("enable_streaks", False),
                    'earliest_hour': earliest_hour,
                    'latest_hour': latest_hour,
                    'today': datetime.now().date()
                }
                
                return render_template("rankings.html", **template_data)
            
        except ValueError as e:
            app.logger.error(f"Date parsing error: {str(e)}")
            return render_template("error.html", 
                                error=f"Invalid date format: {str(e)}")
            
    except Exception as e:
        app.logger.error(f"Rankings error: {str(e)}", exc_info=True)  # Added exc_info for full traceback
        return render_template("error.html", 
                            error=f"Failed to load rankings",
                            details=str(e),
                            back_link=url_for('bp.index'))
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
        
        # Calculate streak for each user
        streak = calculate_current_streak(entry["name"])
        
        rankings.append({
            "name": entry["name"],
            "time": entry["time"],
            "status": entry["status"],
            "points": points,  # Now points is a number, not a dict
            "streak": streak  # Add streak information
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
                        'player1', g.player2,
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
                             back_link=url_for('bp.index'))  # Fix: add bp. prefix
    finally:
        db.close()

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
        return redirect(url_for('bp.tie_breakers'))
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
        
        # Drop and recreate user_streaks table
        db.execute(text("""
            TRUNCATE user_streaks CASCADE;
            -- Reset streak history column too
            UPDATE user_streaks SET streak_history = NULL;
        """))
        
        # Force immediate streak recalculation by monitoring service
        db.execute(text("""
            INSERT INTO monitoring_logs (event_type, details, status)
            VALUES ('streak_reset', '{"triggered_by": "manual_reset"}', 'success')
        """))
        
        db.commit()
        
        return jsonify({
            "message": "Streaks reset successfully. They will be recalculated automatically.",
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
        tie_id = create_test_tie_breaker(db, 'monthly', month_end, 15.0, 'early_bird', test_users)
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
            return redirect(url_for('bp.tie_breakers'))

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
        return redirect(url_for('bp.tie_breakers'))
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
        return redirect(url_for('bp.play_game', game_id=game_id))

    except Exception as e:
        db.rollback()
        app.logger.error(f"Error joining game: {str(e)}")
        return jsonify({"error": "Server error"}), 500
    finally:
        db.close()

@bp.route("/games/<int:game_id>/reset", methods=["POST"])
@login_required
def reset_game(game_id):
    print("Passing")
    pass

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

@bp.route("/api/history")
@login_required
def get_history():
    db = SessionLocal()
    try:
        # Get query parameters with defaults
        page = request.args.get('page', 1, type=int)
        per_page = min(int(request.args.get('per_page', 50)), 500)
        users = request.args.getlist('users[]')
        statuses = request.args.getlist('status[]')
        from_date = request.args.get('fromDate')
        to_date = request.args.get('toDate')

        # Build base query
        query = db.query(Entry)

        # Apply filters
        if users and 'all' not in users:
            query = query.filter(Entry.name.in_(users))
        if statuses and 'all' not in statuses:
            query = query.filter(Entry.status.in_(statuses))
        if from_date:
            query = query.filter(Entry.date >= from_date)
        if to_date:
            query = query.filter(Entry.date <= to_date)

        # Get total count
        total_count = query.count()

        # Get paginated results
        entries = query.order_by(Entry.date.desc(), Entry.time.desc())\
                      .offset((page - 1) * per_page)\
                      .limit(per_page)\
                      .all()

        # Format results
        results = []
        for entry in entries:
            # Calculate position for the day
            day_position = db.query(Entry)\
                .filter(Entry.date == entry.date)\
                .filter(Entry.time <= entry.time)\
                .count()

            results.append({
                'id': entry.id,
                'date': entry.date,
                'time': entry.time,
                'name': entry.name,
                'status': entry.status,
                'position': day_position
            })

        return jsonify({
            'entries': results,
            'total': total_count,
            'page': page,
            'per_page': per_page,
            'total_pages': (total_count + per_page - 1) // per_page
        })

    except Exception as e:
        app.logger.error(f"Error fetching history: {str(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        db.close()

@bp.route("/rankings/day")
@bp.route("/rankings/day/<date>")
@login_required
def day_rankings(date=None):
    db = SessionLocal()

    if date is None:
        date = datetime.now().date().isoformat()
    
    data = load_data()
    settings = load_settings()
    mode = request.args.get('mode', 'last_in')
    
    today_entries = [e for e in data if e["date"] == date]
    today_entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
    
    rankings = []
    total_entries = len(today_entries)
    for position, entry in enumerate(today_entries, 1):
        scores = calculate_daily_score(entry, settings, position, total_entries, mode)
        
        entry_time = datetime.strptime(entry["time"], "%H:%M")
        entry_date = datetime.strptime(entry["date"], "%Y-%m-%d")
        
        weekday = entry_date.strftime('%A').lower()
        day_shift = settings["points"].get("daily_shifts", {}).get(weekday, {
            "hours": settings["points"].get("shift_length", 9),
            "start": "09:00"
        })
        
        shift_length_hours = float(day_shift["hours"])
        shift_length = int(shift_length_hours * 60)
        end_time = entry_time + timedelta(minutes=shift_length)
        
        rankings.append({
            "name": entry["name"],
            "time": entry["time"],
            "time_obj": entry_time,
            "shift_length": shift_length,
            "shift_hours": shift_length_hours,
            "end_time": end_time.strftime('%H:%M'),
            "status": entry["status"],
            "points": scores["last_in"] if mode == 'last_in' else scores["early_bird"],
            "streak": scores.get("current_streak", 0)  # Use get() with default value
        })
    
    # Sort by points descending
    rankings.sort(key=lambda x: x["points"], reverse=True)
    
    # Get shift length based on the day
    weekday = datetime.strptime(date, '%Y-%m-%d').strftime('%A').lower()
    day_shift = settings["points"].get("daily_shifts", {}).get(weekday, {
        "hours": settings["points"].get("shift_length", 9),
        "start": "09:00"
    })
    
    shift_start = datetime.strptime(day_shift["start"], "%H:%M")
    start_hour = shift_start.hour
    start_minute = shift_start.minute
    
    # Calculate earliest and latest hours from actual data
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

    for entry in rankings:
        streak_info = get_current_streak_info(entry['name'], db)
        entry['streak'] = streak_info['length']
        entry['streak_start'] = streak_info['start']
        entry['is_current_streak'] = streak_info['is_current']

    return render_template("day_rankings.html", 
                         rankings=rankings,
                         date=date,
                         mode=mode,
                         start_hour=start_hour,
                         start_minute=start_minute,
                         earliest_hour=earliest_hour,
                         latest_hour=latest_hour)

@bp.route("/history")
@login_required
def history():
    db = SessionLocal()
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        per_page = min(max(per_page, 1), 500)
        query = db.query(Entry).order_by(Entry.timestamp.desc())
        total_entries = query.count()
        total_pages = (total_entries + per_page - 1) // per_page
        offset = (page - 1) * per_page
        results = query.offset(offset).limit(per_page).all()

        return render_template(
            "history.html",
            entries=results,
            current_page=page,
            total_pages=total_pages,
            per_page=per_page
        )
    finally:
        db.close()

@bp.route("/streaks")
@login_required
def view_streaks():
    """View streaks for all users"""
    db = SessionLocal()
    try:
        recent_users = db.query(Entry.name).distinct().filter(
            Entry.date >= (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        ).all()
        recent_users = [u[0] for u in recent_users]
        
        today = datetime.now().date()
        streak_data = []
        
        for username in recent_users:
            # Get complete streak history
            streaks = get_streak_history(username, db)
            
            # Get current streak (if any)
            current_streak = next((s for s in streaks if s['is_current']), None)
            past_streaks = [s for s in streaks if not s['is_current']]
            
            streak_info = {
                'username': username,
                'current_streak': current_streak['length'] if current_streak else 0,
                'current_start': current_streak['start'] if current_streak else None,
                'is_current': bool(current_streak),
                'max_streak': max((s['length'] for s in streaks), default=0),
                'past_streaks': [current_streak] + past_streaks if current_streak else past_streaks
            }
            
            streak_data.append(streak_info)
        
        # Sort by current streak first, then max streak
        streak_data.sort(key=lambda x: (-x['current_streak'], -x['max_streak']))
        
        max_streak = max((s['max_streak'] for s in streak_data), default=0)
        return render_template("streaks.html", 
                             streaks=streak_data,
                             max_streak=max_streak,
                             today=today)
    finally:
        db.close()

# Remove update_user_streak function since it's handled by monitoring service
# Remove other streak-related functions that are no longer needed

@bp.route("/visualisations")
@login_required
def visualisations():
    core_users = get_core_users()  # Use the dynamic function instead of CORE_USERS constant
    return render_template("visualisations.html", core_users=core_users)

@bp.route("/visualization-data")
@login_required
def get_visualization_data():
    try:
        # Add mode to visualization data request
        mode = request.args.get('mode', 'last-in')
        data = load_data()
        if not data:
            return jsonify({
                "weeklyPatterns": {},
                "statusCounts": {"in_office": 0, "remote": 0, "sick": 0, "leave": 0},
                "pointsProgress": {},
                "dailyActivity": {},
                "lateArrivalAnalysis": {},
                "userComparison": {}
            })
            
        date_range = request.args.get('range', 'all')
        user_filter = request.args.get('user', 'all').split(',')
        
        cutoff_date = None
        if date_range != 'all':
            days = int(date_range)
            cutoff_date = datetime.now().date() - timedelta(days=days)
        
        filtered_data = []
        for entry in data:
            try:
                entry_date = datetime.strptime(entry['date'], '%Y-%m-%d').date()
                if cutoff_date and entry_date < cutoff_date:
                    continue
                if 'all' not in user_filter and entry['name'] not in user_filter:
                    continue
                filtered_data.append(entry)
            except (ValueError, KeyError):
                continue
        
        vis_data = {
            'weeklyPatterns': calculate_weekly_patterns(filtered_data),
            'statusCounts': calculate_status_counts(filtered_data),
            'pointsProgress': calculate_points_progression(filtered_data),
            'dailyActivity': calculate_daily_activity(filtered_data),
            'lateArrivalAnalysis': analyze_late_arrivals(filtered_data),
            'userComparison': calculate_user_comparison(filtered_data)
        }
        
        return jsonify(vis_data)
    except Exception as e:
        app.logger.error(f"Visualization error: {str(e)}")
        return jsonify({
            "error": f"Failed to generate visualization data: {str(e)}"
        }), 500
    
@bp.route("/edit/<entry_id>", methods=["PATCH", "DELETE"])
@login_required
def modify_entry(entry_id):
    db = SessionLocal()
    try:
        entry = db.query(Entry).filter_by(id=entry_id).first()
        if not entry:
            return jsonify({"error": "Entry not found"}), 404
        
        if request.method == "PATCH":
            # Store old data for audit
            old_data = {
                "date": entry.date,
                "time": entry.time,
                "name": entry.name,
                "status": entry.status
            }
            
            updated_data = request.json
            
            # Check for duplicates
            existing = db.query(Entry).filter(
                Entry.date == updated_data.get("date", entry.date),
                Entry.name == updated_data.get("name", entry.name),
                Entry.id != entry_id
            ).first()
            
            if existing:
                return jsonify({
                    "message": "Error: Already have an entry for this person on this date.",
                    "type": "error"
                }), 400
            
            # Update entry
            for key, value in updated_data.items():
                setattr(entry, key, value)
            
            log_audit(
                "modify_entry",
                session['user'],
                f"Modified entry for {entry.name} on {entry.date}",
                old_data=old_data,
                new_data=updated_data
            )
        else:
            # Log deletion
            log_audit(
                "delete_entry",
                session['user'],
                f"Deleted entry for {entry.name} on {entry.date}",
                old_data={
                    "date": entry.date,
                    "time": entry.time,
                    "name": entry.name,
                    "status": entry.status
                }
            )
            db.delete(entry)
        
        db.commit()
        
        # Streak updates are now handled by monitoring container
        
        return jsonify({
            "message": "Entry updated successfully.",
            "type": "success"
        })
    
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error modifying entry: {str(e)}")
        return jsonify({
            "message": f"Error modifying entry: {str(e)}",
            "type": "error"
        }), 500
    finally:
        db.close()

@bp.route("/missing-entries")
@login_required
def missing_entries():
    db = SessionLocal()
    try:
        # Get monitoring start date
        settings = db.query(Settings).first()
        start_date = settings.monitoring_start_date if settings else datetime.now().replace(month=1, day=1).date()

        # Updated query to use named parameter
        missing = db.execute(
            text("""
                SELECT 
                    missing_entries.date::date as date, 
                    missing_entries.checked_at 
                FROM missing_entries 
                WHERE missing_entries.date >= :start_date
                ORDER BY missing_entries.date DESC
            """),
            {"start_date": start_date.strftime('%Y-%m-%d')}  # Use dictionary for named parameters
        ).fetchall()

        # Get core users and attendance records
        core_users = get_core_users()
        attendance = db.query(Entry.date, Entry.name).filter(
            Entry.date >= start_date.strftime('%Y-%m-%d')  # Convert date to string
        ).all()

        # Group attendance by date
        attendance_by_date = defaultdict(list)
        for record in attendance:
            attendance_by_date[record.date].append(record.name)

        # Format missing entries
        formatted_entries = []
        for entry in missing:
            entry_date = entry.date.strftime('%Y-%m-%d') if isinstance(entry.date, date) else entry.date
            present_users = attendance_by_date.get(entry_date, [])
            missing_users = [user for user in core_users if user not in present_users]
            
            if missing_users:  # Only include dates with missing users
                formatted_entries.append({
                    'date': entry.date,
                    'checked_at': entry.checked_at,
                    'missing_users': missing_users
                })

        return render_template(
            "missing_entries.html",
            missing_entries=formatted_entries,
            start_date=start_date.strftime('%Y-%m-%d')
        )
    
    except Exception as e:
        app.logger.error(f"Error retrieving missing entries: {str(e)}")
        return render_template("error.html", 
                            error="Failed to retrieve missing entries",
                            details=str(e),
                            back_link=url_for('bp.index'))
    finally:
        db.close()

@bp.errorhandler(404)
def not_found_error(error):
    return render_template('error.html',
                         error="Page Not Found", 
                         details="The requested page could not be found.",
                         back_link=url_for('bp.index')), 404

@bp.errorhandler(500)
def internal_error(error):
    return render_template('error.html',
                         error="Internal Server Error",
                         details="An unexpected error has occurred.", 
                         back_link=url_for('bp.index')), 500

@bp.route('/games/<int:game_id>/resign', methods=['POST'])
@login_required
def resign_game(game_id):
    db = SessionLocal()
    try:
        current_user = session.get('user')
        
        # Get game with locking
        game = db.execute(text("""
            SELECT g.*, t.status as tie_breaker_status 
            FROM tie_breaker_games g
            JOIN tie_breakers t ON g.tie_breaker_id = t.id 
            WHERE g.id = :game_id
            FOR UPDATE
        """), {"game_id": game_id}).fetchone()
        
        if not game or game.status != 'active':
            return jsonify({"error": "Invalid game"}), 400
            
        if current_user not in [game.player1, game.player2]:
            return jsonify({"error": "Not your game"}), 403
            
        # Set other player as winner
        winner = game.player2 if current_user == game.player1 else game.player1
        
        # Update game status
        db.execute(text("""
            UPDATE tie_breaker_games 
            SET status = 'completed',
                winner = :winner,
                completed_at = NOW()
            WHERE id = :game_id
        """), {
            "game_id": game_id,
            "winner": winner
        })
        
        # Check if tie breaker is complete
        check_tie_breaker_completion(db, game.tie_breaker_id)
        
        db.commit()
        
        # Notify other players
        socketio.emit('game_resigned', {
            'game_id': game_id,
            'resigned_by': current_user,
            'winner': winner
        }, room=f'game_{game_id}')
        
        return jsonify({"success": True, "winner": winner})
        
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error resigning game: {str(e)}")
        return jsonify({"error": "Server error"}), 500
    finally:
        db.close()

@bp.route('/games/<int:game_id>/draw', methods=['POST'])
@login_required
def offer_draw(game_id):
    """Offer or accept a draw in a game"""
    db = SessionLocal()
    try:
        current_user = session.get('user')
        action = request.json.get('action')  # 'offer' or 'accept'
        
        game = db.execute(text("""
            SELECT g.*, t.status as tie_breaker_status,
                   g.game_state->>'draw_offered_by' as draw_offered_by
            FROM tie_breaker_games g
            JOIN tie_breakers t ON g.tie_breaker_id = t.id 
            WHERE g.id = :game_id
            FOR UPDATE
        """), {"game_id": game_id}).fetchone()
        
        if not game or game.status != 'active':
            return jsonify({"error": "Invalid game"}), 400
            
        if current_user not in [game.player1, game.player2]:
            return jsonify({"error": "Not your game"}), 403
        
        game_state = game.game_state
        
        if action == 'offer':
            if game_state.get('draw_offered_by'):
                return jsonify({"error": "Draw already offered"}), 400
                
            game_state['draw_offered_by'] = current_user
            
            # Update game state
            db.execute(text("""
                UPDATE tie_breaker_games 
                SET game_state = :game_state
                WHERE id = :game_id
            """), {
                "game_id": game_id,
                "game_state": json.dumps(game_state)
            })
            
            db.commit()
            
            # Notify other player of draw offer
            socketio.emit('draw_offered', {
                'game_id': game_id,
                'offered_by': current_user
            }, room=f'game_{game_id}')
            
            return jsonify({"success": True, "status": "draw_offered"})
            
        elif action == 'accept':
            if not game_state.get('draw_offered_by'):
                return jsonify({"error": "No draw offered"}), 400
                
            if game_state['draw_offered_by'] == current_user:
                return jsonify({"error": "Cannot accept your own draw offer"}), 400
            
            # Create next game with reversed player order
            create_next_game_after_draw(
                db,
                game.tie_breaker_id,
                game.game_type,
                game.player1,
                game.player2
            )
            
            # Update current game as completed with draw
            db.execute(text("""
                UPDATE tie_breaker_games 
                SET status = 'completed',
                    completed_at = NOW()
                WHERE id = :game_id
            """), {"game_id": game_id})
            
            db.commit()
            
            # Notify players of accepted draw
            socketio.emit('draw_accepted', {
                'game_id': game_id
            }, room=f'game_{game_id}')
            
            return jsonify({"success": True, "status": "draw_accepted"})
            
        return jsonify({"error": "Invalid action"}), 400
        
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error handling draw: {str(e)}")
        return jsonify({"error": "Server error"}), 500
    finally:
        db.close()

@bp.route('/games/<int:game_id>/status')
@login_required
def game_status(game_id):
    """Get current game status"""
    db = SessionLocal()
    try:
        game = db.execute(text("""
            SELECT g.*, t.status as tie_breaker_status
            FROM tie_breaker_games g
            JOIN tie_breakers t ON g.tie_breaker_id = t.id
            WHERE g.id = :game_id
        """), {"game_id": game_id}).fetchone()
        
        if not game:
            return jsonify({"error": "Game not found"}), 404
            
        return jsonify({
            "status": game.status,
            "state": game.game_state,
            "winner": game.winner,
            "tie_breaker_status": game.tie_breaker_status
        })
        
    except Exception as e:
        app.logger.error(f"Error getting game status: {str(e)}")
        return jsonify({"error": "Server error"}), 500
    finally:
        db.close()

@bp.route("/profile")
@login_required
def profile():
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=session['user']).first()
        if not user:
            return redirect(url_for('bp.logout'))
        return render_template("profile.html", user=user)
    finally:
        db.close()

@bp.route("/profile/change-password", methods=["POST"])
@login_required
def change_password():
    if not request.is_json:
        return jsonify({"message": "Invalid request"}), 400
        
    current_password = request.json.get("current_password")
    new_password = request.json.get("new_password")
    
    if not current_password or not new_password:
        return jsonify({"message": "Missing required fields"}), 400
        
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=session['user']).first()
        
        if not user or user.password != current_password:  # In production, use proper password hashing
            return jsonify({"message": "Current password is incorrect"}), 401
            
        user.password = new_password  # In production, hash the password
        db.commit()
        
        log_audit(
            "change_password",
            session['user'],
            "Password changed successfully"
        )
        
        return jsonify({"message": "Password updated successfully"})
        
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error changing password: {str(e)}")
        return jsonify({"message": "Error updating password"}), 500
    finally:
        db.close()

@bp.route("/api/attendance/<username>/<start_date>/<end_date>")
@login_required
def get_attendance(username, start_date, end_date):
    db = SessionLocal()
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        attendance = get_attendance_for_period(username, start, end, db)
        return jsonify(attendance)
    except Exception as e:
        app.logger.error(f"Error getting attendance: {str(e)}")
        return jsonify({}), 500
    finally:
        db.close()

# ...existing code...

def api_auth_required(f):
    """Decorator to require API authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({"error": "No authorization token"}), 401
            
        token = auth_header.split(' ')[1]
        
        # Verify token in database
        db = SessionLocal()
        try:
            user = db.query(User).filter_by(api_token=token).first()
            if not user:
                return jsonify({"error": "Invalid token"}), 401
            # Add user to request context
            request.current_user = user.username
            return f(*args, **kwargs)
        finally:
            db.close()
    return decorated_function

@bp.route("/api/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json()
        username = data.get("username")
        password = data.get("password")
        
        if not username or not password:
            return jsonify({"error": "Missing credentials"}), 400
            
        db = SessionLocal()
        try:
            user = db.query(User).filter_by(username=username).first()
            if user and user.password == password:  # In production, use proper password hashing
                # Generate new API token
                token = secrets.token_urlsafe(32)
                user.api_token = token
                db.commit()
                
                return jsonify({
                    "message": "Login successful",
                    "token": token
                })
            return jsonify({"error": "Invalid credentials"}), 401
        finally:
            db.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Update API endpoints to require authentication
@bp.route("/api/rankings/<period>")
@bp.route("/api/rankings/<period>/<date_str>")
@api_auth_required
def api_rankings(period, date_str=None):
    try:
        mode = request.args.get('mode', 'last_in')
        data = load_data()
        if not data:
            return jsonify([])
            
        current_date = datetime.strptime(date_str, '%Y-%m-%d') if date_str else datetime.now()
        rankings = calculate_scores(data, period, current_date, mode=mode)
        return jsonify(rankings)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/streaks")
@api_auth_required
def api_streaks():
    db = SessionLocal()
    try:
        streaks = []
        recent_users = db.query(Entry.name).distinct().all()
        for (username,) in recent_users:
            streak_info = get_current_streak_info(username, db)
            streaks.append({
                "username": username,
                "current_streak": streak_info['length'],
                "max_streak": streak_info.get('max_streak', 0),
                "streak_start": streak_info['start'].isoformat() if streak_info['start'] else None
            })
        return jsonify(streaks)
    finally:
        db.close()

@bp.route("/api/users/<username>/stats")
@api_auth_required
def api_user_stats(username):
    try:
        data = load_data()
        if not data:
            return jsonify({"error": "No data found"}), 404
            
        user_entries = [e for e in data if e["name"] == username]
        if not user_entries:
            return jsonify({"error": "User not found"}), 404
            
        stats = calculate_user_stats(user_entries)
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/api/log", methods=["POST"])
@api_auth_required
def api_log_attendance():
    try:
        entry = request.get_json()
        if not entry:
            return jsonify({"error": "Missing entry data"}), 400
            
        db = SessionLocal()
        try:
            # Check for existing entry
            existing = db.query(Entry).filter_by(
                date=entry["date"],
                name=entry["name"]
            ).first()
            
            if existing:
                return jsonify({"error": "Already logged attendance for this date"}), 400
                
            new_entry = Entry(
                id=str(uuid.uuid4()),
                date=entry["date"],
                time=entry["time"],
                name=entry["name"],
                status=entry["status"]
            )
            
            db.add(new_entry)
            db.commit()
            
            return jsonify({"message": "Attendance logged successfully"})
        finally:
            db.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ...existing code...

def calculate_user_stats(entries):
    """Calculate statistics for a specific user from their entries"""
    if not entries:
        return {
            "stats": {
                "days": 0,
                "in_office": 0,
                "remote": 0,
                "sick": 0,
                "leave": 0
            },
            "score": 0.0,
            "average_arrival_time": "N/A"
        }
        
    total_days = len(entries)
    status_counts = {
        "in_office": sum(1 for e in entries if e["status"] == "in-office"),
        "remote": sum(1 for e in entries if e["status"] == "remote"),
        "sick": sum(1 for e in entries if e["status"] == "sick"),
        "leave": sum(1 for e in entries if e["status"] == "leave")
    }
    
    arrival_times = [
        datetime.strptime(e["time"], "%H:%M").time()
        for e in entries
        if e["status"] in ["in-office", "remote"]
    ]
    
    avg_time = "N/A"
    if arrival_times:
        total_minutes = sum(t.hour * 60 + t.minute for t in arrival_times)
        avg_minutes = total_minutes / len(arrival_times)
        avg_hour = int(avg_minutes // 60)
        avg_min = int(avg_minutes % 60)
        avg_time = f"{avg_hour:02d}:{avg_min:02d}"
    
    # Calculate score based on attendance
    settings = get_settings()
    base_score = (
        status_counts["in_office"] * float(settings["points"].get("in_office", 1)) +
        status_counts["remote"] * float(settings["points"].get("remote", 0.8))
    ) / total_days if total_days > 0 else 0
    
    return {
        "stats": {
            "days": total_days,
            **status_counts
        },
        "score": base_score,
        "average_arrival_time": avg_time
    }

# ...existing code...

@bp.route("/api/query/<period>")
@api_auth_required
def api_query_data(period):
    """Query attendance data with granular filtering"""
    db = SessionLocal()
    try:
        # Get filter parameters
        from_date = request.args.get('from')
        to_date = request.args.get('to')
        user = request.args.get('user')
        mode = request.args.get('mode', 'last-in')
        status = request.args.get('status')
        limit = request.args.get('limit', type=int)

        # Start with base query
        query = db.query(Entry)

        # Apply filters
        if from_date:
            query = query.filter(Entry.date >= from_date)
        if to_date:
            query = query.filter(Entry.date <= to_date)
        if user:
            query = query.filter(Entry.name == user)
        if status:
            query = query.filter(Entry.status == status)

        # Apply ordering
        if mode == 'lastin':
            query = query.order_by(Entry.date.desc(), Entry.time.desc())
        else:  # early-bird
            query = query.order_by(Entry.date.desc(), Entry.time.asc())

        # Apply limit
        if limit:
            query = query.limit(limit)

        # Execute query
        entries = query.all()

        # Format results
        results = []
        for entry in entries:
            # Get streak info for each entry
            streak_info = get_current_streak_info(entry.name, db)
            
            # Calculate score for the entry
            settings = load_settings()
            score = calculate_daily_score(
                {
                    "date": entry.date,
                    "time": entry.time,
                    "name": entry.name,
                    "status": entry.status
                },
                settings,
                mode=mode
            )

            results.append({
                "date": entry.date,
                "name": entry.name,
                "status": entry.status,
                "time": entry.time,
                "score": score[mode] if isinstance(score, dict) else score,
                "streak": streak_info['length'] if streak_info['is_current'] else None
            })

        return jsonify(results)
        
    except Exception as e:
        app.logger.error(f"Error querying data: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

# ...existing code...

@bp.route('/download/cli/<platform>')
@login_required
def download_cli(platform):
    """Download CLI binary"""
    try:
        platform_map = {
            'windows': 'lic-cli-windows-x64.exe',
            'linux': 'lic-cli-linux-x64',
            'macos-x64': 'lic-cli-macos-x64',
            'macos-arm64': 'lic-cli-macos-arm64'
        }
        
        if platform not in platform_map:
            return jsonify({"error": "Invalid platform"}), 400
            
        filename = platform_map[platform]
        app.logger.info(f"Serving CLI file from static/cli/{filename}")
        
        # Set content type based on platform
        mime_type = 'application/x-msdownload' if platform == 'windows' else 'application/octet-stream'
        
        return send_from_directory(
            'static/cli',
            filename,
            as_attachment=True,
            download_name=filename,
            mimetype=mime_type
        )
    except Exception as e:
        app.logger.error(f"Error downloading CLI: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Update index route to include CLI downloads