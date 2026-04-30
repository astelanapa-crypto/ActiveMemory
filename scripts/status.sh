#!/bin/bash
# Check status of ActiveMemory services.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "📊 Status of ActiveMemory services"
echo "======================================"

# Function to check service
check_service() {
    local name=$1
    local pid_file=$2
    local port=$3
    local url=$4
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "🟢 $name: \033[0;32mRUNNING\033[0m (PID: $pid)"
            # Check if port is listening (for web services)
            if [ -n "$port" ]; then
                if command -v curl &> /dev/null; then
                    if curl -s "http://localhost:$port" > /dev/null 2>&1; then
                        echo "   ✓ Responding on port $port"
                    fi
                fi
            fi
        else
            echo -e "🔴 $name: \033[0;33mDEAD\033[0m (stale PID file)"
            rm -f "$pid_file"
        fi
    else
        echo -e "⚪ $name: \033[0;31mNOT RUNNING\033[0m"
    fi
    echo ""
}

# MCP Server (stdio-based, runs as subprocess when MCP client connects)
if [ -f /tmp/active_memory_mcp.pid ] && kill -0 $(cat /tmp/active_memory_mcp.pid) 2>/dev/null; then
    echo -e "🟢 MCP Server: \033[0;32mRUNNING\033[0m (PID: $(cat /tmp/active_memory_mcp.pid)) - stdio mode"
else
    echo -e "⚪ MCP Server: \033[0;33mSTDIO MODE\033[0m (not a daemon - use with MCP client like Hermes)"
fi
echo ""
check_service "Web Dashboard" /tmp/active_memory_web.pid "8788" "http://localhost:8788"
check_service "Telegram Bot" /tmp/active_memory_telegram.pid

# Overall status
RUNNING=0
TOTAL=3

[ -f /tmp/active_memory_mcp.pid ] && kill -0 $(cat /tmp/active_memory_mcp.pid) 2>/dev/null && RUNNING=$((RUNNING + 1))
[ -f /tmp/active_memory_web.pid ] && kill -0 $(cat /tmp/active_memory_web.pid) 2>/dev/null && RUNNING=$((RUNNING + 1))
[ -f /tmp/active_memory_telegram.pid ] && kill -0 $(cat /tmp/active_memory_telegram.pid) 2>/dev/null && RUNNING=$((RUNNING + 1))

echo "======================================"
echo "Summary: $RUNNING/$TOTAL services running"

if [ "$RUNNING" -eq "$TOTAL" ]; then
    echo -e "\033[0;32m✅ All services running\033[0m"
    exit 0
elif [ "$RUNNING" -eq 0 ]; then
    echo -e "\033[0;31m❌ No services running\033[0m"
    exit 1
else
    echo -e "\033[0;33m⚠ Some services not running\033[0m"
    exit 0
fi
