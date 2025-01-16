import logging
import os

from flask import Flask
from prometheus_client import make_wsgi_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from .config import get_database_url
from .database import Base, SessionLocal, engine
from .metrics import metrics_app, start_metrics_updater
from .migrations.run_migrations import run_migrations
from .sockets import notify_game_update, socketio
from .utils import init_settings
from .routes import init_app
from .blueprints import bp


def create_app():
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if os.getenv('FLASK_ENV') == 'development' else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    logger.info("Creating Flask application...")

    app = Flask(__name__)
    app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
    app.config.from_object('config')

    # Attach prometheus WSGI app
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
        '/metrics': metrics_app
    })

    # Database migrations
    logger.info("Running migrations...")
    try:
        run_migrations()
    except ImportError as e:
        logger.error(f"Migrations module not found: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in migrations: {e}")

    # Create tables
    logger.info("Creating database tables if they don't exist.")
    Base.metadata.create_all(bind=engine)

    # Initialize default settings
    logger.info("Initializing default settings...")
    init_settings()

    # Initialize template filters and app settings first
    init_app(app)

    # Register blueprint after filters
    app.register_blueprint(bp)

    # Initialize SocketIO
    socketio.init_app(
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
    )

    # Start metrics updater thread
    start_metrics_updater()

    return app

# Create and configure the application instance
app = create_app()
application = app  # Add this line to expose the application for Gunicorn
