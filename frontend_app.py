#!/usr/bin/env python3
"""
ARGUS Web Frontend — application entry point.
All data logic lives in core/; all routes live in routes/.
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask
from flask_cors import CORS

from config import API_PORT
from core.json_utils import SafeJSONEncoder
from data_dashboard import data_dashboard_bp
from routes.guardian_routes import guardian_bp
from routes.pipeline_routes import pipeline_bp


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    app.json_encoder = SafeJSONEncoder
    app.config["JSON_SORT_KEYS"] = False

    app.register_blueprint(data_dashboard_bp)
    app.register_blueprint(guardian_bp)
    app.register_blueprint(pipeline_bp)
    return app


app = create_app()

if __name__ == "__main__":
    print(f"""
    ╔════════════════════════════════════════════════════════════════╗
    ║         ARGUS Multi-Agent System — Web Frontend                ║
    ║  Listening on http://127.0.0.1:{API_PORT}/                     ║
    ╚════════════════════════════════════════════════════════════════╝
    """)
    app.run(debug=True, port=API_PORT)
