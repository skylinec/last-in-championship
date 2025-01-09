# ...existing code...

from flask_cors import CORS  # Add this import

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
# ...existing code...

# Update routes to return JSON instead of rendering templates
@app.route("/")
def index():
    return jsonify({"status": "ok"})

@app.route("/api/rankings/<period>")
@app.route("/api/rankings/<period>/<date_str>")
def view_rankings(period, date_str=None):
    try:
        mode = request.args.get('mode', 'last-in')
        if date_str:
            current_date = datetime.strptime(date_str, '%Y-%m-%d')
        else:
            current_date = datetime.now()
        
        if period == 'week':
            current_date = current_date - timedelta(days=current_date.weekday())
        
        data = load_data()
        rankings = calculate_scores(data, period, current_date)
        
        return jsonify({
            'rankings': rankings,
            'period': period,
            'current_date': current_date.strftime('%Y-%m-%d'),
            'mode': mode
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Continue updating other routes similarly...
