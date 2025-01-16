# metrics.py
from prometheus_client import make_wsgi_app, Summary, Counter, Gauge, Histogram, CollectorRegistry
import time
from threading import Thread
from .database import SessionLocal
import logging

logger = logging.getLogger(__name__)

registry = CollectorRegistry()

# Define your custom Prometheus metrics here
REQUEST_TIME = Summary('request_processing_seconds', 'Time spent processing request')
REQUEST_COUNT = Counter('request_count', 'Total request count')
IN_PROGRESS = Gauge('in_progress_requests', 'In-progress requests')
ATTENDANCE_COUNT = Gauge('attendance_count_total', 'Total attendance records', ['status'])
RESPONSE_TIME = Histogram('response_time_seconds', 'Response time in seconds', ['endpoint'])
AUDIT_ACTIONS = Counter('audit_actions_total', 'Total audit actions', ['action'])
DB_CONNECTIONS = Gauge('db_connections', 'Number of current DB connections')
AUDIT_TRAIL_COUNT = Counter('audit_trail_count', 'Total audit logs recorded')
ATTENDANCE_DB_COUNT = Counter('attendance_db_count', 'Attendance DB operations', ['operation'])
RANKING_CALLS = Counter('ranking_calls_total', 'Number of times rankings have been requested', registry=registry)

USER_STREAK = Gauge('user_streak_days', 'Current streak for user', ['username'], registry=registry)

# The WSGI metrics app for /metrics
metrics_app = make_wsgi_app()

def start_metrics_updater():
    def update_loop():
        while True:
            try:
                update_prometheus_metrics()
            except Exception as e:
                logger.error(f"Error updating metrics: {str(e)}")
            time.sleep(300)  # Update every 5 minutes

    thread = Thread(target=update_loop, daemon=True)
    thread.start()

def update_prometheus_metrics():
    db = SessionLocal()
    try:
        # Import models inside function to avoid circular imports
        from .models import Entry, UserStreak
        
        # Update attendance_count_total by status
        statuses = ['in-office','remote','sick','leave']
        for s in statuses:
            count = db.query(Entry).filter(Entry.status == s).count()
            ATTENDANCE_COUNT.labels(status=s).set(count)

        # Update user_streak_days
        streaks = db.query(UserStreak).all()
        for us in streaks:
            USER_STREAK.labels(username=us.username).set(us.current_streak)
    finally:
        db.close()

def record_request_metric(method, endpoint, duration):
    """Record request metrics"""
    REQUEST_COUNT.inc()
    REQUEST_TIME.observe(duration)
    RESPONSE_TIME.labels(endpoint=endpoint).observe(duration)

def update_attendance_metrics():
    # ...placeholder for updating attendance metrics...
    pass


def record_audit_action(action):
    AUDIT_ACTIONS.labels(action=action).inc()
