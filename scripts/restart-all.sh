#!/bin/bash
# Restart all ActiveMemory services.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🔄 Restarting all ActiveMemory services..."
echo "======================================="

# Stop all services
bash "$SCRIPT_DIR/stop-all.sh"

# Wait for processes to fully stop
sleep 3

# Start all services
bash "$SCRIPT_DIR/start-all.sh"

echo ""
echo "✅ Restart completed!"
