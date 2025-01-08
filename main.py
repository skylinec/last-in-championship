import json
import os
from datetime import datetime, timedelta
import uuid
from flask import Flask, request, jsonify, render_template, render_template_string, session, redirect, url_for
from functools import wraps

# File to store data
data_file = "championship_data.json"
SETTINGS_FILE = "championship_settings.json"
AUDIT_FILE = "audit_trail.json"
CORE_USERS = ['Matt', 'Kushal', 'Nathan', 'Michael', 'Ben']

def init_files():
    """Initialize JSON files with default data if they don't exist"""
    # Default settings
    if not os.path.exists(SETTINGS_FILE):
        default_settings = {
            "points": {
                "in_office": 10,
                "remote": 8,
                "sick": 5,
                "leave": 5
            },
            "early_bonus": 1
        }
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(default_settings, f, indent=4)

    # Default data file
    if not os.path.exists(data_file):
        with open(data_file, 'w') as f:
            json.dump([], f)

    # Default audit file
    if not os.path.exists(AUDIT_FILE):
        with open(AUDIT_FILE, 'w') as f:
            json.dump([], f)

# Flask app
app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Change this to a random secret key in production
init_files()  # Initialize files when app starts

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def log_audit(action, user, details, old_data=None, new_data=None):
    with open(AUDIT_FILE, 'r+') as f:
        try:
            audit = json.load(f)
        except json.JSONDecodeError:
            audit = []
        
        audit_entry = {
            'timestamp': datetime.now().isoformat(),
            'user': user,
            'action': action,
            'details': details
        }
        
        # Add change details if provided
        if old_data and new_data:
            changes = []
            for key in new_data:
                if key in old_data and old_data[key] != new_data[key]:
                    changes.append({
                        'field': key,
                        'old': old_data[key],
                        'new': new_data[key]
                    })
            if changes:
                audit_entry['changes'] = changes
        
        audit.append(audit_entry)
        f.seek(0)
        json.dump(audit, f, indent=4)
        f.truncate()

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        # Simple authentication - in real system use proper password hashing
        if username and password == "demo":  # Simplified for demo
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
            
        if password != "demo":
            return render_template("register.html", error="For this demo, please use 'demo' as the password")
        
        # In a real system, you would hash the password and store user data
        session['user'] = username
        log_audit("register", username, "New user registration")
        return redirect(url_for('index'))
        
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
    with open(AUDIT_FILE, 'r') as f:
        audit = json.load(f)
    return render_template("audit.html", entries=audit)

@app.route("/check_attendance")
def check_attendance():
    data = load_data()
    today = datetime.now().date().isoformat()
    today_entries = [e for e in data if e["date"] == today]
    missing_users = [user for user in CORE_USERS 
                    if not any(e["name"] == user for e in today_entries)]
    return jsonify(missing_users)

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/names")
@login_required
def get_names():
    data = load_data()
    return jsonify(list(set(entry["name"] for entry in data)))

@app.route("/today-entries")
@login_required
def get_today_entries():
    data = load_data()
    today = datetime.now().date().isoformat()
    return jsonify([
        entry for entry in data 
        if entry["date"] == today
    ])

@app.route("/log", methods=["POST"])
@login_required
def log_attendance():
    data = load_data()
    
    # Check for existing entry for this person today
    today_entries = [
        e for e in data 
        if e["date"] == request.json["date"] and 
        e["name"].lower() == request.json["name"].lower()
    ]
    
    if today_entries:
        return jsonify({
            "message": "Error: Already logged attendance for this person today.",
            "type": "error"
        }), 400
    
    entry = {
        "id": str(uuid.uuid4()),
        "date": request.json["date"],
        "name": request.json["name"],
        "status": request.json["status"],
        "time": request.json["time"],
        "timestamp": datetime.now().isoformat()
    }
    
    data.append(entry)
    save_data(data)
    log_audit(
        "log_attendance",
        session['user'],
        f"Logged attendance for {entry['name']}",
        new_data=entry
    )
    return jsonify({
        "message": "Attendance logged successfully.",
        "type": "success"
    }), 200

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

