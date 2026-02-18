#!/bin/bash
# ARGUS: Cron Job Setup Script
# Configure scheduled tasks on macOS/Linux

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_INTERPRETER="$PROJECT_DIR/venv/bin/python"
DAILY_MONITOR="$PROJECT_DIR/daily_monitor.py"
LOG_DIR="$PROJECT_DIR/data/fed_news/cron_logs"

# Create logging directory
mkdir -p "$LOG_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "ARGUS Cron Job Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "Project directory: $PROJECT_DIR"
echo "Python interpreter: $PYTHON_INTERPRETER"
echo "Daily Monitor: $DAILY_MONITOR"
echo "Logging directory: $LOG_DIR"

echo ""
echo "Select monitoring frequency:"
echo "1. Daily at 9:00 AM (Recommended)"
echo "2. Daily at 12:00 PM (Noon)"
echo "3. Daily at 6:00 PM (Evening)"
echo "4. Every 6 hours"
echo "5. Custom"
echo "0. Exit"
echo ""

read -p "Please select (0-5): " choice

case $choice in
    1)
        CRON_EXPR="0 9 * * *"
        CRON_DESC="Daily at 9:00 AM"
        ;;
    2)
        CRON_EXPR="0 12 * * *"
        CRON_DESC="Daily at 12:00 PM"
        ;;
    3)
        CRON_EXPR="0 18 * * *"
        CRON_DESC="Daily at 6:00 PM"
        ;;
    4)
        CRON_EXPR="0 */6 * * *"
        CRON_DESC="Every 6 hours"
        ;;
    5)
        read -p "Enter cron expression (format: minute hour day month weekday): " CRON_EXPR
        CRON_DESC="Custom: $CRON_EXPR"
        ;;
    0)
        echo "Cancelled"
        exit 0
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

# Build cron command
CRON_CMD="$CRON_EXPR cd '$PROJECT_DIR' && source venv/bin/activate && $PYTHON_INTERPRETER $DAILY_MONITOR >> $LOG_DIR/cron_\$(date +\\%Y\\%m\\%d_\\%H\\%M\\%S).log 2>&1"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Ready to install Cron Job"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "Frequency: $CRON_DESC"
echo "Command: $CRON_CMD"

read -p "Confirm installation of this Cron Job? (y/n): " confirm

if [[ $confirm != [yY] ]]; then
    echo "Cancelled"
    exit 0
fi

# Get existing cron tasks
existing_cron=$(crontab -l 2>/dev/null || echo "")

# Check if the task already exists
if echo "$existing_cron" | grep -q "daily_monitor.py"; then
    echo ""
    echo "⚠️ Warning: A cron task for daily_monitor.py already exists"
    read -p "Override? (y/n): " override

    if [[ $override != [yY] ]]; then
        echo "Cancelled"
        exit 0
    fi

    # Remove old cron task
    echo "$existing_cron" | grep -v "daily_monitor.py" | crontab -
    echo "✓ Old cron task removed"
fi

# Add new cron task
(
    echo "$existing_cron" | grep -v "daily_monitor.py" 2>/dev/null || echo ""
    echo "$CRON_CMD"
) | crontab -

# Verify
if crontab -l | grep -q "daily_monitor.py"; then
    echo ""
    echo "✅ Cron Job installed successfully!"
    echo ""
    echo "Current Cron Tasks:"
    crontab -l | grep -E "daily_monitor|ARGUS" || echo "  (ARGUS monitoring task)"
    echo ""
    echo "Logging file location: $LOG_DIR"
    echo ""
    echo "💡 Tips:"
    echo "  • View cron logs: tail -f $LOG_DIR/*"
    echo "  • View all cron tasks: crontab -l"
    echo "  • Edit cron tasks: crontab -e"
    echo "  • Delete cron task: crontab -r"
else
    echo ""
    echo "❌ Cron Job installation failed"
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
