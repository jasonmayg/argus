"""
`query_econ_indicator` — point lookup of any economic indicator series
from the GCS CSVs AI_Treasury_Project writes daily.

Generalizes `get_fx_rate` (Phase 3A) to the full ~50 series defined in
AI_Treasury_Project's sources.yaml — CPI, M2, policy rate, 10y yield,
fx_reserves, etc. — without requiring the agent to know the FRED
series_id. The agent passes a friendly name like `us_cpi` and gets
the latest + prior observation back.

Allowlist + ID-shape validation prevent the agent from coaxing this
tool into reading arbitrary GCS blobs.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult

# Pattern AI_Treasury_Project's sources.yaml uses: <country>_<indicator>,
# lowercase + digits + underscores. Anchored to prevent paths like
# `../foo/bar` from being treated as series ids.
_SERIES_ID_PAT = re.compile(r"^[a-z][a-z0-9_]{1,40}$")

# Country prefixes we'll accept (matches data_dashboard.COUNTRY_PREFIX_RE).
# Restricting this prevents the agent from constructing arbitrary blob
# paths — only series tagged with a known country prefix get fetched.
_ALLOWED_PREFIXES = (
    "us", "ca", "uk", "de", "jp", "fr", "br", "in",
    "au", "mx", "cn", "eu", "kr",
)

# Recent rows are the most useful signal; truncating keeps tool output
# under the LLM's context budget. `tail` controls how many rows the
# agent sees in both `content` and `structured["tail"]`; `total_rows`
# in the structured payload reports how many were available so the
# agent can request a larger `tail` if it needs more history.
_DEFAULT_TAIL = 10
_MAX_TAIL = 60


class QueryEconIndicator(Tool):
    name = "query_econ_indicator"
    description = (
        "Look up a recent slice of an economic indicator series (CPI, M2, "
        "policy rate, 10y yield, etc.). Series ids follow the "
        "`<country>_<indicator>` convention from AI_Treasury_Project — e.g. "
        "`us_cpi`, `cn_m2`, `jp_policy_rate`, `eu_10y_yield`. Returns the most "
        "recent `tail` rows so the agent can reason about level + direction."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "series_id": {
                "type": "string",
                "description": (
                    "Series id in `<country>_<indicator>` form. Country prefix must "
                    "be one of: " + ", ".join(_ALLOWED_PREFIXES) + "."
                ),
            },
            "tail": {
                "type": "integer",
                "description": f"How many most-recent rows to return. 1-{_MAX_TAIL}, default {_DEFAULT_TAIL}.",
            },
        },
        "required": ["series_id"],
    }

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        series_id = (args.get("series_id") or "").strip().lower()
        if not series_id:
            return ToolResult.failure("series_id is required")
        if not _SERIES_ID_PAT.match(series_id):
            return ToolResult.failure(
                f"invalid series_id '{series_id}' — must match {_SERIES_ID_PAT.pattern}"
            )
        # Enforce the country-prefix allowlist
        prefix = series_id.split("_", 1)[0]
        if prefix not in _ALLOWED_PREFIXES:
            return ToolResult.failure(
                f"unsupported country prefix '{prefix}'. "
                f"Allowed: {', '.join(_ALLOWED_PREFIXES)}"
            )

        raw_tail = args.get("tail")
        if raw_tail in (None, ""):
            tail = _DEFAULT_TAIL
        else:
            try:
                tail = int(raw_tail)
            except (TypeError, ValueError):
                return ToolResult.failure(
                    f"tail must be an integer between 1 and {_MAX_TAIL}, got {raw_tail!r}"
                )
        tail = max(1, min(_MAX_TAIL, tail))

        rows = self._fetch_series(series_id)
        if rows is None:
            # Network/auth error — distinct from "file empty".
            return ToolResult.failure(f"GCS fetch failed for series '{series_id}'")
        if not rows:
            return ToolResult.failure(f"Series '{series_id}' returned no rows")

        recent = rows[-tail:]
        # Sample lines for the LLM. Series CSVs from AI_Treasury_Project
        # have at minimum (date, value) columns; some have extras we
        # surface as a single "extras: k=v, k=v" string.
        lines: List[str] = [f"Series {series_id}, last {len(recent)} obs:"]
        for r in recent:
            date = r.get("date") or r.get("DATE") or "?"
            value = r.get("value") or r.get("VALUE") or "?"
            extras = {
                k: v for k, v in r.items()
                if k.lower() not in ("date", "value") and v not in (None, "")
            }
            extras_txt = f"  {extras}" if extras else ""
            lines.append(f"  {date}  {value}{extras_txt}")

        return ToolResult.success(
            content="\n".join(lines),
            structured={
                "series_id": series_id,
                "total_rows": len(rows),
                "latest": rows[-1],
                "prior": rows[-2] if len(rows) >= 2 else None,
                "tail": recent,
            },
        )

    # ──────────────────────────────────────────────────────────
    def _fetch_series(self, series_id: str) -> Optional[List[Dict[str, str]]]:
        """Read `<series_id>.csv` from GCS. Returns:
          * non-empty list on success
          * empty list when the file exists but has 0 data rows
          * None on transport / auth error (caller surfaces differently
            from 'empty')."""
        try:
            from data_dashboard import (
                _get_gcs_bucket,
                _resolve_data_dashboard_prefix,
            )
            bucket = _get_gcs_bucket()
            prefix = _resolve_data_dashboard_prefix(bucket)
            blob = bucket.blob(f"{prefix}{series_id}.csv")
            if not blob.exists():
                blob = bucket.blob(f"{series_id}.csv")
                if not blob.exists():
                    return []
            text = blob.download_as_text(encoding="utf-8")
            return [dict(r) for r in csv.DictReader(io.StringIO(text)) if r]
        except Exception:
            return None
