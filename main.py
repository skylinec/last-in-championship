from flask import Flask, request, jsonify, render_template, session, redirect, url_for
import os
import logging
from sqlalchemy import create_engine, event, Column, Integer, String, DateTime, Date, Float, JSON, text, Boolean, inspect
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta, date  # Add date import
import uuid
import psycopg2
import psycopg2.extras  # Add this import
import json  # Add explicit json import
from prometheus_client import start_http_server, Summary, Counter, Gauge, Histogram, make_wsgi_app
import time
from threading import Thread
from werkzeug.middleware.dispatcher import DispatcherMiddleware

# Add these imports near the top
from metrics import DB_CONNECTIONS, metrics, record_request_metric, update_attendance_metrics, record_db_operation, record_audit_action, AUDIT_TRAIL_COUNT, ATTENDANCE_DB_COUNT, RANKING_CALLS
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from prometheus_client import make_wsgi_app
import time

# Add new imports
from functools import lru_cache
from prometheus_client import Counter

# Add new metrics
CACHE_HITS = Counter('cache_hits_total', 'Cache hit count', ['function'])
CACHE_MISSES = Counter('cache_misses_total', 'Cache miss count', ['function'])

class CacheWithMetrics:
    """Base cache decorator with metrics tracking"""
    def __init__(self, func):
        self.func = func
        self.name = func.__name__
        self.cache = {}
        self.hits = 0
        self.misses = 0

    def __call__(self, *args, **kwargs):
        key = self._make_key(args, kwargs)
        
        if key in self.cache:
            self.hits += 1
            CACHE_HITS.labels(function=self.name).inc()
            return self.cache[key]
        
        self.misses += 1
        CACHE_MISSES.labels(function=self.name).inc()
        result = self.func(*args, **kwargs)
        self.cache[key] = result
        return result
    
    def _make_key(self, args, kwargs):
        """Default key maker - override in subclasses"""
        return args + tuple(sorted(kwargs.items()))
    
    def cache_info(self):
        """Return cache statistics"""
        return {
            'hits': self.hits,
            'misses': self.misses,
            'maxsize': None,
            'currsize': len(self.cache)
        }
    
    def cache_clear(self):
        """Clear the cache"""
        self.cache.clear()
        self.hits = 0
        self.misses = 0

class HashableCacheWithMetrics(CacheWithMetrics):
    """Cache decorator that handles unhashable types"""
    def _make_key(self, args, kwargs):
        """Make a hashable key from unhashable arguments"""
        def make_hashable(obj):
            if isinstance(obj, dict):
                return tuple(sorted((k, make_hashable(v)) for k, v in obj.items()))
            elif isinstance(obj, (list, tuple)):
                return tuple(make_hashable(x) for x in obj)
            elif isinstance(obj, set):
                return tuple(sorted(make_hashable(x) for x in obj))
            return obj
            
        hashable_args = tuple(make_hashable(arg) for arg in args)
        hashable_kwargs = tuple(sorted((k, make_hashable(v)) for k, v in kwargs.items()))
        return (hashable_args, hashable_kwargs)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if os.getenv('FLASK_ENV') == 'development' else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create Flask app first, before any route definitions
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')  # Default for development

# Set up the metrics middleware properly
metrics_app = make_wsgi_app()
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': metrics_app
})

# Add this after the app creation and before route definitions
def today():
    """Return today's date for template use"""
    return datetime.now().date()

# Add this after the today() function
@app.context_processor
def utility_processor():
    """Make utility functions available to all templates"""
    return {
        'today': today
    }

# Add new template filter before routes
@app.template_filter('format_date')
def format_date(value):
    """Format date for display in templates"""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return value
    return value.strftime('%d/%m/%Y') if value else ''

def check_configuration():
    """Check and log important configuration settings"""
    logger.info("Checking configuration...")
    config = {
        'FLASK_ENV': os.getenv('FLASK_ENV', 'production'),
        'DATABASE_URL': os.getenv('DATABASE_URL', '[MASKED]'),
        'DEBUG': app.debug
    }
    for key, value in config.items():
        if key == 'DATABASE_URL':
            logger.info(f"{key}: {'[MASKED]'}")
        else:
            logger.info(f"{key}: {value}")
    return config

# Database configuration with better error handling
def get_database_url():
    """Get database URL with fallback for development"""
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        logger.warning("DATABASE_URL not set, using development default")
        db_url = 'postgresql://postgres:postgres@localhost:5432/championship'
    return db_url

try:
    DATABASE_URL = get_database_url()
    engine = create_engine(
        DATABASE_URL,
        pool_size=int(os.getenv('DB_POOL_SIZE', '20')),
        max_overflow=int(os.getenv('DB_MAX_OVERFLOW', '30')),
        pool_timeout=int(os.getenv('DB_POOL_TIMEOUT', '60')),
        pool_pre_ping=True,
        pool_recycle=3600
    )
    logger.info("Database engine created successfully")
except Exception as e:
    logger.error(f"Failed to create database engine: {str(e)}")
    raise

# Add connection pool logging with environment check
if os.getenv('FLASK_ENV') == 'development':
    @event.listens_for(engine, "connect")
    def connect(dbapi_connection, connection_record):
        logger.debug("New database connection created")

    @event.listens_for(engine, "checkout")
    def receive_checkout(dbapi_connection, connection_record, connection_proxy):
        logger.debug("Database connection checked out from pool")

    @event.listens_for(engine, "checkin")
    def receive_checkin(dbapi_connection, connection_record):
        logger.debug("Database connection returned to pool")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, psycopg2.extensions.connection):
        # Register the JSONB adapter and type
        psycopg2.extras.register_default_jsonb(dbapi_connection)
        psycopg2.extras.register_json(dbapi_connection)

# Database models
class Entry(Base):
    __tablename__ = "entries"
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
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    points = Column(JSON, nullable=False)
    late_bonus = Column(Float, nullable=False)
    remote_days = Column(JSON, nullable=False)
    core_users = Column(JSON, nullable=False)  # Add this line
    enable_streaks = Column(Boolean, default=False)
    streak_multiplier = Column(Float, default=0.5)  # Points multiplier per day in streak
    streaks_enabled = Column(Boolean, default=False)
    streak_bonus = Column(Float, default=0.5)
    monitoring_start_date = Column(Date, default=lambda: datetime.now().replace(month=1, day=1))
    enable_tiebreakers = Column(Boolean, default=False)
    tiebreaker_points = Column(Integer, default=5)
    tiebreaker_expiry = Column(Integer, default=24)  # Fix: Changed default(24) to default=24
    auto_resolve_tiebreakers = Column(Boolean, default=False)
    tiebreaker_weekly = Column(Boolean, default=True)
    tiebreaker_monthly = Column(Boolean, default=True)

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
    __tablename__ = "user_streaks"
    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False)
    current_streak = Column(Integer, default=0)
    last_attendance = Column(DateTime, nullable=True)
    max_streak = Column(Integer, default=0)

# Create tables
Base.metadata.create_all(bind=engine)

# Add this function to perform the database migration
def migrate_database():
    """Migrate database to add core_users column"""
    db = SessionLocal()
    try:
        inspector = inspect(engine)
        columns = [c['name'] for c in inspector.get_columns('settings')]
        
        if 'core_users' not in columns:
            # Add core_users column
            db.execute(text('ALTER TABLE settings ADD COLUMN core_users JSON'))
            # Update existing rows with default core users
            default_users = ["Matt", "Kushal", "Nathan", "Michael", "Ben"]
            db.execute(
                text("UPDATE settings SET core_users = :users"),
                {"users": json.dumps(default_users)}
            )
            db.commit()
            print("Added core_users column to settings table")
        
        columns_tie_breakers = [c['name'] for c in inspector.get_columns('tie_breakers')]
        if 'period_end' not in columns_tie_breakers:
            db.execute("ALTER TABLE tie_breakers ADD COLUMN period_end TIMESTAMP;")
        
    except Exception as e:
        db.rollback()
        print(f"Migration error: {str(e)}")
        # If column doesn't exist, recreate the table
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        init_settings()
    finally:
        db.close()

# Initialize default settings if not exists
def init_settings():
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
            late_bonus=1,
            remote_days={},
            core_users=["Matt", "Kushal", "Nathan", "Michael", "Ben"],  # Add default core users
            enable_streaks=False,
            streak_multiplier=0.5,
            streaks_enabled=False,
            streak_bonus=0.5,
            enable_tiebreakers=False,
            tiebreaker_points=5,
            tiebreaker_expiry=24,
            auto_resolve_tiebreakers=False,
            tiebreaker_weekly=True,    # Add explicit defaults
            tiebreaker_monthly=True    # Add explicit defaults
        )
        db.add(default_settings)
        db.commit()
    db.close()

# Helper functions for database operations
def load_data():
    db = SessionLocal()
    entries = db.query(Entry).all()
    data = [
        {
            "id": entry.id,
            "date": entry.date,
            "time": entry.time,
            "name": entry.name,
            "status": entry.status,
            "timestamp": entry.timestamp.isoformat()
        }
        for entry in entries
    ]
    db.close()
    return data

