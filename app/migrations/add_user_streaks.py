from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import text

Base = declarative_base()

class UserStreak(Base):
    __tablename__ = "user_streaks"
    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False)
    current_streak = Column(Integer, default=0)
    last_attendance = Column(DateTime, nullable=True)
    max_streak = Column(Integer, default=0)

def migrate(engine):
    # Create the user_streaks table if it doesn't exist
    Base.metadata.create_all(bind=engine, tables=[UserStreak.__table__])
