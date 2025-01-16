from flask import Blueprint

# Create blueprints without url_prefix - we'll handle paths in the route decorators
bp = Blueprint('bp', __name__)
attendance_bp = Blueprint('attendance', __name__)
audit_bp = Blueprint('audit', __name__)
rankings_bp = Blueprint('rankings', __name__)
settings_bp = Blueprint('settings', __name__)
tie_breakers_bp = Blueprint('tie_breakers', __name__)
chatbot_bp = Blueprint('chatbot', __name__)
maintenance_bp = Blueprint('maintenance', __name__)
api_rules_bp = Blueprint('api_rules', __name__)

# Import all routes to register them with blueprints
from . import routes  # This line is crucial - it imports and registers all routes
