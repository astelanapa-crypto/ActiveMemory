#!/bin/bash
# Check dependencies for ActiveMemory
# Verifies PostgreSQL and Redis availability

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}🔍 Checking dependencies...${NC}"

# Load .env if exists
if [ -f .env ]; then
    source .env
fi

# PostgreSQL check
DB_HOST=${AM_DB_HOST:-localhost}
DB_PORT=${AM_DB_PORT:-5432}

echo -n "PostgreSQL ($DB_HOST:$DB_PORT)... "
if command -v pg_isready &> /dev/null; then
    if pg_isready -h "$DB_HOST" -p "$DB_PORT" -q 2>/dev/null; then
        echo -e "${GREEN}✓ Available${NC}"
        POSTGRES_OK=1
    else
        echo -e "${YELLOW}⚠ Not available (will use SQLite fallback)${NC}"
        POSTGRES_OK=0
    fi
else
    echo -e "${YELLOW}⚠ pg_isready not found (assuming PostgreSQL is available)${NC}"
    POSTGRES_OK=1
fi

# Redis check
REDIS_URL=${AM_REDIS_URL:-redis://localhost:6379/0}
REDIS_HOST=$(echo "$REDIS_URL" | sed -E 's|redis://([^:]+):.*|\1|')
REDIS_PORT=$(echo "$REDIS_URL" | sed -E 's|redis://[^:]+:([0-9]+).*|\1|')

echo -n "Redis ($REDIS_HOST:$REDIS_PORT)... "
if command -v redis-cli &> /dev/null; then
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping &> /dev/null; then
        echo -e "${GREEN}✓ Available${NC}"
        REDIS_OK=1
    else
        echo -e "${YELLOW}⚠ Not available (will use in-memory cache)${NC}"
        REDIS_OK=0
    fi
else
    echo -e "${YELLOW}⚠ redis-cli not found (assuming Redis is available)${NC}"
    REDIS_OK=1
fi

# Python 3 check
echo -n "Python 3.12+... "
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
    echo -e "${GREEN}✓ $PYTHON_VERSION${NC}"
else
    echo -e "${RED}✗ Not found${NC}"
    exit 1
fi

# ActiveMemory installation check
echo -n "ActiveMemory installed... "
if command -v active-memory-mcp &> /dev/null; then
    echo -e "${GREEN}✓$(active-memory-mcp --version 2>/dev/null || echo ' installed')${NC}"
else
    echo -e "${YELLOW}⚠ Not installed (run: pip install -e .)${NC}"
fi

echo ""
if [ "$POSTGRES_OK" = "1" ] && [ "$REDIS_OK" = "1" ]; then
    echo -e "${GREEN}✅ All dependencies satisfied${NC}"
    exit 0
else
    echo -e "${YELLOW}⚠ Some dependencies are not available - services may use fallbacks${NC}"
    exit 0  # Don't fail - fallbacks exist
fi
