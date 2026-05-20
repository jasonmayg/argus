"""`core.agents` — agent package.

Phase 3 added concrete agent classes here (`intelligence_agent`,
`response_agent`, `actions_agent`). Adding the package directory
shadowed the pre-existing `core/agents.py` module file, which broke
every legacy caller importing `PERSONA_REGISTRY` / `AnalysisAgent`
/ `build_alert_list` / … from `core.agents`. Python resolves the
package directory ahead of the sibling `.py` file, so the legacy
file became unreachable on a fresh deploy.

Fix: legacy module renamed to `core/agents/legacy.py` and its
public surface re-exported here. Callers continue to write
`from core.agents import PERSONA_REGISTRY` (etc.) and get the
expected behavior; new code targeting Phase 3 agents imports the
submodule directly, e.g.
`from core.agents.intelligence_agent import run_intelligence`.
"""

# Re-export the legacy public surface so historical callers keep
# working without churn. New names from Phase 3+ submodules should
# NOT be added here — import them through their submodule directly
# so the legacy/new split stays visible.
from core.agents.legacy import (  # noqa: F401
    # Constants / registries the routes read.
    FX_RELATED_CATEGORIES,
    PERSONA_CARD_SPECS,
    PERSONA_REGISTRY,
    # Agent classes.
    Agent,
    AnalysisAgent,
    # Builder functions used by routes / agents_system.
    build_alert_list,
    build_cash_manager_dashboard,
    build_country_board,
    build_fx_summary,
    build_generic_persona_dashboard,
    load_monitored_countries,
    # Utility used by external modules.
    resolve_alphabet_root,
)
