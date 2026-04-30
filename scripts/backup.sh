#!/bin/bash
# backup.sh - Automated backup script for ActiveMemory PostgreSQL database
# Usage: ./backup.sh [backup_name]
# Environment variables:
#   AM_DB_HOST, AM_DB_PORT, AM_DB_NAME, AM_DB_USER, AM_DB_PASSWORD
#   AM_BACKUP_DIR (default: ./backups)
#   AM_AUTO_SNAPSHOT (if true, runs automatically)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load .env if exists
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Configuration
DB_HOST="${AM_DB_HOST:-localhost}"
DB_PORT="${AM_DB_PORT:-5432}"
DB_NAME="${AM_DB_NAME:-hermes_memory}"
DB_USER="${AM_DB_USER:-postgres}"
DB_PASS="${AM_DB_PASSWORD:-postgres}"
BACKUP_DIR="${AM_BACKUP_DIR:-$PROJECT_DIR/backups}"
RETENTION_DAYS="${AM_BACKUP_RETENTION_DAYS:-7}"

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Generate backup filename
BACKUP_NAME="${1:-backup_$(date +%Y%m%d_%H%M%S)}"
BACKUP_FILE="$BACKUP_DIR/${BACKUP_NAME}.sql.gz"

echo "Starting backup of database '$DB_NAME' on $DB_HOST:$DB_PORT..."

# Export password for pg_dump
export PGPASSWORD="$DB_PASS"

# Run pg_dump and compress
pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --no-owner --no-acl --clean --if-exists --create \
    | gzip > "$BACKUP_FILE"

# Check if backup was successful
if [ $? -eq 0 ] && [ -s "$BACKUP_FILE" ]; then
    echo "Backup completed successfully: $BACKUP_FILE"
    echo "Size: $(du -h "$BACKUP_FILE" | cut -f1)"
else
    echo "Backup failed!"
    rm -f "$BACKUP_FILE"
    exit 1
fi

# Remove old backups
echo "Cleaning up backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +$RETENTION_DAYS -delete

# Also create a snapshot using the Python API
echo "Creating snapshot via Python API..."
cd "$PROJECT_DIR"
python3 -c "
import sys
sys.path.insert(0, 'src')
from active_memory_mcp.core.snapshot import create_snapshot
result = create_snapshot(label='auto_backup_$(date +%Y%m%d_%H%M%S)')
print(f'Snapshot created: {result}')
" 2>/dev/null || echo "Snapshot creation skipped (database may not be accessible)"

echo "Backup process completed!"
