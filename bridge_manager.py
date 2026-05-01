#!/usr/bin/env python3
"""
ARGUS Bridge Manager
====================
Middleware that connects three independent modules into one automated pipeline:

  Module A  →  ./shared_storage/raw/          (crawler drops raw JSON here)
  Bridge    →  POST http://localhost:5050/api/analyze  (ARGUS analysis)
  Module C  ←  ./shared_storage/analyzed/     (enriched JSON for Guardian dashboard)

File contract
-------------
Input  (raw):
    {
        "source":      "<module_a_identifier>",
        "crawled_at":  "<ISO-8601 timestamp>",
        "news_items":  [ { "title": "...", "date": "...", "link": "...", ... }, ... ]
    }
    A bare JSON array is also accepted and treated as the news_items list.

Output (analyzed):
    {
        "status":       "success",
        "source_file":  "<original filename>",
        "item_count":   <int>,
        "scored_items": [ { ...original fields..., "probability": 3,
                            "probability_label": "Occasional",
                            "probability_reasoning": "...",
                            "impact": 4,
                            "impact_label": "Hazardous",
                            "impact_reasoning": "...",
                            "matrix_level": "High",
                            "matrix_color": "#EF4444",
                            "llm_scored": true }, ... ],
        "summary":      { "high": 2, "medium": 5, "low": 3,
                          "llm_scored": true, "top_keywords": [...],
                          "matrix_summary": [[...], ...] },
        "analyzed_at":  "<ISO-8601 timestamp>",
        "_bridge":      { "original_file": "...", "source": "...",
                          "crawled_at": "...", "processed_at": "...",
                          "item_count": <int> }
    }

Operation modes
---------------
1. Event-driven  — uses the `watchdog` library (pip install watchdog).
                   New files are processed within ~0.5 s of appearing.
2. Polling (fallback) — scans the raw/ directory every BRIDGE_POLL_INTERVAL
                   seconds when watchdog is not installed.

Configuration (environment variables / .env)
--------------------------------------------
ARGUS_ANALYZE_URL      URL of the ARGUS analysis endpoint
                       default: http://localhost:5050/api/analyze
BRIDGE_RAW_DIR         Directory to watch for incoming JSON files
                       default: ./shared_storage/raw
BRIDGE_ANALYZED_DIR    Directory for enriched output files
                       default: ./shared_storage/analyzed
BRIDGE_PROCESSED_DIR   Archive for successfully processed raw files
                       default: ./shared_storage/processed
BRIDGE_FAILED_DIR      Archive for files that could not be processed
                       default: ./shared_storage/failed
BRIDGE_POLL_INTERVAL   Polling interval in seconds (polling mode only)
                       default: 10
BRIDGE_REQUEST_TIMEOUT HTTP timeout for API calls in seconds
                       default: 120
BRIDGE_MAX_RETRIES     Number of retry attempts per file
                       default: 3
BRIDGE_RETRY_DELAY     Seconds to wait between retries
                       default: 5
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars may be set externally

# ── Configuration ──────────────────────────────────────────────────────────────

ANALYZE_API_URL  = os.getenv("ARGUS_ANALYZE_URL",     "http://localhost:5050/api/analyze")
RAW_DIR          = Path(os.getenv("BRIDGE_RAW_DIR",      "./shared_storage/raw"))
ANALYZED_DIR     = Path(os.getenv("BRIDGE_ANALYZED_DIR", "./shared_storage/analyzed"))
PROCESSED_DIR    = Path(os.getenv("BRIDGE_PROCESSED_DIR","./shared_storage/processed"))
FAILED_DIR       = Path(os.getenv("BRIDGE_FAILED_DIR",   "./shared_storage/failed"))
POLL_INTERVAL    = int(os.getenv("BRIDGE_POLL_INTERVAL",    "10"))   # seconds
REQUEST_TIMEOUT  = int(os.getenv("BRIDGE_REQUEST_TIMEOUT", "120"))   # seconds
MAX_RETRIES      = int(os.getenv("BRIDGE_MAX_RETRIES",       "3"))
RETRY_DELAY      = int(os.getenv("BRIDGE_RETRY_DELAY",       "5"))   # seconds

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("argus.bridge")


# ── Directory management ───────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    """Create all required directories if they do not already exist."""
    for d in (RAW_DIR, ANALYZED_DIR, PROCESSED_DIR, FAILED_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _safe_move(src: Path, dst: Path) -> None:
    """Move src → dst; add a numeric suffix to dst if it already exists."""
    if dst.exists():
        base, ext = dst.stem, dst.suffix
        counter = 1
        while dst.exists():
            dst = dst.parent / f"{base}_{counter}{ext}"
            counter += 1
    shutil.move(str(src), str(dst))


# ── File loading / validation ─────────────────────────────────────────────────

def _load_raw(path: Path) -> dict:
    """Read and normalise a raw input file.

    Accepts:
        - A JSON object with a ``news_items`` key.
        - A bare JSON array (treated as the news_items list directly).

    Raises:
        json.JSONDecodeError  — file is not valid JSON.
        ValueError            — JSON has unexpected structure.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Normalise bare list → standard envelope
    if isinstance(data, list):
        data = {"source": "unknown", "crawled_at": "", "news_items": data}

    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object or array, got {type(data).__name__}")

    if "news_items" not in data:
        raise ValueError(
            f"Missing 'news_items' key. Found keys: {list(data.keys())}"
        )

    if not isinstance(data["news_items"], list):
        raise ValueError("'news_items' must be a JSON array")

    return data


