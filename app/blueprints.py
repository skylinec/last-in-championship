from flask import Blueprint

# Create blueprints
bp = Blueprint('bp', __name__)  # Main blueprint
attendance_bp = Blueprint('attendance', __name__)
audit_bp = Blueprint('audit', __name__)
rankings_bp = Blueprint('rankings', __name__)
settings_bp = Blueprint('settings', __name__)
tie_breakers_bp = Blueprint('tie_breakers', __name__)
chatbot_bp = Blueprint('chatbot', __name__)
maintenance_bp = Blueprint('maintenance', __name__)
api_rules_bp = Blueprint('api_rules', __name__)
