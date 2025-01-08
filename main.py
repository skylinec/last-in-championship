from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
import os
import uuid

# Database setup
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/championship')
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now)
    user = Column(String, nullable=False)
    action = Column(String, nullable=False)
    details = Column(String)
    changes = Column(JSON)

# Create tables
Base.metadata.create_all(bind=engine)

# Initialize default settings if not exists
def init_settings():
    db = SessionLocal()
    if not db.query(Settings).first():
        default_settings = Settings(
            points={
                "in_office": 10,
                "remote": 8,
                "sick": 5,
                "leave": 5
            },
            late_bonus=1,
            remote_days={}
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
    settings = db.query(Settings).first()
    if not settings:
        init_settings()
        settings = db.query(Settings).first()
    result = {
        "points": settings.points,
        "late_bonus": settings.late_bonus,
        "remote_days": settings.remote_days
    }
    db.close()
    return result

def save_settings(settings_data):
    db = SessionLocal()
    settings = db.query(Settings).first()
    if settings:
        settings.points = settings_data["points"]
        settings.late_bonus = settings_data["late_bonus"]
        settings.remote_days = settings_data["remote_days"]
    else:
        settings = Settings(**settings_data)
        db.add(settings)
    db.commit()
    db.close()

def log_audit(action, user, details, old_data=None, new_data=None):
    """Log audit entry to database"""
    db = SessionLocal()
    try:
        audit_entry = AuditLog(
            user=user,
            action=action,
            details=details,
            changes={"old": old_data, "new": new_data} if old_data or new_data else None
        )
        db.add(audit_entry)
        db.commit()
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error logging audit: {str(e)}")
    finally:
        db.close()

# Flask app setup
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'supersecretkey')
init_settings()

import json
import os
from datetime import datetime, timedelta
import uuid
from flask import Flask, request, jsonify, render_template, render_template_string, session, redirect, url_for
from functools import wraps

# Core users list
CORE_USERS = ['Matt', 'Kushal', 'Nathan', 'Michael', 'Ben']

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
            return render_template("register.html", error="Username and password are required")
        
        db = SessionLocal()
        try:
            # Check if user exists
            existing_user = db.query(User).filter_by(username=username).first()
            if existing_user:
                return render_template("register.html", error="Username already exists")
            
            # Create new user
            user = User(username=username, password=password)  # TODO: In production, hash the password
            db.add(user)
            db.commit()
            
            session['user'] = username
            log_audit("register", username, "New user registration")
            return redirect(url_for('index'))
        
        except Exception as e:
            db.rollback()
            app.logger.error(f"Error registering user: {str(e)}")
            return render_template("register.html", error="Registration system error")
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
        # Get all audit logs ordered by timestamp (most recent first)
        audit_entries = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).all()
        return render_template("audit.html", entries=[
            {
                "timestamp": entry.timestamp.isoformat(),
                "user": entry.user,
                "action": entry.action,
                "details": entry.details,
                "changes": entry.changes
            } for entry in audit_entries
        ])
    finally:
        db.close()

@app.route("/check_attendance")
def check_attendance():
    db = SessionLocal()
    try:
        today = datetime.now().date().isoformat()
        today_entries = db.query(Entry).filter_by(date=today).all()
        present_users = [entry.name for entry in today_entries]
        missing_users = [user for user in CORE_USERS if user not in present_users]
        return jsonify(missing_users)
    finally:
        db.close()

@app.route("/")
@login_required
def index():
    return render_template("index.html")

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

def calculate_daily_score(entry, settings, position=None, total_entries=None):
    """Calculate score for a single day's entry with all bonuses"""
    status = entry["status"].replace("-", "_")
    
    # Base points for attendance type
    base_points = settings["points"][status]
    late_bonus = 0
    
    # Add late bonus for in-office or eligible remote work
    if position is not None and total_entries is not None:
        if status == "in_office" or (
            status == "remote" and 
            is_eligible_remote_day(entry, settings)
        ):
            # Higher position = later arrival = more points
            # Last person (highest position) gets maximum bonus (4)
            late_bonus = position * settings["late_bonus"]
    
    # Add fixed bonuses for sick and leave
    bonus = 0
    if status == "sick" and "sick_bonus" in settings:
        bonus = settings["sick_bonus"]
    elif status == "leave" and "leave_bonus" in settings:
        bonus = settings["leave_bonus"]
    
    total_points = base_points + late_bonus + bonus
    return total_points

def is_eligible_remote_day(entry, settings):
    """Check if this is an eligible remote work day for late bonus"""
    entry_date = datetime.strptime(entry["date"], '%Y-%m-%d')
    weekday = entry_date.strftime('%a').lower()
    remote_days = settings.get('remote_days', {}).get(entry['name'], [])
    return weekday in remote_days