def save_data(entries):
    db = SessionLocal()
    # Clear existing entries and add new ones
    db.query(Entry).delete()
    for entry in entries:
        db_entry = Entry(**entry)
        db.add(db_entry)
    db.commit()
    db.close()

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

def log_audit(action, user, details, old_data=None, new_data=None):
    """Log audit entry to database with proper change tracking"""
    db = SessionLocal()
    try:
        app.logger.info(f"Starting audit log for {action} by {user}")
        app.logger.debug(f"Old data: {old_data}")
        app.logger.debug(f"New data: {new_data}")
        
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

        # Deep clean the data
        if old_data:
            old_data = {k: clean_value(v) for k, v in old_data.items() if not k.startswith('_')}
        if new_data:
            new_data = {k: clean_value(v) for k, v in new_data.items() if not k.startswith('_')}

        changes = None
        if action == "update_settings":
            # Special handling for settings updates
            changes = []
            if old_data and new_data:
                # Compare settings fields
                fields_to_compare = [
                    'points', 'late_bonus', 'remote_days', 'core_users', 
                    'enable_streaks', 'streak_multiplier', 'enable_tiebreakers',
                    'tiebreaker_points', 'tiebreaker_expiry', 'auto_resolve_tiebreakers',
                    'tiebreaker_weekly', 'tiebreaker_monthly'
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

        elif action == "delete_entry":
            changes = [{
                "field": key,
                "old": value,
                "new": "None",
                "type": "deleted"
            } for key, value in old_data.items()]
        
        elif action == "log_attendance":
            changes = [{
                "field": key,
                "old": "None",
                "new": value,
                "type": "added"
            } for key, value in new_data.items()]
        
        elif old_data and new_data:
            # For modifications, track changes
            changes = []
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

        # Create audit entry if there are changes or it's a non-modification action
        if changes or not (old_data and new_data):
            audit_entry = AuditLog(
                user=user,
                action=action,
                details=details,
                changes=changes
            )
            
            db.add(audit_entry)
            db.commit()
            db.refresh(audit_entry)
            app.logger.info(f"Audit log created: {audit_entry}")
            app.logger.debug(f"Changes recorded: {changes}")
        else:
            app.logger.info("No changes detected, skipping audit log")
        
        # Increment audit action counter
        AUDIT_ACTIONS.labels(action=action).inc()
        
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error logging audit: {str(e)}")
        raise
    finally:
        db.close()

# Flask app setup
import json
import os
from datetime import datetime, timedelta
import uuid
from flask import Flask, request, jsonify, render_template, render_template_string, session, redirect, url_for
from functools import wraps

# Core users list
CORE_USERS = ['Matt', 'Kushal', 'Nathan', 'Michael', 'Ben']

# Add these before the route definitions in main.py
@app.template_filter('time_to_minutes')
def time_to_minutes(time_str):
    """Convert time string (HH:MM) to minutes since midnight"""
    try:
        hours, minutes = map(int, time_str.split(':'))
        return hours * 60 + minutes
    except (ValueError, AttributeError):
        return 0

@app.template_filter('minutes_to_time')
def minutes_to_time(minutes):
    """Convert minutes since midnight to time string (HH:MM)"""
    try:
        minutes = int(minutes)
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours:02d}:{minutes:02d}"
    except (ValueError, TypeError):
        return "00:00"

def verify_user(username, password):
    """Verify user credentials from database"""
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        return user is not None and user.password == password  # In production, use proper password hashing
    except Exception as e:
        app.logger.error(f"Error verifying user: {str(e)}")
        return False
    finally:
        db.close()

def save_user(username, password):
    """Save user to database"""
    db = SessionLocal()
    try:
        user = User(username=username, password=password)  # In production, use proper password hashing
        db.add(user)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error saving user: {str(e)}")
        return False
    finally:
        db.close()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        if username and password and verify_user(username, password):
            session['user'] = username
            log_audit("login", username, "Successful login")
            return redirect(url_for('index'))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
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
            if existing_user:
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

@app.route("/logout")
def logout():
    if 'user' in session:
        log_audit("logout", session['user'], "User logged out")
        session.pop('user', None)
    return redirect(url_for('login'))

@app.route("/audit")
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

@app.route("/check_attendance")
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

@app.route("/")
@login_required
def index():
    return render_template("index.html", core_users=get_core_users())

@app.route("/names")
@login_required
def get_names():
    db = SessionLocal()
    try:
        names = db.query(Entry.name).distinct().all()
        return jsonify([name[0] for name in names])
    finally:
        db.close()

@app.route("/today-entries")
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

@app.route("/log", methods=["POST"])
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

def update_positions_for_date(data, date):
    """Position is now calculated dynamically, no need to store it"""
    # Sort entries by time only
    day_entries = [e for e in data if e["date"] == date]
    day_entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))

def format_uk_date(date_obj):
    """Format any date object to UK format"""
    return date_obj.strftime('%d/%m/%Y')

def parse_uk_date(date_str):
    """Parse a UK format date string"""
    try:
        return datetime.strptime(date_str, '%d/%m/%Y')
    except ValueError:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return datetime.now()

def evaluate_rule(rule, entry, context):
    """Evaluate a single scoring rule for an entry"""
    try:
        if rule['type'] == 'condition':
            if 'time' in rule:
                entry_time = datetime.strptime(entry['time'], '%H:%M').time()
                compare_time = datetime.strptime(rule['value'], '%H:%M').time()
                return compare_times(entry_time, compare_time, rule['operator'])
            elif 'status' in rule:
                return entry['status'] == rule['value']
            elif 'day' in rule:
                entry_date = datetime.strptime(entry['date'], '%Y-%m-%d')
                if rule['value'] == 'weekend':
                    return entry_date.weekday() >= 5
                elif rule['value'] == 'weekday':
                    return entry_date.weekday() < 5
                else:
                    return entry_date.strftime('%A').lower() == rule['value'].lower()
            elif 'streak' in rule:
                streak = context.get('streak', 0)
                return compare_values(streak, float(rule['value']), rule['operator'])
        elif rule['type'] == 'action':
            if 'award' in rule:
                return float(rule['points'])
            elif 'multiply' in rule:
                return context['current_points'] * float(rule['value'])
            elif 'streak_bonus' in rule:
                return context['streak'] * context.get('streak_multiplier', 0.5)
        return 0
    except (ValueError, KeyError, TypeError) as e:
        app.logger.error(f"Error evaluating rule: {str(e)}")
        return 0

def compare_times(time1, time2, operator):
    """Compare two time objects"""
    ops = {
        '<': lambda: time1 < time2,
        '>': lambda: time1 > time2,
        '=': lambda: time1 == time2,
        '>=': lambda: time1 >= time2,
        '<=': lambda: time1 <= time2
    }
    return ops.get(operator, lambda: False)()

def compare_values(val1, val2, operator):
    """Compare two numeric values"""
    ops = {
        '<': lambda: val1 < val2,
        '>': lambda: val1 > val2,
        '=': lambda: val1 == val2,
        '>=': lambda: val1 >= val2,
        '<=': lambda: val1 <= val2
    }
    return ops.get(operator, lambda: False)()

