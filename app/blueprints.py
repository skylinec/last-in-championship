from flask import Blueprint

# Main blueprint (already exists)
bp = Blueprint('bp', __name__)

# Individual feature blueprints
attendance_bp = Blueprint('attendance', __name__, url_prefix='/attendance')
rankings_bp = Blueprint('rankings', __name__, url_prefix='/rankings')
settings_bp = Blueprint('settings', __name__, url_prefix='/settings')
audit_bp = Blueprint('audit', __name__, url_prefix='/audit')
tie_breakers_bp = Blueprint('tie_breakers', __name__, url_prefix='/tie-breakers')
chatbot_bp = Blueprint('chatbot', __name__, url_prefix='/chatbot')
maintenance_bp = Blueprint('maintenance', __name__, url_prefix='/maintenance')
api_rules_bp = Blueprint('api_rules', __name__, url_prefix='/api')
visualisations_bp = Blueprint('visualisations', __name__, url_prefix='/visualisations')
streaks_bp = Blueprint('streaks', __name__, url_prefix='/streaks')
history_bp = Blueprint('history', __name__, url_prefix='/history')

# Import route handlers
from . import routes
