from ..database import Base
from sqlalchemy import Column, Integer, String, DateTime, Boolean

def should_run(engine):
    """Check if this migration should run"""
    inspector = engine.dialect.inspector(engine)
    return 'user_streaks' not in inspector.get_table_names()

def migrate(engine):
    """Create user_streaks table"""
    class UserStreak(Base):
        __tablename__ = 'user_streaks'
        id = Column(Integer, primary_key=True)
        username = Column(String, nullable=False)
        current_streak = Column(Integer, default=0)
        last_attendance = Column(DateTime, nullable=True)
        max_streak = Column(Integer, default=0)
        
    UserStreak.__table__.create(engine, checkfirst=True)