@HashableCacheWithMetrics
def calculate_daily_score(entry, settings, position=None, total_entries=None, mode='last-in'):
    """Calculate score for a single day's entry with proper streak handling"""
    # Get day-specific start time for late calculations
    entry_date = datetime.strptime(entry["date"], '%Y-%m-%d')
    weekday = entry_date.strftime('%A').lower()
    day_shift = settings["points"].get("daily_shifts", {}).get(weekday, {
        "hours": settings["points"].get("shift_length", 9),
        "start": "09:00"
    })
    
    shift_start = datetime.strptime(day_shift["start"], "%H:%M").time()
    entry_time = datetime.strptime(entry["time"], "%H:%M").time()
    
    # Modify late arrival logic to use configured start time
    is_llate = entry_time > shift_start

    # Check if it's a working day for this user
    day_name = entry_date.strftime('%a').lower()
    user_working_days = settings.get("points", {}).get("working_days", {}).get(entry["name"], ['mon','tue','wed','thu','fri'])
    
    # If it's not a working day for this user, return zero points
    if day_name not in user_working_days:
        return {
            "early_bird": 0,
            "last_in": 0,
            "base": 0,
            "streak": 0,
            "position_bonus": 0,
            "breakdown": {
                "base_points": 0,
                "position_bonus": 0,
                "streak_bonus": 0
            }
        }

    # Continue with existing scoring logic
    status = entry["status"].replace("-", "_")
    base_points = settings["points"][status]
    
    # Initialize context for rule evaluation
    context = {
        'current_points': base_points,
        'position': position,
        'total_entries': total_entries,
        'streak_multiplier': settings.get('streak_multiplier', 0.5)
    }
    if 'streak' not in context:  # Provide a default streak
        context['streak'] = 0

    # Apply custom rules if they exist
    rules = settings["points"].get("rules", [])
    if rules:
        for rule in rules:
            if rule['type'] == 'condition':
                if evaluate_rule(rule, entry, context):
                    # Find matching action
                    action_rule = next((r for r in rules if r['type'] == 'action'), None)
                    if action_rule:
                        points_mod = evaluate_rule(action_rule, entry, context)
                        context['current_points'] += points_mod

    # Calculate standard bonuses
    early_bird_bonus = 0
    last_in_bonus = 0
    if position is not None and total_entries is not None and status in ["in_office", "remote"]:
        early_bird_bonus = (total_entries - position + 1) * settings["late_bonus"]
        last_in_bonus = position * settings["late_bonus"]
    
    # Calculate streak bonus only if the entry isn't in the future
    streak_bonus = 0
    if settings.get("enable_streaks", False):
        entry_date = datetime.strptime(entry["date"], '%Y-%m-%d').date()
        current_date = datetime.now().date()
        
        if entry_date <= current_date:  # Only calculate streak for non-future dates
            db = SessionLocal()
            try:
                # Calculate streak up to the entry date
                streak = calculate_streak_for_date(entry["name"], entry_date, db)
                if streak > 0:
                    multiplier = settings.get("streak_multiplier", 0.5)
                    # Reverse logic: In last-in mode, streak subtracts points (penalizes consistent lateness)
                    # In early-bird mode, streak adds points (rewards consistent earliness)
                    streak_bonus = -streak * multiplier if mode == 'last-in' else streak * multiplier
            finally:
                db.close()

    # Apply tie breaker wins if enabled
    tie_breaker_points = 0
    if settings.get("enable_tiebreakers", False):
        db = SessionLocal()
        try:
            # Get tie breaker wins for this user on this date
            wins = db.execute(text("""
                SELECT COUNT(*) FROM tie_breakers t
                JOIN tie_breaker_participants p ON t.id = p.tie_breaker_id
                WHERE p.username = :username
                AND t.period_end::date = :date
                AND p.winner = true
                AND t.status = 'completed'
            """), {
                "username": entry["name"],
                "date": entry["date"]
            }).scalar()
            
            if wins > 0:
                base_points = settings.get("tiebreaker_points", 5) * wins
                # In last-in mode, subtract points for winning (penalize)
                # In early-bird mode, add points for winning (reward)
                tie_breaker_points = -base_points if mode == 'last-in' else base_points
        finally:
            db.close()

    return {
        "early_bird": context['current_points'] + early_bird_bonus - streak_bonus + tie_breaker_points,
        "last_in": context['current_points'] + last_in_bonus + streak_bonus - tie_breaker_points,
        "base": context['current_points'],
        "streak": streak_bonus,
        "position_bonus": last_in_bonus if mode == 'last-in' else early_bird_bonus,
        "tie_breaker": tie_breaker_points,
        "breakdown": {
            "base_points": context['current_points'],
            "position_bonus": last_in_bonus if mode == 'last-in' else early_bird_bonus,
            "streak_bonus": streak_bonus,
            "tie_breaker_points": tie_breaker_points
        }
    }

def calculate_streak_bonus(entry):
    """Calculate bonus points for streak based on settings"""
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings or not settings.enable_streaks:
            return 0
            
        streak = db.query(UserStreak).filter_by(username=entry["name"]).first()
        if not streak or streak.current_streak == 0:
            return 0
            
        return streak.current_streak * settings.streak_multiplier
        
    finally:
        db.close()

def calculate_current_streak(name):
    """Calculate current streak for a user"""
    db = SessionLocal()
    try:
        today = datetime.now().date()
        
        # Get most recent entries, ordered by date descending
        entries = db.query(Entry).filter(
            Entry.name == name,
            Entry.status.in_(['in-office', 'remote'])
        ).order_by(Entry.date.desc()).all()
        
        if not entries:
            return 0
            
        streak = 1
        last_date = datetime.strptime(entries[0].date, "%Y-%m-%d").date()
        
        # Skip first entry as it's counted above
        for entry in entries[1:]:
            entry_date = datetime.strptime(entry.date, "%Y-%m-%d").date()
            days_between = (last_date - entry_date).days
            
            if days_between > 3:  # More than a weekend
                break
            elif days_between > 1:
                # Check if gap only includes weekend
                weekend_only = True
                for d in range(1, days_between):
                    check_date = last_date - timedelta(days=d)
                    if check_date.weekday() < 5:  # Not weekend
                        weekend_only = False
                        break
                if not weekend_only:
                    break
            
            streak += 1
            last_date = entry_date
            
        return streak
        
    finally:
        db.close()

def get_week_bounds(date_str):
    """Get the start and end dates for a week"""
    try:
        if '-W' in date_str:
            # Parse ISO week format (e.g., "2024-W01")
            year, week = map(int, date_str.replace('-W', '-').split('-'))
            # Use ISO calendar to get the correct date
            first_day = datetime.strptime(f'{year}-W%W-%w', '%Y-W%W-%w').date()
            first_day = datetime.combine(first_day, datetime.min.time())
        else:
            # Parse regular date and find its week
            first_day = datetime.strptime(date_str, '%Y-%m-%d')
            # Move to Monday of the week
            first_day = first_day - timedelta(days=first_day.weekday())
    except ValueError:
        first_day = datetime.now() - timedelta(days=datetime.now().weekday())
    
    last_day = first_day + timedelta(days=6)
    return first_day, last_day

# Add this decorator to key routes to track response times
def track_response_time(route_name):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            start = time.time()
            response = f(*args, **kwargs)
            duration = time.time() - start
            RESPONSE_TIME.labels(endpoint=route_name).observe(duration)
            return response
        return wrapped
    return decorator

@app.route("/rankings/<period>")
@app.route("/rankings/<period>/<date_str>")
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

@app.route("/rankings/today")
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

@app.route("/rankings/day")
@app.route("/rankings/day/<date>")
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
    
    # Compute earliest and latest hour from your data (use your own logic)
    earliest_hour = 7  # e.g., fallback default
    latest_hour = 19   # e.g., fallback default
    
    # Calculate earliest and latest hours from the actual data
    all_times = []
    for rank in rankings:
        if rank.get('time'):
            time_obj = datetime.strptime(rank['time'], '%H:%M')
            all_times.append(time_obj)
            if rank.get('end_time'):
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

    return render_template("day_rankings.html", 
                         rankings=rankings,
                         date=date,
                         mode=mode,
                         start_hour=start_hour,
                         start_minute=start_minute,
                         earliest_hour=earliest_hour,
                         latest_hour=latest_hour)  # Pass start_hour and start_minute to template

