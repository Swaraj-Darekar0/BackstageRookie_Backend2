from flask import Flask
from flask_cors import CORS

import os
import sys

def create_app():
    app = Flask(__name__)

    # Add project root to the Python path to allow for absolute imports
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    
    # Configuration
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
    app.config['PULLED_CODE_DIR'] = os.path.join(project_root, 'PulledCode_temp')
    app.config['DATA_DIR'] = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
    app.config['TEMPLATES_DIR'] = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')

    CORS(app, supports_credentials=True, origins=["http://localhost:3000", "https://backstage-rookie-frontend.vercel.app"])
    # Register blueprints
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    from app.routes.GoogleIntegra import google_auth_bp
    from app.routes.main import main_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(google_auth_bp)
     
    
    return app
