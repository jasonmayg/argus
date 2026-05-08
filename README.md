# ARGUS — Developer Handover Documentation

> **Platform:** Argus Risk Intelligence Platform (Automated Risk Guardian Universal Sensing)
> **Stack:** Python 3.10+ · Flask · Vanilla JS (ES6) · SQLite · OpenAI GPT-4o · Anthropic Claude · Google Cloud Storage  
> **Status:** Production-ready · v3.0 · Last updated: 2026-04-16

---

## Quick Start

Run the app locally in 4 steps:

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate
# If you are in Windows, use command below instead
.\.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure API keys
cp .env.example .env       # then edit .env and set OPENAI_API_KEY / ANTHROPIC_API_KEY

# 4. Start the server
python frontend_app.py
```

Then open http://localhost:5050 in your browser.

> **Production:** `gunicorn --bind 0.0.0.0:5050 "frontend_app:create_app()"` — see [Section 9](#9-deployment-guidelines) for full deployment setup.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Repository Structure](#2-repository-structure)
3. [The Persona Configuration Engine](#3-the-persona-configuration-engine)
4. [Core Components](#4-core-components)
5. [Data Flow](#5-data-flow)
6. [API Endpoints Reference](#6-api-endpoints-reference)
7. [Frontend Architecture](#7-frontend-architecture)
8. [Environment Variables](#8-environment-variables)
9. [Deployment Guidelines](#9-deployment-guidelines)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. System Architecture Overview

Argus is a **multi-agent, persona-driven treasury risk platform**. It ingests signals from 37+ global data sources (Federal Reserve press releases, GDELT, USGS, NOAA, FRED, SEC EDGAR, etc.), classifies them through a staged AI pipeline, scores each event on a 5×5 Probability × Impact risk matrix, and surfaces results through a single-page Flask dashboard.

### Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Web server** | Flask 3.x + flask-cors | REST API + SPA host |
| **Frontend** | Vanilla JS ES6 (no framework) | Dashboard UI, modular JS files |
| **Primary AI** | OpenAI GPT-4o (`gpt-4o`) | Intelligence briefs, Guardian Chat, action generation |
| **Secondary AI** | Anthropic Claude (`claude-haiku-4-5-20251001`) | Risk matrix scoring (probability + impact axes) |
| **Database** | SQLite (`data/fed_news/argus_news.db`) | News items, keyword matches, risk analysis, regulatory data |
| **Live data** | Google Cloud Storage | Guardian daily snapshots (`alerts_latest.json`) |
| **Web crawler** | crawl4ai (Playwright-based) | Federal Reserve press release extraction |
| **Config** | YAML (`personas/defaults.yaml`) + JSON profiles | Persona weight baselines and user-saved overrides |
| **Process manager** | Gunicorn (production) / Flask dev server (local) | WSGI application serving |

### Key Architectural Decisions

- **Stateless route blueprints:** All Flask routes are pure I/O — they read from `shared_storage/` or SQLite and write back. No in-memory state is owned by route modules.
- **Shared storage as the pipeline bus:** Every agent writes to and reads from `shared_storage/`. This decouples the agents from each other and allows the bridge watcher to process files independently of the Flask server.
- **No floating config files:** User weight customisations are never written to a persistent config file at runtime. Draft state is owned by the browser (`localStorage`). Only named profiles, explicitly saved by the user, are persisted to `personas/profiles/`.

---

## 2. Repository Structure

```
argus/
│
├── frontend_app.py          # App entry point — blueprint registration only (~55 lines)
├── config.py                # Env vars + shared_storage path constants (ONLY place for these)
├── state.py                 # Shared AgentOrchestrator instance + execution_state dict
├── bridge_manager.py        # File-drop bridge: watches raw/ → POST /api/analyze → analyzed/
├── daily_monitor.py         # Cron-scheduled job: Fed crawl + Guardian POC snapshot
├── generate_actions.py      # Standalone action-generation script (calls pipeline output)
├── risk_matrix.py           # 5×5 P×I matrix helpers (calculate_risk_level, score_to_*)
├── risk_classifier.py       # Keyword-based risk taxonomy (0–100 scoring, 13 categories)
├── classify_news.py         # CLI wrapper: classifies SQLite news rows via risk_classifier
├── classify_regulatory.py   # LLM-based regulatory change classifier (before/after diffs)
├── agents_system.py         # NewsGathererAgent, RiskClassifierAgent, DatabaseAgent,
│                            #   ReportingAgent, ActionsGeneratorAgent, AgentOrchestrator
├── data_dashboard.py        # Separate blueprint: raw GCS data explorer tab
│
├── core/
│   ├── agents.py            # AnalysisAgent (2D matrix scoring), persona dashboards,
│   │                        #   build_alert_list, build_country_board, build_fx_summary
│   ├── data_agent.py        # Unified DataAgent: fetches all 13 source keys → raw/
│   ├── guardian_loader.py   # GCS connectivity, snapshot resolution, alert normalisation,
│   │                        #   daily-warning comparison, Google downside inference
│   └── json_utils.py        # SafeJSONEncoder (handles None), sanitize_for_json()
│
├── routes/
│   ├── pipeline_routes.py   # Pipeline execution, status polling, results, agent logs
│   ├── guardian_routes.py   # Guardian dashboard, personas, profile save/load
│   ├── ai_routes.py         # Intelligence brief, Guardian Chat, news query, home categories
│   ├── cash_routes.py       # Cash positions, FX rates, historical data (Excel source)
│   ├── regulatory_routes.py # Regulatory change tracker (SQLite-backed)
│   ├── data_alert_routes.py # Threshold-based data alerts from GCS macro indicators
│   └── actions_routes.py    # AI-recommended treasury actions
│
├── personas/
│   ├── defaults.yaml        # AI-generated baseline weights for all 5 personas × 9 regions
│   ├── persona_loader.py    # load_defaults(), merge_weights(), region override logic
│   └── profiles/            # Named user-saved weight profiles (JSON, one file per profile)
│       ├── Fx_test.json
│       └── ...
│
├── shared_storage/          # Pipeline data bus — all agents read/write here
│   ├── raw/                 # DataAgent writes here; bridge watches this dir
│   ├── analyzed/            # Bridge writes enriched, 2D-scored output here
│   ├── processed/           # Successfully processed raw files archived here
│   ├── failed/              # Files that could not be processed
│   ├── latest_results.json  # Most recent full pipeline run output
│   └── latest_actions.json  # Most recent actions generation output
│
├── data/
│   ├── fed_news/
│   │   └── argus_news.db    # SQLite: news, keyword_matches, risk_analysis, regulatory_changes
│   ├── cash_positions.xlsx  # Multi-currency cash positions (sheets: positions, history, fx_rates)
│   └── logs/
│
├── guardian_poc/            # Independent Guardian daily-snapshot pipeline
│   ├── src/
│   │   ├── guardian.py      # GuardianEngine: source adapters, scoring, alert normalisation
│   │   ├── daily_job.py     # CLI entry point — runs full snapshot cycle
│   │   └── dashboard.py     # Optional Streamlit dashboard for Guardian data
│   └── config/
│       ├── countries.yaml   # Monitored country list (11 Alphabet Treasury focus countries)
│       ├── risk_keywords.yaml
│       └── data_sources.yaml
│
├── templates/
│   └── dashboard.html       # Single HTML file — all pages rendered as CSS-toggled divs
│
├── static/
│   ├── js/                  # One JS module per feature (see Section 7)
│   └── css/
│       ├── main.css
│       ├── modals.css
│       └── data_console.css
│
├── thresholds.json          # Data alert rules: source, metric, operator, threshold value
├── keywords_config.json     # Risk keyword definitions for the classifier
├── risk_classification_en.json  # Risk category → keyword mapping (English)
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development tools (pytest, black, mypy)
└── setup_cron.sh            # Interactive cron setup script
```

---

## 3. The Persona Configuration Engine

This is the most critical subsystem to understand before making changes to risk scoring or the UI weight editor.

### 3.1 `config.py` — Strictly Environment and Server Settings

`config.py` is the **single source of truth for infrastructure configuration only**. It must not contain business logic, risk weights, or persona definitions.

```python
# config.py — only these three concerns belong here:
API_PORT = int(os.getenv("PORT", "5050"))
ANALYZE_API_URL = os.getenv("ARGUS_ANALYZE_URL", f"http://localhost:{API_PORT}/api/analyze")
_STORAGE_BASE = Path(os.getenv("ARGUS_STORAGE_DIR", "./shared_storage"))
RAW_DIR, ANALYZED_DIR, PROCESSED_DIR, FAILED_DIR = ...
```

Do not add persona weights, thresholds, or feature flags here.

### 3.2 `personas/` Directory — The Three-Layer Config Model

The persona system uses three distinct layers, each with a specific responsibility:

```
personas/
├── defaults.yaml        ← Layer 1: AI-generated baselines (committed to git, never modified at runtime)
├── persona_loader.py    ← Merge logic lives here
└── profiles/            ← Layer 2: Named user overrides (written at runtime by /api/save_profile)
    └── *.json