# Update the get_entries route to handle the new filters
@app.route("/edit", methods=["GET"])
@login_required
def get_entries():
    db = SessionLocal()
    try:
        # Get filter parameters with new per_page parameter
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))
        # Ensure per_page is within limits
        per_page = min(max(per_page, 50), 500)
        
        query = db.query(Entry)
        
        # Handle period filter
        periods = request.args.get("period", "all").split(',')
        if "all" not in periods:
            if "today" in periods:
                query = query.filter(Entry.date == datetime.now().date().isoformat())
            elif "week" in periods:
                week_start = datetime.now().date() - timedelta(days=datetime.now().weekday())
                query = query.filter(Entry.date >= week_start.isoformat())
            elif "month" in periods:
                month_start = datetime.now().date().replace(day=1)
                query = query.filter(Entry.date >= month_start.isoformat())

        # Handle user filter
        users = request.args.get("user", "all").split(',')
        if "all" not in users:
            query = query.filter(Entry.name.in_(users))

        # Handle status filter
        statuses = request.args.get("status", "all").split(',')
        if "all" not in statuses:
            query = query.filter(Entry.status.in_(statuses))

        # Handle date range
        from_date = request.args.get("from")
        to_date = request.args.get("to")
        if from_date:
            query = query.filter(Entry.date >= from_date)
        if to_date:
            query = query.filter(Entry.date <= to_date)

        # Get total count for pagination
        total = query.count()
        
        # Get paginated entries
        entries = query.order_by(Entry.date.desc(), Entry.time.desc())\
                      .offset((page - 1) * per_page)\
                      .limit(per_page)\
                      .all()
        
        return jsonify({
            "entries": [{
                "id": e.id,
                "date": e.date,
                "time": e.time,
                "name": e.name,
                "status": e.status
            } for e in entries],
            "total": total
        })
    except Exception as e:
        app.logger.error(f"Error getting entries: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route("/edit/<entry_id>", methods=["PATCH", "DELETE"])
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

@app.route("/settings", methods=["GET", "POST"])
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

# Replace the hardcoded CORE_USERS with a function
def get_core_users():
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        return settings.core_users if settings else []
    finally:
        db.close()

@app.route("/history")
@login_required
def history():
    core_users = get_core_users()  # Get the list of core users
    return render_template("history.html", users=core_users)  # Pass users to template

@app.route("/visualisations")
@login_required
def visualisations():
    core_users = get_core_users()  # Use the dynamic function instead of CORE_USERS constant
    return render_template("visualisations.html", core_users=core_users)

@app.route("/visualization-data")
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

def normalize_status(status):
    """Normalize status keys to use underscores consistently"""
    return status.replace("-", "_")

def calculate_status_counts(data):
    counts = {'in_office': 0, 'remote': 0, 'sick': 0, 'leave': 0}
    for entry in data:
        status = normalize_status(entry['status'])
        counts[status] = counts.get(status, 0) + 1
    return counts

def calculate_arrival_patterns(data):
    patterns = {}
    for entry in data:
        hour = datetime.strptime(entry['time'], '%H:%M').hour
        day = datetime.fromisoformat(entry['date']).strftime('%A')
        key = f"{day}-{hour}"
        patterns[key] = patterns.get(key, 0) + 1
    return patterns

def calculate_points_progression(data):
    settings = load_settings()
    progression = {}
    mode = request.args.get('mode', 'last-in')
    
    for entry in data:
        try:
            date = entry['date']
            if date not in progression:
                progression[date] = {'total': 0, 'count': 0}
            
            # Get scores for the entry
            scores = calculate_daily_score(entry, settings)
            # Use the appropriate score based on mode
            points = scores['last_in'] if mode == 'last_in' else scores['early_bird']
            
            progression[date]['total'] += points
            progression[date]['count'] += 1
        except (KeyError, TypeError):
            continue
    
    # Calculate averages
    return {
        date: round(stats['total'] / stats['count'], 2)
        for date, stats in progression.items()
        if stats['count'] > 0
    }

def calculate_weekly_patterns(data):
    """Calculate attendance patterns by day and hour"""
    patterns = {}
    try:
        # Initialize all possible day-hour combinations
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
        # Create 15-minute intervals from 7 AM to 12 PM
        hours = []
        for hour in range(7, 13):  # Up to 12 PM
            for minute in range(0, 60, 15):
                hours.append(f"{hour:02d}:{minute:02d}")
        
        # Initialize all combinations with zero
        for day in days:
            for hour in hours:
                patterns[f"{day}-{hour}"] = 0
        
        # Count actual patterns
        for entry in data:
            if normalize_status(entry["status"]) in ["in_office", "remote"]:
                try:
                    date = datetime.strptime(entry["date"], '%Y-%m-%d')
                    time = datetime.strptime(entry["time"], "%H:%M")
                    
                    # Skip weekends
                    if date.weekday() >= 5:
                        continue
                    
                    # Only process times between 7 AM and 12 PM
                    if 7 <= time.hour <= 12:
                        day = date.strftime("%A")
                        # Round to nearest 15 minutes
                        minute = (time.minute // 15) * 15
                        hour = f"{time.hour:02d}:{minute:02d}"
                        
                        key = f"{day}-{hour}"
                        if key in patterns:
                            patterns[key] = patterns.get(key, 0) + 1
                        
                except (ValueError, TypeError) as e:
                    app.logger.debug(f"Error processing entry: {entry}, Error: {e}")
                    continue
                    
        app.logger.debug(f"Generated patterns: {patterns}")
        return patterns
        
    except Exception as e:
        app.logger.error(f"Error in weekly patterns: {str(e)}")
        return {}

def analyze_early_arrivals(data):
    early_stats = {}
    for entry in data:
        status = normalize_status(entry['status'])
        if status == "in_office":  # Already normalized above
            try:
                time = datetime.strptime(entry["time"], "%H:%M").time()
                name = entry["name"]
                if name not in early_stats:
                    early_stats[name] = {"early_count": 0, "total_count": 0}
                
                early_stats[name]["total_count"] += 1
                if time.hour < 9:
                    early_stats[name]["early_count"] += 1
            except (ValueError, KeyError):
                continue
    
    return {
        name: {
            "early_percentage": (stats["early_count"] / stats["total_count"]) * 100,
            "total_days": stats["total_count"]
        }
        for name, stats in early_stats.items()
        if stats["total_count"] > 0
    }

def analyze_late_arrivals(data):
    """Calculate late arrival statistics for each user"""
    try:
        late_stats = {}
        
        for entry in data:
            # Normalize status and skip non-work entries
            status = normalize_status(entry['status'])
            if status not in ["in_office", "remote"]:
                continue

            try:
                # Parse time properly
                time = datetime.strptime(entry["time"], "%H:%M").time()
                name = entry["name"]
                
                # Initialize stats for new users
                if name not in late_stats:
                    late_stats[name] = {
                        "late_count": 0,
                        "total_days": 0
                    }
                
                # Count total workdays
                late_stats[name]["total_days"] += 1
                
                # Count late arrivals (after 9:00)
                if time >= datetime.strptime("09:00", "%H:%M").time():
                    late_stats[name]["late_count"] += 1
                    
            except (ValueError, KeyError) as e:
                app.logger.warning(f"Error processing entry time: {entry.get('time', 'unknown')}, Error: {str(e)}")
                continue
        
        # Calculate percentages for users with data
        result = {}
        for name, stats in late_stats.items():
            if stats["total_days"] > 0:
                result[name] = {
                    "late_percentage": round((stats["late_count"] / stats["total_days"]) * 100, 1),
                    "total_days": stats["total_days"],
                    "late_count": stats["late_count"]
                }
        
        return result
        
    except Exception as e:
        app.logger.error(f"Error in late arrival analysis: {str(e)}")
        return {}

def calculate_daily_activity(data):
    activity = {}
    for entry in data:
        date = entry["date"]
        if date not in activity:
            activity[date] = {
                "total": 0,
                "in_office": 0,
                "remote": 0
            }
        
        activity[date]["total"] += 1
        status = normalize_status(entry["status"])  # Fix: Normalize status
        if status in ["in_office", "remote"]:
            activity[date][status] += 1
    
    return activity

def calculate_user_comparison(data):
    user_stats = {}
    
    for entry in data:
        try:
            status = normalize_status(entry['status'])
            name = entry["name"]
            if name not in user_stats:
                user_stats[name] = {
                    "total_days": 0,
                    "in_office_days": 0,
                    "remote_days": 0,
                    "early_arrivals": 0,
                    "average_arrival_time": [],
                    "points": 0
                }
            
            stats = user_stats[name]
            stats["total_days"] += 1
            
            if status == "in_office":
                stats["in_office_days"] += 1
                arrival_time = datetime.strptime(entry["time"], "%H:%M").time()
                stats["average_arrival_time"].append(arrival_time.hour * 60 + arrival_time.minute)
                if arrival_time.hour < 9:
                    stats["early_arrivals"] += 1
            elif status == "remote":
                stats["remote_days"] += 1
        except (ValueError, KeyError):
            continue
    
    # Calculate averages and percentages
    for stats in user_stats.values():
        if stats["total_days"] > 0:
            stats["in_office_percentage"] = (stats["in_office_days"] / stats["total_days"]) * 100
            stats["remote_percentage"] = (stats["remote_days"] / stats["total_days"]) * 100
            stats["early_arrival_percentage"] = (stats["early_arrivals"] / stats["total_days"]) * 100
            
            if stats["average_arrival_time"]:
                avg_minutes = sum(stats["average_arrival_time"]) / len(stats["average_arrival_time"])
                avg_hour = int(avg_minutes // 60)
                avg_minute = int(avg_minutes % 60)
                stats["average_arrival_time"] = f"{avg_hour:02d}:{avg_minute:02d}"
            else:
                stats["average_arrival_time"] = "N/A"
            
            stats.pop("average_arrival_time", None)
    
    return user_stats

# Add other calculation functions...

def calculate_trends(data):
    """Calculate various trends for visualization"""
    trends = {
        "arrival_patterns": {},
        "points_distribution": {},
        "streaks": {},
        "status_counts": {
            "in_office": 0,
            "remote": 0,
            "sick": 0,
            "leave": 0
        }
    }
    
    # Calculate trends...
    return trends

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

def period_filter(entry, period):
    """Filter entries by period relative to current date"""
    try:
        entry_date = datetime.strptime(entry["date"], '%Y-%m-%d').date()
        now = datetime.now().date()
        
        if period == "today":
            return entry_date == now
        elif period == "week":
            # Get Monday of current week
            week_start = now - timedelta(days=now.weekday())
            return week_start <= entry_date <= now
        elif period == "month":
            # Start of current month
            month_start = now.replace(day=1)
            return month_start <= entry_date <= now
        else:
            return True  # "all" period
    except (ValueError, AttributeError):
        return False

@app.route("/maintenance")
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
        
        return render_template(
            "maintenance.html",
            monitoring_logs=monitoring_logs,
            current_page=page,
            total_pages=total_pages,
            per_page=per_page
        )
    finally:
        db.close()

@app.route("/export-data")
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

@app.route("/import-data", methods=["POST"])
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

@app.route("/clear-database", methods=["POST"])
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

# Move all initialization code to the bottom
def initialize_app():
    """Initialize the application and run all necessary migrations"""
    try:
        # Initialize NLTK
        app.logger.info("Initializing NLTK...")
        try:
            import nltk
            nltk.download('punkt', quiet=True)
            nltk.download('stopwords', quiet=True)
            nltk.download('wordnet', quiet=True)
            app.logger.info("NLTK data downloaded successfully")
        except Exception as e:
            app.logger.warning(f"NLTK initialization error: {str(e)}")
            app.logger.warning("Chatbot functionality may be limited")
        
        # Run migrations first
        app.logger.info("Running database migrations...")
        from migrations.run_migrations import run_migrations
        run_migrations()
        app.logger.info("Database migrations completed successfully")
        
        # Create any missing tables
        app.logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        app.logger.info("Database tables created successfully")
        
        # Initialize settings
        app.logger.info("Initializing application settings...")
        init_settings()
        app.logger.info("Application initialization completed successfully")
        
    except ImportError as e:
        app.logger.error(f"Failed to import migrations module: {str(e)}")
        # Continue with basic initialization if migrations fail
        Base.metadata.create_all(bind=engine)
        init_settings()
    except Exception as e:
        app.logger.error(f"Error during application initialization: {str(e)}")
        raise

# Call initialization at the bottom
initialize_app()

@app.route("/streaks")
@login_required
def view_streaks():
    """View streaks for all users"""
    db = SessionLocal()
    try:
        streaks = db.query(UserStreak).all()
        max_streak = max((s.current_streak for s in streaks), default=0)
        return render_template("streaks.html", streaks=streaks, max_streak=max_streak)
    finally:
        db.close()

def update_user_streak(username, attendance_date):
    """Update streak for a user based on new attendance"""
    db = SessionLocal()
    try:
        streak = db.query(UserStreak).filter_by(username=username).first()
        if not streak:
            streak = UserStreak(username=username)
            db.add(streak)

        # Convert attendance_date to datetime if it's a string
        if isinstance(attendance_date, str):
            attendance_date = datetime.strptime(attendance_date, '%Y-%m-%d')
        
        # Ensure we're comparing dates, not mixing date and datetime
        attendance_date = attendance_date.date() if isinstance(attendance_date, datetime) else attendance_date

        if streak.last_attendance:
            # Convert last_attendance to date for comparison
            last_date = streak.last_attendance.date()
            days_diff = (attendance_date - last_date).days
            if days_diff <= 3:  # Allow for weekends
                streak.current_streak += 1
            else:
                streak.current_streak = 1
        else:
            streak.current_streak = 1

        # Store the full datetime
        streak.last_attendance = datetime.combine(attendance_date, datetime.min.time())
        streak.max_streak = max(streak.max_streak, streak.current_streak)
        db.commit()
    finally:
        db.close()

def generate_streaks():
    """
    Legacy method kept for fallback but logs warning.
    Streaks are now primarily managed by the monitoring container.
    """
    app.logger.warning("generate_streaks() called directly. Streaks are now managed by monitoring container.")
    db = SessionLocal()
    try:
        # Only generate if no recent update (within last 5 minutes)
        latest_streak = db.query(UserStreak).order_by(UserStreak.last_attendance.desc()).first()
        if latest_streak and latest_streak.last_attendance:
            if datetime.now() - latest_streak.last_attendance < timedelta(minutes=5):
                app.logger.info("Skipping streak generation - recent update exists")
                return
        
        # Call original streak generation logic
        _generate_streaks_impl(db)
        
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error generating streaks: {str(e)}")
    finally:
        db.close()

def _generate_streaks_impl(db):
    """Implementation of streak generation, kept for fallback"""
    # ... existing streak generation code ...

def calculate_scores(data, period, current_date):
    """Calculate scores with proper handling of early-bird/last-in modes"""
    settings = load_settings()
    filtered_data = [entry for entry in data if in_period(entry, period, current_date)]
    mode = request.args.get('mode', 'last-in')
    
    # Group entries by date first
    daily_entries = {}
    daily_scores = {}
    
    for entry in filtered_data:
        date = entry["date"]
        if date not in daily_entries:
            daily_entries[date] = []
        daily_entries[date].append(entry)
    
    # Calculate scores for each day
    for date, entries in daily_entries.items():
        # Sort entries by time (always ascending)
        entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
        
        total_entries = len(entries)
        for position, entry in enumerate(entries, 1):
            name = entry["name"]
            if name not in daily_scores:
                daily_scores[name] = {
                    "early_bird_total": 0,
                    "last_in_total": 0,
                    "active_days": 0,
                    "base_points_total": 0,
                    "position_bonus_total": 0,
                    "streak_bonus_total": 0,
                    "stats": {
                        "in_office": 0,
                        "remote": 0,
                        "sick": 0,
                        "leave": 0,
                        "days": 0,
                        "latest_arrivals": 0,
                        "arrival_times": []
                    }
                }
            
            # Calculate scores for both modes
            scores = calculate_daily_score(entry, settings, position, total_entries, mode)
            
            status = entry["status"].replace("-", "_")
            daily_scores[name]["stats"][status] += 1
            daily_scores[name]["stats"]["days"] += 1
            
            if status in ["in_office", "remote"]:
                daily_scores[name]["active_days"] += 1
                daily_scores[name]["early_bird_total"] += scores["early_bird"]
                daily_scores[name]["last_in_total"] += scores["last_in"]
                daily_scores[name]["base_points_total"] += scores["base"]
                daily_scores[name]["position_bonus_total"] += scores["position_bonus"]
                daily_scores[name]["streak_bonus_total"] += scores["streak"]
                
                # Track achievements based on mode
                if (mode == 'last-in' and position == total_entries) or \
                   (mode == 'early-bird' and position == 1):
                    daily_scores[name]["stats"]["latest_arrivals"] += 1
                
                arrival_time = datetime.strptime(entry["time"], "%H:%M")
                daily_scores[name]["stats"]["arrival_times"].append(arrival_time)
    
    # Format rankings
    rankings = []
    for name, scores in daily_scores.items():
        if scores["active_days"] > 0:
            early_bird_avg = scores["early_bird_total"] / scores["active_days"]
            last_in_avg = scores["last_in_total"] / scores["active_days"]
            arrival_times = scores["stats"]["arrival_times"]
            
            rankings.append({
                "name": name,
                "score": round(last_in_avg if mode == 'last-in' else early_bird_avg, 2),
                "streak": calculate_current_streak(name),
                "stats": scores["stats"],
                "average_arrival_time": calculate_average_time(arrival_times) if arrival_times else "N/A",
                "base_points": scores["base_points_total"] / scores["active_days"] if scores["active_days"] > 0 else 0,
                "position_bonus": scores["position_bonus_total"] / scores["active_days"] if scores["active_days"] > 0 else 0,
                "streak_bonus": scores["streak_bonus_total"] / scores["active_days"] if scores["active_days"] > 0 else 0
            })
    
    # Always sort by descending score (scores are already mode-specific)
    rankings.sort(key=lambda x: x["score"], reverse=True)
    return rankings

def calculate_average_time(times):
    """Calculate average time from a list of datetime objects"""
    if not times:
        return "N/A"
    
    try:
        total_minutes = sum((t.hour * 60 + t.minute) for t in times)
        avg_minutes = total_minutes // len(times)
        avg_hour = avg_minutes // 60
        avg_minute = avg_minutes % 60
        return f"{avg_hour:02d}:{avg_minute:02d}"
    except (AttributeError, TypeError):
        return "N/A"

def format_date_range(start_date, end_date, period):
    """Format date range for display in rankings"""
    try:
        if (period == 'day'):
            return start_date.strftime('%d/%m/%Y')
        elif (period == 'week'):
            return f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"
        elif (period == 'month'):
            return start_date.strftime('%B %Y')
        else:
            return start_date.strftime('%d/%m/%Y')
    except (AttributeError, ValueError) as e:
        app.logger.error(f"Error formatting date range: {str(e)}")
        return "Invalid date range"

from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
import nltk

# Download required NLTK data
nltk.download('punkt')
nltk.download('stopwords')
nltk.download('wordnet')

from datetime import datetime, timedelta
from sqlalchemy import or_, and_, func
import re

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

def extract_query_parameters(tokens):
    """Extract key parameters from tokenized query"""
    params = {
        "date_range": None,
        "users": [],
        "status": None,
        "metric": None,
        "limit": None
    }
    
    # Get core users for reference
    core_users = get_core_users()
    
    # Scan for users
    for token in tokens:
        if token.capitalize() in core_users:
            params["users"].append(token.capitalize())
    
    # Look for date references
    date_keywords = ["today", "yesterday", "last week", "this week", "last month", "this month"]
    for kw in date_keywords:
        if kw in " ".join(tokens):
            params["date_range"] = kw
            break
    
    # Detect status
    status_map = {
        "office": "in-office",
        "remote": "remote",
        "sick": "sick",
        "leave": "leave",
        "holiday": "leave",
        "working": "in-office"
    }
    for token in tokens:
        if token in status_map:
            params["status"] = status_map[token]
    
    # Detect metrics
    metrics = {
        "points": ["points", "score", "ranking"],
        "streak": ["streak", "consecutive"],
        "attendance": ["attendance", "present", "in"],
        "time": ["time", "arrival", "late", "early"]
    }
    for metric, keywords in metrics.items():
        if any(kw in tokens for kw in keywords):
            params["metric"] = metric
    
    # Look for limits
    for i, token in enumerate(tokens):
        if token.isdigit() and i > 0 and tokens[i-1] in ["top", "best", "first"]:
            params["limit"] = int(token)
    
    return params

def build_dynamic_query(db, params):
    """Build a dynamic database query based on parameters"""
    query = db.query(Entry)
    
    # Apply date range filter
    if params["date_range"]:
        date = parse_date_reference(params["date_range"])
        if "week" in params["date_range"]:
            start_date = date - timedelta(days=date.weekday())
            end_date = start_date + timedelta(days(6))
            query = query.filter(Entry.date.between(start_date.isoformat(), end_date.isoformat()))
        elif "month" in params["date_range"]:
            start_date = date.replace(day=1)
            if "last" in params["date_range"]:
                start_date = (start_date - timedelta(days(1)).replace(day=1))
            next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
            query = query.filter(Entry.date.between(start_date.isoformat(), (next_month - timedelta(days=1)).isoformat()))
        else:
            query = query.filter(Entry.date == date.isoformat())
    
    # Apply user filter
    if params["users"]:
        query = query.filter(Entry.name.in_(params["users"]))
    
    # Apply status filter
    if params["status"]:
        query = query.filter(Entry.status == params["status"])
    
    # Apply limit
    if params["limit"]:
        query = query.limit(params["limit"])
    
    return query

from fuzzywuzzy import fuzz, process
from collections import defaultdict
import re

# Add these new classes for better query handling
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
        tokens = word_tokenize(query.lower())
        
        # Detect follow-up questions
        if self._is_followup_question(query, context):
            return self._handle_followup(query, context)
        
        # Identify main intent
        intent = self._classify_intent(query, tokens)
        
        # Extract parameters
        params = self._extract_parameters(query, tokens, context)
        
        # Update context
        context.last_query = query
        context.current_topic = intent.type
        context.mentioned_users.extend(params.get('users', []))
        
        return intent, params
    
    def _is_followup_question(self, query, context):
        follow_up_indicators = [
            'what about',
            'how about',
            'and',
            'what else',
            'who else',
            'then',
            'also'
        ]
        return any(indicator in query.lower() for indicator in follow_up_indicators)
    
    def _handle_followup(self, query, context):
        # Inherit context from previous query
        intent = self._classify_intent(query, word_tokenize(query.lower()))
        params = context.follow_up_context.copy()
        
        # Update parameters based on new query
        new_params = self._extract_parameters(query, word_tokenize(query.lower()), context)
        params.update(new_params)
        
        return intent, params
    
    def _classify_intent(self, query, tokens):
        scores = {}
        for intent, pattern in self.intent_patterns.items():
            if re.search(pattern, query.lower()):
                scores[intent] = fuzz.ratio(pattern, query.lower())
        
        if not scores:
            return QueryIntent('status', 0.5)  # Default to status check
            
        best_intent = max(scores.items(), key=lambda x: x[1])
        return QueryIntent(best_intent[0], best_intent[1] / 100)
    
    def _extract_parameters(self, query, tokens, context):
        """Extract parameters with null checks"""
        if not query:
            query = ""
        if not tokens:
            tokens = []
            
        params = {
            'users': self._extract_users(query),
            'date_range': self._extract_date_range(query),
            'status': self._extract_status(query),
            'metrics': self._extract_metrics(query),
            'limit': self._extract_limit(query),
            'comparison_type': self._extract_comparison_type(query),
            'sort': self._extract_sort_preference(query)
        }
        
        # Add temporal context
        if 'yesterday' in query:
            params['temporal_context'] = 'past'
        elif 'tomorrow' in query:
            params['temporal_context'] = 'future'
        
        return params

    def _extract_users(self, query):
        """Extract users with null check"""
        if not query:
            return []
            
        core_users = get_core_users()
        mentioned_users = []
        
        # Direct matches
        for user in core_users:
            if user.lower() in query.lower():
                mentioned_users.append(user)
        
        # Fuzzy matches
        if not mentioned_users:
            words = query.split()
            for word in words:
                matches = process.extractBests(word, core_users, score_cutoff=80)
                mentioned_users.extend([match[0] for match in matches])
        
        return list(set(mentioned_users))

    def _extract_date_range(self, query):
        """Extract date range with null check"""
        if not query:
            return None
            
        # First check for explicit ranges
        range_patterns = {
            'today': r'\btoday\b',
            'yesterday': r'\byesterday\b',
            'this week': r'\bthis\s+week\b',
            'last week': r'\blast\s+week\b',
            'this month': r'\bthis\s+month\b',
            'last month': r'\blast\s+month\b'
        }
        
        for range_type, pattern in range_patterns.items():
            if re.search(pattern, query, re.IGNORECASE):
                return range_type
        
        # Check for specific date mentions
        date_pattern = r'(?:on|for|at)\s+(\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?))'
        match = re.search(date_pattern, query, re.IGNORECASE)
        if match:
            return match.group(1)
        
        return None

    def _extract_status(self, query):
        """Extract status type from query"""
        status_patterns = {
            'in-office': r'\b(?:in|at)\s*(?:the\s*)?office\b',
            'remote': r'\b(?:remote|wfh|home)\b',
            'sick': r'\b(?:sick|ill|unwell)\b',
            'leave': r'\b(?:leave|holiday|vacation)\b'
        }
        
        for status, pattern in status_patterns.items():
            if re.search(pattern, query, re.IGNORECASE):
                return status
        return None

    def _extract_metrics(self, query):
        """Extract metrics to analyze"""
        metrics = []
        metric_patterns = {
            'attendance': r'\b(?:attend|present|here)\w*\b',
            'points': r'\b(?:point|score|ranking)\w*\b',
            'streak': r'\b(?:streak|consecutive|row)\w*\b',
            'time': r'\b(?:time|arrival|start)\w*\b'
        }
        
        for metric, pattern in metric_patterns.items():
            if re.search(pattern, query, re.IGNORECASE):
                metrics.append(metric)
        
        return metrics if metrics else ['attendance']  # Default to attendance

    def _extract_limit(self, query):
        """Extract numerical limits from query"""
        limit_pattern = r'\b(?:top|first|best|worst|bottom)\s+(\d+)\b'
        match = re.search(limit_pattern, query, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _extract_comparison_type(self, query):
        """Extract type of comparison requested"""
        if re.search(r'\b(?:compare|vs|versus|against)\b', query, re.IGNORECASE):
            if re.search(r'\b(?:time|arrival)\b', query, re.IGNORECASE):
                return 'time'
            elif re.search(r'\b(?:point|score)\b', query, re.IGNORECASE):
                return 'points'
            elif re.search(r'\b(?:streak)\b', query, re.IGNORECASE):
                return 'streaks'
        return None

    def _extract_sort_preference(self, query):
        """Extract sorting preference"""
        if re.search(r'\b(?:earliest|first|early)\b', query, re.IGNORECASE):
            return 'asc'
        elif re.search(r'\b(?:latest|last|late)\b', query, re.IGNORECASE):
            return 'desc'
        return None

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
                func.avg(func.extract('hour', func.cast(Entry.time, Time))).label('avg_arrival')
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
        self.last_entities = {}
        self.topic_stack = []
        self.suggestion_context = {}

    def add_message(self, message, is_user=True):
        self.messages.append({
            'content': message,
            'timestamp': datetime.now(),
            'is_user': is_user
        })
        if len(self.messages) > 10:  # Keep last 10 messages
            self.messages.pop(0)

    def get_context(self):
        if not self.messages:
            return None
        return {
            'last_message': self.messages[-1]['content'],
            'topic': self.current_context,
            'entities': self.last_entities,
            'suggestions': self.suggestion_context
        }

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

# Update chatbot route to use enhanced processor
@app.route("/chatbot", methods=["POST"])
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

def calculate_streak_for_date(name, target_date, db):
    """Calculate streak for a user up to a specific date with optimized queries"""
    try:
        target_date = datetime.strptime(target_date, '%Y-%m-%d').date() if isinstance(target_date, str) else target_date
        
        # Get all relevant entries in a single query
        entries = db.query(
            Entry.date
        ).filter(
            Entry.name == name,
            Entry.status.in_(['in-office', 'remote']),
            Entry.date <= target_date.isoformat()
        ). order_by(
            Entry.date.desc()
        ).all()
        
        if not entries:
            return 0
            
        streak = 1
        last_date = datetime.strptime(entries[0].date, "%Y-%m-%d").date()
        
        # Process dates without additional database queries
        dates = [datetime.strptime(entry.date, "%Y-%m-%d").date() for entry in entries[1:]]
        for entry_date in dates:
            days_between = (last_date - entry_date).days
            
            if days_between > 3:  # More than a weekend
                break
            elif days_between > 1:
                # Check if gap only includes weekend days
                weekend_only = all(
                    (last_date - timedelta(days=d)).weekday() >= 5
                    for d in range(1, days_between)
                )
                if not weekend_only:
                    break
            
            streak += 1
            last_date = entry_date
            
        return streak
        
    except Exception as e:
        app.logger.error(f"Error calculating streak: {str(e)}")
        return 0

@app.route("/missing-entries")
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
                            back_link=url_for('index'))
    finally:
        db.close()
        
# Add periodic metrics update
def start_metrics_updater():
    def update_loop():
        while True:
            try:
                update_prometheus_metrics()
            except Exception as e:
                app.logger.error(f"Error updating metrics: {str(e)}")
            time.sleep(300)  # Update every 5 minutes

    thread = Thread(target=update_loop, daemon=True)
    thread.start()
        
if __name__ "__main__":
    # Remove the start_http_server call as we're using WSGI middleware now
    start_metrics_updater()  # Start metrics updater
    debug_mode = os.getenv('FLASK_ENV') == 'development'
    
    app.run(
        host=os.getenv('FLASK_HOST', '0.0.0.0'),
        port=int(os.getenv('FLASK_PORT', '9000')),
        debug=debug_mode
    )

@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html',
                         error="Page Not Found",
                         details="The requested page could not be found.",
                         back_link=url_for('index')), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html',
                         error="Internal Server Error",
                         details="An unexpected error has occurred.",
                         back_link=url_for('index')), 500

# Prometheus metrics
REQUEST_TIME = Summary('request_processing_seconds', 'Time spent processing request')
REQUEST_COUNT = Counter('request_count', 'Total request count')
IN_PROGRESS = Gauge('in_progress_requests', 'In-progress requests')
ATTENDANCE_COUNT = Counter('attendance_count_total', 'Total attendance records', ['status'])
USER_STREAK = Gauge('user_streak_days', 'Current streak for user', ['username'])
POINTS_GAUGE = Gauge('user_points', 'Current points for user', ['username', 'period'])
RESPONSE_TIME = Histogram('response_time_seconds', 'Response time in seconds', ['endpoint'])
AUDIT_ACTIONS = Counter('audit_actions_total', 'Total audit actions', ['action'])
ARRIVAL_TIME = Histogram('arrival_time_hours', 'Arrival time distribution', ['username'])

def update_prometheus_metrics():
    db = SessionLocal()
    try:
        # Update attendance_count_total by status
        statuses = ['in-office','remote','sick','leave']
        for s in statuses:
            count = db.query(Entry).filter(Entry.status == s).count()
            ATTENDANCE_COUNT.labels(status=s).set(count)

        # Update user_streak_days
        streaks = db.query(UserStreak).all()
        for us in streaks:
            USER_STREAK.labels(username=us.username).set(us.current_streak)

        # Update user_points (placeholder for example)
        # ...retrieve and calculate user points from DB or cache...
        # POINTS_GAUGE.labels(username=some_username, period="daily").set(some_points)

        # Update arrival_time_hours histogram
        # ...retrieve times from DB, compute hour, and observe...
        # ARRIVAL_TIME.labels(username=some_username).observe(hour_of_arrival)

        # ...existing code...
    finally:
        db.close()
# ...existing code...

@app.before_request
def before_request():
    request.start_time = time.time()
    IN_PROGRESS.inc()

@app.after_request
def after_request(response):
    IN_PROGRESS.dec()
    if hasattr(request, 'start_time'):
        duration = time.time() - request.start_time
        record_request_metric(
            method=request.method,
            endpoint=request.endpoint,
            duration=duration
        )
    return response

# Update the database session to track connections
@event.listens_for(engine, "checkout")
def receive_checkout(dbapi_connection, connection_record, connection_proxy):
    DB_CONNECTIONS.inc()

@event.listens_for(engine, "checkin")
def receive_checkin(dbapi_connection, connection_record):
    DB_CONNECTIONS.dec()

# Add a new endpoint to handle loading and saving rules
@app.route("/api/rules", methods=["GET", "POST"])
@login_required  # Add login requirement
def handle_rules():
    db = SessionLocal()
    try:
        if request.method == "GET":
            settings = db.query(Settings).first()
            return jsonify(settings.points.get("rules", []))
        else:
            new_rules = request.json.get("rules", [])
            settings = db.query(Settings).first()
            points = settings.points
            points["rules"] = new_rules
            settings.points = points
            db.commit()
            return jsonify({"status": "ok"})
    finally:
        db.close()

# Add performance monitoring for database operations
def record_db_timing(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = f(*args, **kwargs)
        duration = time.time() - start_time
        DB_OPERATION_TIME.labels(
            operation=f.__name__
        ).observe(duration)
        return result
    return wrapper

# Add new metric for DB operation timing
DB_OPERATION_TIME = Histogram(
    'db_operation_seconds',
    'Time spent in database operations',
    ['operation']
)

@record_db_timing
def load_data():
    """Monitor database load operations"""
    db = SessionLocal()
    entries = db.query(Entry).all()
    data = [
        {
            "id": entry.id,
            "date": entry.date,
            "time": entry.time,
            "name": entry.name,
            "status": entry.status,
            "timestamp": entry.timestamp.isoformat()
        }
        for entry in entries
    ]
    db.close()
    return data

@app.route("/health")
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

# ...existing code...

@app.route("/tie-breakers")
@login_required
def tie_breakers():
    db = SessionLocal()
    try:
        # Updated query to use correct column names
        tie_breakers = db.execute(text("""
            WITH tie_breakers_cte AS (
                SELECT 
                    t.id,
                    t.period,
                    t.period_start,
                    t.period_end,
                    t.points,
                    t.status,
                    t.created_at,
                    t.resolved_at,
                    jsonb_agg(jsonb_build_object(
                        'username', tp.username,
                        'game_choice', tp.game_choice,
                        'ready', tp.ready,
                        'winner', tp.winner
                    )) as participants,
                    (
                        SELECT jsonb_build_object(
                            'id', g.id,
                            'player1', g.player1,
                            'player2', g.player2
                        )
                        FROM tie_breaker_games g
                        WHERE g.tie_breaker_id = t.id
                        AND g.winner IS NULL
                        LIMIT 1
                    ) as current_game
                FROM tie_breakers t
                JOIN tie_breaker_participants tp ON t.id = tp.tie_breaker_id
                WHERE t.status != 'completed'
                GROUP BY t.id
            )
            SELECT * FROM tie_breakers_cte
            ORDER BY created_at DESC
        """)).fetchall()
        
        return render_template(
            "tie_breakers.html",
            tie_breakers=tie_breakers,
            current_user=session['user']
        )
    finally:
        db.close()

@app.route("/tie-breakers/<int:tie_id>/choose-game", methods=["POST"])
@login_required
def choose_game(tie_id):
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
            SELECT COUNT(*) = COUNT(CASE WHEN ready THEN 1 END)
            FROM tie_breaker_participants
            WHERE tie_breaker_id = :tie_id
        """), {"tie_id": tie_id}).scalar()

        if all_ready:
            # Start the first game and update status
            create_next_game(db, tie_id)
            # Update tie breaker status to in_progress
            db.execute(text("""
                UPDATE tie_breakers
                SET status = 'in_progress'
                WHERE id = :tie_id
            """), {"tie_id": tie_id})

        db.commit()
        return redirect(url_for('tie_breakers'))
    finally:
        db.close()

@app.route("/games/<int:game_id>")
@login_required
def play_game(game_id):
    db = SessionLocal()
    try:
        game = db.execute(text("""
            SELECT g.*, t.period, t.period_end
            FROM tie_breaker_games g
            JOIN tie_breakers t ON g.tie_breaker_id = t.id
            WHERE g.id = :game_id
        """), {"game_id": game_id}).fetchone()

        if not game:
            return "Game not found", 404

        if session['user'] not in [game.player1, game.player2]:
            return "You are not part of this game", 403

        template = f"games/{game.game_type}.html"
        return render_template(
            template,
            game=game,
            current_user=session['user']
        )
    finally:
        db.close()

def create_next_game(db, tie_id):
    """Create the next game between participants"""
    # Get participants who haven't played against each other
    players = db.execute(text("""
        WITH played_pairs AS (
            SELECT player1, player2
            FROM tie_breaker_games
            WHERE tie_breaker_id = :tie_id
        )
        SELECT p1.username as player1, p2.username as player2
        FROM tie_breaker_participants p1
        CROSS JOIN tie_breaker_participants p2
        WHERE p1.tie_breaker_id = :tie_id
        AND p2.tie_breaker_id = :tie_id
        AND p1.username < p2.username
        AND NOT EXISTS (
            SELECT 1 FROM played_pairs pp
            WHERE (pp.player1 = p1.username AND pp.player2 = p2.username)
            OR (pp.player1 = p2.username AND pp.player2 = p1.username)
        )
        LIMIT 1
    """), {"tie_id": tie_id}).fetchone()

    if players:
        # Create new game with only player1, waiting for player2 to join
        game_type = db.execute(text("""
            SELECT game_choice
            FROM tie_breaker_participants
            WHERE tie_breaker_id = :tie_id
            AND username = :p1
            LIMIT 1
        """), {
            "tie_id": tie_id,
            "p1": players.player1
        }).scalar()

        # Create new game in pending state
        db.execute(text("""
            INSERT INTO tie_breaker_games (
                tie_breaker_id, game_type, player1, game_state, status
            ) VALUES (
                :tie_id, :game_type, :p1, 
                :initial_state,
                'pending'
            )
        """), {
            "tie_id": tie_id,
            "game_type": game_type,
            "p1": players.player1,
            "initial_state": json.dumps({
                "board": [None] * (9 if game_type == 'tictactoe' else 42),
                "current_player": players.player1,
                "moves": []
            })
        })

@app.route("/games/<int:game_id>/join", methods=["POST"])
@login_required
def join_game(game_id):
    db = SessionLocal()
    try:
        game = db.execute(text("""
            SELECT * FROM tie_breaker_games WHERE id = :game_id
        """), {"game_id": game_id}).fetchone()

        if not game or game.player2:
            return jsonify({"error": "Cannot join this game"}), 400

        # Add player2 and change status to active
        db.execute(text("""
            UPDATE tie_breaker_games 
            SET player2 = :player2,
                status = 'active'
            WHERE id = :game_id
            AND player2 IS NULL
        """), {
            "player2": session['user'],
            "game_id": game_id
        })

        db.commit()
        return redirect(url_for('play_game', game_id=game_id))
    finally:
        db.close()

@app.route("/games/<int:game_id>")
@login_required
def play_game(game_id):
    db = SessionLocal()
    try:
        game = db.execute(text("""
            SELECT g.*, t.period, t.period_end
            FROM tie_breaker_games g
            JOIN tie_breakers t ON g.tie_breaker_id = t.id
            WHERE g.id = :game_id
        """), {"game_id": game_id}).fetchone()

        if not game:
            return "Game not found", 404

        # Only allow active games to be played
        if game.status != 'active':
            if session['user'] in [game.player1, game.player2]:
                return "Waiting for other player to join", 202
            return "This game hasn't started yet", 400

        template = f"games/{game.game_type}.html"
        return render_template(
            template,
            game=game,
            current_user=session['user']
        )
    finally:
        db.close()

@app.route("/games/<int:game_id>/move", methods=["POST"])
@login_required
def make_move(game_id):
    """Handle game moves"""
    db = SessionLocal()
    try:
        game = db.execute(text("""
            SELECT * FROM tie_breaker_games WHERE id = :game_id
        """), {"game_id": game_id}).fetchone()

        if not game or session['user'] != game.current_player:
            return jsonify({"error": "Invalid move"}), 400

        move = request.json.get('move')
        game_state = game.game_state
        
        if not is_valid_move(game_state, move, game.game_type):
            return jsonify({"error": "Invalid move"}), 400

        # Apply move
        game_state = apply_move(game_state, move, session['user'], game.game_type)
        
        # Check for winner
        winner = check_winner(game_state, game.game_type)
        
        # Update game state
        db.execute(text("""
            UPDATE tie_breaker_games 
            SET game_state = :state,
                winner = :winner,
                completed_at = CASE WHEN :winner IS NOT NULL THEN NOW() ELSE NULL END
            WHERE id = :game_id
        """), {
            "state": json.dumps(game_state),
            "winner": winner,
            "game_id": game_id
        })

        if winner:
            # Update participant status
            db.execute(text("""
                UPDATE tie_breaker_participants
                SET winner = (username = :winner)
                WHERE tie_breaker_id = :tie_id
                AND username IN (:p1, :p2)
            """), {
                "winner": winner,
                "tie_id": game.tie_breaker_id,
                "p1": game.player1,
                "p2": game.player2
            })

            # Create next game if needed
            create_next_game(db, game.tie_breaker_id)

            # Check if tie breaker is complete
            remaining_games = db.execute(text("""
                SELECT COUNT(*) FROM tie_breaker_games
                WHERE tie_breaker_id = :tie_id
                AND winner IS NULL
            """), {"tie_id": game.tie_breaker_id}).scalar()

            if remaining_games == 0:
                # Determine overall winner and award point
                winner = db.execute(text("""
                    SELECT username FROM tie_breaker_participants
                    WHERE tie_breaker_id = :tie_id
                    AND winner = true
                    LIMIT 1
                """), {"tie_id": game.tie_breaker_id}).scalar()

                if winner:
                    # Update tie breaker status
                    db.execute(text("""
                        UPDATE tie_breakers
                        SET status = 'completed',
                            resolved_at = NOW()
                        WHERE id = :tie_id
                    """), {"tie_id": game.tie_breaker_id})

                    # Award bonus point to winner
                    log_audit(
                        "tie_breaker_resolved",
                        session['user'],
                        f"Tie breaker resolved with winner: {winner}",
                        old_data={"tie_id": game.tie_breaker_id},
                        new_data={"winner": winner}
                    )

        db.commit()
        return jsonify({
            "status": "success",
            "state": game_state,
            "winner": winner
        })
    finally:
        db.close()

def is_valid_move(game_state, move, game_type):
    """Validate move based on game type"""
    board = game_state['board']
    
    if game_type == 'tictactoe':
        return 0 <= move < 9 and board[move] is None
    elif game_type == 'connect4':
        if not (0 <= move < 7):  # Check column is valid
            return False
        # Find first empty spot in column
        for i in range(35, -1, -7):  # Check from bottom up
            if board[i + move] is None:
                return True
        return False
    return False

def apply_move(game_state, move, player, game_type):
    """Apply move to game state"""
    board = game_state['board']
    moves = game_state['moves']
    
    if game_type == 'tictactoe':
        board[move] = player
    elif game_type == 'connect4':
        # Find first empty spot in column
        for i in range(35, -1, -7):
            if board[i + move] is None:
                board[i + move] = player
                break
                
    moves.append({
        "player": player,
        "position": move,
        "timestamp": datetime.now().isoformat()
    })
    
    # Update current player
    current_player = game_state['current_player']
    next_player = moves[0]['player'] if current_player == moves[1]['player'] else moves[1]['player']
    
    return {
        "board": board,
        "moves": moves,
        "current_player": next_player
    }

def check_winner(game_state, game_type):
    """Check for winner based on game type"""
    board = game_state['board']
    
    if game_type == 'tictactoe':
        # Check rows, columns and diagonals
        lines = [
            [0,1,2], [3,4,5], [6,7,8],  # rows
            [0,3,6], [1,4,7], [2,5,8],  # columns
            [0,4,8], [2,4,6]  # diagonals
        ]
        
        for line in lines:
            if (board[line[0]] is not None and
                board[line[0]] == board[line[1]] == board[line[2]]):
                return board[line[0]]
                
    elif game_type == 'connect4':
        # Check horizontal
        for row in range(6):
            for col in range(4):
                idx = row * 7 + col
                if (board[idx] is not None and
                    board[idx] == board[idx+1] == board[idx+2] == board[idx+3]):
                    return board[idx]
        
        # Check vertical
        for row in range(3):
            for col in range(7):
                idx = row * 7 + col
                if (board[idx] is not None and
                    board[idx] == board[idx+7] == board[idx+14] == board[idx+21]):
                    return board[idx]
        
        # Check diagonal (down-right)
        for row in range(3):
            for col in range(4):
                idx = row * 7 + col
                if (board[idx] is not None and
                    board[idx] == board[idx+8] == board[idx+16] == board[idx+24]):
                    return board[idx]
        
        # Check diagonal (down-left)
        for row in range(3):
            for col in range(3, 7):
                idx = row * 7 + col
                if (board[idx] is not None and
                    board[idx] == board[idx+6] == board[idx+12] == board[idx+18]):
                    return board[idx]
    
    return None

# Add WebSocket support for real-time game updates
from flask_socketio import SocketIO, emit, join_room, leave_room

socketio = SocketIO(app)

@socketio.on('join_game')
def on_join_game(data):
    """Join game room for real-time updates"""
    game_id = data['game_id']
    join_room(f'game_{game_id}')

@socketio.on('leave_game')
def on_leave_game(data):
    """Leave game room"""
    game_id = data['game_id']
    leave_room(f'game_{game_id}')

def notify_game_update(game_id, game_state, winner=None):
    """Notify players of game state changes"""
    emit('game_update', {
        'state': game_state,
        'winner': winner
    }, room=f'game_{game_id}')

if __name__ == "__main__":
    # ...existing code...
    socketio.run(app,
        host=os.getenv('FLASK_HOST', '0.0.0.0'),
        port=int(os.getenv('FLASK_PORT', '9000')),
        debug=debug_mode
    )
