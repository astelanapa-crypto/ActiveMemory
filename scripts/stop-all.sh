#!/bin/bash
# Stop all ActiveMemory services.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$PROJECT_DIR/logs"

echo "🛑 Stopping all ActiveMemory services..."

# MCP Server
if [ -f "$LOGS_DIR/active_memory_mcp.pid" ]; then
    PID=$(cat "$LOGS_DIR/active_memory_mcp.pid")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping MCP Server (PID: $PID)..."
        kill "$PID" 2>/dev/null || true
        sleep 2
        # Force kill if still running
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
    fi
    rm "$LOGS_DIR/active_memory_mcp.pid"
    echo "✓ MCP Server stopped"
else
    echo "⚪ MCP Server: not running"
fi

# Web Dashboard
if [ -f "$LOGS_DIR/active_memory_web.pid" ]; then
    PID=$(cat "$LOGS_DIR/active_memory_web.pid")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping Web Dashboard (PID: $PID)..."
        kill "$PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
    fi
    rm "$LOGS_DIR/active_memory_web.pid"
    echo "✓ Web Dashboard stopped"
else
    echo "⚪ Web Dashboard: not running"
fi

# Telegram Bot
if [ -f "$LOGS_DIR/active_memory_telegram.pid" ]; then
    PID=$(cat "$LOGS_DIR/active_memory_telegram.pid")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping Telegram Bot (PID: $PID)..."
        kill "$PID" 2>/dev/null || true
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
    fi
    rm "$LOGS_DIR/active_memory_telegram.pid"
    echo "✓ Telegram Bot stopped"
else
    echo "⚪ Telegram Bot: not running"
fi

echo ""
echo "✅ All services stopped"
