# Repository Guidelines

## Что это

ActiveMemory — память контекста и хранилище знаний/файлов для AI-агента. Включает веб-дашборд для управления памятью и контекстом.

**Архитектура:**
- **Primary:** PostgreSQL + pgvector + BGE-M3 embedding (native endpoint) + кеширование эмбеддингов в Redis
- **Fallback (страховка):** SQLite — только критичные данные для работы агента при падении PostgreSQL

## Структура пакетов

- `core/` — конфигурация (dataclass-based, .env через python-dotenv)
- `storage/` — БД слой (SQLAlchemy, PostgreSQL + pgvector, SQLite fallback)
- `ingest/` — обработка документов и чанкинг (PDF через PyPDF2, text, code файлы)
- `search/` — гибридный поиск (dense, multi-vector/ColBERT, hybrid modes; BGE-M3 через native endpoint)
- `api/` — MCP сервер с 14 tools (поддержка search_mode: dense/multi-vector/hybrid)
- `web/` — FastAPI дашборд (порт 8788, русскоязычный UI)

**Два entry point:**
- MCP сервер: `python3 -m active_memory_mcp.main`
- Дашборд: `web-dashboard` или `active_memory_mcp.web.server:main`

`run_mcp_server.py` — dev-only helper с sys.path манипуляцией; использовать module run или console scripts.

## Команды

