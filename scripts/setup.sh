#!/usr/bin/env bash
set -e

echo "=== ActiveMemory Setup ==="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found"
    exit 1
fi

# Install package in editable mode
echo "Installing ActiveMemory..."
python3 -m pip install --break-system-packages -e .

# Check PostgreSQL
if command -v psql &> /dev/null; then
    echo "PostgreSQL found"
    # Try to create extension (may need sudo)
    sudo -n -u postgres psql -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true
else
    echo "Warning: psql not found, install postgresql-client"
fi

echo "Setup complete!"
echo "Run: python3 -m active_memory_mcp.main"
