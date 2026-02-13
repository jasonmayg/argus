#!/usr/bin/env python3
"""
ARGUS Web Frontend
Flask-based web interface for the multi-agent system with real-time demonstrations
"""

import asyncio
import json
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from datetime import datetime
import threading

from agents_system import AgentOrchestrator


app = Flask(__name__)
CORS(app)

# Global orchestrator and execution state
orchestrator = AgentOrchestrator()
execution_state = {
    "is_running": False,
    "current_stage": None,
    "result": None,
    "agents_status": {}
}


@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')


@app.route('/api/status')
def get_status():
    """Get current system status"""
    return jsonify({
        "is_running": execution_state["is_running"],
        "current_stage": execution_state["current_stage"],
        "agents_status": orchestrator.get_agent_summary(),
        "timestamp": datetime.now().isoformat()
    })


@app.route('/api/execute', methods=['POST'])
def execute_pipeline():
    """Execute the analysis pipeline"""
    if execution_state["is_running"]:
        return jsonify({"error": "Pipeline already running"}), 400

    def run_pipeline():
        """Run pipeline in background thread"""
        execution_state["is_running"] = True
        try:
            # Run the async pipeline
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(orchestrator.execute_pipeline())
            loop.close()

            execution_state["result"] = result
            execution_state["current_stage"] = "completed"
        except Exception as e:
            execution_state["result"] = {"error": str(e)}
            execution_state["current_stage"] = "failed"
        finally:
            execution_state["is_running"] = False

    # Start pipeline in background thread
    thread = threading.Thread(target=run_pipeline)
    thread.daemon = True
    thread.start()

    return jsonify({"status": "started", "message": "Pipeline execution started"})


@app.route('/api/results')
def get_results():
    """Get latest execution results"""
    if execution_state["result"]:
        return jsonify(execution_state["result"])
    return jsonify({"status": "no_results"})


@app.route('/api/agents/logs/<agent_name>')
def get_agent_logs(agent_name):
    """Get logs for specific agent"""
    agent_map = {
        "news_gatherer": orchestrator.news_gatherer,
        "risk_classifier": orchestrator.risk_classifier,
        "database": orchestrator.database_manager,
        "analyzer": orchestrator.analyzer,
        "reporter": orchestrator.reporter
    }

    agent = agent_map.get(agent_name)
    if not agent:
        return jsonify({"error": "Agent not found"}), 404

    return jsonify({
        "agent": agent_name,
        "logs": agent.get_logs(),
        "status": agent.status
    })


@app.route('/api/agents/summary')
def get_agents_summary():
    """Get summary of all agents"""
    return jsonify(orchestrator.get_agent_summary())


@app.route('/api/demo/report')
def get_demo_report():
    """Get the latest generated report"""
    if orchestrator.reporter.reports:
        return jsonify(orchestrator.reporter.reports[-1])
    return jsonify({"status": "no_report_yet"})


@app.route('/api/reset', methods=['POST'])
def reset_system():
    """Reset the system"""
    global orchestrator
    orchestrator = AgentOrchestrator()
    execution_state["is_running"] = False
    execution_state["current_stage"] = None
    execution_state["result"] = None
    return jsonify({"status": "reset_complete"})


if __name__ == '__main__':
    print("""
    ╔════════════════════════════════════════════════════════════════╗
    ║                                                                ║
    ║         ARGUS Multi-Agent System - Web Frontend                ║
    ║                                                                ║
    ║  Starting Flask server on http://localhost:5000/              ║
    ║  Open your browser and navigate to the URL above               ║
    ║                                                                ║
    ╚════════════════════════════════════════════════════════════════╝
    """)
    app.run(debug=True, port=5000)
