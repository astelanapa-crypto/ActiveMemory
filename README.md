# ActiveMemory MCP Server

High-performance knowledge management with PostgreSQL/pgvector, chunking, hybrid search, and web dashboard.

## Features
- Hybrid Search: Vector (pgvector) + Full-text (PostgreSQL FTS)
- Smart Chunking: Automatic text/code splitting
- Local Embeddings: BGE-M3 via FastEmbed
- Web Dashboard: FastAPI interface
- MCP Integration: Claude Desktop/Code
- Token Efficient: 60-70% LLM token savings

## Quick Start

### 1. Install


### 2. PostgreSQL + pgvector


### 3. Configure (.env)


### 4. Run MCP


### 5. Run Dashboard

```bash
web-dashboard
# or
python3 -m active_memory_mcp.web.server
```

### Web templates (canonical path)

Dashboard HTML templates are loaded only from:

`src/active_memory_mcp/web/templates/`

The legacy top-level `web/templates/` directory is no longer used by the FastAPI server.


## MCP Tools
- search_memory(query, top_k)
- store_document(file_path, metadata)
- list_documents()
- get_document(id)
- delete_document(id)
- get_stats()

## Adding to Claude Desktop


## IDE Integration

See [IDE_INTEGRATION.md](IDE_INTEGRATION.md) for:
- Cursor IDE setup
- VS Code extension config
- Telegram bot commands

See [HERMES_INTEGRATION.md](HERMES_INTEGRATION.md) for:
- Hermes Agent as MCP client
- Using ActiveMemory as context memory for long sessions
- Tool examples for development workflows
