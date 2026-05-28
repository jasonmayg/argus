#!/usr/bin/env bash
# diag_regulatory.sh — print the regulatory_changes summary for the local
# SQLite db. Run on each deploy (staging + prod) and diff the outputs to
# locate divergence between environments — total rows, source coverage,
# date range, latest classification timestamp.
#
# Usage:
#   bash scripts/diag_regulatory.sh
#   ARGUS_NEWS_DB=/custom/path.db bash scripts/diag_regulatory.sh
#
# Exit codes:
#   0  ok
#   1  db not found
#   2  table not present (run classify_regulatory.py first)
#   3  sqlite3 itself failed (db locked, corrupt, permissions, etc.)

set -euo pipefail

DB="${ARGUS_NEWS_DB:-$HOME/argus/data/fed_news/argus_news.db}"

if [[ ! -f "$DB" ]]; then
    echo "ERROR: db not found at $DB" >&2
    echo "  Override with ARGUS_NEWS_DB=/path/to/argus_news.db" >&2
    exit 1
fi

# Verify the table exists before SELECTing. Done in two steps —
# capture sqlite's exit + stderr separately so a real DB-side failure
# (locked, corrupt, permissions denied) gets surfaced as exit 3 with
# the driver's actual message, instead of getting collapsed into the
# "table not present" diagnosis and pointing operators at the wrong
# fix. The earlier `if ! sqlite3 ... | grep -q` form had this trap
# because `set -euo pipefail` lets the failing pipe propagate but
# `if !` catches it, so an empty stdout looked indistinguishable
# from "table missing".
table_check=$(sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='regulatory_changes';" 2>&1) || {
    echo "ERROR: sqlite3 failed on $DB:" >&2
    echo "  $table_check" >&2
    exit 3
}
if [[ "$table_check" != *regulatory_changes* ]]; then
    echo "ERROR: table 'regulatory_changes' not present in $DB" >&2
    echo "  Run: python classify_regulatory.py" >&2
    exit 2
fi

echo "=== db: $DB ==="
echo "=== host: $(hostname) ==="
echo ""

echo "=== summary ==="
sqlite3 -header -column "$DB" "SELECT COUNT(*) AS total_rows, COUNT(DISTINCT source_type) AS distinct_sources, MIN(published_at) AS earliest_pub, MAX(published_at) AS latest_pub, MAX(classified_at) AS last_classified_at, SUM(CASE WHEN confidence >= 0.7 THEN 1 ELSE 0 END) AS high_conf_rows FROM regulatory_changes;"

echo ""
echo "=== by source ==="
sqlite3 -header -column "$DB" "SELECT source_type, COUNT(*) AS rows, MAX(published_at) AS latest_pub FROM regulatory_changes GROUP BY source_type ORDER BY rows DESC;"

echo ""
echo "=== classification activity by month (last 12 mo) ==="
# Use classified_at (always ISO UTC) instead of published_at — the latter
# is free-text per source ("Wed, 29 Apr 2026 09:30:00 +0100" vs raw ISO),
# so substr(...) groupings would be garbage.
sqlite3 -header -column "$DB" "SELECT substr(classified_at, 1, 7) AS month, COUNT(*) AS rows FROM regulatory_changes WHERE classified_at IS NOT NULL AND classified_at >= datetime('now', '-12 months') GROUP BY month ORDER BY month DESC;"
