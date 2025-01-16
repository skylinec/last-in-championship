# app.py
import os
import logging
import time
import random
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_cors import CORS
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from datetime import datetime
from threading import Thread, Lock

# Prometheus and SocketIO
from prometheus_client import make_wsgi_app
from flask_socketio import SocketIO, emit, join_room, leave_room
from gevent import monkey
monkey.patch_all()

# Local modules
from database import engine, SessionLocal, Base
from models import init_settings
from metrics import metrics_app, start_metrics_updater, record_request_metric  # Our custom metrics module
from caching import CACHE_HITS, CACHE_MISSES  # Or import the entire caching module
from routes import bp as main_blueprint  # Single blueprint with all routes

# Create Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')

# Attach Prometheus WSGI app to /metrics
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': metrics_app
})

# CORS setup if needed
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Range", "X-Total-Count"],
        "supports_credentials": True
    }
})

# SocketIO setup
socketio = SocketIO(
    app,
    async_mode='gevent',
    cors_allowed_origins=["http://localhost:9000", "https://lic.mattdh.me"],
    ping_timeout=60,
    ping_interval=25,
    path='/socket.io',
    always_connect=True,
    manage_session=True,
    logger=True,
    engineio_logger=True,
    transports=['websocket', 'polling'],
    max_http_buffer_size=1000000,
    cookie=None,
    transport_options={
        'websocket': {
            'pingTimeout': 60000,
            'pingInterval': 25000,
            'maxPayload': 1000000,
            'perMessageDeflate': True,
            'httpCompression': True,
            'origins': '*'
        },
        'polling': {
            'timeout': 60000
        }
    }
)

# Logging
logging.basicConfig(
    level=logging.DEBUG if os.getenv('FLASK_ENV') == 'development' else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("Starting Flask application...")

# Database initialization
logger.info("Creating database tables if they don't exist...")
Base.metadata.create_all(bind=engine)

# Initialize default settings
logger.info("Initializing default settings if not present...")
init_settings()

# Register our single Blueprint containing all routes
app.register_blueprint(main_blueprint)

# Start background metrics updater
start_metrics_updater()

# Store WebSocket connections
websocket_connections = {}

# SocketIO event handlers
@socketio.on('connect')
def on_connect():
    try:
        current_user = session.get('user')
        if not current_user:
            logging.warning("Unauthenticated connection attempt")
            return False

        logging.info(f"Client connected (sid={request.sid}) - User: {current_user}")
        websocket_connections[request.sid] = {
            'user': current_user,
            'connected_at': datetime.now(),
            'reconnect_count': 0
        }
        emit('connected', {'status': 'ok', 'sid': request.sid})
        return True
    except Exception as e:
        logging.error(f"Connection error: {str(e)}")
        return False

@socketio.on('disconnect')
def on_disconnect():
    try:
        conn_info = websocket_connections.pop(request.sid, None)
        if conn_info:
            logging.info(
                f"Client disconnected (sid={request.sid}) - "
                f"User: {conn_info['user']}, "
                f"Duration: {datetime.now() - conn_info['connected_at']}"
            )
    except Exception as e:
        logging.error(f"Disconnect error: {str(e)}")

# ...add other socket event handlers from sockets.py...

def notify_game_update(game_id, game_state, winner=None):
    """Broadcast game updates to all clients in the game room."""
    emit(
        'game_update',
        {'state': game_state, 'winner': winner},
        room=f'game_{game_id}'
    )

# Example of adding global error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template(
        'error.html',
        error="Page Not Found",
        details="The requested page could not be found.",
        back_link=url_for('bp.index')  # or just url_for('index') depending on your naming
    ), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template(
        'error.html',
        error="Internal Server Error",
        details="An unexpected error has occurred.",
        back_link=url_for('bp.index')
    ), 500

# If you have before_request / after_request logic, place it here
@app.before_request
def before_request():
    request.start_time = time.time()
    # IN_PROGRESS.inc()

@app.after_request
def after_request(response):
    # IN_PROGRESS.dec()
    if hasattr(request, 'start_time'):
        duration = time.time() - request.start_time
        record_request_metric(
            method=request.method,
            endpoint=request.endpoint,
            duration=duration
        )
    return response

# Finally, run the app via SocketIO
if __name__ == "__main__":
    debug_mode = os.getenv('FLASK_ENV') == 'development'
    socketio.run(
        app,
        host='0.0.0.0',
        port=int(os.getenv('PORT', '9000')),
        debug=debug_mode,
        use_reloader=debug_mode,
        log_output=True,
        ssl_context=None,  # In production, handle SSL externally
        allow_unsafe_werkzeug=True if debug_mode else False
    )
