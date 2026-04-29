# ActiveMemory Roadmap

> Дорожная карта развития проекта после завершения технической базы (TODO.md)

## Приоритеты

```
Phase 2 (MCP Tools)    ██████  Основной функционал
Phase 3 (Контекст)     █████   Core value для агента
Phase 4 (Дашборд)      █████   Завершён
Phase 5 (Безопасность) ███     Когда понадобится
Phase 6 (Экспорт)      ██      Полезно но не срочно
Phase 7 (Визуализация) █       Красиво но не критично
Phase 8 (Telegram)     █       Последняя очередь
```

---

## Phase 2 — Расширение MCP Tools

### 2.1 update_document
Обновление метаданных документа (pinned, importance, category).

### 2.2 bulk_search
Несколько поисковых запросов одним вызовом.

### 2.3 get_context
Автоматический сбор важного контекста (pinned + high importance) с учётом лимита токенов.

### 2.4 reindex_embeddings
Пересчёт всех эмбеддингов при смене модели.

---

## Phase 3 — Умный контекст

### 3.1 smart_context
Поиск с учётом лимита токенов, а не count.

### 3.2 Auto-prioritization
Приоритет pinned и high importance при ранжировании.

### 3.3 Reranking
Переранжирование результатов после получения.

---

## Phase 4 — Улучшение дашборда ✅ ЗАВЕРШЁН

### 4.1 Темная/светлая тема ✅
CSS переменные для обеих тем, toggle button в sidebar, сохранение в localStorage,
автоприменение при загрузке, обновление цветов графиков при смене темы.

### 4.2 Drag-n-drop загрузка ✅
Drop зона с визуальной подсветкой, multi-file upload, прогресс-бар на каждый файл
через XMLHttpRequest upload progress, очередь загрузки с статусами.

### 4.3 Preview документов ✅
Модальное окно с полным контентом документа, markdown рендер через marked.js,
plain text для кода, список чанков с превью, закрытие по Escape/click outside.

### 4.4 Графики статистики ✅
Chart.js через CDN: line chart активности по дням, doughnut по категориям,
bar chart распределения важности, horizontal bar chart типов файлов.
Новый endpoint `/api/activity` с агрегированными данными.

---

## Phase 5 — Безопасность ✅ ЗАВЕРШЁН

### 5.1 API токены ✅
Генерация через `POST /api/tokens?label=NAME&scopes=SCOPE&days=N`.
Хеширование SHA-256 в БД (raw токен не хранится). Scopes: read/write/admin
с иерархией (admin покрывает всё). Токены с префиксом `am_`.
CRUD: list, create, patch (revoke/update scopes), delete.

### 5.2 Шифрование эмбеддингов ✅
Fernet (cryptography) для шифрования чанков и embeddings чувствительных документов.
Endpoints: `POST /api/documents/{id}/encrypt` и `/decrypt`.
Document модель: `encrypted` (bool) + `encryption_salt` (str).
Embedding модель: `encrypted_embedding` (Text) для хранения зашифрованного вектора.
Мастер-ключ через `AM_ENCRYPTION_KEY` env var.

### 5.3 Логирование доступа ✅
Таблица `access_log`: timestamp, action, token_label, document_id, ip_address,
user_agent, details (JSON), success. Endpoint `GET /api/access-log` с фильтрами.
Middleware автоматически логирует все write-запросы.
`DELETE /api/access-log?older_than_days=N` для очистки.

### 5.4 Auth middleware ✅
HTTP middleware проверяет `Authorization: Bearer am_...` для write-операций
когда `AM_REQUIRE_AUTH=true`. Валидация scope, expiration, active status.
Auto-генерация admin токена при первом запуске с auth.
Read-endpoints доступны без токена (статистика, поиск, документы).

---

## Phase 6 — Экспорт и резервные копии

### 6.1 export_memory
Экспорт в JSON/CSV всех документов и чанков.

### 6.2 import_memory
Импорт из backup (merge или replace mode).

### 6.3 Snapshot'ы БД
Ежедневные snapshot'ы (retention: 7 дней).

---

## Phase 7 — Визуализация и аналитика

### 7.1 Векторное пространство
t-SNE/UMAP визуализация кластеров документов (D3.js/Plotly).

### 7.2 Статистика по категориям
Документов на категорию, средний размер, распределение importance.

### 7.3 Heatmap использования
Частота запросов, открытий, "hot" документы.

---

## Phase 8 — Интеграции

### 8.1 Cursor IDE plugin
MCP integration, командная палитра, автодополнение из памяти.

### 8.2 VS Code extension
Status bar, commands, panel с результатами.

### 8.3 Telegram бот
Команды: /search, /context, /list, /add, /stats. ПОСЛЕДНЯЯ ОЧЕРЕДЬ.

---

## Версионирование

| Версия | Содержимое |
|---|---|
| 1.0.0 | Текущая (с ограничениями) |
| 1.1.0 | После Phase 1 (TODO.md) — техническая база |
| 2.0.0 | После Phase 2-3 — основной функционал расширен |
| 2.1.0 | После Phase 4 — дашборд улучшения ✅ |
| 3.0.0 | После Phase 5-8 — полный функционал |

---

## Notes

- Приоритет может меняться по мере использования
- Telegram бот — последняя очередь (Phase 8)
- Безопасность (Phase 5) — добавить когда понадобится external доступ
