"""
Unit tests for guardian_poc/src/guardian.py adapter noise filters.

Phase 2.7 persona eval surfaced two upstream noise sources that
distorted every persona's top-N:

  1. ECB FX reference rates — published daily for 9 currency pairs at
     base_severity=0.6 each, crowding out real risk signal.
  2. NOAA NWS routine advisories — Small Craft Advisory etc. coming
     through at base alert weight, polluting disaster_risk.

These tests lock in the fixes:
  * ECB events get the new low base_severity=0.3 floor
  * NOAA adapter only emits Severe / Extreme by default
  * Severity whitelist is configurable per source

Stubs `requests.Session.get` so no network is hit.

Run:
    pytest tests/test_guardian_adapters.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Guardian module is heavy — only import what we need so test startup
# stays under the rest of the suite's budget.
from guardian_poc.src.guardian import GuardianEngine  # noqa: E402


# ──────────────────────────────────────────────────────────────────
# Test fixtures — minimal in-memory GuardianEngine
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def guardian(tmp_path) -> GuardianEngine:
    """Build a GuardianEngine with empty yaml inputs so we can invoke
    adapters in isolation. The adapter methods don't read personas /
    sources / countries — they only need cfg + session."""
    personas = tmp_path / "p.yaml"
    sources = tmp_path / "s.yaml"
    countries = tmp_path / "c.yaml"
    keywords = tmp_path / "k.yaml"
    personas.write_text("personas: {}\n")
    sources.write_text("sources: {}\n")
    countries.write_text("countries: {}\n")
    keywords.write_text("risk_taxonomy: {}\n")
    g = GuardianEngine(
        countries_cfg=str(countries),
        keywords_cfg=str(keywords),
        personas_cfg=str(personas),
        sources_cfg=str(sources),
    )
    return g


def _stub_response(status_code: int, json_payload=None, text_payload: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_payload if json_payload is not None else {}
    resp.text = text_payload
    return resp


# ──────────────────────────────────────────────────────────────────
# _base_severity: ECB rate gets explicit low floor
# ──────────────────────────────────────────────────────────────────

class TestBaseSeverity:
    def test_ecb_is_below_default(self):
        """ECB rates publish daily regardless of materiality — they
        should not start at the same severity as a real event."""
        assert GuardianEngine._base_severity("ecb") < GuardianEngine._base_severity("gdelt")

    def test_ecb_specifically_03(self):
        """Pin the exact value so we notice when someone bumps it
        without revisiting Phase 2.7's noise findings."""
        assert GuardianEngine._base_severity("ecb") == 0.3

    def test_real_event_sources_still_higher(self):
        """Sanity: USGS / reliefweb / fred shouldn't have been touched."""
        assert GuardianEngine._base_severity("usgs") == 0.8
        assert GuardianEngine._base_severity("reliefweb") == 0.7
        assert GuardianEngine._base_severity("fred") == 0.55

    def test_unknown_source_default(self):
        assert GuardianEngine._base_severity("anything_else") == 0.6


# ──────────────────────────────────────────────────────────────────
# _adapter_noaa_nws: severity whitelist filters out routine advisories
# ──────────────────────────────────────────────────────────────────

def _nws_feature(headline: str, severity: str, alert_id: str = None) -> dict:
    return {
        "properties": {
            "id":         alert_id or f"nws-{headline[:30]}",
            "headline":   headline,
            "event":      headline,
            "severity":   severity,
            "description": f"{headline} description text",
            "sent":       "2026-05-19T12:00:00+00:00",
            "uri":        f"https://example/api/nws/{alert_id or headline[:30]}",
        }
    }


