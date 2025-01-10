from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, Float, JSON, event, text, Boolean, inspect
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta, date  # Add date import
import os
import uuid
import psycopg2
import psycopg2.extras  # Add this import
import json  # Add explicit json import

# Create Flask app first, before any route definitions
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'supersecretkey')

# Add this after the app creation and before route definitions
def today():
    """Return today's date for template use"""
    return datetime.now().date()

# Make today function available to all templates
@app.context_processor
def utility_processor():
    return {'today': today}

# Database setup - update engine configuration
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/championship')
engine = create_engine(
    DATABASE_URL,
    pool_size=20,  # Increase from default 5
    max_overflow=30,  # Increase from default 10
    pool_timeout=60,  # Increase from default 30
    pool_pre_ping=True,  # Enable connection health checks
    pool_recycle=3600  # Recycle connections after 1 hour
)

# Add connection pool logging
@event.listens_for(engine, "connect")
def connect(dbapi_connection, connection_record):
    app.logger.info("New database connection created")

@event.listens_for(engine, "checkout")
def receive_checkout(dbapi_connection, connection_record, connection_proxy):
    app.logger.debug("Database connection checked out from pool")

@event.listens_for(engine, "checkin")
def receive_checkin(dbapi_connection, connection_record):
    app.logger.debug("Database connection returned to pool")

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
            streak_bonus=0.5
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
            "rules": settings.points.get('rules', [])  # Add rules to response
        }
        return result
    finally:
        db.close()

def save_settings(settings_data):
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
                return {k: clean_value(v) for k, v in v.items()}
            if isinstance(v, (list, tuple)):
                return [clean_value(x) for x in v]
            return str(v)

        # Deep clean the data
        if old_data:
            old_data = {k: clean_value(v) for k, v in old_data.items()}
        if new_data:
            new_data = {k: clean_value(v) for k, v in new_data.items()}

        changes = None
        if action == "delete_entry":
            # For deletions, show all fields as being removed
            changes = [{
                "field": key,
                "old": value,
                "new": "None",
                "type": "deleted"
            } for key, value in old_data.items()]
        elif action == "log_attendance":
            # For new entries, show all fields as being added
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
                    if isinstance(old_value, dict) and isinstance(new_value, dict):
                        old_str = json.dumps(old_value, sort_keys=True)
                        new_str = json.dumps(new_value, sort_keys=True)
                        if old_str != new_str:
                            changes.append({
                                "field": key,
                                "old": old_value,
                                "new": new_value,
                                "type": "modified"
                            })
                    else:
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
        
        # Get filter parameters
        page = request.args.get('page', 1, type=int)
        per_page = 20
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
                update_user_streak(entry.name, entry.date)
        
        # Add this: Generate streaks for all users after new entry
        settings = db.query(Settings).first()
        if settings and settings.enable_streaks:
            generate_streaks()
        
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