def get_week_dates(year, week):
    """Get start and end dates for a given ISO week"""
    # Create a date object for January 1st of the year
    jan1 = datetime(year, 1, 1)
    # Get the day of the week (1-7, treating Monday as 1)
    day_of_week = jan1.isoweekday()
    # Calculate the date of week 1's Monday
    week1_monday = jan1 - timedelta(days=day_of_week - 1)
    # Calculate the Monday of our target week
    target_monday = week1_monday + timedelta(weeks=week-1)
    # Calculate the Sunday of our target week
    target_sunday = target_monday + timedelta(days=6)
    return target_monday, target_sunday

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
    try:
        # Get current date (either from URL or today)
        if date_str:
            current_date = datetime.strptime(date_str, '%Y-%m-%d')
        else:
            current_date = datetime.now()
        
        # For weekly view, always snap to Monday
        if period == 'week':
            # Calculate Monday of the week
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
        rankings = calculate_scores(data, period, current_date)
        
        template_data = {
            'rankings': rankings,
            'period': period,
            'current_date': current_date.strftime('%Y-%m-%d'),
            'current_display': format_date_range(current_date, period_end, period),
            'current_month_value': current_date.strftime('%Y-%m')
        }
        
        return render_template("rankings.html", **template_data)
        
    except Exception as e:
        app.logger.error(f"Rankings error: {str(e)}")
        return render_template("error.html", message="Failed to load rankings")

def format_date_range(start_date, end_date, period):
    """Format date range for display"""
    if period == 'day':
        return start_date.strftime('%d/%m/%Y')
    elif period == 'week':
        return f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"
    else:
        return start_date.strftime('%B %Y')

def calculate_scores(data, period, current_date):
    settings = load_settings()
    filtered_data = [entry for entry in data if in_period(entry, period, current_date)]
    
    # Group entries by date for position calculation
    daily_entries = {}
    for entry in filtered_data:
        date = entry["date"]
        if date not in daily_entries:
            daily_entries[date] = []
        daily_entries[date].append(entry)
    
    # Calculate scores for each person
    scores = {}
    for date, entries in daily_entries.items():
        entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
        total_entries = len(entries)
        
        for position, entry in enumerate(entries, 1):
            name = entry["name"]
            if name not in scores:
                scores[name] = {
                    "total": 0,
                    "active_days": 0,  # Only count in-office and remote days
                    "days": 0,         # Count all days for stats
                    "in_office": 0,
                    "remote": 0,
                    "sick": 0,
                    "leave": 0,
                    "latest_arrivals": 0,
                    "arrival_times": []  # Track arrival times for average calculation
                }
            
            stats = scores[name]
            status = entry["status"].replace("-", "_")
            stats[status] += 1
            stats["days"] += 1
            
            # Only include in-office and remote days in scoring
            if status in ["in_office", "remote"]:
                stats["active_days"] += 1
                points = calculate_daily_score(entry, settings, position, total_entries)
                stats["total"] += points
                
                if position == total_entries:
                    stats["latest_arrivals"] += 1
                
                # Track arrival times for average calculation
                arrival_time = datetime.strptime(entry["time"], "%H:%M")
                stats["arrival_times"].append(arrival_time)
    
    # Format rankings with average scores (only considering active days)
    rankings = []
    for name, stats in scores.items():
        if stats["active_days"] > 0:  # Only include people with active days
            avg_arrival_time = calculate_average_time(stats["arrival_times"])
            rankings.append({
                "name": name,
                "score": round(stats["total"] / stats["active_days"], 2),  # Average only active days
                "latest_percentage": round((stats["latest_arrivals"] / stats["active_days"]) * 100, 1),
                "average_arrival_time": avg_arrival_time,
                "stats": stats
            })
    
    rankings.sort(key=lambda x: (x["score"], x["latest_percentage"]), reverse=True)
    return rankings

def calculate_average_time(times):
    """Calculate the average time from a list of datetime.time objects"""
    if not times:
        return "N/A"
    
    total_minutes = sum(t.hour * 60 + t.minute for t in times)
    avg_minutes = total_minutes // len(times)
    avg_hour = avg_minutes // 60
    avg_minute = avg_minutes % 60
    return f"{avg_hour:02d}:{avg_minute:02d}"

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
        points = calculate_daily_score(entry, settings, position, total_entries)
        rankings.append({
            "name": entry["name"],
            "time": entry["time"],
            "status": entry["status"],
            "points": points
        })
    
    # Sort by points descending (latest arrivals get more points)
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
    
    # Get entries for the specified date and sort by time
    day_entries = [e for e in data if e["date"] == date]
    day_entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
    
    # Calculate points and prepare rankings
    rankings = []
    total_entries = len(day_entries)
    for position, entry in enumerate(day_entries, 1):
        points = calculate_daily_score(entry, settings, position, total_entries)
        rankings.append({
            "name": entry["name"],
            "time": entry["time"],
            "status": entry["status"],
            "points": points
        })
    
    # Sort by points descending (latest arrivals get more points)
    rankings.sort(key=lambda x: x["points"], reverse=True)
    return render_template("day_rankings.html", 
                         rankings=rankings,
                         date=date)

