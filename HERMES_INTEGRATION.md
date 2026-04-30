# Hermes Agent Integration

## Overview

**ActiveMemory** — это MCP сервер для управления памятью, контекстом и данными AI-агента.

**Hermes Agent** — AI-агент от NousResearch для мультиплатформенной коммуникации (Telegram, Discord, Slack). Поддерживает MCP протокол в двустороннем режиме.

### Архитектура интеграции

```
Hermes Agent ──── MCP ──── ActiveMemory MCP Server
                      │
                      ├── search_memory (поиск)
                      ├── smart_context (умный контекст)
                      ├── store_document (сохранение)
                      └── 11 других tools
```

ActiveMemory выступает как **MCP сервер памяти** для Hermes, предоставляя:
- Гибридный поиск (вектор + текст)
- Умный контекст с лимитом токенов
- Хранение документов, кода, медиа
- Экспорт/импорт базы знаний

---

## Setup

### Требования

- Hermes Agent установлен (`hermes-agent`)
- ActiveMemory MCP сервер установлен и настроен
- PostgreSQL + pgvector (рекомендуется) или SQLite fallback

### Шаг 1: Настройка ActiveMemory

Создайте `.env` файл:

```bash
# PostgreSQL (рекомендуется)
AM_DB_HOST=localhost
AM_DB_PORT=5432
AM_DB_NAME=hermes_memory
AM_DB_USER=postgres
AM_DB_PASSWORD=postgres

# Embeddings
AM_EMBEDDING_MODEL=BAAI/bge-m3
AM_EMBEDDING_DIM=1024
AM_USE_LOCAL_EMBEDDING=true

# Web Dashboard (опционально)
AM_WEB_PORT=8788
```

Запустите MCP сервер:

```bash
# Через entry point
active-memory-mcp

# Или через Python module
python3 -m active_memory_mcp.main
```

### Шаг 2: Добавление в Hermes config.yaml

Откройте `~/.hermes/config.yaml` и добавьте:

```yaml
mcp_servers:
  active-memory:
    command: "active-memory-mcp"
    args: []
    # Для удаленного сервера:
    # url: "http://localhost:8787/mcp"
    # transport: "http"
```

Или через `uvx` (если установлено):

```yaml
mcp_servers:
  active-memory:
    command: "uvx"
    args: ["active-memory-mcp"]
```

### Шаг 3: Перезагрузите Hermes

```bash
# В чате Hermes
/reload-mcp
```

Проверьте, что сервер подключился:

```bash
# Hermes покажет список доступных tools
/tools
```

---

## Доступные инструменты (14 tools)

### Управление контекстом

| Tool | Описание | Пример |
|---|---|---|
| `search_memory` | Гибридный поиск (вектор + текст) | "Search memory for Python async patterns" |
| `smart_context` | Умный контекст с лимитом токенов | "Get smart context about auth system" |
| `get_context` | Автосбор важного контекста | "Get context with pinned docs" |

### Управление документами

| Tool | Описание | Пример |
|---|---|---|
| `store_document` | Сохранение файла в память | "Store this spec to memory" |
| `list_documents` | Список документов | "List all documents" |
| `get_document` | Получить документ с чанками | "Get document 42" |
| `delete_document` | Удалить документ | "Delete document 42" |
| `update_document` | Обновить метаданные | "Update doc 42 metadata" |
| `rename_document` | Переименовать | "Rename document 42 to new_name.pdf" |

### Импорт / Экспорт

| Tool | Описание | Пример |
|---|---|---|
| `export_memory` | Экспорт в JSON/CSV | "Export memory to JSON" |
| `import_memory` | Импорт из backup | "Import memory from backup.json" |

### Администрирование

| Tool | Описание | Пример |
|---|---|---|
| `get_stats` | Статистика памяти | "Get memory stats" |
| `bulk_search` | Несколько поисков | "Search for ['auth', 'api', 'middleware']" |
| `search_by_date` | Поиск по диапазону дат | "Search from 2026-04-01 to 2026-04-30" |
| `reindex_embeddings` | Пересчёт эмбеддингов | "Reindex embeddings for doc 42" |

---

## Примеры промтов для Hermes

### Контекст сессии разработки

```
Search my memory for the current project architecture
Get smart context about the authentication system we're building
What documents are related to the API implementation?
Store this technical specification to memory
```

### Работа с данными

```
Find all documents related to user management
Export my memory backup for the last week as JSON
List the most important documents about the backend
Import data from old_backup.json (merge mode)
```

### Поиск по коду / документации

```
Search for Python async patterns in my memory
Get context about the middleware implementation we discussed
Find all test files related to user authentication
Store this code snippet as reference
```

### Длинные сессии разработки (удержание контекста)

