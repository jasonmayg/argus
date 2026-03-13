# ARGUS Web Frontend - Deployment and Demo Guide

## Quick Start (2 minutes)

### 1. Install Dependencies
```bash
pip install flask flask-cors
```

### 2. Start the Frontend Server
```bash
python frontend_app.py
```

Expected output:
```
╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║         ARGUS Multi-Agent System - Web Frontend                ║
║                                                                ║
║  Starting Flask server on http://localhost:5050/              ║
║  Open your browser and navigate to the URL above               ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

### 3. Open Dashboard
Navigate to **http://localhost:5050** in your browser

---

## Dashboard Features

### Main Interface
- **Pipeline Visualization**: Watch the 5-stage execution flow
  - Stage 1: News Gathering (Crawl Federal Reserve news)
  - Stage 2: Risk Classification (Analyze risk levels)
  - Stage 3: Database Management (Store and index)
  - Stage 4: Risk Analysis (Deep analysis)
  - Stage 5: Report Generation (Create summary)

- **Agent Status Monitor**: Real-time status of 5 agents
  - NewsGatherer: Crawls news from Federal Reserve
  - RiskClassifier: Classifies risks in news items
  - DatabaseManager: Manages data storage
  - RiskAnalyzer: Performs comprehensive analysis
  - Reporter: Generates analysis reports

- **Results Dashboard**:
  - Summary tab: High-level statistics
  - Distribution tab: Risk level breakdown (Critical/High/Medium/Low)
  - Details tab: Complete analysis data

- **System Logs**: Real-time execution logs in terminal style

### Controls
- **Start Pipeline**: Execute complete analysis (News → Analysis → Report)
- **Reset System**: Clear all execution state and restart
- **Status Auto-Update**: Updates every 1 second during execution

---

## Running a Demo

### Demo Scenario 1: Complete Pipeline Execution (Recommended)

1. **Click "Start Pipeline"** button
2. **Watch the visualization**:
   - Stage indicators light up as each agent completes
   - Agent status cards update from "Idle" → "Running" → "Success"
   - Progress bar animates through all 5 stages

3. **View Results** when complete:
   - **Summary Tab**: Shows total items processed, critical/high/medium/low counts
   - **Distribution Tab**: Visual breakdown with color-coded boxes
   - **Details Tab**: Complete structured results with matched keywords

4. **Inspect Logs**: Scroll through system logs to see execution details

### Demo Scenario 2: Status Monitoring

1. **Click "Start Pipeline"**
2. **Immediately check** "Agent Summary" cards to see:
   - NewsGatherer status: "Running" → "Success"
   - RiskClassifier status: Shows classification count
   - DatabaseManager status: Shows records stored
   - RiskAnalyzer status: Analysis completion indicator
   - Reporter status: Reports generated count

3. **Verify completion** when all agents show "Success" status

### Demo Scenario 3: Reset and Re-Run

1. After a completed pipeline execution, click **"Reset System"**
2. All status cards return to "Idle"
3. Results are cleared
4. System is ready for another pipeline execution

---

## Expected Results

### Successful Execution Output

```
[STAGE 1] News Gathering...
[NewsGatherer] INFO: Starting news gathering...
[NewsGatherer] INFO: Crawling: https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm
[NewsGatherer] INFO: Successfully gathered 14 news items
[NewsGatherer] INFO: News gathering completed successfully

[STAGE 2] Risk Classification...
[RiskClassifier] INFO: Starting classification of 14 items
[RiskClassifier] INFO: Successfully classified 14 items
[RiskClassifier] INFO: Distribution: {'Low': 9, 'Medium': 4, 'High': 1}

[STAGE 3] Database Management...
[DatabaseManager] INFO: Executing database action: insert
[DatabaseManager] INFO: Inserted 14 records

[STAGE 4] Risk Analysis...
[AnalysisAgent] INFO: Starting analysis of 14 items
[AnalysisAgent] INFO: Critical: 0, High: 1, Medium: 4, Low: 9
[AnalysisAgent] INFO: Analysis completed successfully

[STAGE 5] Report Generation...
[Reporter] INFO: Generating report...
[Reporter] INFO: Report generated successfully