- `python3 -m pip install -e .` — install в editable mode (Hatchling build backend)
- `scripts/setup.sh` — install + --break-system-packages + pgvector extension если psql доступен
- `python3 -m active_memory_mcp.main` — MCP сервер из исходников
- `active-memory-mcp` — installed MCP console script
- `web-dashboard` — FastAPI дашборд (http://localhost:8788)
- `python3 -m pytest` — полный набор тестов
- `ruff check src tests` — lint check (без auto-fix)

**Verification order:** `ruff check src tests` → `python3 -m pytest`

## Переменные окружения (ключевые)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `AM_DB_HOST` | localhost | PostgreSQL хост |
| `AM_DB_PORT` | 5432 | PostgreSQL порт |
| `AM_DB_NAME` | hermes_memory | Имя БД |
| `AM_STORAGE_BACKEND` | postgresql | Форсировать backend (postgresql/sqlite) |
| `AM_ENABLE_SQLITE_FALLBACK` | true | Авто-fallback при падении PostgreSQL |
| `AM_USE_PGVECTOR` | true | Включить pgvector extension |
| `AM_EMBEDDING_ENDPOINT_NATIVE` | http://192.168.1.199:8899/embedding | Native BGE-M3 endpoint (multi-vector) |
| `AM_EMBEDDING_MODEL` | bge-m3-q8_0.gguf | BGE-M3 модель (1024 dims, INT8) |
| `AM_EMBEDDING_DIM` | 1024 | Размерность эмбеддинга |
| `AM_OPTIMAL_CHUNK_TOKENS` | 40 | Оптимальный размер чанка (токены) |
| `AM_USE_LOCAL_EMBEDDING` | false | Использовать локальный embedding (отключено, используется native endpoint) |
| `AM_SEARCH_TOP_K` | 5 | Результатов по умолчанию |
| `AM_HYBRID_ALPHA` | 0.5 | Баланс vector/keyword (0=vector, 1=keyword) |
| `AM_WEB_PORT` | 8788 | Дашборд порт |
| `AM_USE_REDIS` | true | Включить Redis кэш эмбеддингов (LRU cache, 10000 capacity) |
| `AM_REDIS_URL` | redis://localhost:6379/0 | Redis URL |
| `AM_CACHE_TTL` | 3600 | TTL кэша секунды |
| `AM_REQUIRE_AUTH` | true | Включить auth middleware для write-операций |
| `AM_ENCRYPTION_KEY` | LMDGRdWx-ARfhNgSOwxicQk3pB8c6LgKHWNpF52Ymo= | Мастер-ключ для Fernet шифрования |
| `AM_ACCESS_LOG_RETENTION` | 30 | Дней хранения access_log |
| `AM_AUTO_SNAPSHOT` | false | Автоматические снапшоты (pg_dump + Python API) |

## MCP Tools (14 штук)

- `search_memory(query, top_k, filetype, search_mode)` — гибридный поиск (search_mode: dense/multi-vector/hybrid)
- `store_document(file_path, metadata)` — ingest файла
- `list_documents(filetype, limit)` — список документов
- `get_document(document_id)` — детали документа
- `delete_document(document_id)` — удаление с cascade
- `get_stats()` — статистика (документы, чанки, эмбеддинги, токены)
- `update_document(document_id, metadata)` — обновление метаданных
- `bulk_search(queries, search_mode)` — несколько поисков одним вызовом
- `update_document_metadata(document_id, metadata)` — обновление только метаданных
- `search_by_date(query, date_from, date_to, search_mode)` — поиск по диапазону дат
- `rename_document(document_id, new_filename)` — переименование
- `smart_context(query, max_tokens, search_mode)` — контекст с лимитом токенов
- `get_context(max_tokens, pinned, important)` — авто-сбор важного контекста
- `reindex_embeddings(document_id)` — пересчёт эмбеддингов

## Web Dashboard (Phase 4-5 + Security)

Дашборд включает:
- **Тёмная/светлая тема** — CSS переменные, toggle в sidebar, localStorage
- **Drag-n-drop загрузка** — multi-file upload с прогресс-баром через XHR
- **Preview документов** — модальное окно с markdown рендером (marked.js)
- **Графики** — Chart.js: активность, категории, важность, типы файлов
- **Безопасность** — API токены с scopes, auth middleware, rate limiting (slowapi), access log
- **Шифрование** — Fernet для чанков и embeddings (encrypt/decrypt endpoints)
- **Новые API:** `GET /api/documents/{id}/content`, `GET /api/activity`
- **Security API:** `GET/POST/PATCH/DELETE /api/tokens`, `GET/DELETE /api/access-log`
- **Encryption:** `POST /api/documents/{id}/encrypt`, `POST /api/documents/{id}/decrypt`
- **Monitoring:** `/metrics` endpoint для Prometheus (requests, documents, chunks, embeddings)
- **Export/Import:** JSON/CSV экспорт, импорт с валидацией
- **Snapshots:** создание и восстановление снапшотов

## Известные ограничения

**Решено (Phase 1-8):**
1. ~~pgvector Vector тип~~ — интегрирован, HNSW индекс настроен
2. ~~PostgreSQL FTS~~ — tsvector + GIN индекс + auto-update trigger
3. ~~Redis cache~~ — подключён с LRU eviction (10000 capacity)
4. ~~MemoryCache мёртвый код~~ — удалён
5. ~~N-gram fallback~~ — character-level n-gram hash, семантическая близость
6. ~~Нет auth~~ — API токены с scopes, middleware, access log
7. ~~Нет шифрования~~ — Fernet для чанков и embeddings
8. ~~FastEmbed зависимость~~ — удалён, используется native BGE-M3 endpoint
9. ~~Rate limiting~~ — slowapi integration с per-endpoint limits
10. ~~CORS wildcard~~ — ограничен до localhost:8788
11. ~~Log rotation~~ — перенесено из /tmp в logs/, добавлен logrotate
12. ~~Deprecation warnings~~ — datetime.utcnow() → datetime.now(UTC)

**Остаётся:**
- SQLite fallback не поддерживает Vector/FTS (cosine similarity в Python + ilike)
- Remote embedding API (AM_EMBEDDING_ENDPOINT) не подключён — используется native endpoint
- top_k=0 в search возвращает default результаты (должен возвращать [])
- Нет CSRF защиты для dashboard forms
- Нет автоматического cron для auto-snapshot

## Тестирование

Тесты используют pytest (`test_*.py` в `tests/`). Группировать тесты в классы (`TestConfig`, `TestEmbedder`, `TestChunker`). Большинство тестов используют SQLite fallback через `AM_WEB_SQLITE=true` чтобы не требовать живой PostgreSQL.

**Test status:** 84 passed, ruff 0 errors, Prometheus /metrics active.

## Безопасность и конфигурация

- Credentials и локальные настройки в `.env` (никогда не коммитить)
- Не коммитить `.db`, `*.sqlite`, BGE-M3 модели, приватные документы в `data/`, `.venv/`, `.env`
- `--break-system-packages` в `setup.sh` — намеренно для system Python на Debian-based системах
- Docker: `Dockerfile` + `docker-compose.yml` с health checks (postgres + redis + app)
- CI/CD: `.github/workflows/ci.yml` с ruff check + pytest (PostgreSQL + Redis services)
- Backup: `scripts/backup.sh` с pg_dump + compression + Python snapshot API
