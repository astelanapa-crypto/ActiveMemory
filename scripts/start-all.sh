#!/bin/bash
# Start all ActiveMemory services.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$PROJECT_DIR/logs"

# Create logs directory
mkdir -p "$LOGS_DIR"

echo "🚀 Starting all ActiveMemory services..."

# Load .env if exists
if [ -f "$PROJECT_DIR/.env" ]; then
    source "$PROJECT_DIR/.env"
    echo "✓ Loaded .env"
fi

# Check dependencies
if [ -f "$SCRIPT_DIR/check-dependencies.sh" ]; then
    bash "$SCRIPT_DIR/check-dependencies.sh"
fi

# Start MCP Server
echo "📡 Starting MCP Server..."
nohup python3 -m active_memory_mcp.main > "$LOGS_DIR/active_memory_mcp.log" 2>&1 &
echo $! > "$LOGS_DIR/active_memory_mcp.pid"
echo "  ✓ MCP Server started (PID: $!)"

# Start Web Dashboard
echo "🌐 Starting Web Dashboard..."
nohup python3 -m active_memory_mcp.web.server > "$LOGS_DIR/active_memory_web.log" 2>&1 &
echo $! > "$LOGS_DIR/active_memory_web.pid"
echo "  ✓ Web Dashboard started (PID: $!) - http://localhost:${AM_WEB_PORT:-8788}"

# Start Telegram Bot (if configured)
if command -v python3 && python3 -c "import telegram" 2>/dev/null; then
    if [ -n "$TG_BOT_TOKEN" ]; then
        echo "📱 Starting Telegram Bot..."
        nohup python3 -m active_memory_mcp.telegram.bot > "$LOGS_DIR/active_memory_telegram.log" 2>&1 &
        echo $! > "$LOGS_DIR/active_memory_telegram.pid"
        echo "  ✓ Telegram Bot started (PID: $!)"
    else
        echo "⚠️ TG_BOT_TOKEN not set — Telegram bot skipped"
    fi
else
    echo "⚠️ python-telegram-bot not installed — Telegram bot skipped"
fi

echo ""
echo "✅ All services started!"
echo "📊 PID files: $LOGS_DIR/*.pid"
echo "📝 Logs: $LOGS_DIR/*.log"
echo ""
echo "Check status: $SCRIPT_DIR/status.sh"
