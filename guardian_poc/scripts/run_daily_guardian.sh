#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

python3 guardian_poc/src/daily_job.py \
  --persona treasury_ops \
  --max-records 120 \
  --fallback-demo-if-empty \
  --sources-config guardian_poc/config/data_sources.yaml \
  --output-dir guardian_poc/data/snapshots \
  --latest-path guardian_poc/data/alerts_latest.json
