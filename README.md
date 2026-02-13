# ARGUS - Automated Risk Guardian for Universal Sensing

A comprehensive multi-agent system for monitoring Alphabet's global treasury risk through Federal Reserve news analysis and classification.

## 🎯 Project Overview

ARGUS automatically monitors Federal Reserve announcements and news, classifies financial risks using a sophisticated three-tier framework, and generates comprehensive risk reports. The system is powered by intelligent agents that work together to:

1. **Gather News** - Crawl Federal Reserve press releases
2. **Classify Risks** - Analyze content against 100+ risk keywords
3. **Store Data** - Maintain SQLite database of all news and classifications
4. **Analyze Trends** - Identify patterns and critical signals
5. **Generate Reports** - Create automated risk assessment reports

## ✨ Key Features

### 🚀 Multi-Agent Architecture
- **NewsGathererAgent**: Crawls Federal Reserve news using Crawl4AI
- **RiskClassifierAgent**: Classifies risks across 14 categories
- **DatabaseAgent**: Manages persistent data storage
- **AnalysisAgent**: Performs cross-item pattern analysis
- **ReportingAgent**: Generates actionable reports

### 📊 Risk Classification System
- **3-Tier Framework**: Core Categories → Intermediate Categories → Risk Sources
- **100+ Keywords**: Automatic risk signal identification
- **0-100 Risk Scoring**: Critical/High/Medium/Low/None levels
- **6 High-Priority Keywords**: Immediate alert triggers (crisis, default, bankruptcy, fraud, cyber attack, scandal)

### 🌐 Web Dashboard
- **Real-time Monitoring**: Watch agents execute in real-time
- **Pipeline Visualization**: 5-stage execution flow display
- **Agent Status Cards**: Live agent status and statistics
- **Results Tabs**: Summary, Distribution, and Detailed views
- **System Logs**: Terminal-style real-time log display

### ⚙️ Automation & Integration
- **Daily Monitoring**: Automated daily news gathering
- **Cron Integration**: One-click scheduling setup
- **Claude API**: Deep risk analysis with AI
- **Data Export**: Export results in JSON/Markdown formats

## 🚀 Quick Start (5 minutes)

### 1. Install Dependencies
```bash
pip install flask flask-cors crawl4ai anthropic
```

See `INSTALL_DEPENDENCIES.md` for detailed installation instructions.

### 2. Verify System
```bash
python verify_system.py
```

Expected output: ✅ All checks passed!

### 3. Start Frontend
```bash
python frontend_app.py
```

Server will start at **http://localhost:5000**

### 4. Open Dashboard
Open your browser and navigate to http://localhost:5000

### 5. Run a Demo
Click the **"Start Pipeline"** button to execute a complete analysis

## 📚 Documentation

| Document | Purpose | Time |
|----------|---------|------|
| **RUN_FRONTEND.md** | Start web frontend & run demos | 5 min |
| **INSTALL_DEPENDENCIES.md** | Install required packages | 2 min |
| **NEXT_STEPS.md** | Comprehensive quick start | 10 min |
| **FED_CRAWLER_GUIDE.md** | Detailed crawler usage | 15 min |
| **RISK_CLASSIFICATION_GUIDE_EN.md** | Risk framework explained | 20 min |
| **IMPLEMENTATION_GUIDE.md** | Technical architecture | 30 min |

## 🎮 Quick Demo Steps

1. **Install**: `pip install flask flask-cors crawl4ai anthropic`
2. **Verify**: `python verify_system.py`
3. **Run**: `python frontend_app.py`
4. **Access**: Open http://localhost:5000 in browser
5. **Demo**: Click "Start Pipeline" button and watch agents execute

## 🛠️ Command Reference

```bash
# Start web dashboard
python frontend_app.py

# Run daily monitoring
python daily_monitor.py

# Verify system
python verify_system.py

# Setup automation
bash setup_cron.sh
```

## 📊 Risk Classification

- **Critical** (80-100): Immediate action required
- **High** (60-79): Conduct deep analysis
- **Medium** (40-59): Monitor regularly
- **Low** (1-39): Routine monitoring
- **None** (0): No special action

High-priority triggers: crisis, default, bankruptcy, fraud, cyber attack, scandal

## 🚨 Troubleshooting

**Flask won't start**: Check if port 5000 is available. Use `lsof -i :5000` to find conflicting process.

**Dashboard not loading**: Clear browser cache and ensure Flask server is running.

**Missing dependencies**: Run `pip install flask flask-cors crawl4ai anthropic`

See `RUN_FRONTEND.md` for more troubleshooting.

## 📈 Project Status

✅ **Complete and Production Ready**
- Multi-agent system with 5 agents
- Web dashboard with real-time monitoring
- Risk classification (14 categories, 100+ keywords)
- Database management
- Complete documentation

## 📖 Next Steps

1. Read `RUN_FRONTEND.md` - Deployment and demo guide
2. Run `python verify_system.py` - System verification
3. Start `python frontend_app.py` - Launch dashboard
4. Open http://localhost:5000 - View dashboard
5. Click "Start Pipeline" - Run complete demo

---

**Version**: 1.0 | **Status**: ✅ Production Ready | **Last Updated**: 2026-02-13

Welcome to ARGUS - Your Automated Risk Guardian! 🚀
