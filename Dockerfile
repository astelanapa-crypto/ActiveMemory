FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY pyproject.toml .

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Copy source code
COPY src/ ./src/
COPY scripts/ ./scripts/

# Create necessary directories
RUN mkdir -p /app/data /app/logs

# Set environment variables
ENV AM_DB_HOST=postgresql
ENV AM_DB_PORT=5432
ENV AM_DB_NAME=hermes_memory
ENV AM_REDIS_URL=redis://redis:6379/0
ENV AM_WEB_PORT=8788
ENV PYTHONUNBUFFERED=1

# Expose ports
EXPOSE 8788

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8788/health || exit 1

# Run web dashboard by default
CMD ["web-dashboard"]
