from flask import Blueprint

# Create blueprints with explicit url_prefix
bp = Blueprint('bp', __name__, url_prefix='')  # Root blueprint with empty prefix
attendance_bp = Blueprint('attendance', __name__, url_prefix='/attendance')
audit_bp = Blueprint('audit', __name__, url_prefix='/audit')
rankings_bp = Blueprint('rankings', __name__, url_prefix='/rankings')
settings_bp = Blueprint('settings', __name__, url_prefix='/settings')
tie_breakers_bp = Blueprint('tie_breakers', __name__, url_prefix='/tie-breakers')
chatbot_bp = Blueprint('chatbot', __name__, url_prefix='/chatbot')
maintenance_bp = Blueprint('maintenance', __name__, url_prefix='/maintenance')
api_rules_bp = Blueprint('api_rules', __name__, url_prefix='/api')
