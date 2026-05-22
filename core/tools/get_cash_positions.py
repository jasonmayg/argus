"""
`get_cash_positions` — current cash positions by country.

Phase 3D ActionsAgent uses this to ground recommendations in real
exposure data ("Mexico has $4.2M USD-equivalent at risk → suggest
hedging" beats "Mexico exposure" with no number). The agent calls
this with no args for the full table, or with `?country=...` to
pull just one row.

Data source: the same Excel workbook `/api/cash/positions` serves
(`data/cash_positions.xlsx` locally, or GCS-backed via
`ARGUS_CASH_GCS_PATH`). We call the existing cache-aware loader so
agent calls during a request burst don't each re-download the
spreadsheet — the cache TTL (`CASH_CACHE_TTL`, default 5 min)
shapes refresh cadence.

Fail-soft contract: if the loader raises (missing file, GCS auth,
pandas not installed), the tool returns ToolResult.failure with a
truncated cause string. The agent gets a clear "exposure data
unavailable" signal and can either skip cash-anchored
recommendations or note the data gap explicitly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult

# Cap how many position rows we serialize back to the LLM. The full
# workbook has ~15-20 countries today; capping at 50 leaves headroom
# without ever flooding the agent's context window.
_MAX_ROWS = 50


class GetCashPositions(Tool):
    name = "get_cash_positions"
    description = (
        "Look up current cash positions across treasury entities. "
        "Returns USD-equivalent balance, local-currency balance, FX rate, "
        "30-day change %, YTD change %, and risk_level per country. Pass "
        "an optional `country` filter (e.g. 'Mexico', 'United States') "
        "to get just one row; omit to get the full table (capped at 50)."
    )
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "country": {
                "type": "string",
                "description": (
                    "Optional case-insensitive country name filter. "
                    "Must match the spreadsheet's `Country` column exactly "
                    "(common values: 'United States', 'Mexico', 'United Kingdom', "
                    "'Japan', 'China', 'Germany', 'France', 'Canada', 'Brazil', "
                    "'India', 'Australia'). Omit for the full table."
                ),
            },
        },
        "required": [],
    }

    def __init__(self, request_id: Optional[str] = None) -> None:
        # request_id accepted for parity with the search_* tools — not
        # currently logged anywhere (no rag_queries equivalent for cash),
        # but the agent_runner instantiation site passes it generically.
        self.request_id = request_id

    def execute(self, args: Dict[str, Any]) -> ToolResult:
        country_filter = (args.get("country") or "").strip().lower() or None

        try:
            rows = self._fetch_positions()
        except Exception as e:
            return ToolResult.failure(
                f"cash positions unavailable: {type(e).__name__}: {str(e)[:200]}"
            )

        if not rows:
            return ToolResult.success(
                content="No cash position rows found.",
                # Keep `row_count` present on every success path so the
                # structured contract stays stable for downstream agent
                # code — `result["structured"]["row_count"]` is always safe.
                structured={"positions": [], "row_count": 0},
            )

        if country_filter:
            rows = [r for r in rows if (r.get("country") or "").lower() == country_filter]
            if not rows:
                return ToolResult.success(
                    content=f"No cash position found for country='{country_filter}'.",
                    structured={"positions": [], "row_count": 0},
                )

        rows = rows[:_MAX_ROWS]
        lines = ["Cash positions (USD-equiv):"]
        for r in rows:
            lines.append(self._format_row(r))
        return ToolResult.success(
            content="\n".join(lines),
            structured={"positions": rows, "row_count": len(rows)},
        )

    @staticmethod
    def _format_row(r: Dict[str, Any]) -> str:
        """Compact one-line render for the LLM context. Includes the
        fields the agent will most often reason on."""
        country = r.get("country") or "?"
        usd     = r.get("usd_equivalent")
        ccy     = r.get("currency") or ""
        chg30   = r.get("change_30d_pct")
        risk    = r.get("risk_level") or "?"
        usd_str = f"${usd:,.0f}" if isinstance(usd, (int, float)) else str(usd)
        chg_str = f"{chg30:+.1f}%" if isinstance(chg30, (int, float)) else "n/a"
        return f"  - {country:18s} {usd_str:>15}  ({ccy:>3})  30d {chg_str:>7}  risk={risk}"

    def _fetch_positions(self) -> List[Dict[str, Any]]:
        """Read positions through the same cache-aware loader the
        `/api/cash/positions` endpoint uses. Imported lazily so this
        tool's import surface stays small — pandas / openpyxl /
        google-cloud-storage don't load until first call."""
        from routes.cash_routes import _get_positions, _load_excel, _cache_valid

        # Trigger reload if cache is stale. Same logic the route handler
        # uses; keeping it here so the tool doesn't get colder data than
        # the dashboard would show.
        if not _cache_valid():
            _load_excel()
        return _get_positions() or []