def calculate_daily_score(entry, settings, position=None):
    """Calculate score for a single day's entry with remote work day bonus"""
    status = entry["status"].replace("-", "_")
    points = settings["points"][status]
    
    # Get the day of week
    entry_date = datetime.strptime(entry["date"], '%Y-%m-%d')
    weekday = entry_date.strftime('%a').lower()
    
    # Check if user gets early bonus for remote work on this day
    remote_days = settings.get('remote_days', {}).get(entry['name'], [])
    is_remote_bonus_day = weekday in remote_days
    
    # Add early bonus for in-office or eligible remote work
    if position is not None and (status == "in_office" or (status == "remote" and is_remote_bonus_day)):
        points += max((5 - position) * settings["early_bonus"], 0)
    
    return points

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
            first_day = datetime.strptime(f'{year}-W{week}-1', '%Y-W%W-%w').date()
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
        # Sort entries by time for each day
        entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
        
        # Calculate daily scores
        for position, entry in enumerate(entries, 1):
            name = entry["name"]
            if name not in scores:
                scores[name] = {
                    "total": 0,
                    "days": 0,
                    "in_office": 0,
                    "remote": 0,
                    "sick": 0,
                    "leave": 0
                }
            
            stats = scores[name]
            stats["days"] += 1
            status = entry["status"].replace("-", "_")
            stats[status] += 1
            
            # Calculate points consistently
            points = calculate_daily_score(entry, settings, position)
            stats["total"] += points
    
    # Format rankings with average scores
    rankings = []
    for name, stats in scores.items():
        if stats["days"] > 0:
            rankings.append({
                "name": name,
                "score": round(stats["total"] / stats["days"], 2),
                "stats": stats
            })
    
    return sorted(rankings, key=lambda x: x["score"], reverse=True)

def in_period(entry, period, current_date):
    """Check if entry falls within the specified period"""
    entry_date = datetime.fromisoformat(entry["date"]).date()
    
    if period == 'day':
        return entry_date == current_date.date()
    elif period == 'week':
        week_start = current_date.date()
        week_end = (current_date + timedelta(days=6)).date()
        return week_start <= entry_date <= week_end
    elif period == 'month':
        return (entry_date.year == current_date.year and 
                entry_date.month == current_date.month)
    return True

@app.route("/rankings/today")
@login_required
def daily_rankings():
    data = load_data()
    settings = load_settings()
    today = datetime.now().date().isoformat()
    
    today_entries = [e for e in data if e["date"] == today]
    today_entries.sort(key=lambda x: datetime.strptime(x["time"], "%H:%M"))
    
    rankings = []
    for entry in today_entries:
        points = settings["points"][entry["status"].replace("-", "_")]
        if entry["status"] == "in-office":
            points += max((5 - entry["position"]) * settings["early_bonus"], 0)
            
        rankings.append({
            "name": entry["name"],
            "time": entry["time"],
            "status": entry["status"],
            "points": points
        })
    
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
    for position, entry in enumerate(day_entries, 1):
        points = settings["points"][entry["status"].replace("-", "_")]
        if entry["status"] == "in-office":
            points += max((5 - position) * settings["early_bonus"], 0)
            
        rankings.append({
            "name": entry["name"],
            "time": entry["time"],
            "status": entry["status"],
            "points": points
        })
    
    return render_template("day_rankings.html", 
                         rankings=rankings,
                         date=date)

@app.route("/edit", methods=["GET"])
@login_required
def get_entries():
    data = load_data()
    period = request.args.get("period", "all")
    page = int(request.args.get("page", 1))
    
    filtered = [entry for entry in data if period_filter(entry, period)]
    filtered.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    
    start_idx = (page - 1) * 20
    end_idx = start_idx + 20
    
    return jsonify({
        "entries": filtered[start_idx:end_idx],
        "total": len(filtered)
    })

@app.route("/edit/<entry_id>", methods=["PATCH", "DELETE"])
@login_required
def modify_entry(entry_id):
    data = load_data()
    entry_index = next((i for i, e in enumerate(data) if e.get("id") == entry_id), None)
    
    if entry_index is None:
        return jsonify({"error": "Entry not found"}), 404
    
    if request.method == "PATCH":
        # Store old data for audit
        old_data = data[entry_index].copy()
        updated_data = request.json
        
        # Check for duplicates
        existing_entry = next(
            (e for e in data 
             if e["date"] == updated_data.get("date", old_data["date"]) and
             e["name"].lower() == updated_data.get("name", old_data["name"]).lower() and
             e.get("id") != entry_id),
            None
        )
        
        if existing_entry:
            return jsonify({
                "message": "Error: Cannot update - Already have an entry for this person on this date.",
                "type": "error"
            }), 400
            
        data[entry_index].update(updated_data)
        log_audit(
            "modify_entry",
            session['user'],
            f"Modified entry for {old_data['name']} on {old_data['date']}",
            old_data=old_data,
            new_data=data[entry_index]
        )
    else:
        # Log deletion
        deleted_entry = data[entry_index]
        log_audit(
            "delete_entry",
            session['user'],
            f"Deleted entry for {deleted_entry['name']} on {deleted_entry['date']}",
            old_data=deleted_entry,
            new_data=None
        )
        data.pop(entry_index)
        
    save_data(data)
    return jsonify({
        "message": "Entry updated successfully.",
        "type": "success"
    })

