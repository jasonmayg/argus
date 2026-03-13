# ARGUS - Automated Risk Guardian for Universal Sensing

ARGUS is a multi-agent treasury risk monitoring platform for Federal Reserve and global macro signals.
It now includes two complementary tracks:

- **Core ARGUS (Flask)**: 5-agent pipeline for Fed news ingestion, classification, analysis, and reporting.
- **Guardian PoC (`guardian_poc/`)**: global, multi-source risk snapshots and country-level treasury alert board.

## Project Overview

ARGUS automates the full risk-monitoring loop:

1. Gather data from policy/news/event sources
2. Classify risk using keyword taxonomy and scoring rules
3. Persist and organize results
4. Analyze trends and cross-item signals
5. Produce dashboard-ready and report-ready outputs

## Key Features

### Core Multi-Agent Pipeline (Flask)
- `NewsGathererAgent`: crawls Federal Reserve releases
- `RiskClassifierAgent`: assigns risk levels and categories
- `DatabaseAgent`: stores structured records
- `AnalysisAgent`: computes aggregates and patterns
- `ReportingAgent`: produces actionable summaries

### Guardian Global Risk Layer (`guardian_poc/`)
- Country-aware risk scoring with configurable personas
- Multi-source ingestion (for example GDELT, ReliefWeb, FRED, USGS, NOAA, World Bank)
- Daily snapshot output (`alerts_latest.json` + historical snapshots)
- Country report export (`guardian_poc/reports/<date>/`)
- Optional Streamlit dashboard for risk board visualization

### Risk Classification
- Three-tier taxonomy: category -> signal -> source context
- 0-100 score bands: Critical / High / Medium / Low / None
- High-priority trigger keywords for immediate attention

## Repository Structure

```text
argus-main/
├── frontend_app.py                 # Flask dashboard + APIs (core ARGUS)
├── agents_system.py                # Agent orchestrator
├── daily_monitor.py                # Daily Fed monitoring job
├── setup_cron.sh                   # Cron setup helper
├── verify_system.py                # Basic environment and file checks
├── templates/dashboard.html        # Flask UI template
├── docs/                           # Project docs (runbooks/guides)
└── guardian_poc/
    ├── src/guardian.py             # Guardian scoring engine
    ├── src/daily_job.py            # Snapshot pipeline entrypoint
    ├── src/dashboard.py            # Streamlit dashboard
    ├── config/*.yaml               # Countries, personas, risk keywords, sources
    ├── scripts/run_daily_guardian.sh
    └── reports/                    # Exported country reports
```

## Quick Start

### 1. Create environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you plan to run `guardian_poc` Streamlit/dashboard features, also install:

```bash
pip install -r guardian_poc/requirements.txt
```

### 2. Run core ARGUS web dashboard (Flask)

```bash
python frontend_app.py
```

Open [http://localhost:5050](http://localhost:5050)

### 3. Run daily monitor job (core)

```bash
python daily_monitor.py
```

### 4. Run Guardian daily snapshot pipeline

```bash
python guardian_poc/src/daily_job.py \
  --persona treasury_ops \
  --sources-config guardian_poc/config/data_sources.yaml \
  --output-dir guardian_poc/data/snapshots \
  --latest-path guardian_poc/data/alerts_latest.json
```

Or use the helper script:

```bash
bash guardian_poc/scripts/run_daily_guardian.sh
```

### 5. (Optional) Run Guardian Streamlit dashboard

```bash
streamlit run guardian_poc/src/dashboard.py
```

## Command Reference

```bash
# Core web UI
python frontend_app.py

# Core daily monitor
python daily_monitor.py

# Core verification
python verify_system.py

# Core cron setup
bash setup_cron.sh

# Guardian daily snapshot
python guardian_poc/src/daily_job.py --persona treasury_ops

# Guardian dashboard
streamlit run guardian_poc/src/dashboard.py
```

## Documentation

- `docs/RUN_FRONTEND.md` - Flask frontend run and demo flow
- `docs/INSTALL_DEPENDENCIES.md` - dependency setup details
- `docs/NEXT_STEPS.md` - end-to-end quick start checklist
- `docs/FED_CRAWLER_GUIDE.md` - Fed crawler usage and examples
- `docs/RISK_CLASSIFICATION_GUIDE_EN.md` - risk taxonomy and scoring
- `docs/IMPLEMENTATION_GUIDE.md` - technical architecture details

## Troubleshooting

- Port conflict on `5050`: run `lsof -i :5050` and stop the existing process.
- Missing modules: reinstall with `pip install -r requirements.txt`.
- Guardian dashboard import errors: ensure `pip install -r guardian_poc/requirements.txt`.
- Empty Guardian snapshot: run with `--fallback-demo-if-empty` for demo validation.

## Version

- **Version**: 1.1
- **Status**: Active Development / Demo Ready
- **Last Updated**: 2026-02-16
