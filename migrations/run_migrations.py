from sqlalchemy import create_engine, inspect
import os
import importlib

def check_table_exists(engine, table_name):
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()

def run_migrations():
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/championship')
    engine = create_engine(DATABASE_URL)
    
    # List all migration modules in order
    migrations = [
        'add_user_streaks',
    ]
    
    for migration in migrations:
        try:
            print(f"Running migration: {migration}")
            migration_module = importlib.import_module(f'migrations.{migration}')
            if hasattr(migration_module, 'should_run'):
                if migration_module.should_run(engine):
                    migration_module.migrate(engine)
                    print(f"Successfully completed migration: {migration}")
                else:
                    print(f"Skipping migration {migration} - already applied")
            else:
                migration_module.migrate(engine)
                print(f"Successfully completed migration: {migration}")
        except Exception as e:
            print(f"Error in migration {migration}: {str(e)}")
            raise

if __name__ == "__main__":
    run_migrations()
