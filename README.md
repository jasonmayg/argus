# ARGUS — Automated Risk Guardian for Universal Sensing

ARGUS is a modular, multi-agent treasury risk platform.
It ingests Federal Reserve policy releases and global macro signals, classifies them through a 5-agent pipeline, scores every event on a **2D Probability × Impact risk matrix**, and surfaces results through a live Flask dashboard.

---

## Architecture

```
argus/
├── frontend_app.py          # App entry point (~40 lines) — blueprint registration only
├── state.py                 # Shared orchestrator + execution state
├── config.py                # Single source of truth: port, shared_storage paths
├── bridge_manager.py        # File-drop bridge: raw/ → /api/analyze → analyzed/
├── daily_monitor.py         # Scheduled monitoring job (Fed + Guardian POC)
│
├── core/
│   ├── data_agent.py        # Unified DataAgent: all sources → shared_storage/raw/
│   ├── agents.py            # AnalysisAgent (2D matrix), persona dashboards
│   ├── guardian_loader.py   # GCS/snapshot loading, alert normalisation, daily warnings
│   └── json_utils.py        # SafeJSONEncoder, sanitize_for_json
│
├── routes/
│   ├── guardian_routes.py   # /api/guardian/dashboard, /api/personas, /api/persona/dashboard
│   └── pipeline_routes.py   # /, /api/status, /api/execute, /api/analyze, /api/results …
│
├── agents_system.py         # NewsGathererAgent, RiskClassifierAgent, DatabaseAgent,
│                            #   ReportingAgent, AgentOrchestrator
├── risk_matrix.py           # 5×5 Probability × Impact matrix helpers
├── risk_classifier.py       # Keyword-based risk taxonomy (0–100 scoring)
│
├── shared_storage/          # Single source of truth for all pipeline data
│   ├── raw/                 # Every DataAgent source writes here; bridge watches this dir
│   ├── analyzed/            # Bridge writes enriched, 2D-scored output here
│   ├── processed/           # Successfully processed raw files archived here
│   └── failed/              # Files that could not be processed
│
├── templates/dashboard.html # Single-page Flask UI
├── data_dashboard.py        # Data-explorer blueprint (separate concern)
├── guardian_poc/            # Guardian global-risk snapshot pipeline
│   ├── src/                 # guardian.py, daily_job.py, dashboard.py
│   └── config/              # countries.yaml, risk_keywords.yaml, data_sources.yaml
└── docs/                    # Runbooks and reference guides
```

---

## Data Flow

```
DataAgent (core/data_agent.py)
  │  Fetches from: federal_reserve, gcs, gdelt, usgs, noaa_nws,
  │                nasa_eonet, reliefweb, world_bank, bank_of_canada,
  │                fiscal_data, sec_edgar, fred, <custom URL>
  │
  ▼ writes bridge input contract JSON
shared_storage/raw/
  │
  ▼ bridge_manager.py watches and calls POST /api/analyze
RiskClassifierAgent → AnalysisAgent (2D matrix, Claude LLM)
  │
  ▼
shared_storage/analyzed/   (enriched results)
shared_storage/processed/  (archived source files)
```

All modules read from and write to `shared_storage/`. No module maintains a private data cache.

---

## Risk Matrix

Every news item is scored on two independent axes by Claude (`claude-haiku-4-5-20251001`):

| Dimension | Scale | Labels |
|-----------|-------|--------|
| **Probability** | 1–5 | Unlikely / Seldom / Occasional / Likely / Frequent |
| **Impact** | 1–5 | Negligible / Minor / Moderate / Hazardous / Catastrophic |

The 5×5 lookup table maps (P, I) to **Low / Medium / High / Extreme** with per-item reasoning shown in the UI. A keyword-score heuristic is used as fallback when no API key is present.

---

## Quick Start

