from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Any
from requests import request
import logging

from .data import calculate_daily_score, load_data
from .helpers import calculate_average_time, normalize_status
from .utils import load_settings

logger = logging.getLogger(__name__)

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
                    logger.debug(f"Error processing entry: {entry}, Error: {e}")
                    continue
                    
        logger.debug(f"Generated patterns: {patterns}")
        return patterns
        
    except Exception as e:
        logger.error(f"Error in weekly patterns: {str(e)}")
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
                logger.warning(f"Error processing entry time: {entry.get('time', 'unknown')}, Error: {str(e)}")
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
        logger.error(f"Error in late arrival analysis: {str(e)}")
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
