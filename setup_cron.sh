#!/bin/bash
# ARGUS: Cron Job Setup Script
# 用于Configuration macOS/Linux 上的定时任务

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_INTERPRETER="$PROJECT_DIR/venv/bin/python"
DAILY_MONITOR="$PROJECT_DIR/daily_monitor.py"
LOG_DIR="$PROJECT_DIR/data/fed_news/cron_logs"

# 创建Logging目录
mkdir -p "$LOG_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "ARGUS Cron Job Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "项目目录: $PROJECT_DIR"
echo "Python 解释器: $PYTHON_INTERPRETER"
echo "Daily Monitor: $DAILY_MONITOR"
echo "Logging目录: $LOG_DIR"

echo ""
echo "选择监控频率:"
echo "1. 每天 9:00 AM (推荐)"
echo "2. 每天 12:00 PM (中午)"
echo "3. 每天 6:00 PM (下午)"
echo "4. 每 6 hours执行一次"
echo "5. 自定义"
echo "0. 退出"
echo ""

read -p "请选择 (0-5): " choice

case $choice in
    1)
        CRON_EXPR="0 9 * * *"
        CRON_DESC="每天 9:00 AM"
        ;;
    2)
        CRON_EXPR="0 12 * * *"
        CRON_DESC="每天 12:00 PM"
        ;;
    3)
        CRON_EXPR="0 18 * * *"
        CRON_DESC="每天 6:00 PM"
        ;;
    4)
        CRON_EXPR="0 */6 * * *"
        CRON_DESC="每 6 hours"
        ;;
    5)
        read -p "输入 cron 表达式 (格式: minute hour day month weekday): " CRON_EXPR
        CRON_DESC="自定义: $CRON_EXPR"
        ;;
    0)
        echo "已取消"
        exit 0
        ;;
    *)
        echo "无效选择"
        exit 1
        ;;
esac

# 构建 cron 命令
CRON_CMD="$CRON_EXPR cd '$PROJECT_DIR' && source venv/bin/activate && $PYTHON_INTERPRETER $DAILY_MONITOR >> $LOG_DIR/cron_\$(date +\\%Y\\%m\\%d_\\%H\\%M\\%S).log 2>&1"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "准备安装 Cron Job"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "频率: $CRON_DESC"
echo "命令: $CRON_CMD"

read -p "确认安装此 Cron Job? (y/n): " confirm

if [[ $confirm != [yY] ]]; then
    echo "已取消"
    exit 0
fi

# 获取现有 cron 任务
existing_cron=$(crontab -l 2>/dev/null || echo "")

# Check是否已存在该任务
if echo "$existing_cron" | grep -q "daily_monitor.py"; then
    echo ""
    echo "⚠️ Warning: 已存在 daily_monitor.py 的 cron 任务"
    read -p "是否覆盖? (y/n): " override

    if [[ $override != [yY] ]]; then
        echo "已取消"
        exit 0
    fi

    # 移除旧的 cron 任务
    echo "$existing_cron" | grep -v "daily_monitor.py" | crontab -
    echo "✓ 已移除旧的 cron 任务"
fi

# 添加新的 cron 任务
(
    echo "$existing_cron" | grep -v "daily_monitor.py" 2>/dev/null || echo ""
    echo "$CRON_CMD"
) | crontab -

# Verify
if crontab -l | grep -q "daily_monitor.py"; then
    echo ""
    echo "✅ Cron Job 安装成功!"
    echo ""
    echo "当前 Cron 任务:"
    crontab -l | grep -E "daily_monitor|ARGUS" || echo "  (ARGUS 监控任务)"
    echo ""
    echo "LoggingFile位置: $LOG_DIR"
    echo ""
    echo "💡 提示:"
    echo "  • 查看 cron Logging: tail -f $LOG_DIR/*"
    echo "  • 查看所有 cron 任务: crontab -l"
    echo "  • 编辑 cron 任务: crontab -e"
    echo "  • Delete cron 任务: crontab -r"
else
    echo ""
    echo "❌ Cron Job 安装失败"
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