# ── API call ───────────────────────────────────────────────────────────────────

def _call_analyze_api(news_items: list, source_file: str) -> dict:
    """POST news_items to the ARGUS /api/analyze endpoint.

    Retries up to MAX_RETRIES times on connection errors, timeouts, and
    HTTP 5xx responses.  Raises RuntimeError if all attempts fail.
    """
    payload = {"news_items": news_items, "source_file": source_file}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(
                "  API call attempt %d/%d → %s", attempt, MAX_RETRIES, ANALYZE_API_URL
            )
            resp = requests.post(
                ANALYZE_API_URL, json=payload, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            result = resp.json()

            if result.get("status") != "success":
                log.warning(
                    "  API returned non-success status: %s", result.get("error", result)
                )
            return result

        except requests.exceptions.ConnectionError:
            log.warning(
                "  Connection refused — is ARGUS running on %s?", ANALYZE_API_URL
            )
        except requests.exceptions.Timeout:
            log.warning("  Request timed out after %ds", REQUEST_TIMEOUT)
        except requests.exceptions.HTTPError as exc:
            log.warning("  HTTP error: %s", exc)
            # 4xx errors will not improve with retries
            if exc.response is not None and exc.response.status_code < 500:
                raise RuntimeError(f"HTTP {exc.response.status_code}: {exc}") from exc
        except json.JSONDecodeError as exc:
            log.warning("  Response is not valid JSON: %s", exc)

        if attempt < MAX_RETRIES:
            log.info("  Retrying in %ds…", RETRY_DELAY)
            time.sleep(RETRY_DELAY)

    raise RuntimeError(f"API call failed after {MAX_RETRIES} attempt(s)")


# ── Output serialisation ───────────────────────────────────────────────────────

def _save_analyzed(enriched: dict, raw_path: Path) -> Path:
    """Write the enriched result to ANALYZED_DIR.

    The output filename is: <original_stem>_analyzed_<YYYYMMDD_HHMMSS>.json
    """
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ANALYZED_DIR / f"{raw_path.stem}_analyzed_{ts}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    return out_path


# ── Per-file processing pipeline ──────────────────────────────────────────────

def process_file(raw_path: Path) -> bool:
    """Run the full bridge pipeline for a single raw file.

    Steps:
        1. Load and validate the JSON file.
        2. Call the ARGUS analysis API (with retries).
        3. Attach provenance metadata (_bridge block).
        4. Write enriched output to ANALYZED_DIR.
        5. Archive the raw file to PROCESSED_DIR (success) or FAILED_DIR (error).

    Returns:
        True on success, False on any failure.
    """
    log.info("▶  Processing: %s  (%d bytes)", raw_path.name, raw_path.stat().st_size)

    # Step 1: load & validate
    try:
        raw = _load_raw(raw_path)
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("  Bad file format — %s", exc)
        _safe_move(raw_path, FAILED_DIR / raw_path.name)
        return False

    n      = len(raw["news_items"])
    source = raw.get("source", "unknown")
    log.info("  %d item(s) from source '%s'", n, source)

    if n == 0:
        log.warning("  File contains zero news_items — skipping")
        _safe_move(raw_path, FAILED_DIR / raw_path.name)
        return False

    # Step 2: call API
    try:
        enriched = _call_analyze_api(raw["news_items"], raw_path.name)
    except RuntimeError as exc:
        log.error("  %s", exc)
        _safe_move(raw_path, FAILED_DIR / raw_path.name)
        return False

    # Step 3: attach provenance
    enriched["_bridge"] = {
        "original_file": raw_path.name,
        "source":        source,
        "crawled_at":    raw.get("crawled_at", ""),
        "processed_at":  datetime.now().isoformat(),
        "item_count":    n,
    }

    # Step 4: write output
    out_path = _save_analyzed(enriched, raw_path)
    log.info("  ✔  Saved enriched output → %s", out_path)

    # Step 5: archive raw file
    _safe_move(raw_path, PROCESSED_DIR / raw_path.name)
    log.info("  ✔  Archived raw file → processed/")

    summary = enriched.get("summary", {})
    log.info(
        "  Matrix summary — High:%d  Medium:%d  Low:%d  (LLM:%s)",
        summary.get("high", 0),
        summary.get("medium", 0), summary.get("low", 0),
        summary.get("llm_scored", False),
    )
    return True


# ── Watch / poll loops ────────────────────────────────────────────────────────

def _process_existing() -> None:
    """Process any JSON files already present in RAW_DIR at startup."""
    existing = sorted(RAW_DIR.glob("*.json"))
    if existing:
        log.info("Found %d pre-existing file(s) in raw/ — processing now", len(existing))
        for path in existing:
            process_file(path)


def _watchdog_loop() -> None:
    """Event-driven monitoring using the `watchdog` library."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    class _RawDirHandler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix.lower() != ".json":
                return
            # Give the writer a moment to finish flushing
            time.sleep(0.5)
            if path.exists():   # may have been moved already
                process_file(path)

        def on_moved(self, event):
            """Handle atomic rename-based writes (common on Linux)."""
            if event.is_directory:
                return
            path = Path(event.dest_path)
            if path.suffix.lower() == ".json" and path.parent == RAW_DIR:
                time.sleep(0.1)
                if path.exists():
                    process_file(path)

    handler  = _RawDirHandler()
    observer = Observer()
    observer.schedule(handler, str(RAW_DIR), recursive=False)
    observer.start()
    log.info("Watching %s (event-driven via watchdog)", RAW_DIR.resolve())

    _process_existing()

    try:
        while observer.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down watchdog observer")
        observer.stop()
    observer.join()


def _poll_loop() -> None:
    """Polling fallback when watchdog is not installed."""
    log.info(
        "Polling %s every %ds (install watchdog for event-driven mode)",
        RAW_DIR.resolve(), POLL_INTERVAL,
    )
    _process_existing()

    while True:
        try:
            json_files = sorted(RAW_DIR.glob("*.json"))
            if json_files:
                log.info("Poll: found %d new file(s)", len(json_files))
                for path in json_files:
                    process_file(path)
        except Exception as exc:
            log.error("Unexpected error in poll loop: %s", exc)

        time.sleep(POLL_INTERVAL)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    _ensure_dirs()

    log.info("=" * 60)
    log.info("ARGUS Bridge Manager")
    log.info("=" * 60)
    log.info("  RAW dir       : %s", RAW_DIR.resolve())
    log.info("  ANALYZED dir  : %s", ANALYZED_DIR.resolve())
    log.info("  PROCESSED dir : %s", PROCESSED_DIR.resolve())
    log.info("  FAILED dir    : %s", FAILED_DIR.resolve())
    log.info("  API endpoint  : %s", ANALYZE_API_URL)
    log.info("  Timeout       : %ds | Retries: %d", REQUEST_TIMEOUT, MAX_RETRIES)
    log.info("=" * 60)

    try:
        import watchdog  # noqa: F401
        _watchdog_loop()
    except ImportError:
        log.warning(
            "watchdog not installed — falling back to polling every %ds. "
            "Run `pip install watchdog` for event-driven mode.",
            POLL_INTERVAL,
        )
        _poll_loop()


if __name__ == "__main__":
    main()