@app.route("/edit", methods=["GET"])
@login_required
def get_entries():
    db = SessionLocal()
    try:
        period = request.args.get("period", "all")
        page = int(request.args.get("page", 1))
        per_page = 20
        
        query = db.query(Entry)
        
        # Add period filtering
        if period == "today":
            query = query.filter(Entry.date == datetime.now().date().isoformat())
        elif period == "week":
            week_start = datetime.now().date() - timedelta(days=datetime.now().weekday())
            query = query.filter(Entry.date >= week_start.isoformat())
        elif period == "month":
            month_start = datetime.now().date().replace(day=1)
            query = query.filter(Entry.date >= month_start.isoformat())
        
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
                "status": e.status,
                "timestamp": e.timestamp.isoformat()
            } for e in entries],
            "total": total
        })
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

@app.route("/settings", methods=["GET", "POST"])
@login_required
def manage_settings():
    db = SessionLocal()
    try:
        if request.method == "GET":
            settings = db.query(Settings).first()
            if not settings:
                init_settings()
                settings = db.query(Settings).first()
                
            return render_template("settings.html", 
                                settings={
                                    "points": settings.points,
                                    "late_bonus": settings.late_bonus,
                                    "remote_days": settings.remote_days
                                },
                                core_users=CORE_USERS)
        else:
            old_settings = db.query(Settings).first()
            if old_settings:
                old_data = {
                    "points": old_settings.points,
                    "late_bonus": old_settings.late_bonus,
                    "remote_days": old_settings.remote_days
                }
            
            new_settings = request.json
            
            # Validate remote days
            if 'remote_days' in new_settings:
                for user, days in new_settings['remote_days'].items():
                    if not isinstance(days, list):
                        return jsonify({"error": "Invalid remote days format"}), 400
                    new_settings['remote_days'][user] = [
                        day.lower() for day in days 
                        if day.lower() in ['mon', 'tue', 'wed', 'thu', 'fri']
                    ]
            
            if old_settings:
                old_settings.points = new_settings["points"]
                old_settings.late_bonus = new_settings["late_bonus"]
                old_settings.remote_days = new_settings["remote_days"]
            else:
                settings = Settings(**new_settings)
                db.add(settings)
            
            db.commit()
            
            log_audit(
                "update_settings",
                session['user'],
                "Updated point settings",
                old_data=old_data if old_settings else None,
                new_data=new_settings
            )
            
            return jsonify({"message": "Settings updated successfully"})
    except Exception as e:
        db.rollback()
        app.logger.error(f"Error managing settings: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()

@app.route("/history")
@login_required
def history():
    return render_template("history.html")

@app.route("/visualizations")
@login_required
def visualizations():
    return render_template("visualizations.html", core_users=CORE_USERS)

@app.route("/visualization-data")
@login_required
def get_visualization_data():
    try:
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
    
    for entry in data:
        try:
            date = entry['date']
            if date not in progression:
                progression[date] = {'total': 0, 'count': 0}
            
            points = calculate_daily_score(entry, settings)
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
        for entry in data:
            # Only include in-office and remote work for time patterns
            if normalize_status(entry["status"]) in ["in_office", "remote"]:
                date = datetime.strptime(entry["date"], '%Y-%m-%d')
                time = datetime.strptime(entry["time"], "%H:%M")
                
                day = date.strftime("%A")
                hour = f"{time.hour:02d}:00"
                
                key = f"{day}-{hour}"
                patterns[key] = patterns.get(key, 0) + 1
                
    except (ValueError, KeyError) as e:
        app.logger.error(f"Error in weekly patterns: {str(e)}")
    
    return patterns

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
    late_stats = {}
    for entry in data:
        status = normalize_status(entry['status'])
        # Only include in-office and remote work for late analysis
        if status in ["in_office", "remote"]:
            try:
                time = datetime.strptime(entry["time"], "%H:%M").time()
                name = entry["name"]
                if name not in late_stats:
                    late_stats[name] = {"late_count": 0, "total_count": 0}
                
                late_stats[name]["total_count"] += 1
                if time.hour >= 9:
                    late_stats[name]["late_count"] += 1
            except (ValueError, KeyError):
                continue
    
    return {
        name: {
            "late_percentage": (stats["late_count"] / stats["total_count"]) * 100,
            "total_days": stats["total_count"]
        }
        for name, stats in late_stats.items()
        if stats["total_count"] > 0
    }

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

if __name__ == "__main__":
    # Create tables on startup
    Base.metadata.create_all(bind=engine)
    app.run(debug=True)
