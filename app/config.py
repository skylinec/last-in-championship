import os
import logging

def get_database_url():
    """Get database URL with fallback for development"""
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        logging.warning("DATABASE_URL not set, using development default.")
        db_url = 'postgresql://postgres:postgres@localhost:5432/championship'
    return db_url

def check_configuration(app):
    """Check and log important configuration settings"""
    logger = logging.getLogger(__name__)
    logger.info("Checking configuration...")
    config = {
        'FLASK_ENV': os.getenv('FLASK_ENV', 'production'),
        'DATABASE_URL': os.getenv('DATABASE_URL', '[MASKED]'),
        'DEBUG': app.debug
    }
    for key, value in config.items():
        if key == 'DATABASE_URL':
            logger.info(f"{key}: [MASKED]")
        else:
            logger.info(f"{key}: {value}")
    return config
