"""
Unit tests for ``generate_actions._archive_to_chroma`` (Phase 2E).

The helper translates the nested generate_actions output shape into the
flat schema VectorStore.add_action_history expects. Field renames are
non-obvious (recommended_action → recommendation, why → why_now,
pros → recommendation_pros, etc.), and if any of them drifts silently
the Phase 2D few-shot retrieval starts returning empty docs while the
JSON cache still looks fine. These tests pin the mapping.

Stubs ``generate_actions.dual_write_action_history`` (imported lazily
inside _archive_to_chroma) so we never need ChromaDB at test time.

Run:
    pytest tests/test_actions_archive.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generate_actions import _archive_to_chroma  # noqa: E402


def _sample_result() -> dict:
    """A minimal but realistic generate_actions output."""
    return {
        "persona":      "fx_manager",
        "generated_at": "2026-05-19 01:30 UTC",
        "country_groups": [
            {
                "country": "China",
                "category_cards": [
                    {
                        "category":        "Capital Controls",
                        "effective_score": 78.4,
                        "top_alert_title": "PBOC tightens cross-border RMB rules",
                        "action": {
                            "recommended_action": "Reduce CNY exposure to <10% of book",
                            "why":                "PBOC capital controls escalating; settlement risk rising",
                            "expected_outcome":   "Lower tail risk on CNY positions",
                            "pros":               ["Limits drawdown", "Compliance buffer"],
                            "cons":               ["Carry give-up", "Rebalancing cost"],
                            "alternatives":       ["Hedge via NDF", "Diversify into HKD"],
                        },
                    },
                    {
                        "category":        "Banking Regulation",
                        "effective_score": 62.0,
                        "top_alert_title": "CBIRC issues new liquidity guidance",
                        "action": {
                            "recommended_action": "Refresh counterparty list",
                            "why":                "New liquidity rules tighten bank lending",
                            "pros":               ["Compliance"],
                            "cons":               ["Manual effort"],
                        },
                    },
                ],
            },
            {
                "country": "Mexico",
                "category_cards": [
                    {
                        "category":        "FX Volatility",
                        "effective_score": 55.0,
                        "top_alert_title": "Banxico signals dovish pivot",
                        "action": {
                            "recommended_action": "Trim USDMXN long",
                            "why":                "Banxico dovish; carry compresses",
                            "pros":               ["Lock gains"],
                            "cons":               ["Miss further rally"],
                        },
                    },
                ],
            },
        ],
    }


@pytest.fixture
def captured(monkeypatch) -> list:
    """Replace dual_write_action_history with a recorder. The hook is
    imported lazily inside _archive_to_chroma, so we patch it in the
    chroma_writer module rather than generate_actions module-level."""
    import core.chroma_writer as cw

    calls: list[dict] = []

    def fake(action: dict) -> bool:
        calls.append(action)
        return True

    monkeypatch.setattr(cw, "dual_write_action_history", fake)
    return calls


class TestArchiveToChroma:
    def test_each_card_mirrors_once(self, captured):
        _archive_to_chroma(_sample_result())
        assert len(captured) == 3, "one mirror call per category card"

    def test_field_mapping_flattens_nested_action(self, captured):
        _archive_to_chroma(_sample_result())
        first = captured[0]
        assert first["country"]             == "China"
        assert first["category"]            == "Capital Controls"
        assert first["persona"]             == "fx_manager"
        assert first["generated_at"]        == "2026-05-19 01:30 UTC"
        # nested action.recommended_action → flat recommendation
        assert first["recommendation"]      == "Reduce CNY exposure to <10% of book"
        # nested action.why → flat why_now
        assert first["why_now"]             == "PBOC capital controls escalating; settlement risk rising"
        # nested action.pros / cons → recommendation_pros / recommendation_cons
        assert first["recommendation_pros"] == ["Limits drawdown", "Compliance buffer"]
        assert first["recommendation_cons"] == ["Carry give-up", "Rebalancing cost"]
        # card-level fields pass through
        assert first["top_title"]           == "PBOC tightens cross-border RMB rules"
        assert first["effective_score"]     == 78.4

    def test_country_from_outer_group_not_card(self, captured):
        """country lives on the country_group, not the card — make sure
        the helper grabs it from the right place for each card it walks."""
        _archive_to_chroma(_sample_result())
        by_country = [c["country"] for c in captured]
        assert by_country == ["China", "China", "Mexico"]

    def test_empty_result_does_not_call_mirror(self, captured):
        _archive_to_chroma({"persona": "x", "generated_at": "", "country_groups": []})
        assert captured == []

    def test_card_with_missing_action_block(self, captured):
        """The action dict may be missing (LLM fallback path). The
        helper should still emit a payload — let the downstream
        recommendation='' guard in add_action_history decide to drop it."""
        result = {
            "persona": "fx_manager", "generated_at": "",
            "country_groups": [{
                "country": "EU",
                "category_cards": [{"category": "Monetary Policy",
                                    "effective_score": 50, "top_alert_title": "x"}],
            }],
        }
        _archive_to_chroma(result)
        assert len(captured) == 1
        assert captured[0]["recommendation"] == ""
        assert captured[0]["recommendation_pros"] == []
        assert captured[0]["recommendation_cons"] == []

    def test_mirror_failure_does_not_raise(self, monkeypatch):
        """Phase 2E contract: dual_write returning False (or even
        raising) must not propagate. The JSON cache is authoritative."""
        import core.chroma_writer as cw

        def boom(action):
            raise RuntimeError("chroma exploded")

        monkeypatch.setattr(cw, "dual_write_action_history", boom)
        # Should not raise — but currently _archive_to_chroma doesn't
        # catch, so we wrap and confirm the contract here. If it raises,
        # that's a regression worth catching.
        try:
            _archive_to_chroma(_sample_result())
        except Exception as e:
            pytest.fail(f"_archive_to_chroma must be fail-soft, got: {e}")
