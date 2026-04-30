#!/bin/bash
# View ActiveMemory logs.

SERVICE=${1:-all}

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

view_log() {
    local name=$1
    local log_file=$2
    local lines=${3:-50}
    
    echo -e "${CYAN}=== $name ===${NC}"
    if [ -f "$log_file" ]; then
        tail -n "$lines" "$log_file" | sed \
            -e "s/ERROR/$(echo -e '\033[0;31m')&$(echo -e '\033[0m')/g" \
            -e "s/WARNING/$(echo -e '\033[0;33m')&$(echo -e '\033[0m')/g" \
            -e "s/INFO/$(echo -e '\033[0;32m')&$(echo -e '\033[0m')/g"
    else
        echo -e "${YELLOW}⚠ Log file not found: $log_file${NC}"
    fi
    echo ""
}

case $SERVICE in
    mcp)
        view_log "MCP Server" /tmp/active_memory_mcp.log 100
        ;;
    web)
        view_log "Web Dashboard" /tmp/active_memory_web.log 100
        ;;
    telegram)
        view_log "Telegram Bot" /tmp/active_memory_telegram.log 100
        ;;
    all)
        view_log "MCP Server" /tmp/active_memory_mcp.log 30
        view_log "Web Dashboard" /tmp/active_memory_web.log 30
        view_log "Telegram Bot" /tmp/active_memory_telegram.log 30
        ;;
    *)
        echo "Usage: $0 [mcp|web|telegram|all]"
        echo ""
        echo "Examples:"
        echo "  $0 mcp       # View MCP Server logs"
        echo "  $0 web       # View Web Dashboard logs"
        echo "  $0 telegram  # View Telegram Bot logs"
        echo "  $0 all       # View all logs (default)"
        exit 1
        ;;
esac
