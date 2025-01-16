# database.py
import os
import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import get_database_url

logger = logging.getLogger(__name__)

Base = declarative_base()

engine = create_engine(
    get_database_url(),
    echo=False,
    future=True
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    # ...existing code if needed for SQLite...
    pass

if os.getenv('FLASK_ENV') == 'development':
    @event.listens_for(engine, "connect")
    def connect(dbapi_connection, connection_record):
        logging.debug("New database connection created")

    @event.listens_for(engine, "checkout")
    def receive_checkout(dbapi_connection, connection_record, connection_proxy):
        logging.debug("Database connection checked out")

    @event.listens_for(engine, "checkin")
    def receive_checkin(dbapi_connection, connection_record):
        logging.debug("Database connection returned to pool")
