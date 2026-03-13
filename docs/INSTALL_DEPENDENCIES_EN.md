# ARGUS Dependencies Installation Guide

## Quick Installation (2 minutes)

### Required Dependencies for Frontend

```bash
pip install flask flask-cors
```

### Optional Dependencies (for advanced features)

```bash
pip install anthropic
```

### All Dependencies in One Command

```bash
pip install flask flask-cors crawl4ai anthropic
```

---

## Detailed Installation

### macOS
```bash
# Using pip (recommended)
pip install flask flask-cors crawl4ai anthropic

# Or using Homebrew (alternative)
brew install python3
pip3 install flask flask-cors crawl4ai anthropic
```

### Linux
```bash
# Ubuntu/Debian
sudo apt-get install python3-pip
pip install flask flask-cors crawl4ai anthropic

# Fedora/RHEL
sudo dnf install python3-pip
pip install flask flask-cors crawl4ai anthropic
```

### Windows
```bash
# Command Prompt (CMD)
pip install flask flask-cors crawl4ai anthropic

# Or PowerShell
python -m pip install flask flask-cors crawl4ai anthropic
```

---

## Module Descriptions

### Required for Web Frontend
- **flask** (3.0+) - Web server framework
- **flask-cors** (4.0+) - Cross-origin request handling

### Required for News Crawling
- **crawl4ai** - Web scraping with JavaScript rendering

### Optional but Recommended
- **anthropic** - Claude API integration for deep risk analysis

---

## Verification

After installation, verify all dependencies:

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

---

## Troubleshooting Installation

### Issue: "ModuleNotFoundError: No module named 'pip'"

**Solution**: Upgrade pip first:
```bash
python -m pip install --upgrade pip
```

### Issue: "Permission denied" error

**Solution**: Use `--user` flag or use a virtual environment:
```bash
# Option 1: Install for current user
pip install --user flask flask-cors

# Option 2: Use virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install flask flask-cors crawl4ai anthropic
```

### Issue: Installation is very slow

**Solution**: Use a faster pip index:
```bash
pip install -i https://mirrors.aliyun.com/pypi/simple flask flask-cors
```

### Issue: "No module named '_ssl'"

**Solution**: This is a Python SSL issue. Reinstall Python or use:
```bash
pip install --upgrade certifi
```

---

## Virtual Environment Setup (Recommended)

Create a dedicated environment for ARGUS:

```bash
# 1. Create virtual environment
python -m venv venv

# 2. Activate it
# On macOS/Linux:
source venv/bin/activate

# On Windows:
# venv\Scripts\activate

# 3. Install all dependencies
pip install flask flask-cors crawl4ai anthropic

# 4. Verify installation
python verify_system.py
```

To deactivate the virtual environment:
```bash
deactivate
```

---

## Using .env File (Optional)

For API keys like ANTHROPIC_API_KEY, create a `.env` file:

```bash
# .env file
ANTHROPIC_API_KEY=sk-ant-...
```

Then in your code:
```python
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv('ANTHROPIC_API_KEY')
```

Install python-dotenv if needed:
```bash
pip install python-dotenv
```

---

## Version Check

Verify installed versions:

```bash
python -c "import flask; print(f'Flask: {flask.__version__}')"
python -c "import crawl4ai; print(f'Crawl4AI: {crawl4ai.__version__}')"
python -c "import anthropic; print(f'Anthropic: {anthropic.__version__}')"
```

---

## Next Steps

1. **Start the Frontend**:
   ```bash
   python frontend_app.py
   ```

2. **Open Dashboard**:
   Navigate to http://localhost:5050

3. **Run a Demo**:
   Click "Start Pipeline" button

See `RUN_FRONTEND.md` for detailed instructions.

---

**Last Updated**: 2026-02-13
**Status**: ✅ Production Ready