================================================================================
PIPELINE STATUS: SUCCESS
================================================================================
```

### Results Display

Results are displayed in three formats:

**Summary View**:
```json
{
  "title": "Federal Reserve News Risk Analysis Report",
  "total_items": 14,
  "critical_count": 0,
  "high_count": 1,
  "medium_count": 4,
  "high_count": 1
}
```

**Distribution View**:
```
Critical: 0 items    🔴
High:     1 item     🟠
Medium:   4 items    🟡
Low:      9 items    🟢
```

**Details View**:
Complete structured JSON with:
- Risk categories breakdown
- Keyword matches per item
- Top 10 keywords
- Category distribution
- Management recommendations

---

## Troubleshooting

### Issue: "Connection refused" when opening http://localhost:5050

**Solution**:
- Verify Flask server is running (check terminal for startup message)
- Try accessing http://127.0.0.1:5050 instead
- Port 5050 may be in use - restart the Flask app or check with `lsof -i :5050`

### Issue: Pipeline executes but shows no results

**Solution**:
- Check browser console (F12 → Console tab) for JavaScript errors
- Verify all Python modules are imported correctly
- Check that Crawl4AI is installed: `pip install crawl4ai`

### Issue: "ModuleNotFoundError" when starting Flask

**Solution**:
Install all required dependencies:
```bash
pip install flask flask-cors crawl4ai anthropic
```

### Issue: Frontend updates slowly or doesn't refresh

**Solution**:
- Clear browser cache (Ctrl+Shift+Delete or Cmd+Shift+Delete)
- Hard refresh (Ctrl+F5 or Cmd+Shift+R)
- Close and reopen the browser

---

## Architecture Overview

### Multi-Agent System Design

```
┌─────────────────────────────────────────────────────────┐
│         ARGUS Multi-Agent Orchestrator                   │
└────────────────┬────────────────────────────────────────┘
                 │
        ┌────────┼────────┬─────────┬──────────┐
        │        │        │         │          │
        ▼        ▼        ▼         ▼          ▼
   ┌─────────┐ ┌────────┐ ┌──────┐ ┌────────┐ ┌────────┐
   │ News    │ │ Risk   │ │Data  │ │Analysis│ │Report  │
   │Gatherer │→│Classif.│→│Manager│→│Agent   │→│Agent   │
   └─────────┘ └────────┘ └──────┘ └────────┘ └────────┘
        │          │          │        │         │
        └──────────┴──────────┴────────┴─────────┘
                   │
                   ▼
           Flask Web API
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
    Status     Results    Agent Logs
    Endpoint   Endpoint   Endpoints
        │          │          │
        └──────────┴──────────┘
                   │
                   ▼
          Browser Dashboard
          (HTML/CSS/JavaScript)
```

### Agent Responsibilities

1. **NewsGathererAgent**:
   - Crawls Federal Reserve press releases
   - Extracts news titles, dates, links
   - Returns structured news items

2. **RiskClassifierAgent**:
   - Analyzes news titles for risk keywords
   - Assigns risk scores (0-100)
   - Categories: Critical/High/Medium/Low/None

3. **DatabaseAgent**:
   - Stores news items in SQLite
   - Tracks keyword matches
   - Manages metadata

4. **AnalysisAgent**:
   - Performs cross-item analysis
   - Identifies risk patterns
   - Extracts top keywords
   - Categorizes by risk level

5. **ReportingAgent**:
   - Synthesizes analysis results
   - Generates management recommendations
   - Creates summary statistics

---

## API Endpoints Reference

### GET /
Returns the main dashboard HTML interface.

### GET /api/status
Returns current execution status:
```json
{
  "is_running": false,
  "current_stage": "completed",
  "agents_status": {
    "news_gatherer": {"status": "success", "logs": 5},
    "risk_classifier": {"status": "success", "classified_count": 14},
    "database_manager": {"status": "success", "records_stored": 14},
    "analyzer": {"status": "success", "analysis_ready": true},
    "reporter": {"status": "success", "reports_generated": 1}
  },
  "timestamp": "2026-02-13T12:34:56.789123"
}
```

### POST /api/execute
Starts the analysis pipeline in background.

Request: Empty POST
Response:
```json
{
  "status": "started",
  "message": "Pipeline execution started"
}
```

### GET /api/results
Returns latest execution results with all stages and analysis.

### GET /api/agents/summary
Returns summary statistics for all agents.

### GET /api/agents/logs/<agent_name>
Returns logs for specific agent. Valid names:
- news_gatherer
- risk_classifier
- database
- analyzer
- reporter

### POST /api/reset
Reinitializes all agents and clears state.

---

## Performance Notes

- **First execution**: 5-8 seconds (crawling network request)
- **Subsequent executions**: 2-4 seconds (classified items cached)
- **Real-time updates**: 1-second polling interval
- **Max news items per crawl**: 50-100 depending on page
- **Concurrent agents**: Sequential execution for data consistency

---

## Next Steps

### Customization Options

1. **Change crawl URL**:
   Edit `agents_system.py` line 71 to target different years:
   ```python
   url = f"https://www.federalreserve.gov/newsevents/pressreleases/{year}-press.htm"
   ```

2. **Adjust risk thresholds**:
   Edit `risk_classifier.py` get_risk_level() method to change score boundaries

3. **Add new keywords**:
   Edit `risk_classification_en.json` to add keywords to categories

4. **Customize report format**:
   Edit ReportingAgent._generate_recommendations() in `agents_system.py`

### Integration Examples

#### Schedule daily execution:
```bash
bash setup_cron.sh
```

#### Monitor specific agent:
```bash
curl http://localhost:5050/api/agents/logs/risk_classifier
```

#### Get analysis results:
```bash
curl http://localhost:5050/api/results | python -m json.tool
```

---

## Support & Documentation

- **Complete Setup Guide**: See SETUP_SUMMARY.md
- **Crawler Documentation**: See FED_CRAWLER_GUIDE.md
- **Risk Classification**: See RISK_CLASSIFICATION_GUIDE_EN.md
- **Implementation Details**: See IMPLEMENTATION_GUIDE.md
- **Quick Reference**: See RISK_CLASSIFICATION_QUICK_REFERENCE_EN.md

---

**Status**: ✅ Production Ready
**Last Updated**: 2026-02-13
**Version**: 1.0
