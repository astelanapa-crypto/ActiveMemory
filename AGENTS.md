# Repository Guidelines

## Project Structure & Module Organization

ActiveMemory is a Python 3.12 MCP server packaged from `src/active_memory_mcp`.
Core configuration is in `core/`, persistence in `storage/`, ingestion and
chunking in `ingest/`, search and embeddings in `search/`, MCP/API entry points
in `api/`, and the FastAPI dashboard in `web/`. Dashboard templates live in
`src/active_memory_mcp/web/templates/`. Tests are in `tests/`. The top-level
`run_mcp_server.py` helper starts the MCP server from the source tree. Generated
runtime files such as SQLite fallback databases, local data, caches, and
environment files should not be treated as source.

## Build, Test, and Development Commands

- `python3 -m pip install -e .` installs the package in editable mode.
- `scripts/setup.sh` installs the package and attempts to enable PostgreSQL
  `vector` support when `psql` is available.
- `python3 -m active_memory_mcp.main` runs the MCP server from source.
- `active-memory-mcp` runs the installed MCP console script.
- `web-dashboard` starts the installed FastAPI dashboard.
- `python3 -m pytest` runs the full test suite.
- `ruff check src tests` runs lint checks when Ruff is installed.

## Coding Style & Naming Conventions

Follow Python 3.12 syntax and the Ruff settings in `pyproject.toml`: 100-character
line length and `py312` target. Use 4-space indentation, snake_case for modules
and functions, PascalCase for classes, and explicit imports from
`active_memory_mcp`. Keep production code under `src/active_memory_mcp`; avoid
adding import-path manipulation outside test files.

## Testing Guidelines

Tests use pytest and follow the `test_*.py` naming pattern. Group related tests
with classes such as `TestConfig`, `TestEmbedder`, and `TestChunker`. Add focused
coverage for configuration defaults, chunk boundaries, deterministic embeddings,
search result serialization, API behavior, and web-server imports. Prefer tests
that do not require a live database unless the dependency is isolated and clearly
documented.

## Commit & Pull Request Guidelines

Recent commits use concise imperative summaries, for example `Add comprehensive
test suite...` and `Fix config.py syntax errors...`. Keep commit titles specific
and short. Pull requests should describe the behavior changed, list validation
commands run, mention database or configuration impacts, link related issues,
and include screenshots for dashboard UI changes.

## Security & Configuration Tips

Keep credentials and local settings in `.env`. Do not commit generated databases,
downloaded model caches, private documents in `data/`, or machine-specific
configuration.
