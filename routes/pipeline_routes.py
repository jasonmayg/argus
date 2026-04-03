"""
Pipeline & utility route blueprint.
Handles:
  GET  /
  GET  /api/status
  POST /api/execute
  POST /api/analyze
  GET  /api/results
  GET  /api/agents/logs/<agent_name>
  GET  /api/agents/summary
  GET  /api/demo/report
  POST /api/reset
"""

import asyncio
import threading
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from agents_system import AgentOrchestrator, RiskClassifierAgent
from core.agents import AnalysisAgent
from core.json_utils import sanitize_for_json
from state import execution_state, get_orchestrator, reset_orchestrator

pipeline_bp = Blueprint("pipeline", __name__)


@pipeline_bp.route("/")
def index():
    return render_template("dashboard.html")


@pipeline_bp.route("/api/status")
def get_status():
    return jsonify({
        "is_running": execution_state["is_running"],
        "current_stage": execution_state["current_stage"],
        "agents_status": get_orchestrator().get_agent_summary(),
        "timestamp": datetime.now().isoformat(),
    })


@pipeline_bp.route("/api/execute", methods=["POST"])
def execute_pipeline():
    if execution_state["is_running"]:
        return jsonify({"error": "Pipeline already running"}), 400

    def run_pipeline():
        execution_state["is_running"] = True
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(get_orchestrator().execute_pipeline())
            loop.close()
            execution_state["result"] = result
            execution_state["current_stage"] = "completed"
        except Exception as e:
            execution_state["result"] = {"error": str(e)}
            execution_state["current_stage"] = "failed"
        finally:
            execution_state["is_running"] = False

    thread = threading.Thread(target=run_pipeline, daemon=True)
    thread.start()
    return jsonify({"status": "started", "message": "Pipeline execution started"})


@pipeline_bp.route("/api/analyze", methods=["POST"])
def analyze_items():
    """Synchronous analysis endpoint for the Bridge Manager.

    Accepts:
        POST {"news_items": [...], "source_file": "optional_label"}

    Returns:
        Enriched JSON with 2D risk-matrix scores for every item, plus an
        aggregate summary.  Runs the RiskClassifier and AnalysisAgent inline
        (blocking); use /api/execute for the full async pipeline instead.
    """
    body = request.get_json(silent=True) or {}
    news_items = body.get("news_items")

    if not news_items or not isinstance(news_items, list):
        return jsonify({"error": "Request body must include a non-empty 'news_items' list"}), 400

    source_file = str(body.get("source_file", "bridge_input"))

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        classifier = RiskClassifierAgent()
        classify_result = loop.run_until_complete(classifier.execute(news_items=news_items))
        if classify_result["status"] != "success":
            loop.close()
            return jsonify({"error": "Classification failed", "detail": classify_result}), 500

        analyzer = AnalysisAgent()
        analysis_result = loop.run_until_complete(analyzer.execute(classified_news=classify_result["data"]))
        loop.close()

        if analysis_result["status"] != "success":
            return jsonify({"error": "Analysis failed", "detail": analysis_result}), 500

        analysis = analysis_result["analysis"]
        by_level = analysis["by_risk_level"]

        return jsonify(sanitize_for_json({
            "status":       "success",
            "source_file":  source_file,
            "item_count":   len(news_items),
            "scored_items": analysis["scored_items"],
            "summary": {
                "extreme":        len(by_level.get("extreme", [])),
                "high":           len(by_level.get("high",    [])),
                "medium":         len(by_level.get("medium",  [])),
                "low":            len(by_level.get("low",     [])),
                "llm_scored":     analysis.get("llm_scored", False),
                "top_keywords":   analysis.get("top_keywords", []),
                "matrix_summary": analysis.get("matrix_summary", []),
            },
            "analyzed_at": datetime.now().isoformat(),
        }))

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@pipeline_bp.route("/api/results")
def get_results():
    if execution_state["result"]:
        return jsonify(sanitize_for_json(execution_state["result"]))
    return jsonify({"status": "no_results"})


@pipeline_bp.route("/api/agents/logs/<agent_name>")
def get_agent_logs(agent_name):
    orchestrator = get_orchestrator()
    agent_map = {
        "news_gatherer":  orchestrator.news_gatherer,
        "risk_classifier": orchestrator.risk_classifier,
        "database":        orchestrator.database_manager,
        "analyzer":        orchestrator.analyzer,
        "reporter":        orchestrator.reporter,
    }
    agent = agent_map.get(agent_name)
    if not agent:
        return jsonify({"error": "Agent not found"}), 404
    return jsonify({"agent": agent_name, "logs": agent.get_logs(), "status": agent.status})


@pipeline_bp.route("/api/agents/summary")
def get_agents_summary():
    return jsonify(get_orchestrator().get_agent_summary())


@pipeline_bp.route("/api/demo/report")
def get_demo_report():
    reporter = get_orchestrator().reporter
    if not reporter.reports:
        return jsonify({"recommendations": [], "top_keywords": []})
    return jsonify(sanitize_for_json(reporter.reports[-1]))


@pipeline_bp.route("/api/reset", methods=["POST"])
def reset_system():
    reset_orchestrator()
    execution_state["is_running"] = False
    execution_state["current_stage"] = None
    execution_state["result"] = None
    return jsonify({"status": "reset_complete"})
