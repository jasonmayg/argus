#!/usr/bin/env python3
"""
ARGUS: Daily Monitoring Job
Fetches Federal Reserve news and runs the Guardian POC daily snapshot.
All data is written to shared_storage/raw/ via the unified DataAgent.
"""

import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict

from core.data_agent import DataAgent


class DailyMonitor:
    """Daily monitoring system for ARGUS."""

    def __init__(self):
        self._data_agent = DataAgent()
        self.guardian_poc_path = (
            Path(__file__).parent / "guardian_poc" / "src" / "daily_job.py"
        )

    def _run_guardian_job(self, verbose: bool = True) -> Dict:
        """Run Guardian POC daily job to classify global events into risk alerts."""
        guardian_result = {
            "status": "skipped",
            "message": "Guardian POC daily_job.py not found",
            "events_count": 0,
            "alerts_count": 0,
            "triggered_count": 0,
        }

        if not self.guardian_poc_path.exists():
            if verbose:
                print(f"  Guardian POC daily job not found at {self.guardian_poc_path}")
            return guardian_result

        try:
            if verbose:
                print("\n[2/4] Running Guardian POC daily job...")

            cmd = [
                sys.executable,
                str(self.guardian_poc_path),
                "--persona", "treasury_ops",
                "--fallback-demo-if-empty",
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=Path(__file__).parent,
            )

            if result.returncode != 0:
                guardian_result["status"] = "error"
                guardian_result["message"] = f"Guardian job failed: {result.stderr}"
                if verbose:
                    print(f"  Guardian job error: {result.stderr[:200]}")
                return guardian_result

            output = result.stdout.strip()
            if verbose:
                print(f"  {output}")

            for part in output.split():
                if part.startswith("events="):
                    guardian_result["events_count"] = int(part.split("=")[1])
                elif part.startswith("alerts="):
                    guardian_result["alerts_count"] = int(part.split("=")[1])
                elif part.startswith("triggered="):
                    guardian_result["triggered_count"] = int(part.split("=")[1])

            guardian_result["status"] = "success"
            guardian_result["message"] = output

            # Phase 2E: mirror the fresh snapshot into Chroma so semantic
            # alert retrieval picks up new events without waiting for the
            # next backfill run.
            mirrored = self._mirror_alerts_to_chroma(verbose=verbose)
            guardian_result["chroma_mirrored"] = mirrored

            return guardian_result

        except subprocess.TimeoutExpired:
            guardian_result["status"] = "error"
            guardian_result["message"] = "Guardian job timed out (>120s)"
            if verbose:
                print("  Guardian job timed out")
            return guardian_result
        except Exception as e:
            guardian_result["status"] = "error"
            guardian_result["message"] = f"Guardian job exception: {str(e)}"
            if verbose:
                print(f"  Guardian job error: {e}")
            return guardian_result

    def _mirror_alerts_to_chroma(self, verbose: bool = True) -> int:
        """Read the freshly-produced Guardian snapshot and dual-write its
        alerts to Chroma. Fail-soft: returns 0 on any error."""
        snapshot_path = (
            Path(__file__).parent / "guardian_poc" / "data" / "alerts_latest.json"
        )
        if not snapshot_path.exists():
            return 0
        try:
            snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception as e:
            if verbose:
                print(f"  Chroma mirror: cannot read {snapshot_path.name} ({e})")
            return 0

        alerts = snap.get("alerts") or snap.get("scored_alerts") or []
        snapshot_date = (
            snap.get("generated_at", "")[:10]
            or snap.get("date")
            or datetime.now().strftime("%Y-%m-%d")
        )
        if not alerts:
            return 0

        try:
            from core.chroma_writer import dual_write_alerts
            n = dual_write_alerts(snapshot_date, alerts)
        except Exception as e:
            if verbose:
                print(f"  Chroma mirror: dual-write failed ({e})")
            return 0

        if verbose and n:
            print(f"  Chroma mirror: {n} alerts upserted (snapshot_date={snapshot_date})")
        return n

    async def run(self, verbose: bool = True) -> Dict:
        """Execute daily monitoring.

        Steps:
            1. Fetch Federal Reserve news via DataAgent → shared_storage/raw/
            2. Run Guardian POC daily job (external subprocess)
            3. Save monitoring log to data/logs/
        """
        if verbose:
            print("\n" + "=" * 70)
            print(f"ARGUS Daily Monitoring  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 70)

        result: Dict = {"timestamp": datetime.now().isoformat(), "status": "running"}

        try:
            # Step 1 — Crawl Federal Reserve news
            if verbose:
                print("\n[1/4] Fetching Federal Reserve news...")

            fetch_result = await self._data_agent.fetch(
                sources=["federal_reserve"],
                write_raw=True,
            )

            if fetch_result["status"] == "failed":
                result["status"] = "error"
                result["error"] = "DataAgent failed to fetch federal_reserve"
                return result

            items = fetch_result["items"]
            paths = fetch_result["paths"]

            if verbose:
                print(f"  Found {len(items)} news items")
                for p in paths:
                    print(f"  Wrote → {p}")

            result["news_count"] = len(items)
            result["raw_paths"] = [str(p) for p in paths]

            # Step 2 — Guardian POC daily job
            guardian_result = self._run_guardian_job(verbose=verbose)
            result["guardian"] = guardian_result

            # Step 3 — Summary
            if verbose:
                print("\n[3/4] Summary...")

            result["status"] = "success"

            if verbose:
                self._print_summary(result)

            # Step 4 — Save log
            self._save_log(result, verbose=verbose)

            return result

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            if verbose:
                print(f"\n  Error: {e}")
            return result

    def _print_summary(self, result: Dict):
        print("\n" + "=" * 70)
        print("Monitoring Summary")
        print("=" * 70)
        print(f"Status: {result['status']}")
        print(f"\nFederal Reserve News:")
        print(f"  News items fetched : {result.get('news_count', 0)}")
        for p in result.get("raw_paths", []):
            print(f"  Raw file written   : {p}")

        guardian = result.get("guardian", {})
        if guardian:
            print(f"\nGuardian POC Global Event Classification:")
            print(f"  Status             : {guardian.get('status', 'unknown')}")
            if guardian.get("events_count"):
                print(f"  Raw events fetched : {guardian['events_count']}")
            if guardian.get("alerts_count"):
                print(f"  Classified alerts  : {guardian['alerts_count']}")
            if guardian.get("triggered_count"):
                print(f"  High-risk triggered: {guardian['triggered_count']}")
            if guardian.get("status") in ("error", "skipped") and guardian.get("message"):
                print(f"  Message            : {guardian['message']}")
        print("=" * 70)

    def _save_log(self, result: Dict, verbose: bool = True):
        log_dir = Path("data/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"daily_monitor_{datetime.now().strftime('%Y%m%d')}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"\n[4/4] Log saved: {log_file}")


async def main():
    monitor = DailyMonitor()
    result = await monitor.run(verbose=True)
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    asyncio.run(main())