```

**Layer 1 — `defaults.yaml` (source of truth for AI baselines)**

Defines `alert_threshold` and `risk_weights` (13 multipliers) for each of the 5 personas globally, plus `region_overrides` for 9 regions per persona. Region overrides are *shallow diffs* — only keys that differ from the global baseline need to be listed.

```yaml
personas:
  cash_manager:
    alert_threshold: 40
    risk_weights:
      liquidity_risk: 1.6
      process_risk: 1.4
      credit_risk: 1.3
      # ... 10 more categories
    region_overrides:
      uk:
        regulatory_risk: 1.6   # only keys that differ from global
        liquidity_risk: 1.5
      japan:
        disaster_risk: 1.4
        political_risk: 0.7
```

**Layer 2 — `personas/profiles/*.json` (user-saved overrides)**

Written by `POST /api/save_profile`. Each file is a named profile keyed to a persona. The file is never read at startup — it is loaded on-demand by `POST /api/load_profile` and merged in-memory.

```json
{
  "persona": "fx_manager",
  "name": "Q2 Conservative FX",
  "weights": {
    "risk_weights": {
      "market_risk": 1.8,
      "political_risk": 1.5
    },
    "alert_threshold": 50
  }
}
```

**Layer 3 — Frontend draft state (never written to disk)**

When a user drags sliders without clicking **Save Profile**, that state lives exclusively in the browser's `psDraft` / `tsDraft` objects in `persona-settings.js` and `tune-modal.js`. It is transmitted to the backend only on API calls (e.g., `POST /api/actions/generate`) as a request body parameter — it is never persisted server-side.

### 3.3 The Fallback / Merge Logic

`persona_loader.py` exposes a clean public API. The merge chain is:

```
global baseline (defaults.yaml)
    └─ .update(region_override)         → load_defaults_for_region()
         └─ deep_merge(profile weights) → merge_weights() at call site in routes
```

**Concrete example — loading a profile:**

```python
# In /api/load_profile route (guardian_routes.py)
from personas.persona_loader import load_defaults, merge_weights

base    = load_defaults("fx_manager")               # Layer 1: global baseline from YAML
profile = json.loads(profile_path.read_text())      # Layer 2: user-saved JSON
merged  = merge_weights(base, profile["weights"])   # Layer 2 shadows Layer 1
# merged is returned to the frontend and used for scoring — never written to disk
```

**Concrete example — region merge:**

```python
from personas.persona_loader import load_defaults_for_region

cfg = load_defaults_for_region("cash_manager", "uk")
# cfg["risk_weights"]["regulatory_risk"] == 1.6   (overridden)
# cfg["risk_weights"]["liquidity_risk"]  == 1.6   (global value, not overridden for UK)
# cfg["alert_threshold"]                == 40     (threshold never changes per region)
```

**`merge_weights()` is a deep merge, not a shallow `dict.update()`:**

```python
def merge_weights(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)  # recurse into nested dicts
        else:
            result[key] = value
    return result
```

This means a profile that only overrides `risk_weights.market_risk` does not zero out the other 12 categories — only the specified keys are overwritten. Neither `base` nor `override` is mutated.

### 3.4 The 13 Risk Categories

All weight keys are snake_case and map to the free-text labels used by the classifier:

| YAML key | Display label | Group |
|---|---|---|
| `market_risk` | Market | Financial |
| `credit_risk` | Credit | Financial |
| `liquidity_risk` | Liquidity | Financial |
| `systematic_risk` | Systematic | Financial |
| `operational_risk` | Operational | Operational |
| `process_risk` | Process | Operational |
| `business_risk` | Business | Operational |
| `regulatory_risk` | Regulatory | Geopolitical |
| `political_risk` | Political | Geopolitical |
| `reputation_risk` | Reputation | Geopolitical |
| `disaster_risk` | Disaster | Environment |
| `supply_risk` | Supply | Environment |
| `demand_risk` | Demand | Environment |

The mapping from free-text classifier output to YAML keys lives in `personas/persona_loader.py::_CATEGORY_TO_WEIGHT_KEY`. If you add a new risk category, you must update this dict, `defaults.yaml`, and `TUNE_CATS` in `tune-modal.js` and `PS_CATEGORIES` in `persona-settings.js`.

---

## 4. Core Components

### 4.1 Agent Pipeline (`agents_system.py`)

The 9-stage pipeline runs in sequence when `POST /api/execute` is called. Each stage is an agent class with `execute(**kwargs) -> dict`.

| Stage | Agent | Output |
|---|---|---|
| 1 | `NewsGathererAgent` | Raw news items from Fed Reserve → `shared_storage/raw/` |
| 2 | `RiskClassifierAgent` | Risk category + keyword matches per item |
| 3 | `DatabaseAgent` | Inserts classified items into SQLite |
| 4 | `AnalysisAgent` | 5×5 matrix scores using Claude Haiku |
| 5 | `ReportingAgent` | Structured JSON → `shared_storage/latest_results.json` |
| 6 | Guardian job | Loads 37+ external feeds, writes `alerts_latest.json` |
| 7 | Data alert scan | Evaluates macro indicators vs `thresholds.json` |
| 8 | `RegulatoryClassifier` | Flags news as regulatory changes with before/after diffs |
| 9 | `ActionsGeneratorAgent` | GPT-4o action recommendations → `shared_storage/latest_actions.json` |

Pipeline state is held in `state.py::execution_state` (a plain dict) and the shared `AgentOrchestrator` instance.

### 4.2 Risk Matrix (`risk_matrix.py`)

Every news item is scored on two independent axes by Claude Haiku:

| Axis | Scale | Labels |
|---|---|---|
| **Probability** | 1–5 | Unlikely / Seldom / Occasional / Likely / Frequent |
| **Impact** | 1–5 | Negligible / Minor / Moderate / Hazardous / Catastrophic |

```
Matrix levels (5×5 lookup table):
          Impact →
          1      2      3      4      5
P=1  [ Low,   Low,   Low,   Med,   Med  ]
P=2  [ Low,   Low,   Med,   Med,   High ]
P=3  [ Low,   Med,   Med,   High,  High ]
P=4  [ Med,   Med,   High,  High,  Ext  ]
P=5  [ Med,   High,  High,  Ext,   Ext  ]
```

When no API key is set, `risk_classifier.py` provides a keyword-heuristic fallback (0–100 score → P/I axes via `score_to_probability()` / `score_to_impact()`).

### 4.3 Guardian Loader (`core/guardian_loader.py`)

Manages all interactions with GCS and local snapshot files. Resolves the data source with this priority:

1. GCS bucket (`your-gcs-bucket`) — attempts `alerts_latest.json` via `google-cloud-storage`
2. Quality check: if GCS snapshot has zero scored alerts, falls back to local
3. Local file: `guardian_poc/data/alerts_latest.json`
4. Returns HTTP 404 if neither is available

Source whitelist (`GUARDIAN_SOURCE_WHITELIST`) limits which sources appear in the dashboard to prevent noisy low-signal feeds from polluting the risk board.

### 4.4 `core/json_utils.py`

Two utilities that must be used in all routes returning JSON:

```python
from core.json_utils import SafeJSONEncoder, sanitize_for_json

# In create_app():
app.json_encoder = SafeJSONEncoder  # handles None, datetime, Path objects

# Before jsonify() on any dict that touched pipeline/guardian data:
return jsonify(sanitize_for_json(data))
```

Omitting `sanitize_for_json` on data that may contain `None` values will cause a `TypeError` in Flask's default JSON sort (`sort_keys=True` fails when comparing `None` to `str`).

### 4.5 Bridge Manager (`bridge_manager.py`)

A file-watcher that decouples external data producers from the Flask server. Drop any JSON file matching the bridge input contract into `shared_storage/raw/` and the bridge will:

1. Call `POST /api/analyze` with the file contents
2. Write enriched output to `shared_storage/analyzed/`
3. Archive the source to `shared_storage/processed/` (or `failed/` on error)

**Bridge input contract:**
```json
{
  "source": "my_module",
  "crawled_at": "2026-04-16T10:00:00",
  "news_items": [
    { "title": "...", "date": "4/16/2026", "link": "https://..." }
  ]
}
```

---

## 5. Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  External sources (37+)                                         │
│  Fed Reserve · GDELT · USGS · NOAA · FRED · SEC · World Bank…  │
└────────────────────────┬────────────────────────────────────────┘
                         │
                    DataAgent (core/data_agent.py)
                         │ writes JSON files
                         ▼
              shared_storage/raw/
                         │
              bridge_manager.py (watches dir)
                         │ POST /api/analyze
                         ▼
         RiskClassifierAgent → AnalysisAgent (Claude Haiku)
                         │
              shared_storage/analyzed/
              shared_storage/latest_results.json
                         │
              ┌──────────┴──────────────┐
              │                         │
         SQLite DB                GCS snapshot
    (news, risk_analysis,       (Guardian alerts,
     regulatory_changes)         daily comparison)
              │                         │
              └──────────┬──────────────┘
                         │
               Flask routes (read-only)
                         │
              templates/dashboard.html
               (Vanilla JS SPA)
```

---

## 6. API Endpoints Reference

All endpoints are prefixed with no base path. CORS is enabled globally for all origins in `create_app()`.

### Pipeline

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve `dashboard.html` |
| `GET` | `/api/status` | Pipeline running state, current stage, agent summary |
| `POST` | `/api/execute` | Start full 9-stage pipeline (async, poll `/api/status`) |
| `POST` | `/api/analyze` | Synchronous classify + score (used by bridge; accepts bridge contract JSON) |
| `GET` | `/api/results` | Latest `shared_storage/latest_results.json` |
| `GET` | `/api/agents/logs/<agent_name>` | Last N log lines for a named agent |
| `GET` | `/api/agents/summary` | All agents' status summary |
| `GET` | `/api/demo/report` | Static demo report (no pipeline required) |
| `POST` | `/api/reset` | Reset orchestrator and clear execution_state |

### Guardian & Personas

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/guardian/dashboard` | Full Guardian multi-country dashboard (GCS → local fallback) |
| `GET` | `/api/personas` | All 5 personas with AI baseline weights + all region defaults |
| `GET` | `/api/persona/dashboard?persona=<key>` | Role-specific dashboard payload |
| `POST` | `/api/save_profile` | Write named profile JSON to `personas/profiles/` |
| `GET` | `/api/list_profiles` | List all saved profiles for the current persona |
| `POST` | `/api/load_profile` | Load a saved profile; returns merged weights (not persisted) |

### AI

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/brief` | Generate intelligence brief via GPT-4o (body: `{persona}`) |
| `POST` | `/api/chat` | Guardian chatbot (body: `{message, history, persona}`) |
| `GET` | `/api/news` | Recent news items from SQLite with risk scores |
| `GET` | `/api/home/risk-categories` | Risk category card data for home page |

### Cash

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/cash/positions` | Current positions from `cash_positions.xlsx` (Sheet: positions) |
| `GET` | `/api/cash/history` | Historical monthly data (Sheet: history) |
| `GET` | `/api/cash/fx` | FX rates reference (Sheet: fx_rates) |
| `POST` | `/api/cash/cache/invalidate` | Force cache refresh (TTL: 5 min by default) |

### Regulatory

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/regulatory` | List regulatory changes (query params: `country`, `category`, `severity`, `limit`) |
| `GET` | `/api/regulatory/summary` | Summary stats by country and category (used to populate filter buttons) |
| `POST` | `/api/regulatory/refresh` | Trigger re-classification of unprocessed news items |

### Data Alerts

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/data/alerts` | Run all enabled threshold rules, return triggered alerts |
| `GET` | `/api/data/alerts/rules` | List all configured rules from `thresholds.json` |
| `POST` | `/api/data/alerts/refresh` | Force cache refresh |
| `GET` | `/api/data/alerts/matrix-items` | Return triggered alerts formatted for the 5×5 matrix |

### Actions

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/actions` | Return latest actions (pipeline output preferred; cache fallback) |
| `POST` | `/api/actions/generate` | Force GPT-4o action regeneration with optional custom weights body |

---

## 7. Frontend Architecture

The frontend is a **single HTML file** (`templates/dashboard.html`) with all 8 pages rendered as `<div class="page">` elements toggled by `navigation.js::showPage()`. There is no client-side router.

### JS Module Map

| File | Responsibility |
|---|---|
| `app.js` | Bootstrap: wires all modules on `DOMContentLoaded`, keyboard shortcuts |
| `navigation.js` | `showPage()`, lazy-loads page data on tab switch |
| `persona.js` | Persona selector dropdown, `currentPersona` state |
| `tune-modal.js` | Quick-access Tune Strategy modal — 13 sliders + 9 region tabs |
| `persona-settings.js` | Full weight editor page — all personas + per-region sliders |
| `geomap.js` | SVG world map (TopoJSON + Mercator projection without D3) |
| `results.js` | 5×5 matrix rendering, alert list, GCS matrix item merge |
| `risk-categories.js` | Home risk category cards, data alert badges |
| `actions.js` | Risk posture banner, action cards, stale-detection, filter settings |
| `cash.js` | KPI cards, position table, bar chart, history chart |
| `regulatory.js` | Regulatory change list, filter buttons, re-analyse trigger |
| `brief.js` | Intelligence brief generation, typewriter animation, signal pills |
| `chat.js` | Guardian Chat multi-session management, history rendering |
| `guardian.js` | Guardian dashboard data fetch and rendering |
| `pipeline.js` | Pipeline execution, status polling (1.5 s interval), stage progress |
| `news.js` | News feed fetch and rendering |
| `sidebar.js` | Sidebar expand/collapse, `Ctrl+B` shortcut |
| `utils.js` | Shared helpers: `escapeHtml`, `fmtUSD`, `log()`, `showAlert()` |

### State Management

There is no global state store. Each module owns its state in module-level `let` variables (e.g., `CASH_DATA`, `actionsState`, `chatSessions`). Cross-module communication happens through direct function calls. Persistent UI state uses `localStorage` (saved profiles, active persona, last-used region).

---

## 8. Environment Variables

Create a `.env` file at the project root (copy from `.env.example`). All variables are optional unless marked required.

```bash
# ── AI Models ──────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...              # Required for: brief, chat, actions generation
OPENAI_MODEL=gpt-4o               # Default: gpt-4o
ANTHROPIC_API_KEY=sk-ant-...      # Required for: risk matrix scoring (Claude Haiku)
                                  # Falls back to keyword heuristic if unset

# ── Server ────────────────────────────────────────────────────────
PORT=5050                          # Flask/Gunicorn listen port (default: 5050)
ARGUS_ANALYZE_URL=http://localhost:5050/api/analyze  # Used by bridge_manager

# ── Data Paths ────────────────────────────────────────────────────
ARGUS_STORAGE_DIR=./shared_storage # Pipeline data bus root
ARGUS_NEWS_DB=data/fed_news/argus_news.db
ARGUS_CASH_FILE=data/cash_positions.xlsx
ARGUS_THRESHOLDS_FILE=thresholds.json
CASH_CACHE_TTL=300                 # Seconds (default: 300)
DATA_ALERT_CACHE_TTL=300

# ── Google Cloud Storage (Guardian live data) ─────────────────────
GOOGLE_APPLICATION_CREDENTIALS=service-account.json
GUARDIAN_GCS_BUCKET=your-gcs-bucket
GUARDIAN_GCS_PREFIX=                  # Optional GCS path prefix
GUARDIAN_GCS_ENABLED=true            # Set to false to force local-only mode

# ── External APIs ─────────────────────────────────────────────────
FRED_API_KEY=                      # Required only for FRED economic indicator source
```

---

## 9. Deployment Guidelines

### 9.1 Prerequisites Checklist

- [ ] Python 3.10+ installed on the server
- [ ] Service account JSON key for GCS (`GOOGLE_APPLICATION_CREDENTIALS`) securely uploaded
- [ ] `.env` file populated with all required API keys
- [ ] `personas/profiles/` directory exists and is **writable** by the app process
- [ ] `shared_storage/` and all four subdirectories (`raw/`, `analyzed/`, `processed/`, `failed/`) exist and are writable
- [ ] `data/fed_news/` directory exists (SQLite will create `argus_news.db` on first run)

```bash
# Create required writable directories
mkdir -p shared_storage/{raw,analyzed,processed,failed}
mkdir -p data/fed_news data/logs personas/profiles
```

### 9.2 Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn          # Production WSGI server
pip install pandas openpyxl   # Required for cash positions endpoint
```

### 9.3 Gunicorn Configuration

For production, run Argus under Gunicorn. The recommended configuration:

```bash
gunicorn \
  --workers 2 \
  --threads 4 \
  --worker-class gthread \
  --bind 0.0.0.0:5050 \
  --timeout 120 \
  --access-logfile logs/access.log \
  --error-logfile logs/error.log \
  "frontend_app:create_app()"
```

> **Worker count:** Keep workers low (2–3). The pipeline uses `asyncio` internally and spawns threads; high worker counts cause contention on `shared_storage/` file writes.

**`gunicorn.conf.py` (recommended for cloud deployments):**

```python
bind = "0.0.0.0:5050"
workers = 2
threads = 4
worker_class = "gthread"
timeout = 120
keepalive = 5
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"
```

```bash
gunicorn -c gunicorn.conf.py "frontend_app:create_app()"
```

### 9.4 CORS Configuration

CORS is enabled globally in `create_app()` via `flask-cors`:

```python
CORS(app)   # allows all origins — suitable for internal deployments
```

For public-facing deployments, restrict origins by updating `frontend_app.py`:

```python
from flask_cors import CORS
CORS(app, resources={r"/api/*": {"origins": ["https://your-domain.com"]}})
```

### 9.5 Reverse Proxy (nginx)

Place Gunicorn behind nginx for TLS termination and static file serving:

```nginx
server {
    listen 443 ssl;
    server_name argus.your-domain.com;

    ssl_certificate     /etc/ssl/certs/argus.crt;
    ssl_certificate_key /etc/ssl/private/argus.key;

    # Serve Flask static files directly (avoids Python for every asset)
    location /static/ {
        alias /opt/argus/static/;
        expires 1h;
    }

    location / {
        proxy_pass         http://127.0.0.1:5050;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 120s;  # Match Gunicorn timeout
    }
}
```

### 9.6 File System Permissions

The app process must have **read-write** access to:

```
personas/profiles/    ← /api/save_profile writes JSON files here
shared_storage/       ← All pipeline agents write here
data/fed_news/        ← SQLite DB is created and written here
data/logs/            ← daily_monitor.py writes logs here
```

```bash
# Example: if running as user 'argus' in group 'www-data'
chown -R argus:www-data personas/profiles shared_storage data/
chmod -R 775 personas/profiles shared_storage data/
```

If `personas/profiles/` is not writable, `POST /api/save_profile` will return a 500 error silently — verify permissions before going live.

### 9.7 Cron / Scheduled Jobs

The pipeline should run automatically to keep data fresh. Add to crontab:

```bash
crontab -e
```

```cron
# Run daily monitoring job every 4 hours
0 */4 * * * cd /opt/argus && source .venv/bin/activate && python daily_monitor.py >> data/logs/daily_monitor.log 2>&1

