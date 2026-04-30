# ActiveMemory IDE Integration

## Cursor IDE

ActiveMemory работает с Cursor через MCP протокол.

### Подключение

1. Откройте Cursor Settings → **Features** → **MCP Servers**
2. Нажмите **+ Add new MCP server**
3. Заполните поля:
   - **Name**: `active-memory`
   - **Type**: `command`
   - **Command**: 
     ```bash
     active-memory-mcp
     ```
     Или через npx:
     ```bash
     npx @modelcontextprotocol/server-python path/to/ActiveMemory/src/active_memory_mcp
     ```

### Альтернативный способ (mcp.json)

Создайте файл `.cursor/mcp.json` в корне проекта:

```json
{
  "mcpServers": {
    "active-memory": {
      "command": "active-memory-mcp",
      "args": []
    }
  }
}
```

### Доступные команды в Cursor

После подключения MCP сервера, вы можете использовать:

- **Search** — поиск по памяти через чат
- **Context** — автоматический контекст для AI
- **Store** — сохранение файлов в память

### Примеры использования

В чате Cursor:
```
@active-memory Search for Python decorator patterns
@active-memory Get context about FastAPI middleware
```

---

## VS Code

### Подключение

1. Установите расширение **MCP Client** (если доступно)
2. Или создайте файл `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "active-memory": {
      "command": "active-memory-mcp",
      "args": []
    }
  }
}
```

### Настройка через Settings

Добавьте в `settings.json`:

```json
{
  "mcp.servers": {
    "active-memory": {
      "command": "active-memory-mcp"
    }
  }
}
```

### Использование

- Откройте Command Palette (`Ctrl+Shift+P`)
- Введите: `MCP: List servers`
- Выберите `active-memory`
- Доступные команды появятся в контекстном меню

---

## Доступные MCP Tools

После подключения доступны 14 tools:

1. **search_memory** — гибридный поиск (вектор + текст)
2. **store_document** — сохранение документа
3. **list_documents** — список документов
4. **get_document** — получить документ
5. **delete_document** — удалить документ
6. **get_stats** — статистика
7. **export_memory** — экспорт в JSON/CSV
8. **import_memory** — импорт из JSON/CSV
9. **smart_context** — умный контекст с лимитом токенов
10. **get_context** — автосбор важного контекста
11. **update_document** — обновление метаданных
12. **bulk_search** — несколько поисков одним вызовом
13. **search_by_date** — поиск по диапазону дат
14. **rename_document** — переименование
15. **reindex_embeddings** — пересчёт эмбеддингов

---

## Telegram Bot

### Настройка

1. Получите токен у @BotFather
2. Добавьте в `.env`:
   ```
   TG_BOT_TOKEN=your_token_here
   TG_ADMIN_IDS=your_telegram_id
   ```

3. Запустите бота:
   ```bash
   tg-bot
   ```

### Команды бота

- `/search <query>` — поиск по памяти
- `/context <query>` — получить контекст
- `/list [limit]` — список документов
- `/add` — добавить документ (reply с файлом)
- `/stats` — статистика
- `/help` — справка

---

## Примеры .env

```bash
# PostgreSQL
AM_DB_HOST=localhost
AM_DB_PORT=5432
AM_DB_NAME=hermes_memory
AM_DB_USER=postgres
AM_DB_PASSWORD=postgres

# Embeddings
AM_EMBEDDING_MODEL=BAAI/bge-m3
AM_EMBEDDING_DIM=1024
AM_USE_LOCAL_EMBEDDING=true

# Web Dashboard
AM_WEB_PORT=8788

# Telegram Bot
TG_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TG_ADMIN_IDS=12345678

# Security
AM_REQUIRE_AUTH=false
AM_ENCRYPTION_KEY=your-secret-key-here
```

---

## Полезные ссылки

- MCP Protocol: https://modelcontextprotocol.io
- Cursor Docs: https://docs.cursor.com
- VS Code MCP: https://code.visualstudio.com/docs
- **Hermes Agent:** See [HERMES_INTEGRATION.md](HERMES_INTEGRATION.md) for using ActiveMemory as context memory for Hermes Agent