class TestNoaaNwsSeverityFilter:
    def test_severe_alert_kept(self, guardian, monkeypatch):
        payload = {"features": [
            _nws_feature("Hurricane Warning for Miami", "Severe"),
        ]}
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, payload))
        events = guardian._adapter_noaa_nws("noaa_nws", {})
        assert len(events) == 1
        assert "Hurricane" in events[0].title

    def test_extreme_alert_kept(self, guardian, monkeypatch):
        payload = {"features": [
            _nws_feature("Tornado Warning", "Extreme"),
        ]}
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, payload))
        events = guardian._adapter_noaa_nws("noaa_nws", {})
        assert len(events) == 1

    def test_small_craft_advisory_filtered(self, guardian, monkeypatch):
        """The Phase 2.7 finding: Small Craft Advisory is rated Moderate
        by NWS — it must NOT appear in the alerts pool by default."""
        payload = {"features": [
            _nws_feature("Small Craft Advisory", "Moderate"),
        ]}
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, payload))
        events = guardian._adapter_noaa_nws("noaa_nws", {})
        assert events == []

    def test_minor_filtered(self, guardian, monkeypatch):
        payload = {"features": [
            _nws_feature("Special Weather Statement", "Minor"),
        ]}
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, payload))
        events = guardian._adapter_noaa_nws("noaa_nws", {})
        assert events == []

    def test_unknown_severity_filtered(self, guardian, monkeypatch):
        """NWS sometimes emits severity='Unknown' for legacy or
        incomplete feeds. Default whitelist drops these — treating
        unknown as 'might be important' floods the pool again."""
        payload = {"features": [_nws_feature("Wind Statement", "Unknown")]}
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, payload))
        events = guardian._adapter_noaa_nws("noaa_nws", {})
        assert events == []

    def test_mixed_feed_only_keeps_qualifying(self, guardian, monkeypatch):
        payload = {"features": [
            _nws_feature("Hurricane Warning", "Severe", alert_id="a"),
            _nws_feature("Small Craft Advisory", "Moderate", alert_id="b"),
            _nws_feature("Special Weather Statement", "Minor", alert_id="c"),
            _nws_feature("Tornado Warning", "Extreme", alert_id="d"),
        ]}
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, payload))
        events = guardian._adapter_noaa_nws("noaa_nws", {})
        assert len(events) == 2
        titles = {e.title for e in events}
        assert titles == {"Hurricane Warning", "Tornado Warning"}

    def test_config_override_widens_filter(self, guardian, monkeypatch):
        """Operators can include Moderate via cfg if their use case
        requires it — the default is the noise-reducing whitelist,
        not a hardcoded rule."""
        payload = {"features": [
            _nws_feature("Hurricane Warning", "Severe", alert_id="a"),
            _nws_feature("Small Craft Advisory", "Moderate", alert_id="b"),
        ]}
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, payload))
        events = guardian._adapter_noaa_nws(
            "noaa_nws", {"severities": ["Severe", "Moderate"]},
        )
        assert len(events) == 2

    def test_empty_severity_field_treated_as_unknown(self, guardian, monkeypatch):
        payload = {"features": [{
            "properties": {
                "id": "x", "headline": "Some Alert", "event": "Some Alert",
                # missing severity entirely
                "description": "desc",
                "sent": "2026-05-19T12:00:00+00:00",
            },
        }]}
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, payload))
        events = guardian._adapter_noaa_nws("noaa_nws", {})
        # No severity → treated as unknown → filtered out
        assert events == []


# ──────────────────────────────────────────────────────────────────
# ECB adapter still emits events; the noise reduction is in scoring
# (via _base_severity), not in the adapter. Just sanity-check the
# adapter wires source="ecb" correctly so the severity case fires.
# ──────────────────────────────────────────────────────────────────

class TestEcbAdapterSourceTagging:
    def test_events_carry_source_ecb(self, guardian, monkeypatch):
        """The _base_severity hook only kicks in for source == 'ecb',
        so any drift in how the adapter tags its events would silently
        re-introduce the noise. Pin the contract here."""
        csv_text = (
            "TIME_PERIOD,OBS_VALUE\n"
            "2026-05-15,1.0852\n"
        )
        monkeypatch.setattr(guardian.session, "get",
                            lambda *_a, **_kw: _stub_response(200, text_payload=csv_text))
        events = guardian._adapter_ecb("ecb", {"url": "https://example/sdmx"})
        assert events, "adapter should emit at least one event with the stubbed CSV"
        for e in events:
            assert e.source == "ecb"
