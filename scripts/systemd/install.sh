#!/bin/bash
# Install/Uninstall ActiveMemory systemd services.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 [install|uninstall]"
    echo ""
    echo "Install ActiveMemory services to systemd:"
    echo "  sudo $0 install"
    echo ""
    echo "Uninstall services:"
    echo "  sudo $0 uninstall"
    exit 1
}

install_services() {
    echo -e "${YELLOW}🔧 Installing ActiveMemory systemd services...${NC}"
    
    if [ ! -d "$SYSTEMD_DIR" ]; then
        echo -e "${RED}❌ $SYSTEMD_DIR not found${NC}"
        exit 1
    fi
    
    for service in active-memory-mcp active-memory-web active-memory-telegram; do
        src="$SCRIPT_DIR/${service}.service"
        dst="$SYSTEMD_DIR/${service}.service"
        
        if [ -f "$src" ]; then
            echo "Installing $service..."
            cp "$src" "$dst"
            systemctl daemon-reload
            echo -e "${GREEN}✓ $service installed${NC}"
        else
            echo -e "${YELLOW}⚠️ $src not found — skipped${NC}"
        fi
    done
    
    echo ""
    echo -e "${GREEN}✅ Installation complete!${NC}"
    echo ""
    echo "Enable services:"
    echo "  sudo systemctl enable active-memory-mcp"
    echo "  sudo systemctl enable active-memory-web"
    echo "  sudo systemctl enable active-memory-telegram"
    echo ""
    echo "Start services:"
    echo "  sudo systemctl start active-memory-mcp"
    echo "  sudo systemctl start active-memory-web"
    echo "  sudo systemctl start active-memory-telegram"
}

uninstall_services() {
    echo -e "${YELLOW}🗑️ Uninstalling ActiveMemory systemd services...${NC}"
    
    for service in active-memory-mcp active-memory-web active-memory-telegram; do
        dst="$SYSTEMD_DIR/${service}.service"
        
        if [ -f "$dst" ]; then
            echo "Stopping $service..."
            systemctl stop "$service" 2>/dev/null || true
            echo "Disabling $service..."
            systemctl disable "$service" 2>/dev/null || true
            echo "Removing $service..."
            rm -f "$dst"
            echo -e "${GREEN}✓ $service removed${NC}"
        else
            echo "⚪ $service not installed"
        fi
    done
    
    systemctl daemon-reload
    echo ""
    echo -e "${GREEN}✅ Uninstallation complete!${NC}"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ This script must be run as root (use sudo)${NC}"
    exit 1
fi

case "$1" in
    install)
        install_services
        ;;
    uninstall)
        uninstall_services
        ;;
    *)
        usage
        ;;
esac
