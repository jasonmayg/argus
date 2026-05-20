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
        # The GCS bridge — wraps `_load_guardian_snapshot_from_gcs` →
        # `GuardianEngine.score_events` → `alerts_latest.json` write.
        # See `docs/PERSONA_EVAL_REBASELINE_2026-05-19.md` for why
        # this exists alongside the legacy POC step.
        self.gcs_bridge_path = (
            Path(__file__).parent / "scripts" / "refresh_local_snapshot_from_gcs.py"
        )
        # Snapshot path is the contract between the bridge (writer)
        # and the Chroma mirror / persona_eval / actions API
        # (readers). Holding it as an attribute lets tests override
        # it without touching globals.
        self.snapshot_path = (
            Path(__file__).parent / "guardian_poc" / "data" / "alerts_latest.json"
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
                print("\n[2/5] Running Guardian POC daily job (legacy)...")

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

    def _run_gcs_bridge(self, verbose: bool = True) -> Dict:
        """Refresh `guardian_poc/data/alerts_latest.json` from the GCS
        bucket so the snapshot consumed by /api/actions, persona_eval,
        and the Chroma mirror reflects today's raw data with Guardian
        classification + scoring applied.

        This runs AFTER `_run_guardian_job` so that even when the
        legacy POC adapters succeed (and they mostly don't —
        2026-05-14 was the last refresh), the bridge's GCS-sourced
        snapshot is what ends up on disk. The chroma mirror call below
        then sees the up-to-date data.
        """
        bridge_result: Dict = {
            "status":          "skipped",
            "message":         "GCS bridge script not found",
            "events_count":    0,
            "alerts_count":    0,
            "triggered_count": 0,
            "chroma_mirrored": 0,
        }

        if not self.gcs_bridge_path.exists():
            if verbose:
                print(f"  GCS bridge not found at {self.gcs_bridge_path}")
            return bridge_result

        try:
            if verbose:
                print("\n[3/5] Refreshing snapshot from GCS via bridge…")

            cmd = [sys.executable, str(self.gcs_bridge_path)]
            result = subprocess.run(
                cmd,
                capture_output=True, text=True,
                # Bridge is ~5-10s end-to-end (GCS list + download +
                # scoring). 60s gives ample headroom without blocking
                # the daily monitor on a stuck network call.
                timeout=60,
                cwd=Path(__file__).parent,
            )
            if result.returncode != 0:
                bridge_result["status"] = "error"
                # Bridge logs via logging → stderr; surface that.
                bridge_result["message"] = (
                    result.stderr[-500:] or result.stdout[-500:] or
                    f"non-zero exit ({result.returncode})"
                )
                if verbose:
                    print(f"  GCS bridge failed (rc={result.returncode}): "
                          f"{bridge_result['message'][:200]}")
                return bridge_result

            # Read counts back from the file the bridge just wrote.
            # Going via the file (vs parsing stdout) keeps daily_monitor
            # decoupled from the bridge's log format.
            if self.snapshot_path.exists():
                try:
                    snap = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
                    bridge_result["events_count"]    = int(snap.get("event_count", 0))
                    bridge_result["alerts_count"]    = int(snap.get("alert_count", 0))
                    bridge_result["triggered_count"] = int(snap.get("triggered_count", 0))
                    bridge_result["data_mode"]       = snap.get("data_mode", "")
                except Exception as e:
                    if verbose:
                        print(f"  GCS bridge: cannot parse {self.snapshot_path.name} ({e})")

            bridge_result["status"]  = "success"
            bridge_result["message"] = result.stdout[-500:] if result.stdout else ""

            # Mirror fresh alerts to Chroma so semantic retrieval picks
            # them up. Same call the legacy POC step does — running it
            # again is a no-op for unchanged rows.
            mirrored = self._mirror_alerts_to_chroma(verbose=verbose)
            bridge_result["chroma_mirrored"] = mirrored

            if verbose:
                print(f"  GCS bridge: events={bridge_result['events_count']} "
                      f"alerts={bridge_result['alerts_count']} "
                      f"triggered={bridge_result['triggered_count']}")
            return bridge_result

        except subprocess.TimeoutExpired:
            bridge_result["status"] = "error"
            bridge_result["message"] = "GCS bridge timed out (>60s)"
            if verbose:
                print("  GCS bridge timed out")
            return bridge_result
        except Exception as e:
            bridge_result["status"] = "error"
            bridge_result["message"] = f"GCS bridge exception: {str(e)}"
            if verbose:
                print(f"  GCS bridge error: {e}")
            return bridge_result

    def _mirror_alerts_to_chroma(self, verbose: bool = True) -> int:
        """Read the freshly-produced Guardian snapshot and dual-write its
        alerts to Chroma. Fail-soft: returns 0 on any error."""
        if not self.snapshot_path.exists():
            return 0
        try:
            snap = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except Exception as e:
            if verbose:
                print(f"  Chroma mirror: cannot read {self.snapshot_path.name} ({e})")
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
            2. Run legacy Guardian POC daily job (external subprocess) —
               kept around for now but mostly a no-op since its adapters
               stopped producing data; will be retired once the
               core/guardian_loader integration lands.
            3. Refresh snapshot from GCS via the bridge script — this
               is the path that actually keeps alerts_latest.json fresh
               with today's classified alerts.
            4. Save monitoring log to data/logs/
        """
        if verbose:
            print("\n" + "=" * 70)
            print(f"ARGUS Daily Monitoring  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 70)

        result: Dict = {"timestamp": datetime.now().isoformat(), "status": "running"}

        try:
            # Step 1 — Crawl Federal Reserve news
            if verbose:
                print("\n[1/5] Fetching Federal Reserve news...")

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

            # Step 2 — Legacy Guardian POC daily job
            guardian_result = self._run_guardian_job(verbose=verbose)
            result["guardian"] = guardian_result

            # Step 3 — GCS bridge refresh (the path that actually
            # keeps the snapshot fresh; runs after the legacy step
            # so its output wins if both ran).
            gcs_bridge_result = self._run_gcs_bridge(verbose=verbose)
            result["gcs_bridge"] = gcs_bridge_result

            # Step 4 — Summary
            if verbose:
                print("\n[4/5] Summary...")

            result["status"] = "success"

            if verbose:
                self._print_summary(result)

            # Step 5 — Save log
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
            print(f"\nGuardian POC Global Event Classification (legacy):")
            print(f"  Status             : {guardian.get('status', 'unknown')}")
            if guardian.get("events_count"):
                print(f"  Raw events fetched : {guardian['events_count']}")
            if guardian.get("alerts_count"):
                print(f"  Classified alerts  : {guardian['alerts_count']}")
            if guardian.get("triggered_count"):
                print(f"  High-risk triggered: {guardian['triggered_count']}")
            if guardian.get("status") in ("error", "skipped") and guardian.get("message"):
                print(f"  Message            : {guardian['message']}")

        bridge = result.get("gcs_bridge", {})
        if bridge:
            print(f"\nGCS Bridge Refresh (alerts_latest.json source of truth):")
            print(f"  Status             : {bridge.get('status', 'unknown')}")
            if bridge.get("events_count"):
                print(f"  Input events       : {bridge['events_count']}")
            if bridge.get("alerts_count"):
                print(f"  Scored alerts      : {bridge['alerts_count']}")
            if bridge.get("triggered_count"):
                print(f"  High-risk triggered: {bridge['triggered_count']}")
            if bridge.get("chroma_mirrored"):
                print(f"  Chroma mirrored    : {bridge['chroma_mirrored']}")
            if bridge.get("status") in ("error", "skipped") and bridge.get("message"):
                print(f"  Message            : {bridge['message'][:200]}")
        print("=" * 70)

    def _save_log(self, result: Dict, verbose: bool = True):
        log_dir = Path("data/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"daily_monitor_{datetime.now().strftime('%Y%m%d')}.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"\n[5/5] Log saved: {log_file}")


async def main():
    monitor = DailyMonitor()
    result = await monitor.run(verbose=True)
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    asyncio.run(main())