def calculate_daily_score(entry, settings, position=None, total_entries=None, mode='last-in'):
    """Calculate score for a single day's entry with all bonuses"""
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
    
    # Calculate streak bonus
    streak_bonus = 0
    if settings.get("enable_streaks", False):
        streak = context['streak']  # Use the streak calculated for the specific date
        if streak > 0:
            streak_bonus = streak * settings.get("streak_multiplier", 0.5)
    
    return {
        "early_bird": context['current_points'] + early_bird_bonus + streak_bonus,
        "last_in": context['current_points'] + last_in_bonus - streak_bonus,
        "base": context['current_points'],
        "streak": streak_bonus,
        "position_bonus": last_in_bonus if mode == 'last-in' else early_bird_bonus,
        "breakdown": {
            "base_points": context['current_points'],
            "position_bonus": last_in_bonus if mode == 'last-in' else early_bird_bonus,
            "streak_bonus": streak_bonus
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

@app.route("/rankings/<period>")
@app.route("/rankings/<period>/<date_str>")
@login_required
def view_rankings(period, date_str=None):
    db = SessionLocal()
    try:
        mode = request.args.get('mode', 'last-in')
        app.logger.debug(f"Rankings request - Period: {period}, Date: {date_str}, Mode: {mode}")
        
        # Get current date (either from URL or today)
        try:
            if date_str:
                current_date = datetime.strptime(date_str, '%Y-%m-%d')
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
        # Get filter parameters
        page = int(request.args.get("page", 1))
        per_page = 20
        
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
        
        # Add this: Regenerate streaks after any modifications
        settings = db.query(Settings).first()
        if settings and settings.enable_streaks:
            generate_streaks()
        
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

    return {
        "points": normalized_points,
        "late_bonus": float(settings_dict.get("late_bonus", 0)),
        "remote_days": {
            user: sorted(days) for user, days in settings_dict.get("remote_days", {}).items()
        },
        "core_users": sorted(settings_dict.get("core_users", [])),
        "enable_streaks": bool(settings_dict.get("enable_streaks", False)),
        "streak_multiplier": float(settings_dict.get("streak_multiplier", 0.5))
    }

@app.route("/settings", methods=["GET", "POST"])
@login_required
def manage_settings():
    db = SessionLocal()
    try:
        if request.method == "GET":
            settings_data = load_settings()
            if not settings_data.get('monitoring_start_date'):
                settings_data['monitoring_start_date'] = datetime.now().replace(month=1, day=1).date()
            
            registered_users = [user[0] for user in db.query(User.username).all()]
            core_users = settings_data.get("core_users", [])
            
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
                old_settings = db.query(Settings).first()
                new_settings = request.json
                
                # Add monitoring_start_date to normalized settings
                normalized_settings = normalize_settings(new_settings)
                normalized_settings['monitoring_start_date'] = datetime.strptime(
                    new_settings.get('monitoring_start_date'),
                    '%Y-%m-%d'
                ).date()
                
                if old_settings:
                    # Update existing settings including monitoring_start_date
                    old_settings.monitoring_start_date = normalized_settings['monitoring_start_date']
                    old_settings.points = normalized_settings["points"]
                    old_settings.late_bonus = normalized_settings["late_bonus"]
                    old_settings.remote_days = normalized_settings["remote_days"]
                    old_settings.core_users = normalized_settings["core_users"]
                    old_settings.enable_streaks = normalized_settings["enable_streaks"]
                    old_settings.streak_multiplier = normalized_settings["streak_multiplier"]
                else:
                    # Create new settings
                    settings = Settings(**normalized_settings)
                    db.add(settings)
                
                db.commit()
                
                # Log the update
                log_audit(
                    "update_settings",
                    session['user'],
                    "Updated settings",
                    old_data=old_settings.__dict__ if old_settings else None,
                    new_data=normalized_settings
                )
                
                return jsonify({"message": "Settings updated successfully"})
                
            except (ValueError, TypeError, KeyError) as e:
                db.rollback()
                app.logger.error(f"Error managing settings: {str(e)}")
                return jsonify({"error": f"Invalid settings data: {str(e)}"}), 400
                
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error managing settings: {str(e)}")
        if request.method == "POST":
            return jsonify({
                "error": f"Server error: {str(e)}",
                "details": str(e)
            }), 500
        return render_template("error.html", 
                            error="Failed to load settings",
                            details=str(e),
                            back_link=url_for('index'))
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
    return render_template("visualisations.html", core_users=CORE_USERS)

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
            week_end = week_start + timedelta(days=6)
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
    return render_template("maintenance.html")

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
    """Generate all user streaks with optimized database queries"""
    db = SessionLocal()
    try:
        # Clear existing streaks in a single query
        db.query(UserStreak).delete()
        
        # Get all relevant entries in a single query
        entries = db.query(
            Entry.name,
            Entry.date,
            Entry.timestamp
        ).filter(
            Entry.status.in_(['in-office', 'remote'])
        ).order_by(
            Entry.name,
            Entry.date.desc()
        ).all()
        
        # Process entries by user
        current_user = None
        current_streak = 0
        max_streak = 0
        last_date = None
        new_streaks = []
        
        for entry in entries:
            if entry.name != current_user:
                # Save previous user's streak
                if current_user and current_streak > 0:
                    new_streaks.append(UserStreak(
                        username=current_user,
                        current_streak=current_streak,
                        max_streak=max_streak,
                        last_attendance=last_timestamp
                    ))
                # Reset for new user
                current_user = entry.name
                current_streak = 1
                max_streak = 1
                last_date = datetime.strptime(entry.date, "%Y-%m-%d").date()
                last_timestamp = entry.timestamp
                continue
            
            entry_date = datetime.strptime(entry.date, "%Y-%m-%d").date()
            days_between = (last_date - entry_date).days
            
            if days_between <= 3:  # Within streak range
                if days_between <= 1 or all(
                    (last_date - timedelta(days=d)).weekday() >= 5
                    for d in range(1, days_between)
                ):
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
            else:
                current_streak = 1
            
            last_date = entry_date
            last_timestamp = entry.timestamp
        
        # Don't forget the last user
        if current_user and current_streak > 0:
            new_streaks.append(UserStreak(
                username=current_user,
                current_streak=current_streak,
                max_streak=max_streak,
                last_attendance=last_timestamp
            ))
        
        # Bulk insert all streaks
        if new_streaks:
            db.bulk_save_objects(new_streaks)
            db.commit()
        
        app.logger.info(f"Generated streaks for {len(new_streaks)} users")
        
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error generating streaks: {str(e)}")
    finally:
        db.close()

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
            end_date = start_date + timedelta(days=6)
            query = query.filter(Entry.date.between(start_date.isoformat(), end_date.isoformat()))
        elif "month" in params["date_range"]:
            start_date = date.replace(day=1)
            if "last" in params["date_range"]:
                start_date = (start_date - timedelta(days=1)).replace(day=1)
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

        # Get missing entries with proper SQL query
        missing = db.execute(
            text("""
                SELECT 
                    missing_entries.date::date as date, 
                    missing_entries.checked_at 
                FROM missing_entries 
                WHERE missing_entries.date >= :start_date::date
                ORDER BY missing_entries.date DESC
            """),
            {"start_date": start_date.strftime('%Y-%m-%d')}
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
        
if __name__ == "__main__":
    app.run(debug=True)

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
