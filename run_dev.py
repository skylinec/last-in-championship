from main import app
from config import config
import os

if __name__ == "__main__":
    # Set development config
    app.config.from_object(config['development'])
    
    # Enable hot reloading
    app.jinja_env.auto_reload = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    
    # Run dev server
    app.run(debug=True, host='0.0.0.0', port=9000)
