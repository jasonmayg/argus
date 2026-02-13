# ARGUS Setup Guide

Complete installation and configuration guide for the ARGUS system.

**Updated**: 2026-02-13 | **Version**: 1.0

---

## 📋 Table of Contents

1. [System Requirements](#system-requirements)
2. [Installation Methods](#installation-methods)
3. [Virtual Environment Setup](#virtual-environment-setup)
4. [Dependency Files](#dependency-files)
5. [Verification](#verification)
6. [Configuration](#configuration)
7. [Troubleshooting](#troubleshooting)

---

## 🖥️ System Requirements

### Minimum Requirements
- **Python**: 3.8+ (tested with 3.10+)
- **OS**: macOS, Linux, or Windows
- **RAM**: 2GB minimum (4GB recommended)
- **Disk**: 500MB free space
- **Internet**: Required for crawling Federal Reserve website

### Recommended Setup
- **Python**: 3.10+
- **RAM**: 4GB+
- **Virtual Environment**: Yes (strongly recommended)
- **Database**: SQLite (included)

---

## 📦 Installation Methods

### Method 1: Quick Installation (Recommended for First-Time Users)

Perfect for getting started immediately.

```bash
# 1. Navigate to project directory
cd /path/to/argus

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install core requirements
pip install -r requirements.txt

# 4. Verify installation
python verify_system.py
```

**Time**: 5-10 minutes
**Includes**: All core dependencies needed to run ARGUS

---

### Method 2: Development Setup (For Development/Customization)

Complete setup with testing, linting, and development tools.

```bash
# 1. Navigate to project directory
cd /path/to/argus

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install development requirements
pip install -r requirements-dev.txt

# 4. Verify installation
python verify_system.py

# 5. Run tests (if test suite exists)
pytest
```

**Time**: 10-15 minutes
**Includes**: Core + Testing + Code Quality tools

**Available Commands**:
```bash
pytest                    # Run tests
black .                  # Format code
flake8 .               # Check code quality
mypy .                 # Type checking
ipython                # Interactive shell
```

---

### Method 3: Advanced Features Setup (For Data Analysis & Visualization)

Setup with data analysis, visualization, and monitoring tools.

```bash
# 1. Navigate to project directory
cd /path/to/argus

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install optional requirements
pip install -r requirements-optional.txt

# 4. Verify installation
python verify_system.py
```

**Time**: 15-20 minutes
**Includes**: Core + Data Analysis + Visualization tools

**Available Features**:
- Data analysis with pandas
- Visualization with matplotlib/seaborn/plotly
- Advanced database operations
- Enhanced logging
- Prometheus metrics

---

### Method 4: Production Setup (All Features)

Complete production-grade setup.

```bash
# 1. Navigate to project directory
cd /path/to/argus

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install all requirements
pip install -r requirements.txt
pip install -r requirements-optional.txt
pip install -r requirements-dev.txt

# 4. Verify installation
python verify_system.py

# 5. Configure environment
cp .env.example .env
# Edit .env with your API keys and settings
```

**Time**: 20-30 minutes
**Includes**: Everything

---

## 🔧 Virtual Environment Setup

Virtual environments isolate project dependencies. **Highly recommended**.

### Creating Virtual Environment

**macOS/Linux**:
```bash
# Create
python -m venv venv

# Activate
source venv/bin/activate

# Deactivate (when done)
deactivate
```

**Windows (Command Prompt)**:
```bash
# Create
python -m venv venv

# Activate
venv\Scripts\activate

# Deactivate (when done)
deactivate
```

**Windows (PowerShell)**:
```bash
# Create
python -m venv venv

# Activate
venv\Scripts\Activate.ps1

# Deactivate (when done)
deactivate
```

### Verifying Virtual Environment

Check that you're using the correct Python:

```bash
# Should show path to venv
which python          # macOS/Linux
where python          # Windows

# Should show venv directory
pip --version

# Should list only venv packages
pip list
```

---

## 📋 Dependency Files

### requirements.txt (Core)
Essential dependencies for running ARGUS.

**Size**: ~10 packages
**Install time**: 2-5 minutes
**Space**: ~200MB

**Includes**:
- Flask web server
- Crawl4AI web scraping
- Anthropic API
- Database support
- Configuration utilities

**Install**: `pip install -r requirements.txt`

---

### requirements-dev.txt (Development)
Development, testing, and code quality tools.

**Size**: ~15 additional packages
**Install time**: 3-5 minutes
**Space**: ~100MB additional

**Includes**:
- pytest testing framework
- Code formatters (black, isort)
- Linters (flake8, pylint)
- Type checker (mypy)
- Debuggers (ipdb, pdbpp)

**Install**: `pip install -r requirements-dev.txt`

---

### requirements-optional.txt (Advanced)
Data analysis, visualization, and advanced features.

**Size**: ~12 additional packages
**Install time**: 5-10 minutes
**Space**: ~300MB additional

**Includes**:
- pandas (data analysis)
- numpy (numerical computing)
- matplotlib/seaborn (visualization)
- SQLAlchemy (ORM)
- loguru (advanced logging)

**Install**: `pip install -r requirements-optional.txt`

---

## ✅ Verification

### Quick Verification

```bash
python verify_system.py
```

Expected output:
```
✅ Core Files
✅ Config Files
✅ Documentation
✅ Dependencies
✅ Content
✅ Config Validation

✅ All checks passed! System is ready.
```

### Manual Verification

Check individual components:

```bash
# Check Flask
python -c "import flask; print(f'Flask {flask.__version__}')"

# Check Crawl4AI
python -c "import crawl4ai; print('Crawl4AI OK')"

# Check Anthropic
python -c "import anthropic; print('Anthropic OK')"

# Check database
python -c "import sqlite3; print('SQLite OK')"

# List all installed packages
pip list
```

### Test Installation

```bash
# Test crawler
python -c "from fed_news_crawler import FedNewsCrawler; print('Crawler OK')"

# Test classifier
python -c "from risk_classifier import RiskClassifier; print('Classifier OK')"

# Test database
python -c "from fed_news_db import FedNewsDatabase; print('Database OK')"

# Test agents
python -c "from agents_system import AgentOrchestrator; print('Agents OK')"

# Test Flask app
python -c "from frontend_app import app; print('Flask OK')"
```

---

## ⚙️ Configuration

### Environment Variables

Create a `.env` file for sensitive configuration:

```bash
# .env file
ANTHROPIC_API_KEY=sk-ant-...
FLASK_ENV=development
FLASK_DEBUG=True
DATABASE_PATH=./data/fed_news/fed_news.db
LOG_LEVEL=INFO
```

### Configuration Files

Edit these files to customize behavior:

**keywords_config.json** - Keyword definitions
```json
{
  "high_priority": ["crisis", "default", ...],
  "financial": ["market", "credit", ...],
  ...
}
```

**risk_classification_en.json** - Risk mapping
```json
{
  "risk_categories": {
    "FINANCIAL_RISK": {
      "subcategories": {
        "MARKET_RISK": {
          "keywords": [...],
          "outer_sources": [...]
        }
      }
    }
  }
}
```

---

## 🚀 Quick Start After Installation

### 1. Verify System (1 minute)
```bash
python verify_system.py
```

### 2. Start Dashboard (1 minute)
```bash
python frontend_app.py
```

### 3. Open in Browser
```
http://localhost:5000
```

### 4. Run Demo (2 minutes)
Click "Start Pipeline" button and watch execution

---

## 🔍 Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'flask'"

**Cause**: Requirements not installed

**Solution**:
```bash
# Make sure you're in the virtual environment
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

---

### Issue: "Permission denied" when installing

**Cause**: Trying to install system-wide

**Solution**:
```bash
# Use virtual environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# OR use --user flag
pip install --user -r requirements.txt
```

---

### Issue: "pip: command not found"

**Cause**: Python/pip not in PATH

**Solution** (macOS/Linux):
```bash
# Use python -m pip instead
python -m pip install -r requirements.txt

# Or check Python installation
which python3
python3 -m pip install -r requirements.txt
```

**Solution** (Windows):
```bash
# Use python -m pip
python -m pip install -r requirements.txt
```

---

### Issue: Installation is very slow

**Cause**: Using default PyPI mirror

**Solution**: Use faster mirror
```bash
# Aliyun mirror (for China)
pip install -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt

# Official PyPI
pip install -i https://pypi.org/simple -r requirements.txt
```

---

### Issue: "No module named '_ssl'"

**Cause**: Python built without SSL support

**Solution**:
```bash
# Upgrade certifi
pip install --upgrade certifi

# Or reinstall Python properly
# macOS: brew install python@3.10
# Linux: apt install python3.10
# Windows: Download from python.org
```

---

### Issue: Virtual environment not activating

**Cause**: Incorrect activation command or corrupted venv

**Solution**:
```bash
# Delete and recreate
rm -rf venv

# Create new
python -m venv venv

# Activate (correct for your OS)
# macOS/Linux: source venv/bin/activate
# Windows CMD: venv\Scripts\activate
# Windows PS: venv\Scripts\Activate.ps1

# Verify activation (should show (venv) in prompt)
which python
```

---

### Issue: Port 5000 already in use

**Cause**: Another process using port 5000

**Solution**:
```bash
# Find process on port 5000
lsof -i :5000

# Kill the process
kill -9 <PID>

# Or use different port
# Edit frontend_app.py, change line 145:
# app.run(debug=True, port=5000)
# to:
# app.run(debug=True, port=5001)
```

---

### Issue: "SSL: CERTIFICATE_VERIFY_FAILED"

**Cause**: SSL certificate issue

**Solution**:
```bash
# Install/update certificates
pip install --upgrade certifi

# Or on macOS, run the certificate installer
/Applications/Python\ 3.x/Install\ Certificates.command

# Or use PYTHONHTTPSVERIFY
PYTHONHTTPSVERIFY=0 python your_script.py
```

---

## 📊 Installation Comparison

| Aspect | Quick | Dev | Optional | Production |
|--------|-------|-----|----------|-----------|
| Time | 5 min | 10 min | 15 min | 30 min |
| Size | ~200MB | ~300MB | ~500MB | ~800MB |
| Testing | ❌ | ✅ | ✅ | ✅ |
| Quality Tools | ❌ | ✅ | ✅ | ✅ |
| Data Analysis | ❌ | ❌ | ✅ | ✅ |
| Monitoring | ❌ | ❌ | ✅ | ✅ |
| For: | Users | Developers | Analysts | Production |

---

## ✨ Post-Installation

### Next Steps

1. **Verify**: `python verify_system.py`
2. **Understand**: Read README.md or INDEX.md
3. **Run**: `python frontend_app.py`
4. **Demo**: Open http://localhost:5000
5. **Configure**: Edit configuration files if needed

### Common Next Actions

```bash
# Run daily monitoring
python daily_monitor.py

# Setup automation
bash setup_cron.sh

# View example usage
python example_usage.py

# Check dependencies
pip show flask crawl4ai anthropic
```

---

## 📚 Documentation

For more information, see:
- **README.md** - Project overview
- **RUN_FRONTEND.md** - Dashboard deployment
- **NEXT_STEPS.md** - Getting started
- **IMPLEMENTATION_GUIDE.md** - Technical details
- **INSTALL_DEPENDENCIES.md** - Alternative guide

---

## 🎯 Summary

**Quick Installation** (Recommended):
```bash
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
python verify_system.py
python frontend_app.py
# Open http://localhost:5000
```

**That's it!** Your ARGUS system is ready to use.

---

**Last Updated**: 2026-02-13
**Status**: ✅ Production Ready
