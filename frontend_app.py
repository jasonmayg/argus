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
from routes.ai_routes import ai_bp          # ← 新增
from routes.cash_routes import cash_bp      # ← 新增
from routes.data_alert_routes import data_alert_bp  # ← 新增
from routes.regulatory_routes import regulatory_bp
from routes.actions_routes import actions_bp      # ← 新增
from routes.rag_stats_routes import rag_stats_bp
from routes.intelligence_routes import intelligence_bp


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    app.json_encoder = SafeJSONEncoder
    app.config["JSON_SORT_KEYS"] = False

    app.register_blueprint(data_dashboard_bp)
    app.register_blueprint(guardian_bp)
    app.register_blueprint(pipeline_bp)
    app.register_blueprint(ai_bp)           # ← 新增
    app.register_blueprint(cash_bp)          # ← 新增
    app.register_blueprint(data_alert_bp)      # ← 新增
    app.register_blueprint(regulatory_bp)        # ← 新增
    app.register_blueprint(actions_bp)            # ← 新增
    app.register_blueprint(rag_stats_bp)
    app.register_blueprint(intelligence_bp)

    return app


app = create_app()

if __name__ == "__main__":
    print(f"""
    ╔════════════════════════════════════════════════════════════════╗
    ║         ARGUS Multi-Agent System — Web Frontend                ║
    ║  Listening on http://0.0.0.0:{API_PORT}/                     ║
    ╚════════════════════════════════════════════════════════════════╝
    """)
    app.run(debug=True, host='0.0.0.0', port=API_PORT)