```
I'm working on the Phase 5 security module.
Get all context about encryption, tokens, and access logs.
Search for examples of Fernet encryption in my docs.
Store the new security specification to memory.
```

---

## Use Cases

### 1. Длинные сессии разработки ПО

**Проблема:** При длинных сессиях (разработка сложного ПО) AI-агент теряет контекст.

**Решение через ActiveMemory:**
- Hermes делает `smart_context` → получает релевантный контекст с лимитом токенов
- Все важные документы закреплены (`pinned=True`) → автоматически попадают в контекст
- История обсужений сохранена → `search_memory` восстанавливает прошлые решения

**Пример:**
```
We've been discussing the database schema for 3 hours.
Get smart context about database models and migrations (max 4000 tokens)
```

### 2. Мультиплатформенный доступ к памяти

**Сценарий:**
- Hermes получает сообщение в Telegram: "Find the API docs"
- Использует `search_memory` через ActiveMemory
- Возвращает результат в Telegram

**Конфигурация:**
```yaml
# Hermes config.yaml
mcp_servers:
  active-memory:
    command: "active-memory-mcp"
```

### 3. Автоматизация документации

```
# Сохранение автогенерируемой документации
Store this API documentation to memory (metadata: {"category": "docs", "importance": 4})

# Поиск по документации
Search for "middleware" in my memory, filter by filetype "markdown"
```

### 4. Backup и миграция

```
# Экспорт всей памяти
Export all memory to JSON format with embeddings

# Импорт в новую систему
Import memory from backup.json (replace mode)
```

---

## Переменные окружения для Hermes

Создайте `.env` файл для ActiveMemory:

```bash
# Database
AM_DB_HOST=localhost
AM_DB_PORT=5432
AM_DB_NAME=hermes_memory
AM_DB_USER=postgres
AM_DB_PASSWORD=postgres

# Embeddings (FastEmbed local)
AM_EMBEDDING_MODEL=BAAI/bge-m3
AM_EMBEDDING_DIM=1024
AM_USE_LOCAL_EMBEDDING=true

# Опционально: Remote API
# AM_EMBEDDING_ENDPOINT=http://192.168.1.199:8899/v1/embeddings

# Web Dashboard
AM_WEB_PORT=8788

# Security (опционально)
AM_REQUIRE_AUTH=false
AM_ENCRYPTION_KEY=your-secret-key-here
```

---

## Troubleshooting

### MCP не загружается в Hermes

**Симптом:** `/reload-mcp` не показывает ActiveMemory

**Решение:**
1. Проверьте, что `active-memory-mcp` в PATH:
   ```bash
   which active-memory-mcp
   ```

2. Проверьте, что MCP сервер запускается вручную:
   ```bash
   active-memory-mcp
   ```

3. Проверьте логи Hermes на ошибки загрузки MCP

### Инструменты не работают

**Симптом:** Hermes показывает tools, но вызовы падают с ошибкой

**Решение:**
1. Проверьте подключение к PostgreSQL:
   ```bash
   psql -h localhost -U postgres -d hermes_memory
   ```

2. Проверьте, что FastEmbed модель скачана:
   ```bash
   ls ~/.cache/huggingface/hub/
   ```

3. Проверьте права доступа к файлам

### Ошибки эмбеддингов

**Симптом:** `search_memory` возвращает пустые результаты

**Решение:**
1. Проверьте, что документы проиндексированы:
   ```bash
   # Через Hermes
   "Reindex embeddings for all documents"
   ```

2. Проверьте размерность эмбеддингов (должна быть 1024 для BAAI/bge-m3)

---

## Связь с другими файлами

| Файл | Связь |
|---|---|
| `IDE_INTEGRATION.md` | Cursor/VS Code интеграция |
| `README.md` | Общая документация проекта |
| `AGENTS.md` | Инструкции для AI-агентов |
| `ROADMAP.md` | План развития (Phase 8.1) |
| `.env.example` | Пример переменных окружения |

---

## Next Steps

После настройки интеграции:

1. **Тестирование:**
   ```
   "Search memory for test query"
   "Get context about ActiveMemory"
   "List all documents"
   ```

2. **Настройка автоматизации:**
   - Закрепите важные документы: `update_document` с `pinned=true`
   - Настройте importance для приоритезации

3. **Мониторинг:**
   - Откройте Dashboard: http://localhost:8788
   - Проверьте статистику через Hermes: "Get memory stats"

---

## Links

- **ActiveMemory:** https://github.com/anomalyco/ActiveMemory
- **Hermes Agent:** https://hermes-agent.ai
- **MCP Protocol:** https://modelcontextprotocol.io
- **NousResearch:** https://nousresearch.com
- **pgvector:** https://github.com/pgvector/pgvector

---

**ActiveMemory + Hermes = Полный контекст для вашего AI-агента! 🚀**
