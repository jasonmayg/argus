"""
Shared application state.
Holds the AgentOrchestrator instance and the execution-state dict so that
both the Flask app and route blueprints refer to the same live objects.
"""

from agents_system import AgentOrchestrator

# Mutable container so reset_orchestrator() can swap the instance without
# needing a `global` declaration inside blueprint route functions.
_holder: dict = {"orchestrator": AgentOrchestrator()}

execution_state: dict = {
    "is_running": False,
    "current_stage": None,
    "result": None,
    "agents_status": {},
}


def get_orchestrator() -> AgentOrchestrator:
    return _holder["orchestrator"]


def reset_orchestrator() -> None:
    _holder["orchestrator"] = AgentOrchestrator()
