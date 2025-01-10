from datetime import datetime, timedelta
import random
from main import SessionLocal, Entry, Settings, init_settings, app
import uuid

def generate_demo_data():
    """Generate demo data for development environment starting from January 1st"""
    db = SessionLocal()
    try:
        existing_users = db.query(Entry).count()
        if existing_users > 0:
            print("Test data already exists; skipping creation.")
            return

        # Define demo users with their typical arrival patterns
        users = {
            "Alex Smith": {"early": 0.7, "time_range": (8, 9)},
            "Emma Davis": {"early": 0.3, "time_range": (8, 10)},
            "James Wilson": {"early": 0.5, "time_range": (8, 9, 30)},
            "Sarah Brown": {"early": 0.2, "time_range": (9, 10)},
            "Michael Lee": {"early": 0.4, "time_range": (8, 30, 9, 30)}
        }
        
        statuses = ["in-office", "remote", "sick", "leave"]
        
        # Generate entries from January 1st
        entries = []
        end_date = datetime.now().date()
        start_date = datetime(end_date.year, 1, 1).date()
        
        for date in (start_date + timedelta(n) for n in range((end_date - start_date).days + 1)):
            # Skip weekends
            if date.weekday() >= 5:
                continue
                
            for username, patterns in users.items():
                existing_user = db.query(User).filter_by(username=username).first()
                if not existing_user:
                    new_user = User(username=username, password="demo")
                    db.add(new_user)

                # 90% chance of attendance on regular days, lower on mondays/fridays
                attendance_chance = 0.9
                if date.weekday() in [0, 4]:  # Monday or Friday
                    attendance_chance = 0.85
                
                if random.random() < attendance_chance:
                    # Generate time based on user pattern
                    if random.random() < patterns["early"]:
                        # Early arrival
                        hour = random.randint(patterns["time_range"][0], patterns["time_range"][1])
                        minute = random.randint(0, 59)
                    else:
                        # Later arrival
                        hour = random.randint(9, 10)
                        minute = random.randint(0, 59)
                    
                    time = f"{hour:02d}:{minute:02d}"
                    
                    # Weight status probabilities based on day
                    if date.weekday() in [0, 4]:  # Monday/Friday
                        weights = [0.5, 0.4, 0.05, 0.05]  # More remote work
                    else:
                        weights = [0.7, 0.2, 0.05, 0.05]  # More office work
                    
                    status = random.choices(statuses, weights=weights)[0]
                    
                    entry = Entry(
                        id=str(uuid.uuid4()),
                        date=date.strftime("%Y-%m-%d"),
                        time=time,
                        name=username,
                        status=status,
                        timestamp=datetime.combine(date, datetime.strptime(time, "%H:%M").time())
                    )
                    entries.append(entry)
        
        # Clear existing data and add new entries
        db.query(Entry).delete()
        db.add_all(entries)
        
        # Initialize settings with these users as core
        init_settings()
        settings = db.query(Settings).first()
        if settings:
            settings.core_users = list(users.keys())
            
        db.commit()
        print(f"Generated {len(entries)} demo entries from {start_date} to {end_date}")
        
    except Exception as e:
        db.rollback()
        print(f"Error generating demo data: {str(e)}")
    finally:
        db.close()

if __name__ == "__main__":
    generate_demo_data()
    app.run(
        host='0.0.0.0',
        port=9000, 
        debug=True,
        use_reloader=True,
        threaded=True
    )