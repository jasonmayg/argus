"""
Regression test for the `core.agents` package vs module shadowing
bug that took down deployment on 2026-05-19.

Adding `core/agents/intelligence_agent.py` in PR #48 turned
`core/agents/` into a package; Python then shadowed the sibling
`core/agents.py` file containing PERSONA_REGISTRY / AnalysisAgent /
the dashboard builders. On a fresh deploy (no stale .pyc cache),
every route doing `from core.agents import PERSONA_REGISTRY` died
at import time.

The fix renamed the file to `core/agents/legacy.py` and re-exported
its public surface from `core/agents/__init__.py`. These tests pin
that contract — a future refactor that drops a name from the
re-export list (or moves things into a non-package layout) breaks
a test rather than the deployment.

Run:
    pytest tests/test_core_agents_namespace.py -v
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestLegacyTopLevelImports:
    """Every name routes/agents_system/newsalerts import from
    `core.agents` MUST stay reachable via the top-level package.
    The list below is the actual import surface across the repo;
    keep them resolvable via this single entry."""

    def test_constants_resolvable(self):
        from core.agents import (
            FX_RELATED_CATEGORIES,
            PERSONA_CARD_SPECS,
            PERSONA_REGISTRY,
        )
        # Sanity: each must be the expected container shape, not a
        # placeholder. A trivial `= None` re-export would still pass
        # the import but break callers at use-time.
        assert isinstance(PERSONA_REGISTRY, dict) and PERSONA_REGISTRY
        assert isinstance(PERSONA_CARD_SPECS, dict)
        assert isinstance(FX_RELATED_CATEGORIES, (set, frozenset, list, tuple))

    def test_agent_classes_resolvable(self):
        from core.agents import Agent, AnalysisAgent
        # Both must be classes (the routes do `isinstance` checks
        # against AnalysisAgent in places).
        assert isinstance(Agent, type)
        assert isinstance(AnalysisAgent, type)
        # AnalysisAgent should inherit from Agent (real contract,
        # not just both being classes).
        assert issubclass(AnalysisAgent, Agent)

    def test_builder_functions_resolvable(self):
        from core.agents import (
            build_alert_list,
            build_cash_manager_dashboard,
            build_country_board,
            build_fx_summary,
            build_generic_persona_dashboard,
            load_monitored_countries,
            resolve_alphabet_root,
        )
        # callables, not None / not modules
        for fn in (build_alert_list, build_cash_manager_dashboard,
                   build_country_board, build_fx_summary,
                   build_generic_persona_dashboard,
                   load_monitored_countries, resolve_alphabet_root):
            assert callable(fn), f"{fn} re-export is not callable"


class TestPhase3SubmodulesCoexist:
    """The Phase 3 agents (added in PR #48) live as submodules under
    `core/agents/`. Both import patterns must work side-by-side."""

    def test_intelligence_submodule_importable(self):
        from core.agents.intelligence_agent import (
            run_intelligence, _TOOL_REGISTRY,
        )
        assert callable(run_intelligence)
        assert isinstance(_TOOL_REGISTRY, dict)
        # The 4 Phase 3B tools must be registered (defensive — if
        # someone deletes the package init re-exports they could
        # also accidentally break this).
        for name in ("search_news", "search_alerts",
                     "search_regulatory", "query_econ_indicator"):
            assert name in _TOOL_REGISTRY

    def test_package_dir_is_indeed_a_package(self):
        """Sanity check: `core.agents` should resolve to the package
        (the directory's __init__.py), not the legacy file. If a
        future refactor moves things back into a flat file, this
        test fails loudly so we don't reintroduce the shadowing."""
        import core.agents
        # Real packages have __path__; flat modules do not.
        assert hasattr(core.agents, "__path__"), (
            "core.agents is not a package — the directory layout "
            "regressed. Phase 3 submodules will be unreachable."
        )


class TestNoStaleModuleFileAtTopLevel:
    """Belt-and-braces: ensure `core/agents.py` (the flat file)
    doesn't reappear next to `core/agents/`. Having both creates
    the exact bug this test file was written about."""

    def test_no_flat_agents_file_alongside_package(self):
        flat = ROOT / "core" / "agents.py"
        package_dir = ROOT / "core" / "agents"
        assert package_dir.is_dir(), "expected core/agents/ to be a package"
        assert not flat.exists(), (
            "core/agents.py exists alongside core/agents/ package — "
            "Python will shadow the file with the package and break "
            "every legacy `from core.agents import X` caller."
        )

    def test_legacy_submodule_present(self):
        """The legacy content lives at `core/agents/legacy.py`. If
        someone deletes it (e.g. thinking it's unused) the
        re-exports in __init__ will fail at import time."""
        legacy_path = ROOT / "core" / "agents" / "legacy.py"
        assert legacy_path.exists(), (
            "core/agents/legacy.py was deleted — __init__.py "
            "re-exports will ImportError."
        )

    def test_init_module_can_reload_clean(self):
        """A fresh import (no stale cache) must succeed end-to-end.
        This catches the original deploy failure shape — locally we
        were masked by .pyc cache for hours before noticing."""
        # Drop any cached modules so the next import goes through
        # the real disk layout.
        for mod_name in list(sys.modules):
            if mod_name == "core.agents" or mod_name.startswith("core.agents."):
                del sys.modules[mod_name]
        importlib.invalidate_caches()
        # Use importlib instead of `from … import …` so this test
        # exercises the same code path Python takes on a fresh deploy.
        mod = importlib.import_module("core.agents")
        assert hasattr(mod, "PERSONA_REGISTRY")
        assert hasattr(mod, "AnalysisAgent")