### 1. Create environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY
```

### 3. Run the Flask dashboard

```bash
python frontend_app.py
```

Open [http://localhost:5050](http://localhost:5050)

### 4. Run a daily monitoring job

```bash
python daily_monitor.py
```

Fetches Federal Reserve news via `DataAgent`, writes to `shared_storage/raw/`, and runs the Guardian POC daily snapshot. Logs are saved to `data/logs/`.

### 5. Run the bridge (watch shared_storage/raw/ for incoming files)

```bash
# Terminal 1 — start the API server first
python frontend_app.py

# Terminal 2 — start the bridge watcher
python bridge_manager.py
```

Drop any JSON file into `shared_storage/raw/` and the bridge will call `/api/analyze`, write enriched output to `shared_storage/analyzed/`, and archive the source file.

**Bridge input contract** (`shared_storage/raw/my_feed.json`):
```json
{
  "source": "my_module",
  "crawled_at": "2026-03-13T10:00:00",
  "news_items": [
    { "title": "...", "date": "3/13/2026", "link": "https://..." }
  ]
}
```

---

## DataAgent Sources

`core/data_agent.py` supports the following source keys out of the box:

| Source key | Description |
|------------|-------------|
| `federal_reserve` | Fed Reserve press releases (crawl4ai, markdown link extraction) |
| `gcs` | Google Cloud Storage Guardian snapshot (`alerts_latest.json`) |
| `gdelt` | GDELT global conflict / macro news |
| `usgs` | USGS significant earthquake feed |
| `noaa_nws` | NOAA National Weather Service active alerts |
| `nasa_eonet` | NASA EONET natural hazard events |
| `reliefweb` | UN OCHA ReliefWeb humanitarian reports |
| `world_bank` | World Bank macro / CPI indicators |
| `bank_of_canada` | Bank of Canada daily FX rates |
| `fiscal_data` | US Treasury auction data |
| `sec_edgar` | SEC EDGAR corporate filings |
| `fred` | FRED economic indicators (requires `FRED_API_KEY`) |
| `https://...` | Any custom URL (auto-crawled via crawl4ai) |

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | Dashboard UI |
| `GET`  | `/api/status` | System + agent status |
| `POST` | `/api/execute` | Run the full 5-agent pipeline (async) |
| `POST` | `/api/analyze` | Synchronous classify + score endpoint (used by bridge) |
| `GET`  | `/api/results` | Latest pipeline results |
| `GET`  | `/api/guardian/dashboard` | Guardian multi-country risk board |
| `GET`  | `/api/personas` | Available role-based personas |
| `GET`  | `/api/persona/dashboard?persona=cash_manager` | Role-specific dashboard |
| `GET`  | `/api/agents/logs/<name>` | Per-agent logs |
| `POST` | `/api/reset` | Reset orchestrator state |

---

## Guardian POC

The `guardian_poc/` module generates daily multi-source risk snapshots independently of the Flask server.

```bash
# Run daily snapshot pipeline
python guardian_poc/src/daily_job.py \
  --persona treasury_ops \
  --sources-config guardian_poc/config/data_sources.yaml \
  --output-dir guardian_poc/data/snapshots \
  --latest-path guardian_poc/data/alerts_latest.json

# Optional: Streamlit dashboard
streamlit run guardian_poc/src/dashboard.py

# Schedule via cron helper
bash setup_cron.sh
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Port 5050 already in use | `lsof -i :5050` then kill the process, or set `PORT=5051` |
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| Guardian snapshot missing | Run `guardian_poc/src/daily_job.py` or check GCS env vars |
| Bridge: "Connection refused" | Start `frontend_app.py` before `bridge_manager.py` |
| LLM scoring falls back to heuristic | Set `ANTHROPIC_API_KEY` in `.env` |
| `daily_monitor.py` crawl fails | Check `ANTHROPIC_API_KEY` and network access to federalreserve.gov |

---

## Version

- **Version**: 3.0
- **Status**: Production-ready
- **Last Updated**: 2026-03-13