# Optional: run Guardian snapshot independently (if guardian_poc is deployed separately)
30 */4 * * * cd /opt/argus && source .venv/bin/activate && python guardian_poc/src/daily_job.py >> data/logs/guardian.log 2>&1
```

Alternatively, use the interactive setup script:

```bash
bash setup_cron.sh
```

### 9.8 Google Cloud Deployment Notes

If deploying to Cloud Run or GCE:

- Mount the GCS service account JSON as a secret (e.g., GCP Secret Manager) and set `GOOGLE_APPLICATION_CREDENTIALS` to its path.
- Set `PORT` via the environment — Cloud Run injects this automatically.
- `shared_storage/` must be a persistent volume or Cloud Storage FUSE mount, not the container's ephemeral filesystem.
- Set `GUARDIAN_GCS_ENABLED=true` and ensure the service account has `storage.objectViewer` on the `your-gcs-bucket` bucket.

### 9.9 Health Check

```bash
curl http://localhost:5050/api/status
# Expected: {"is_running": false, "current_stage": null, "agents_status": {...}}
```

---

## 10. Troubleshooting

| Symptom | Root Cause | Fix |
|---|---|---|
| `POST /api/save_profile` returns 500 | `personas/profiles/` not writable | `chmod 775 personas/profiles/` |
| Intelligence brief returns `"OPENAI_API_KEY is not set"` | Missing env var | Add `OPENAI_API_KEY` to `.env` |
| Risk matrix scores all show heuristic (0–100) instead of P/I | `ANTHROPIC_API_KEY` unset | Set key or accept heuristic fallback |
| Guardian dashboard returns `status: "missing"` | No snapshot in GCS or local | Run `python guardian_poc/src/daily_job.py` |
| `TypeError: '<' not supported` on `/api/results` | `None` values in JSON sort | Use `sanitize_for_json()` before `jsonify()` |
| Port 5050 already in use | Another process bound to port | `lsof -i :5050` → kill PID, or set `PORT=5051` |
| `ModuleNotFoundError: crawl4ai` | Dependencies not installed | `pip install -r requirements.txt` |
| Cash positions show `"Error loading cash data"` | `pandas`/`openpyxl` missing | `pip install pandas openpyxl` |
| Bridge: `Connection refused` on `/api/analyze` | Flask server not running | Start `frontend_app.py` before `bridge_manager.py` |
| GCS data loads but shows zero scored alerts | Snapshot has unscored data | Guardian loader auto-falls back to local; check daily job logs |
| `psDraft` weight changes not reflected in actions | Actions cache is stale | Click **Re-generate** — actions are cached; weight changes do not auto-refresh them |

---

*For architecture questions, see inline docstrings in `personas/persona_loader.py` and `core/agents.py`. For data source details, see `core/data_agent.py` module docstring.*
