#!/usr/bin/env python3
"""
ARGUS System Verification Script
Checks that all components are properly configured and working
"""

import os
import sys
import json
from pathlib import Path

def print_header(text):
    """Print formatted header"""
    print(f"\n{'='*70}")
    print(f"  {text}")
    print(f"{'='*70}")

def print_success(text):
    """Print success message"""
    print(f"  ✅ {text}")

def print_error(text):
    """Print error message"""
    print(f"  ❌ {text}")

def print_info(text):
    """Print info message"""
    print(f"  ℹ️  {text}")

def check_file_exists(filepath, description):
    """Check if a file exists"""
    if os.path.exists(filepath):
        print_success(f"{description}: {filepath}")
        return True
    else:
        print_error(f"{description}: {filepath} NOT FOUND")
        return False

def check_module_available(module_name):
    """Check if a Python module is available"""
    try:
        __import__(module_name)
        print_success(f"Module '{module_name}' is available")
        return True
    except ImportError:
        print_error(f"Module '{module_name}' is NOT available - Install with: pip install {module_name}")
        return False

def verify_file_content(filepath, expected_content):
    """Check if file contains expected content"""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            if expected_content in content:
                print_success(f"File contains expected content: {filepath}")
                return True
            else:
                print_error(f"File missing expected content: {filepath}")
                return False
    except Exception as e:
        print_error(f"Cannot read file {filepath}: {str(e)}")
        return False

def main():
    """Main verification routine"""

    print_header("ARGUS System Verification")

    results = {}

    # 1. Check Core Files
    print_header("1. Checking Core Files")

    core_files = [
        ("fed_news_crawler.py", "Fed News Crawler"),
        ("fed_news_db.py", "Database Manager"),
        ("risk_classifier.py", "Risk Classifier"),
        ("agents_system.py", "Multi-Agent System"),
        ("frontend_app.py", "Flask Web Server"),
        ("templates/dashboard.html", "Web Dashboard"),
        ("risk_classification_en.json", "Risk Classification Config"),
    ]

    core_files_ok = True
    for filepath, description in core_files:
        if not check_file_exists(filepath, description):
            core_files_ok = False
    results["core_files"] = core_files_ok

    # 2. Check Configuration Files
    print_header("2. Checking Configuration Files")

    config_files = [
        ("keywords_config.json", "Keywords Configuration"),
        ("risk_classification_en.json", "Risk Classification (English)"),
    ]

    config_ok = True
    for filepath, description in config_files:
        if not check_file_exists(filepath, description):
            config_ok = False
    results["config_files"] = config_ok

    # 3. Check Documentation
    print_header("3. Checking Documentation")

    doc_files = [
        ("docs/RUN_FRONTEND.md", "Frontend Deployment Guide"),
        ("docs/NEXT_STEPS.md", "Quick Start Guide"),
        ("docs/FED_CRAWLER_GUIDE.md", "Crawler Guide"),
        ("docs/RISK_CLASSIFICATION_GUIDE_EN.md", "Risk Classification Guide"),
        ("docs/IMPLEMENTATION_GUIDE.md", "Implementation Guide"),
        ("docs/JSON_FIX_SUMMARY.md", "JSON Fix Summary"),
        ("README.md", "Main README"),
    ]

    docs_ok = True
    for filepath, description in doc_files:
        if not check_file_exists(filepath, description):
            docs_ok = False
    results["documentation"] = docs_ok

    # 4. Check Python Dependencies
    print_header("4. Checking Python Dependencies")

    required_modules = [
        "flask",
        "flask_cors",
        "crawl4ai",
    ]

    optional_modules = [
        "anthropic",
    ]

    deps_ok = True
    for module in required_modules:
        if not check_module_available(module):
            deps_ok = False

    print_info("Checking optional dependencies (for advanced features):")
    for module in optional_modules:
        check_module_available(module)

    results["dependencies"] = deps_ok

    # 5. Verify File Content
    print_header("5. Verifying File Content")

    content_checks = [
        ("agents_system.py", "class AgentOrchestrator", "Agent Orchestrator"),
        ("agents_system.py", "async def execute_pipeline", "Pipeline Execution"),
        ("frontend_app.py", "app = Flask(__name__)", "Flask App"),
        ("frontend_app.py", "@app.route('/api/execute'", "Execute Endpoint"),
        ("templates/dashboard.html", "ARGUS Multi-Agent System", "Dashboard"),
        ("risk_classifier.py", "class RiskClassifier", "Risk Classifier"),
    ]

    content_ok = True
    for filepath, content, description in content_checks:
        if not verify_file_content(filepath, content):
            content_ok = False
    results["content"] = content_ok

    # 6. Check Configuration JSON
    print_header("6. Verifying Configuration Files")

    config_ok = True
    try:
        with open("risk_classification_en.json", "r") as f:
            risk_config = json.load(f)
            if "risk_categories" in risk_config:
                print_success("Risk classification config has correct structure")
            else:
                print_error("Risk classification config missing 'risk_categories' key")
                config_ok = False
    except Exception as e:
        print_error(f"Cannot parse risk_classification_en.json: {str(e)}")
        config_ok = False

    try:
        with open("keywords_config.json", "r") as f:
            keywords_config = json.load(f)
            print_success("Keywords config is valid JSON")
    except Exception as e:
        print_error(f"Cannot parse keywords_config.json: {str(e)}")
        config_ok = False

    results["config_validation"] = config_ok

    # 7. Summary
    print_header("Summary")

    all_ok = all(results.values())

    for category, status in results.items():
        status_symbol = "✅" if status else "❌"
        print(f"  {status_symbol} {category.replace('_', ' ').title()}")

    print()
    if all_ok:
        print_success("All checks passed! System is ready.")
        print_info("Next steps:")
        print_info("  1. Start frontend: python frontend_app.py")
        print_info("  2. Open: http://localhost:5000")
        print_info("  3. Click 'Start Pipeline' to run a demo")
        return 0
    else:
        print_error("Some checks failed. Please review the errors above.")
        print_info("Installation guide: See docs/RUN_FRONTEND.md")
        return 1

if __name__ == "__main__":
    sys.exit(main())
