from prometheus_client import Counter, Gauge, Histogram, Summary, CollectorRegistry, generate_latest
from datetime import datetime
from flask import Blueprint

# Create a new registry for metrics
metrics = Blueprint('metrics', __name__)
registry = CollectorRegistry()

# Application metrics
REQUEST_COUNT = Counter('request_count_total', 'Total request count', ['method', 'endpoint'], registry=registry)
IN_PROGRESS = Gauge('requests_in_progress', 'Number of requests in progress', registry=registry)
REQUEST_LATENCY = Histogram('request_latency_seconds', 'Request latency in seconds', ['endpoint'], registry=registry)

# Business metrics
ATTENDANCE_COUNT = Counter('attendance_count_total', 'Total attendance records', ['status'], registry=registry)
USER_STREAK = Gauge('user_streak_days', 'Current streak for user', ['username'], registry=registry)
POINTS_GAUGE = Gauge('user_points', 'Current points for user', ['username', 'period'], registry=registry)
ARRIVAL_TIME = Histogram('arrival_time_hours', 'Arrival time distribution', ['username'], buckets=[7, 8, 9, 10, 11, 12], registry=registry)

# Specialized metrics
AUDIT_ACTIONS = Counter('audit_actions_total', 'Total audit actions', ['action', 'user'], registry=registry)
DB_CONNECTIONS = Gauge('db_connections_current', 'Number of active database connections', registry=registry)
CACHE_HITS = Counter('cache_hits_total', 'Total cache hits', registry=registry)
CACHE_MISSES = Counter('cache_misses_total', 'Total cache misses', registry=registry)

# Performance metrics
DB_QUERY_TIME = Histogram('db_query_duration_seconds', 'Database query duration', ['query_type'], registry=registry)
RESPONSE_TIME = Histogram('response_time_seconds', 'Response time in seconds', ['endpoint'], registry=registry)

@metrics.route('/metrics')
def metrics_endpoint():
    """Endpoint that serves all Prometheus metrics"""
    return generate_latest(registry), 200, {'Content-Type': 'text/plain; version=0.0.4'}

def record_request_metric(method, endpoint, duration=None):
    """Record request-related metrics"""
    REQUEST_COUNT.labels(method=method, endpoint=endpoint).inc()
    if duration:
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)

def update_attendance_metrics(entries, status_counts, user_streaks, points_data):
    """Update all attendance-related metrics"""
    # Update attendance counters
    for status, count in status_counts.items():
        ATTENDANCE_COUNT.labels(status=status).set(count)

    # Update user streaks
    for username, streak in user_streaks.items():
        USER_STREAK.labels(username=username).set(streak)

    # Update points
    for username, points in points_data.items():
        for period, value in points.items():
            POINTS_GAUGE.labels(username=username, period=period).set(value)

    # Update arrival times
    for entry in entries:
        if entry.status in ['in-office', 'remote']:
            time_obj = datetime.strptime(entry.time, '%H:%M')
            hours = time_obj.hour + time_obj.minute / 60
            ARRIVAL_TIME.labels(username=entry.name).observe(hours)

def record_db_operation(operation_type, duration):
    """Record database operation metrics"""
    DB_QUERY_TIME.labels(query_type=operation_type).observe(duration)

def record_audit_action(action, user):
    """Record audit log metrics"""
    AUDIT_ACTIONS.labels(action=action, user=user).inc()

def update_db_connection_count(count):
    """Update database connection gauge"""
    DB_CONNECTIONS.set(count)

def record_cache_operation(hit):
    """Record cache operation metrics"""
    if hit:
        CACHE_HITS.inc()
    else:
        CACHE_MISSES.inc()
