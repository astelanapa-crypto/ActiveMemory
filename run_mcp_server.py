#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from active_memory_mcp.api.mcp_server import main

if __name__ == "__main__":
    asyncio.run(main())
