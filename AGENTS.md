# Repository Guidelines

## Что это

ActiveMemory — память контекста и хранилище знаний/файлов для AI-агента. Включает веб-дашборд для управления памятью и контекстом.

**Архитектура:**
- **Primary:** PostgreSQL + pgvector + embedding LLM + кеширование эмбеддингов в Redis
- **Fallback (страховка):** SQLite — только критичные данные для работы агента при падении PostgreSQL

## Структура пакетов

- `core/` — конфигурация (dataclass-based, .env через python-dotenv)
- `storage/` — БД слой (SQLAlchemy, PostgreSQL + pgvector, SQLite fallback)
- `ingest/` — обработка документов и чанкинг (PDF через PyPDF2, text, code файлы)
- `search/` — гибридный поиск (vector similarity + keyword matching, embeddings через FastEmbed)
- `api/` — MCP сервер с 6 tools
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
| `AM_EMBEDDING_ENDPOINT` | http://192.168.1.199:8899/v1/embeddings | Remote embedding API |
| `AM_USE_LOCAL_EMBEDDING` | true | Использовать FastEmbed вместо remote |
| `AM_EMBEDDING_DIM` | 1024 | Размерность эмбеддинга |
| `AM_SEARCH_TOP_K` | 5 | Результатов по умолчанию |
| `AM_HYBRID_ALPHA` | 0.5 | Баланс vector/keyword (0=vector, 1=keyword) |
| `AM_WEB_PORT` | 8788 | Дашборд порт |
| `AM_USE_REDIS` | true | Включить Redis кэш эмбеддингов |
| `AM_REDIS_URL` | redis://localhost:6379/0 | Redis URL |
| `AM_CACHE_TTL` | 3600 | TTL кэша секунды |

## MCP Tools (6 штук)

- `search_memory(query, top_k, filetype)` — гибридный поиск
- `store_document(file_path, metadata)` — ingest файла
- `list_documents(filetype, limit)` — список документов
- `get_document(document_id)` — детали документа
- `delete_document(document_id)` — удаление с cascade
- `get_stats()` — статистика (документы, чанки, эмбеддинги, токены)

## Известные ограничения текущей версии

Текущий код имеет архитектурные проблемы. **TODO.md** (в корне проекта) содержит полный план исправлений.

**Основные проблемы:**
1. Эмбеддинги хранятся как JSON text в БД, не как pgvector Vector тип → векторный поиск делается brute-force в Python (O(n))
2. Keyword search использует `ilike('%token%')` вместо PostgreSQL FTS → нет морфологии, нет ранжирования
3. CacheConfig существует но не используется → Redis кэш эмбеддингов не подключён
4. MemoryCache SQLAlchemy модель существует но никем не используется → мёртвый код
5. Remote embedding API (AM_EMBEDDING_ENDPOINT) настроен но не подключён → всегда используется FastEmbed или fallback
6. Fallback эмбеддинги на простом hash() → семантически бессмысленные вектора (cat и kitten ортогональны)

**TODO.md покрывает:**
- Интеграцию pgvector Vector типа
- HNSW индекс для быстрого векторного поиска
- PostgreSQL FTS для keyword search
- Redis кэш эмбеддингов с in-memory fallback
- Подключение remote embedding API
- N-gram fallback с семантической близостью
- Обновление тестов
- Graceful degradation для SQLite fallback

## Тестирование

Тесты используют pytest (`test_*.py` в `tests/`). Группировать тесты в классы (`TestConfig`, `TestEmbedder`, `TestChunker`). Большинство тестов используют SQLite fallback через `AM_WEB_SQLITE=true` чтобы не требовать живой PostgreSQL.

## Безопасность и конфигурация

- Credentials и локальные настройки в `.env` (никогда не коммитить)
- Не коммитить `.db`, `*.sqlite`, FastEmbed модель кэши, приватные документы в `data/`, `.venv/`, `.env`
- `--break-system-packages` в `setup.sh` — намеренно для system Python на Debian-based системах
