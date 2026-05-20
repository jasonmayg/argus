"""
`get_fx_rate` — point lookup of a USD-cross FX rate from the GCS CSVs
that AI_Treasury_Project's FRED fetcher writes daily.

Phase 3A scope: USD-crossed pairs only (USDJPY, USDCNY, USDEUR, etc.).
Non-USD crosses (e.g. EURJPY) would require triangulating two series,
which we leave to Phase 3B+ when a richer FX surface is justified.

The agent uses this to ground "what's the current USDJPY?" or "how
has USDCNY moved since the last PBOC announcement?" without the LLM
inventing numbers.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult


# (currency_code → CSV id) — keep in sync with
# AI_Treasury_Project/src/config/sources.yaml.
_USD_PAIRS: Dict[str, str] = {
    "CAD": "ca_usdcad",
    "CNY": "cn_usdcny",
    "EUR": "eu_usdeur",
    "GBP": "uk_usdgbp",
    "JPY": "jp_usdjpy",
    "AUD": "au_usdaud",
    "KRW": "kr_usdkrw",
    "INR": "in_usdinr",
    "MXN": "mx_usdmxn",
}


def _resolve_csv_id(from_ccy: str, to_ccy: str) -> Optional[str]:
    """Normalize a (from, to) currency pair into the CSV id.
    Either order is fine — USDJPY or JPYUSD both resolve to jp_usdjpy."""
    fa, ta = from_ccy.upper(), to_ccy.upper()
    if fa == "USD" and ta in _USD_PAIRS:
        return _USD_PAIRS[ta]
    if ta == "USD" and fa in _USD_PAIRS:
        return _USD_PAIRS[fa]
    return None


class GetFxRate(Tool):
    name = "get_fx_rate"
    description = (
        "Look up the latest available FX rate for a USD pair from Argus's "
        "GCS-backed daily series. Supports USD vs CAD/CNY/EUR/GBP/JPY/AUD/"
        "KRW/INR/MXN. Returns the most recent observation and the prior "
        "value so the agent can reason about direction."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "from_ccy": {"type": "string", "description": "3-letter ISO currency (USD or major)."},
            "to_ccy":   {"type": "string", "description": "3-letter ISO currency (the other side of the pair)."},
        },
        "required": ["from_ccy", "to_ccy"],
    }

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        from_ccy = (args.get("from_ccy") or "").strip()
        to_ccy = (args.get("to_ccy") or "").strip()
        if not from_ccy or not to_ccy:
            return ToolResult.failure("from_ccy and to_ccy are required")

        csv_id = _resolve_csv_id(from_ccy, to_ccy)
        if csv_id is None:
            supported = ", ".join(sorted(_USD_PAIRS))
            return ToolResult.failure(
                f"Unsupported pair {from_ccy}/{to_ccy}. "
                f"Phase 3A supports USD vs {supported}."
            )

        rows = self._fetch_series(csv_id)
        if not rows:
            return ToolResult.failure(f"Series '{csv_id}' returned no rows")

        # AI_Treasury_Project's writer produces CSVs in DESCENDING date
        # order (newest first). Older code here assumed ascending and
        # was returning the OLDEST two rows as "latest" + "prior" —
        # confirmed against today's GCS bucket: eu_usdeur.csv begins
        # with 2026-05-15 and ends with 2026-04-06. Sort defensively so
        # this still works if the writer's order ever changes again.
        sorted_rows = sorted(rows, key=lambda r: (r.get("date") or ""))
        last = sorted_rows[-1]
        prior = sorted_rows[-2] if len(sorted_rows) >= 2 else None

        latest_val = last.get("value", "")
        latest_dt = last.get("date", "")
        bits = [
            f"Latest {csv_id}: {latest_val}  (date={latest_dt})",
        ]
        if prior:
            bits.append(f"Prior:   {prior.get('value', '')}  (date={prior.get('date', '')})")
        content = "\n".join(bits)

        return ToolResult.success(
            content=content,
            structured={
                "csv_id": csv_id,
                "latest": last,
                "prior": prior,
                "row_count": len(rows),
            },
        )

    def _fetch_series(self, csv_id: str) -> List[Dict[str, str]]:
        """Read `<csv_id>.csv` from GCS. Returns [] if the file is
        missing or unreadable — agents handle empty results via the
        ToolResult.failure path in execute()."""
        try:
            # Local import: keep test-time import light (GCS client is
            # heavy and adds startup cost for cases that stub the fetch).
            from data_dashboard import (
                _get_gcs_bucket,
                _resolve_data_dashboard_prefix,
            )
            bucket = _get_gcs_bucket()
            prefix = _resolve_data_dashboard_prefix(bucket)
            blob = bucket.blob(f"{prefix}{csv_id}.csv")
            if not blob.exists():
                blob = bucket.blob(f"{csv_id}.csv")
                if not blob.exists():
                    return []
            text = blob.download_as_text(encoding="utf-8")
            return [dict(r) for r in csv.DictReader(io.StringIO(text)) if r]
        except Exception:
            return []
