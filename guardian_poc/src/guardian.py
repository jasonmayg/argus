from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
import yaml
from dateutil import parser as dt_parser

APP_TZ = ZoneInfo("America/Los_Angeles")

@dataclass
class Event:
    source: str
    event_id: str
    title: str
    summary: str
    url: str
    published_at: datetime


@dataclass
class ScoredAlert:
    country: str
    category_key: str
    category_label: str
    score: float
    severity: float
    recency: float
    source: str
    title: str
    url: str
    published_at: datetime
    recommendation: str
    recommendation_source: str = "rule"
    rationale: str = ""


class GuardianEngine:
    def __init__(
        self,
        countries_cfg: str,
        keywords_cfg: str,
        personas_cfg: str,
        sources_cfg: str | None = None,
        timeout_sec: int = 15,
    ) -> None:
        self.countries = self._load_yaml(countries_cfg)["countries"]
        self.risk_taxonomy = self._load_yaml(keywords_cfg)["risk_taxonomy"]
        self.personas = self._load_yaml(personas_cfg)["personas"]
        self.sources = (self._load_yaml(sources_cfg).get("sources", {}) if sources_cfg else {})
        self.timeout_sec = timeout_sec
        self.session = requests.Session()
        self.source_adapters: dict[str, Callable[[str, dict[str, Any], int], list[Event]]] = {
            "gdelt": self._adapter_gdelt,
            "reliefweb": self._adapter_reliefweb,
            "usgs": self._adapter_usgs,
            "fred": self._adapter_fred,
            "fiscal_data": self._adapter_fiscal_data,
            "noaa_nws": self._adapter_noaa_nws,
            "nasa_eonet": self._adapter_nasa_eonet,
            "bank_of_canada": self._adapter_bank_of_canada,
            "ofac_sls": self._adapter_ofac_sls,
            # Framework-ready placeholders.
            "uk_sanctions": self._adapter_placeholder,
            "un_sanctions": self._adapter_placeholder,
            "sec_edgar": self._adapter_sec_edgar,
            "world_bank": self._adapter_world_bank,
            "bis": self._adapter_placeholder,
            "ecb": self._adapter_ecb,
            "imf": self._adapter_imf,
            "eia": self._adapter_placeholder,
            "un_comtrade": self._adapter_placeholder,
            "noaa_cdo": self._adapter_placeholder,
            "openfema": self._adapter_placeholder,
            "acled": self._adapter_placeholder,
            "finnhub": self._adapter_placeholder,
            "newsapi": self._adapter_placeholder,
            "alpha_vantage": self._adapter_placeholder,
            "marinetraffic": self._adapter_placeholder,
            "copernicus_cds": self._adapter_placeholder,
            "cisa_kev": self._adapter_placeholder,
            "cisa_kev_github": self._adapter_placeholder,
            "nvd": self._adapter_placeholder,
            "mitre_attack_taxii": self._adapter_placeholder,
            "reddit": self._adapter_placeholder,
            "telegram": self._adapter_placeholder,
        }

    @staticmethod
    def _load_yaml(path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def fetch_events(self, max_records: int = 80) -> list[Event]:
        events: list[Event] = []
        for source_key, cfg in self.sources.items():
            if not self._source_enabled(source_key, default=False):
                continue
            adapter_name = str(cfg.get("adapter") or source_key)
            adapter = self.source_adapters.get(adapter_name)
            if adapter is None:
                continue
            try:
                events.extend(adapter(source_key, cfg, max_records))
            except Exception:
                # Keep ingestion resilient: one adapter failure should not stop the pipeline.
                continue
        return events

    def _source_enabled(self, key: str, default: bool) -> bool:
        cfg = self.sources.get(key)
        if cfg is None:
            return default
        return bool(cfg.get("enabled", default))

    def get_source_status(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key, cfg in self.sources.items():
            adapter_name = str(cfg.get("adapter") or key)
            adapter = self.source_adapters.get(adapter_name)
            implemented = adapter is not None and adapter.__name__ != "_adapter_placeholder"
            rows.append(
                {
                    "source": key,
                    "tier": cfg.get("tier"),
                    "enabled": bool(cfg.get("enabled", False)),
                    "adapter": adapter_name,
                    "implemented": implemented,
                }
            )
        return rows

    def _adapter_gdelt(self, _source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        risk_clause = (
            '(bank OR "capital controls" OR sanctions OR "payment regulation" OR '
            'earthquake OR flood OR conflict OR "supply disruption" OR '
            '"liquidity crunch" OR "credit downgrade")'
        )
        # Bias GDELT retrieval toward monitored countries so non-US markets appear consistently.
        country_terms: list[str] = []
        for country, c_cfg in self.countries.items():
            country_terms.append(f'"{country}"')
            for a in c_cfg.get("aliases", []):
                if isinstance(a, str) and a.strip():
                    country_terms.append(f'"{a.strip()}"')
        country_clause = "(" + " OR ".join(country_terms[:80]) + ")"
        query = f"{risk_clause} AND {country_clause}"
        url = str(cfg.get("url") or "https://api.gdeltproject.org/api/v2/doc/doc")
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(max_records),
            "sort": "DateDesc",
        }
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        out: list[Event] = []
        for item in data.get("articles", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue
            seen = item.get("seendate") or item.get("socialimage")
            published = self._parse_datetime(seen)
            out.append(
                Event(
                    source="gdelt",
                    event_id=str(item.get("url", title)),
                    title=title,
                    summary=(item.get("snippet") or "").strip(),
                    url=item.get("url") or "",
                    published_at=published,
                )
            )
        return out

    def _adapter_reliefweb(self, _source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        url = str(cfg.get("url") or "https://api.reliefweb.int/v1/reports")
        params = {
            "appname": "alphabet-guardian-poc",
            "limit": max_records // 2,
            "profile": "list",
            "sort[]": "date:desc",
        }
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        out: list[Event] = []
        for row in data.get("data", []):
            fields = row.get("fields", {})
            title = (fields.get("title") or "").strip()
            if not title:
                continue
            out.append(
                Event(
                    source="reliefweb",
                    event_id=str(row.get("id", title)),
                    title=title,
                    summary=(fields.get("body-html") or fields.get("body") or "")[:600],
                    url=fields.get("url") or "",
                    published_at=self._parse_datetime(fields.get("date", {}).get("created")),
                )
            )
        return out

    def _adapter_usgs(self, _source_key: str, cfg: dict[str, Any], _max_records: int = 80) -> list[Event]:
        url = str(cfg.get("url") or "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson")
        try:
            resp = self.session.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        out: list[Event] = []
        for item in data.get("features", []):
            props = item.get("properties", {})
            title = (props.get("title") or "").strip()
            if not title:
                continue
            ts = props.get("time")
            published = (
                datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
                if isinstance(ts, (int, float))
                else datetime.now(tz=timezone.utc)
            )
            out.append(
                Event(
                    source="usgs",
                    event_id=str(item.get("id", title)),
                    title=title,
                    summary=(props.get("place") or "").strip(),
                    url=props.get("url") or "",
                    published_at=published,
                )
            )
        return out

    def _adapter_fred(self, _source_key: str, cfg: dict[str, Any], _max_records: int = 80) -> list[Event]:
        url = cfg.get("url", "https://api.stlouisfed.org/fred/series/observations")
        api_key_var = ((cfg.get("env") or {}).get("api_key")) or "FRED_API_KEY"
        api_key = os.getenv(api_key_var, "").strip()
        if not api_key:
            return []

        out: list[Event] = []
        for series in cfg.get("series", []):
            series_id = series.get("id")
            if not series_id:
                continue
            params = {
                "series_id": str(series_id),
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
            }
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_sec)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue
            observations = data.get("observations", [])
            if not observations:
                continue
            latest = observations[-1]
            value = str(latest.get("value", "N/A"))
            date = latest.get("date")
            label = series.get("label", series_id)
            risk_hint = series.get("risk_hint", "market risk")
            out.append(
                Event(
                    source="fred",
                    event_id=f"fred-{series_id}-{date}",
                    title=f"US {label} update: {value}",
                    summary=f"FRED indicator {series_id} latest value is {value}. {risk_hint}.",
                    url="https://fred.stlouisfed.org/",
                    published_at=self._parse_datetime(date),
                )
            )
        return out

    def _adapter_fiscal_data(self, source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        url = str(cfg.get("url") or "")
        if not url:
            return []
        params = {"page[size]": min(max_records, 25), "sort": "-auction_date"}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        out: list[Event] = []
        for item in data.get("data", []):
            auction_date = item.get("auction_date") or item.get("record_date")
            security_type = item.get("security_type", "Treasury Auction")
            amount = item.get("offering_amt") or item.get("offering_amount")
            title = f"US Treasury {security_type} auction"
            summary = f"Auction amount: {amount}. treasury auction sovereign funding liquidity."
            out.append(
                Event(
                    source=source_key,
                    event_id=f"{source_key}-{item.get('record_date', auction_date)}-{security_type}",
                    title=title,
                    summary=summary,
                    url=url,
                    published_at=self._parse_datetime(auction_date),
                )
            )
        return out

    # NWS publishes a properties.severity field per alert, taking values
    # Extreme / Severe / Moderate / Minor / Unknown. Phase 2.7 persona
    # eval flagged that routine Minor advisories ("Small Craft Advisory",
    # "Special Weather Statement") were polluting disaster_risk for
    # risk_manager and treasury_ops — they're operational hazards but
    # not treasury-relevant. We keep only Extreme / Severe by default;
    # operators who need a wider net can override via cfg["severities"].
    _NWS_DEFAULT_SEVERITIES = ("Extreme", "Severe")

    def _adapter_noaa_nws(self, source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        url = str(cfg.get("url") or "https://api.weather.gov/alerts/active")
        # Allow YAML config to widen / narrow the severity filter without
        # a code change. Falsy / missing → use the default whitelist.
        allowed_severities = {
            s.strip() for s in (cfg.get("severities") or self._NWS_DEFAULT_SEVERITIES) if s
        }
        headers = {"User-Agent": "AlphabetTreasuryGuardian/1.0"}
        try:
            resp = self.session.get(url, timeout=self.timeout_sec, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        out: list[Event] = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            severity = (props.get("severity") or "").strip()
            # The treasury-relevant signal is in Extreme/Severe storms
            # affecting financial centers and port operations. Routine
            # marine and forecast advisories (Minor/Moderate) are
            # rejected here so persona_eval's alerts pool stays clean.
            if severity not in allowed_severities:
                continue
            title = (props.get("headline") or props.get("event") or "").strip()
            if not title:
                continue
            summary = (props.get("description") or props.get("instruction") or "")[:500]
            out.append(
                Event(
                    source=source_key,
                    event_id=str(props.get("id", title)),
                    title=title,
                    summary=f"{summary} severe weather disaster risk infrastructure disruption.",
                    url=props.get("uri") or url,
                    published_at=self._parse_datetime(props.get("sent") or props.get("onset")),
                )
            )
            if len(out) >= max_records:
                break
        return out

    def _adapter_nasa_eonet(self, source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        url = str(cfg.get("url") or "https://eonet.gsfc.nasa.gov/api/v3/events")
        params = {"limit": min(max_records, 50), "status": "open"}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        out: list[Event] = []
        for row in data.get("events", []):
            title = (row.get("title") or "").strip()
            if not title:
                continue
            cats = ", ".join(c.get("title", "") for c in row.get("categories", []))
            geos = row.get("geometry", [])
            published = self._parse_datetime((geos[-1].get("date") if geos else None))
            out.append(
                Event(
                    source=source_key,
                    event_id=str(row.get("id", title)),
                    title=title,
                    summary=f"{cats}. natural event disaster risk and potential supply disruption.",
                    url=row.get("link") or url,
                    published_at=published,
                )
            )
        return out

    def _adapter_bank_of_canada(self, source_key: str, cfg: dict[str, Any], _max_records: int = 80) -> list[Event]:
        url = str(cfg.get("url") or "")
        if not url:
            return []
        try:
            resp = self.session.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        observations = data.get("observations", [])
        if not observations:
            return []
        latest = observations[-1]
        date = latest.get("d")
        # Keep text generic; exact field keys vary by series group.
        summary = "Bank of Canada market update. foreign exchange and interest rate risk context."
        return [
            Event(
                source=source_key,
                event_id=f"{source_key}-{date}",
                title="Bank of Canada Valet daily market observation",
                summary=summary,
                url=url,
                published_at=self._parse_datetime(date),
            )
        ]

    def _adapter_ofac_sls(self, source_key: str, cfg: dict[str, Any], _max_records: int = 80) -> list[Event]:
        # Keep adapter wired but do not emit pseudo-events to avoid skewing country scores.
        # Configure a concrete OFAC export endpoint and parser before enabling sanctions events.
        _ = source_key, cfg
        return []

    def _adapter_world_bank(self, source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        url = str(cfg.get("url") or "")
        if not url:
            return []
        try:
            resp = self.session.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return []
        if not isinstance(payload, list) or len(payload) < 2:
            return []
        rows = payload[1] if isinstance(payload[1], list) else []

        def to_monitored_country(name: str) -> str | None:
            low = (name or "").lower()
            for c, c_cfg in self.countries.items():
                aliases = [c] + list(c_cfg.get("aliases", []))
                for a in aliases:
                    if low == str(a).lower():
                        return c
            return None

        latest_by_country: dict[str, dict[str, Any]] = {}
        for row in rows:
            c_name = ((row.get("country") or {}).get("value") or "").strip()
            c = to_monitored_country(c_name)
            if not c:
                continue
            value = row.get("value")
            date = str(row.get("date") or "")
            if value is None or not date:
                continue
            prev = latest_by_country.get(c)
            if prev is None or date > str(prev.get("date")):
                latest_by_country[c] = {"value": value, "date": date}

        out: list[Event] = []
        for c, item in latest_by_country.items():
            out.append(
                Event(
                    source=source_key,
                    event_id=f"{source_key}-{c}-{item['date']}",
                    title=f"{c} inflation indicator update",
                    summary=f"{c} CPI inflation indicator value {item['value']}. inflation market risk liquidity planning.",
                    url=url,
                    published_at=self._parse_datetime(f"{item['date']}-12-31" if len(item["date"]) == 4 else item["date"]),
                )
            )
            if len(out) >= max_records:
                break
        return out

    def _adapter_sec_edgar(self, source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        url = str(cfg.get("url") or "")
        if not url:
            return []
        user_agent = ((cfg.get("headers") or {}).get("user_agent") or "AlphabetTreasuryGuardian/1.0 contact@example.com")
        headers = {"User-Agent": user_agent}
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        filings = (data.get("filings") or {}).get("recent") or {}
        forms = filings.get("form") or []
        dates = filings.get("filingDate") or []
        accession = filings.get("accessionNumber") or []
        primary_doc = filings.get("primaryDocument") or []
        cik = str(data.get("cik", "1652044")).lstrip("0") or "1652044"
        cik_10 = cik.zfill(10)

        out: list[Event] = []
        for idx, form in enumerate(forms[: max_records * 3]):
            f = str(form or "").strip().upper()
            if f not in {"10-K", "10-Q", "8-K", "6-K"}:
                continue
            filing_date = dates[idx] if idx < len(dates) else None
            acc = str(accession[idx] if idx < len(accession) else "").strip()
            acc_plain = acc.replace("-", "")
            doc = str(primary_doc[idx] if idx < len(primary_doc) else "").strip()
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_plain}/{doc}" if acc_plain and doc else url
            title = f"Alphabet SEC filing {f}"
            summary = f"SEC {f} filing update for Alphabet. legal, regulatory and disclosure risk context for USA operations."
            out.append(
                Event(
                    source=source_key,
                    event_id=f"{source_key}-{acc or idx}",
                    title=title,
                    summary=summary,
                    url=filing_url,
                    published_at=self._parse_datetime(filing_date),
                )
            )
            if len(out) >= max_records:
                break
        return out

    def _adapter_ecb(self, source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        base_url = str(cfg.get("url") or "").rstrip("/")
        if not base_url:
            return []

        # EXR dataset keys; each series emits a compact FX event for monitored countries.
        series_specs = [
            ("D.USD.EUR.SP00.A", "USD/EUR", "USA Germany France euro area foreign exchange market risk"),
            ("D.GBP.EUR.SP00.A", "GBP/EUR", "UK Germany France euro area foreign exchange market risk"),
            ("D.JPY.EUR.SP00.A", "JPY/EUR", "Japan Germany France euro area foreign exchange market risk"),
            ("D.CAD.EUR.SP00.A", "CAD/EUR", "Canada Germany France euro area foreign exchange market risk"),
            ("D.AUD.EUR.SP00.A", "AUD/EUR", "Australia Germany France euro area foreign exchange market risk"),
            ("D.BRL.EUR.SP00.A", "BRL/EUR", "Brazil Germany France euro area foreign exchange market risk"),
            ("D.MXN.EUR.SP00.A", "MXN/EUR", "Mexico Germany France euro area foreign exchange market risk"),
            ("D.INR.EUR.SP00.A", "INR/EUR", "India Germany France euro area foreign exchange market risk"),
            ("D.CNY.EUR.SP00.A", "CNY/EUR", "China Germany France euro area foreign exchange market risk"),
        ]

        out: list[Event] = []
        for key, pair, country_context in series_specs:
            endpoint = f"{base_url}/EXR/{key}"
            params = {"format": "csvdata", "lastNObservations": 1}
            try:
                resp = self.session.get(endpoint, params=params, timeout=self.timeout_sec)
                resp.raise_for_status()
                text = resp.text
            except Exception:
                continue

            value = None
            time_period = None
            try:
                reader = csv.DictReader(text.splitlines())
                rows = [r for r in reader if isinstance(r, dict)]
                if rows:
                    row = rows[-1]
                    for obs_key in ("OBS_VALUE", "OBSVALUE", "value", "Value"):
                        if row.get(obs_key):
                            value = row.get(obs_key)
                            break
                    for t_key in ("TIME_PERIOD", "TIMEPERIOD", "time_period", "Date"):
                        if row.get(t_key):
                            time_period = row.get(t_key)
                            break
            except Exception:
                continue

            if value is None:
                continue
            out.append(
                Event(
                    source=source_key,
                    event_id=f"{source_key}-{pair}-{time_period or 'latest'}",
                    title=f"ECB FX reference rate {pair}: {value}",
                    summary=f"ECB reference rate update for {pair}. {country_context}.",
                    url=endpoint,
                    published_at=self._parse_datetime(time_period),
                )
            )
            if len(out) >= max_records:
                break
        return out

    def _adapter_imf(self, source_key: str, cfg: dict[str, Any], max_records: int = 80) -> list[Event]:
        base_url = str(cfg.get("url") or "").rstrip("/")
        if not base_url:
            return []
        indicator = "PCPIPCH"  # Inflation, annual %
        url = f"{base_url}/{indicator}"
        country_map = {
            "US": "USA",
            "GB": "UK",
            "DE": "Germany",
            "FR": "France",
            "JP": "Japan",
            "CN": "China",
            "IN": "India",
            "AU": "Australia",
            "CA": "Canada",
            "BR": "Brazil",
            "MX": "Mexico",
        }
        params = {"countries": ",".join(country_map.keys())}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout_sec)
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return []

        values = ((payload.get("values") or {}).get(indicator) or {})
        out: list[Event] = []
        for code, country in country_map.items():
            series = values.get(code) or {}
            if not isinstance(series, dict) or not series:
                continue
            # Pick latest numeric point.
            latest_year = None
            latest_val = None
            for year, val in series.items():
                try:
                    val_num = float(val)
                except Exception:
                    continue
                if latest_year is None or str(year) > str(latest_year):
                    latest_year = year
                    latest_val = val_num
            if latest_year is None:
                continue
            out.append(
                Event(
                    source=source_key,
                    event_id=f"{source_key}-{code}-{latest_year}",
                    title=f"IMF inflation projection update for {country}: {latest_val:.2f}%",
                    summary=f"{country} IMF inflation indicator update. inflation, rates and FX planning market risk.",
                    url=url,
                    published_at=self._parse_datetime(f"{latest_year}-12-31"),
                )
            )
            if len(out) >= max_records:
                break
        return out

    def _adapter_placeholder(self, _source_key: str, _cfg: dict[str, Any], _max_records: int = 80) -> list[Event]:
        return []

    @staticmethod
    def _clean_text(value: str, max_len: int) -> str:
        text = re.sub(r"\s+", " ", (value or "")).strip()
        return text[:max_len]

    def enhance_alerts_with_llm(
        self,
        alerts: list[ScoredAlert],
        persona: str,
        model: str,
        max_output_tokens: int,
        max_input_chars: int,
        max_alerts: int,
        timeout_sec: int,
    ) -> dict[str, Any]:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return {"enabled": False, "reason": "OPENAI_API_KEY not set"}

        selected = alerts[: max(0, max_alerts)]
        if not selected:
            return {"enabled": False, "reason": "no alerts to enhance"}

        compact_items: list[dict[str, Any]] = []
        char_budget = max(200, max_input_chars)
        for idx, a in enumerate(selected):
            item = {
                "idx": idx,
                "country": a.country,
                "category": a.category_label,
                "score": a.score,
                "title": self._clean_text(a.title, 180),
                "summary": self._clean_text(a.recommendation + " " + a.url, 180),
            }
            chunk = json.dumps(item, ensure_ascii=False)
            if len(chunk) > char_budget:
                break
            compact_items.append(item)
            char_budget -= len(chunk)

        if not compact_items:
            return {"enabled": False, "reason": "input budget too low"}

        system_prompt = (
            "You are a treasury risk co-pilot. Return JSON only. "
            "For each alert, produce one concise action recommendation (<=120 chars) "
            "and one rationale (<=140 chars), tailored to the persona."
        )
        user_prompt = {
            "persona": persona,
            "format": {"updates": [{"idx": 0, "recommendation": "string", "rationale": "string"}]},
            "alerts": compact_items,
        }
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": max_output_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            resp = self.session.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=timeout_sec,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            return {"enabled": False, "reason": f"llm_call_failed: {exc}"}

        content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        usage = payload.get("usage", {})
        try:
            parsed = json.loads(content)
            updates = parsed.get("updates", [])
        except Exception:
            return {"enabled": False, "reason": "llm_response_parse_failed", "usage": usage}

        for upd in updates:
            try:
                idx = int(upd.get("idx"))
            except Exception:
                continue
            if idx < 0 or idx >= len(selected):
                continue
            rec = self._clean_text(str(upd.get("recommendation", "")), 140)
            rat = self._clean_text(str(upd.get("rationale", "")), 180)
            if rec:
                selected[idx].recommendation = rec
                selected[idx].recommendation_source = "llm"
            if rat:
                selected[idx].rationale = rat
        return {
            "enabled": True,
            "updated": len(updates),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
        }

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if not value:
            return datetime.now(tz=timezone.utc)
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        text = str(value)
        # GDELT dates are often like YYYYMMDDHHMMSS.
        if re.fullmatch(r"\d{14}", text):
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        try:
            dt = dt_parser.parse(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return datetime.now(tz=timezone.utc)

    def score_events(
        self,
        events: list[Event],
        persona: str,
        country_overrides: dict[str, float] | None = None,
    ) -> list[ScoredAlert]:
        country_overrides = country_overrides or {}
        persona_cfg = self.personas[persona]
        risk_weights = persona_cfg["risk_weights"]
        alerts: list[ScoredAlert] = []

        for event in events:
            text = f"{event.title}\n{event.summary}".lower()
            countries = self._detect_countries(text)
            if not countries:
                continue
            categories = self._detect_categories(text)
            if not categories:
                categories = self._infer_categories(text=text, source=event.source)
            if not categories:
                continue

            base_severity = self._base_severity(event.source)
            recency = self._recency_score(event.published_at)

            for country in countries:
                base_weight = float(self.countries[country].get("base_weight", 1.0))
                final_country_weight = country_overrides.get(country, base_weight)
                for category_key, high_alert_hit in categories:
                    severity = min(base_severity + (0.2 if high_alert_hit else 0.0), 1.0)
                    persona_weight = float(risk_weights.get(category_key, 1.0))
                    score = max(0.0, min(100.0, 100.0 * severity * recency * final_country_weight * persona_weight))
                    recommendation = self._recommendation(category_key, country, score)
                    alerts.append(
                        ScoredAlert(
                            country=country,
                            category_key=category_key,
                            category_label=self.risk_taxonomy[category_key]["label"],
                            score=round(score, 2),
                            severity=round(severity, 3),
                            recency=round(recency, 3),
                            source=event.source,
                            title=event.title,
                            url=event.url,
                            published_at=event.published_at,
                            recommendation=recommendation,
                        )
                    )
        alerts.sort(key=lambda a: a.score, reverse=True)
        return alerts

    def _detect_countries(self, text: str) -> list[str]:
        found: list[str] = []
        for country, cfg in self.countries.items():
            aliases = [country] + list(cfg.get("aliases", []))
            for alias in aliases:
                pattern = r"\b" + re.escape(alias.lower()) + r"\b"
                if re.search(pattern, text):
                    found.append(country)
                    break
        return found

    def _detect_categories(self, text: str) -> list[tuple[str, bool]]:
        out: list[tuple[str, bool]] = []
        for key, cfg in self.risk_taxonomy.items():
            primary = cfg.get("primary", [])
            high_alert = cfg.get("high_alert", [])
            has_primary = any(k.lower() in text for k in primary)
            if not has_primary:
                continue
            has_high_alert = any(k.lower() in text for k in high_alert)
            out.append((key, has_high_alert))
        return out

    def _infer_categories(self, text: str, source: str) -> list[tuple[str, bool]]:
        # Fallback mapping prevents empty dashboards when external sources use phrasing
        # that does not exactly match keyword lists.
        #
        # Most central banks straddle market policy and prudential
        # supervision; we surface BOTH so persona weights for
        # market_risk vs regulatory_risk produce different top-Ns.
        # Bank of Japan stays single-category because BoJ press
        # releases historically run heavily on monetary policy and
        # adding regulatory dilutes signal. FCA UK is supervision-only.
        # U.S. Treasury straddles market policy (debt issuance, rates)
        # and sanctions / political tools.
        source_defaults: dict[str, list[str]] = {
            "world_bank":     ["market_risk", "credit_risk"],
            "fred":           ["market_risk", "liquidity_risk"],
            "fiscal_data":    ["liquidity_risk"],
            "bank_of_canada": ["market_risk", "regulatory_risk"],
            "imf":            ["market_risk", "credit_risk"],
            "ecb":            ["market_risk", "regulatory_risk"],
            "fed":            ["market_risk", "regulatory_risk"],
            "boe":            ["market_risk", "regulatory_risk"],
            "boj":            ["market_risk"],
            "fca":            ["regulatory_risk", "operational_risk"],
            "treasury":       ["market_risk", "political_risk"],
            "bis":            ["systematic_risk", "regulatory_risk"],
            "eia":            ["market_risk", "supply_risk"],
            "usgs":           ["disaster_risk"],
            "reliefweb":      ["disaster_risk"],
            "nasa_eonet":     ["disaster_risk"],
            "noaa_nws":       ["disaster_risk"],
            "openfema":       ["disaster_risk"],
            "ofac_sls":       ["political_risk", "regulatory_risk"],
            "uk_sanctions":   ["political_risk", "regulatory_risk"],
            "un_sanctions":   ["political_risk", "regulatory_risk"],
        }
        # Term map. Press release titles rarely contain risk_keywords.yaml
        # primary phrases ("foreign exchange", "currency devaluation"),
        # so we keep a permissive vocab of single-word triggers here.
        # Multi-category triggers (a single needle → two categories) are
        # appended sequentially so a "stress test" headline lands as
        # BOTH credit_risk and regulatory_risk.
        term_map: list[tuple[str, str]] = [
            ("inflation",       "market_risk"),
            ("cpi",             "market_risk"),
            ("interest",        "market_risk"),
            ("yield",           "market_risk"),
            # Central-bank vocabulary that hits press releases.
            ("monetary policy", "market_risk"),
            ("rate decision",   "market_risk"),
            ("fomc",            "market_risk"),
            ("liquidity",       "liquidity_risk"),
            # Credit / supervisory vocab.
            ("default",         "credit_risk"),
            ("downgrade",       "credit_risk"),
            ("stress test",     "credit_risk"),
            ("stress test",     "regulatory_risk"),    # also a supervisory action
            ("capital requirements", "regulatory_risk"),
            ("supervision",     "regulatory_risk"),
            ("supervisory",     "regulatory_risk"),
            # Political / sanctions.
            ("sanction",        "political_risk"),
            ("tariff",          "political_risk"),
            # Regulatory.
            ("regulation",      "regulatory_risk"),
            ("compliance",      "regulatory_risk"),
            ("enforcement",     "regulatory_risk"),
            ("fines",           "regulatory_risk"),
            ("tokenisation",    "regulatory_risk"),  # FCA/BoE crypto focus
            ("tokenization",    "regulatory_risk"),
            ("stablecoin",      "regulatory_risk"),
            ("digital asset",   "regulatory_risk"),
            # Operational.
            ("outage",          "operational_risk"),
            ("cyber",           "operational_risk"),
            # Disaster.
            ("earthquake",      "disaster_risk"),
            ("flood",           "disaster_risk"),
            ("wildfire",        "disaster_risk"),
            ("hurricane",       "disaster_risk"),
            ("supply",          "supply_risk"),
            ("shipping",        "supply_risk"),
        ]
        out_keys: list[str] = []
        for needle, key in term_map:
            if needle in text and key in self.risk_taxonomy and key not in out_keys:
                out_keys.append(key)
        for key in source_defaults.get(source, []):
            if key in self.risk_taxonomy and key not in out_keys:
                out_keys.append(key)
        return [(k, False) for k in out_keys]

    @staticmethod
    def _base_severity(source: str) -> float:
        if source == "usgs":
            return 0.8
        if source == "reliefweb":
            return 0.7
        if source == "fred":
            return 0.55
        if source == "ecb":
            # ECB publishes FX reference rates daily for every monitored
            # pair regardless of whether anything material changed. Phase
            # 2.7 persona eval flagged that at base_severity=0.6 these
            # routine publications dominate the top-N for every persona,
            # crowding out true risk signal. A daily-rate publication is
            # informational; only treat as alert-worthy at the floor.
            # When the adapter learns to fire only on > N bps moves
            # (a separate Phase 2.7 follow-up), this can be re-raised.
            return 0.3
        return 0.6

    @staticmethod
    def _recency_score(published_at: datetime) -> float:
        now = datetime.now(tz=timezone.utc)
        hours = max((now - published_at).total_seconds() / 3600.0, 0.0)
        return max(0.25, math.exp(-hours / 72.0))

    @staticmethod
    def _recommendation(category_key: str, country: str, score: float) -> str:
        urgency = "HIGH" if score >= 70 else "MEDIUM" if score >= 45 else "LOW"
        templates = {
            "market_risk": f"[{urgency}] {country}: tighten FX/rate limits and refresh hedge coverage.",
            "credit_risk": f"[{urgency}] {country}: review counterparty ratings and reduce unsecured exposure.",
            "liquidity_risk": f"[{urgency}] {country}: raise local liquidity buffers and pre-fund critical payments.",
            "operational_risk": f"[{urgency}] {country}: activate payment rail fallback and incident response runbook.",
            "political_risk": f"[{urgency}] {country}: increase sanctions and country-risk monitoring cadence.",
            "regulatory_risk": f"[{urgency}] {country}: validate compliance impacts and alternate cross-border routes.",
            "reputation_risk": f"[{urgency}] {country}: monitor sentiment escalation and prepare comms controls.",
            "systematic_risk": f"[{urgency}] {country}: run stress scenarios and tighten portfolio-wide risk guardrails.",
            "disaster_risk": f"[{urgency}] {country}: trigger BCP checks for treasury operations and banking connectivity.",
            "supply_risk": f"[{urgency}] {country}: diversify suppliers and secure contingency sourcing.",
            "demand_risk": f"[{urgency}] {country}: adjust cash forecasts and rebalance working-capital assumptions.",
            "process_risk": f"[{urgency}] {country}: harden process controls and validate cyber/logistics continuity.",
            "business_risk": f"[{urgency}] {country}: reassess legal/contract exposure and strategic dependencies.",
        }
        return templates.get(category_key, f"[{urgency}] {country}: review treasury risk controls.")


def parse_country_weight_overrides(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    out: dict[str, float] = {}
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        country, value = pair.split("=", 1)
        country = country.strip()
        try:
            out[country] = float(value.strip())
        except ValueError:
            continue
    return out


def format_markdown(alerts: list[ScoredAlert], threshold: float) -> str:
    lines = [
        "# Alphabet Treasury Guardian PoC",
        "",
        f"Alert threshold: {threshold}",
        "",
        "| Country | Score | Category | Source | Published (PT) | Recommendation |",
        "|---|---:|---|---|---|---|",
    ]
    for a in alerts:
        if a.score < threshold:
            continue
        when = a.published_at.astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M")
        rec = a.recommendation.replace("|", "/")
        if a.rationale:
            rec = f"{rec} ({a.rationale.replace('|', '/')})"
        if a.recommendation_source == "llm":
            rec = f"{rec} [LLM]"
        lines.append(f"| {a.country} | {a.score:.2f} | {a.category_label} | {a.source} | {when} | {rec} |")
    if len(lines) == 6:
        lines.append("| - | - | - | - | - | No alerts above threshold. |")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Global Treasury Guardian PoC")
    parser.add_argument("--persona", default="treasury_ops", choices=["fx_trader", "treasury_ops", "treasury_tech"])
    parser.add_argument("--country-weights", default="", help="Override country weights, e.g. China=1.5,India=1.2")
    parser.add_argument("--max-records", type=int, default=80)
    parser.add_argument("--poll-seconds", type=int, default=0, help="If >0, poll continuously at this interval.")
    parser.add_argument("--config-dir", default="guardian_poc/config")
    parser.add_argument("--sources-config", default="guardian_poc/config/data_sources.yaml")
    parser.add_argument("--show-sources", action="store_true", help="Print source registry status and exit.")
    parser.add_argument("--demo", action="store_true", help="Use built-in demo events when live APIs are unavailable.")
    parser.add_argument("--llm-enable", action="store_true", help="Enable LLM recommendation enhancement.")
    parser.add_argument("--llm-model", default="gpt-4o-mini")
    parser.add_argument("--llm-max-output-tokens", type=int, default=220)
    parser.add_argument("--llm-max-input-chars", type=int, default=2800)
    parser.add_argument("--llm-max-alerts", type=int, default=8)
    parser.add_argument("--llm-timeout-sec", type=int, default=20)
    return parser


def demo_events(countries: list[str] | None = None) -> list[Event]:
    now = datetime.now(tz=timezone.utc)
    countries = countries or ["USA", "UK", "Germany", "Japan", "France", "Canada", "Brazil", "India", "Australia", "Mexico", "China"]
    template_pool: dict[str, list[tuple[str, str]]] = {
        "market_risk": [
            ("gdelt", "inflation spike and foreign exchange volatility"),
            ("world_bank", "interest rate shock with bond yield spikes"),
            ("fred", "commodity price jump and currency devaluation pressure"),
        ],
        "credit_risk": [
            ("gdelt", "credit downgrade and default concern"),
            ("sec_edgar", "counterparty risk and non-performing loans stress"),
            ("bis", "bankruptcy headlines and sovereign default fears"),
        ],
        "liquidity_risk": [
            ("fiscal_data", "liquidity crunch in local banking system"),
            ("gdelt", "bank run chatter and interbank lending freeze"),
            ("bank_of_canada", "funding liquidity stress in money markets"),
        ],
        "operational_risk": [
            ("newsapi", "technology outage in payment operations"),
            ("finnhub", "human error and model failure incident"),
            ("reddit", "payment system outage discussion among market users"),
        ],
        "political_risk": [
            ("acled", "sanctions and tariff escalation"),
            ("gdelt", "trade wars and border closure rhetoric"),
            ("un_sanctions", "export controls and diplomatic decoupling signals"),
        ],
        "regulatory_risk": [
            ("imf", "capital controls and remittance restrictions"),
            ("ecb", "data residency and open banking compliance updates"),
            ("uk_sanctions", "regulatory enforcement and transfer ban concerns"),
        ],
        "reputation_risk": [
            ("reddit", "market rumors driving confidence concern"),
            ("telegram", "viral misinformation and confidence crisis discussion"),
            ("gdelt", "industry reputation pressure after governance scrutiny"),
        ],
        "systematic_risk": [
            ("bis", "panic sell and contagion concerns"),
            ("world_bank", "cross-market chain reaction stress"),
            ("alpha_vantage", "broad market suspension and circuit-breaker risk"),
        ],
        "disaster_risk": [
            ("usgs", "earthquake warning and infrastructure damage"),
            ("reliefweb", "flood impact and power grid failure"),
            ("noaa_nws", "hurricane alert and port closure risk"),
            ("nasa_eonet", "wildfire progression near logistics corridors"),
        ],
        "supply_risk": [
            ("un_comtrade", "supply delivery disruption and procurement delays"),
            ("marinetraffic", "shipment delay and logistics bottleneck"),
            ("eia", "raw material shortage and supply price shock"),
        ],
        "demand_risk": [
            ("finnhub", "demand volatility and sudden order cancellations"),
            ("newsapi", "consumer preference shift and inventory glut signals"),
            ("alpha_vantage", "price sensitivity and demand collapse concerns"),
        ],
        "process_risk": [
            ("cisa_kev", "cybersecurity incident and coordination failure"),
            ("nvd", "ransomware exposure and process continuity risk"),
            ("mitre_attack_taxii", "production service interruption via attack pattern"),
        ],
        "business_risk": [
            ("sec_edgar", "legal dispute and contractual breach concern"),
            ("gdelt", "strategic competition pressure in core markets"),
            ("world_bank", "structural business risk from policy shifts"),
        ],
    }
    category_keys = list(template_pool.keys())

    events: list[Event] = []
    day_key = now.strftime("%Y-%m-%d")
    for country in countries:
        h = hashlib.sha256(f"{day_key}-{country}".encode("utf-8")).hexdigest()
        # Deterministic 0-10 warnings per country for each day.
        n = int(h[:2], 16) % 11
        base_offset = int(h[2:4], 16)
        for i in range(n):
            cat = category_keys[(base_offset + i * 3) % len(category_keys)]
            cat_variants = template_pool[cat]
            source, phrase = cat_variants[(base_offset + i) % len(cat_variants)]
            high_alert = (int(h[(4 + i) % len(h)], 16) % 4) == 0
            severity_phrase = "state of emergency risk" if high_alert else "heightened monitoring required"
            events.append(
                Event(
                    source=source,
                    event_id=f"demo-{day_key}-{country}-{i}",
                    title=f"{country} treasury risk update: {phrase}",
                    summary=f"{country} reported {phrase}; {severity_phrase}.",
                    url=f"https://example.com/demo/{country.lower()}/{i}",
                    published_at=now,
                )
            )
    return events


def run_once(args: argparse.Namespace) -> None:
    engine = GuardianEngine(
        countries_cfg=f"{args.config_dir}/countries.yaml",
        keywords_cfg=f"{args.config_dir}/risk_keywords.yaml",
        personas_cfg=f"{args.config_dir}/personas.yaml",
        sources_cfg=args.sources_config,
    )
    events = demo_events() if args.demo else engine.fetch_events(max_records=args.max_records)
    overrides = parse_country_weight_overrides(args.country_weights)
    alerts = engine.score_events(events, persona=args.persona, country_overrides=overrides)
    threshold = float(engine.personas[args.persona]["alert_threshold"])
    llm_status: dict[str, Any] | None = None
    if args.llm_enable:
        ranked = [a for a in alerts if a.score >= threshold]
        llm_status = engine.enhance_alerts_with_llm(
            alerts=ranked,
            persona=args.persona,
            model=args.llm_model,
            max_output_tokens=args.llm_max_output_tokens,
            max_input_chars=args.llm_max_input_chars,
            max_alerts=args.llm_max_alerts,
            timeout_sec=args.llm_timeout_sec,
        )
    print(f"Fetched events: {len(events)} | Scored alerts: {len(alerts)}")
    if llm_status is not None:
        print(f"LLM: {json.dumps(llm_status, ensure_ascii=False)}")
    print(format_markdown(alerts, threshold=threshold))


def print_source_status(engine: GuardianEngine) -> None:
    rows = engine.get_source_status()
    print("| Source | Tier | Enabled | Adapter | Implemented |")
    print("|---|---:|---|---|---|")
    for r in rows:
        print(f"| {r['source']} | {r['tier']} | {r['enabled']} | {r['adapter']} | {r['implemented']} |")


def main() -> None:
    args = build_parser().parse_args()
    if args.show_sources:
        engine = GuardianEngine(
            countries_cfg=f"{args.config_dir}/countries.yaml",
            keywords_cfg=f"{args.config_dir}/risk_keywords.yaml",
            personas_cfg=f"{args.config_dir}/personas.yaml",
            sources_cfg=args.sources_config,
        )
        print_source_status(engine)
        return
    if args.poll_seconds <= 0:
        run_once(args)
        return
    while True:
        print(f"\n## Snapshot at {datetime.now(tz=APP_TZ).strftime('%Y-%m-%d %H:%M:%S')} PT\n")
        run_once(args)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
