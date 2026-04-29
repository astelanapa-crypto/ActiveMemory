"""Entry point for ActiveMemory MCP server."""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from active_memory_mcp.core.config import config
from active_memory_mcp.storage.db import init_db
from active_memory_mcp.api.mcp_server import main as mcp_main

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

async def startup():
    """Initialize services."""
    logger.info("Starting ActiveMemory MCP server...")
    logger.info(f"Config: PostgreSQL at {config.db.host}:{config.db.port}/{config.db.database}")
    logger.info(f"Chunk size: {config.chunking.chunk_size}, overlap: {config.chunking.chunk_overlap}")
    logger.info(f"Top K: {config.search.top_k}, Hybrid alpha: {config.search.hybrid_alpha}")
    
    # Initialize database
    init_db()
    logger.info("Database initialized")

async def main():
    """Main entry point."""
    await startup()
    await mcp_main()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)