@app.route("/settings", methods=["GET", "POST"])
@login_required
def manage_settings():
    if request.method == "GET":
        settings = load_settings()
        return render_template("settings.html", settings=settings, core_users=CORE_USERS)
    else:
        old_settings = load_settings()
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
        
        save_settings(new_settings)
        log_audit(
            "update_settings",
            session['user'],
            "Updated point settings",
            old_data=old_settings,
            new_data=new_settings
        )
        return jsonify({"message": "Settings updated successfully"})

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
                "earlyBirdAnalysis": {},
                "userComparison": {}
            })
            
        date_range = request.args.get('range', 'all')
        user_filter = request.args.get('user', 'all')
        
        # Convert date strings to datetime objects for comparison
        cutoff_date = None
        if date_range != 'all':
            days = int(date_range)
            cutoff_date = datetime.now().date() - timedelta(days=days)
        
        # Filter data
        filtered_data = []
        for entry in data:
            try:
                entry_date = datetime.strptime(entry['date'], '%Y-%m-%d').date()
                if cutoff_date and entry_date < cutoff_date:
                    continue
                if user_filter != 'all' and entry['name'] != user_filter:
                    continue
                filtered_data.append(entry)
            except (ValueError, KeyError):
                # Skip malformed entries
                continue
        
        # Calculate visualizations data
        vis_data = {
            'weeklyPatterns': calculate_weekly_patterns(filtered_data),
            'statusCounts': calculate_status_counts(filtered_data),
            'pointsProgress': calculate_points_progression(filtered_data),
            'dailyActivity': calculate_daily_activity(filtered_data),
            'earlyBirdAnalysis': analyze_early_arrivals(filtered_data),
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
    patterns = {}
    for entry in data:
        try:
            if normalize_status(entry["status"]) == "in_office":  # Fix: Normalize status
                date = datetime.strptime(entry["date"], '%Y-%m-%d')
                time = datetime.strptime(entry["time"], "%H:%M")
                day = date.strftime("%A")
                hour = f"{time.hour:02d}:00"
                key = f"{day}-{hour}"
                patterns[key] = patterns.get(key, 0) + 1
        except (ValueError, KeyError):
            continue
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

def load_data():
    """Load data with better error handling"""
    try:
        with open(data_file, "r") as file:
            data = json.load(file)
            # Ensure all entries have IDs
            for entry in data:
                if "id" not in entry:
                    entry["id"] = str(uuid.uuid4())
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        # If file doesn't exist or is corrupted, create new
        empty_data = []
        save_data(empty_data)
        return empty_data

def save_data(data):
    with open(data_file, "w") as file:
        json.dump(data, file, indent=4)

def load_settings():
    """Load settings with better error handling"""
    try:
        with open(SETTINGS_FILE, "r") as file:
            settings = json.load(file)
            # Ensure remote_days exists
            if 'remote_days' not in settings:
                settings['remote_days'] = {}
            return settings
    except (FileNotFoundError, json.JSONDecodeError):
        # If file doesn't exist or is corrupted, create new with defaults
        default_settings = {
            "points": {
                "in_office": 10,
                "remote": 8,
                "sick": 5,
                "leave": 5
            },
            "early_bonus": 1,
            "remote_days": {}
        }
        save_settings(default_settings)
        return default_settings

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as file:
        json.dump(settings, file, indent=4)

def period_filter(entry, period):
    entry_date = datetime.fromisoformat(entry["date"])
    now = datetime.now()

    if period == "week":
        return (now - entry_date).days < 7
    elif period == "month":
        return (now - entry_date).days < 30
    else:
        return True

if __name__ == "__main__":
    app.run(debug=True)
