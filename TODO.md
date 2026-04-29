# TODO — ActiveMemory Refactoring Plan

> **CONTEXT RESTORATION FOR AI AGENTS:** If you're reading this file without prior conversation context, this document outlines a refactoring plan for ActiveMemory — a Python 3.12 MCP server for knowledge management. The project uses PostgreSQL/pgvector (with SQLite fallback), FastEmbed for embeddings, and has a FastAPI dashboard. The current codebase has several architectural gaps: embeddings stored as JSON text instead of native vectors (no real pgvector usage), keyword search via `ilike` instead of PostgreSQL FTS, dead config/code (CacheConfig, MemoryCache, unused deps), no remote embedding API despite being configured, and a hash-based fallback that produces semantically meaningless vectors. This plan fixes all of these issues.

---

## 1. `pyproject.toml` — Зависимости

**FILE:** `/home/silentstorm/Documents/Projects/ActiveMemory/pyproject.toml`
**CURRENT STATE:** 42 lines. Hatchling build backend. Dependencies listed at lines 13-31. Ruff config at lines 40-42.
**WHY CHANGE:** Add pgvector for native vector support, remove dead deps that inflate install size and create false expectations.

### Что изменить

- [ ] 1.1 Добавить `pgvector>=0.2.0` в dependencies (после `numpy`)
  - **Зачем:** Python-биндинг к pgvector для SQLAlchemy. Даёт тип `Vector` и методы `cosine_distance()`, `l2_distance()`.
  - **Где вставить:** В список `dependencies` в `pyproject.toml`, строка ~30

- [ ] 1.2 Добавить `redis>=5.0.0` в dependencies
  - **Зачем:** Redis-клиент для кэширования эмбеддингов. Primary кэш перед БД. Fallback на in-memory dict если Redis недоступен.
  - **Где вставить:** В список `dependencies` в `pyproject.toml`

- [ ] 1.3 Удалить `alembic>=1.13.1`
  - **Зачем:** Миграций нет. Проект использует `Base.metadata.create_all()`. Если появятся миграции — вернём.
  - **Строка для удаления:** ~18

- [ ] 1.4 Удалить `python-docx>=1.1.0`
  - **Зачем:** Нигде не импортируется. `_extract_pdf`, `_extract_text`, `_extract_markdown` — DOCX не обрабатывается.
  - **Строка для удаления:** ~28

- [ ] 1.5 Удалить `beautifulsoup4>=4.12.2`
  - **Зачем:** Нигде не импортируется. Markdown extraction просто делегирует `_extract_text`.
  - **Строка для удаления:** ~29

- [ ] 1.6 Удалить `markdown>=3.5.2`
  - **Зачем:** Нигде не импортируется.同上.
  - **Строка для удаления:** ~30

> **AI NOTE:** Не удаляй `httpx` — он понадобится для Remote Embedding API (уже в deps). Не удаляй `jinja2` — может использоваться web-сервером. Проверь `web/server.py` — там используется `DASHBOARD_HTML` как inline constant, но `jinja2` может быть нужен позже.

---

## 2. `core/config.py` — CacheConfig: переделать, не удалять

**FILE:** `/home/silentstorm/Documents/Projects/ActiveMemory/src/active_memory_mcp/core/config.py`
**CURRENT STATE:** 100 lines. Dataclass-based config. CacheConfig at lines 64-68, used at line 87.
**WHY CHANGE:** CacheConfig нужен для Redis кэша эмбеддингов. Но сейчас он dead code — нигде не используется. Нужно добавить `use_redis` флаг и сделать его осмысленным.

### Что изменить

- [ ] 2.1 В `CacheConfig` добавить поле:
  ```python
  use_redis: bool = os.getenv("AM_USE_REDIS", "true").lower() == "true"
  ```
  - **Зачем:** Флаг включения/выключения Redis кэша. Позволяет graceful fallback если Redis не установлен.
  - **AI NOTE:** Не удаляй `redis_url` и `ttl_seconds` — они нужны для подключения Redis.

- [ ] 2.2 Удалить `AM_REDIS_URL`, `AM_CACHE_TTL` из docstring в шапке файла
  - **Зачем:** Очистить документацию, добавить `AM_USE_REDIS`

- [ ] 2.3 Обновить docstring — добавить `AM_USE_REDIS` в список переменных (строка ~10)

> **AI NOTE:** НЕ удаляй CacheConfig целиком. Он нужен для Redis embedding cache. MemoryCache (SQLAlchemy модель) будет удалена — кэш теперь в Redis, а не в БД.

---

## 3. `storage/db.py` — Самая большая правка

**FILE:** `/home/silentstorm/Documents/Projects/ActiveMemory/src/active_memory_mcp/storage/db.py`
**CURRENT STATE:** 197 lines. 4 SQLAlchemy models (Document, Chunk, Embedding, MemoryCache). Engine init with PostgreSQL→SQLite fallback. Serialize/deserialize helpers for embeddings.
**WHY CHANGE:** Эмбеддинги сейчас хранятся как JSON text — векторный поиск делает brute-force в Python. Нужно использовать pgvector Vector-тип для нативного поиска через БД-индексы.

### Что изменить

- [ ] 3.1 Добавить импорт (после строки 8):
  ```python
  from sqlalchemy import text
  ```
  - **Зачем:** Для DDL-запросов (ALTER TABLE, CREATE INDEX, CREATE TRIGGER)

- [ ] 3.2 Добавить условный импорт pgvector Vector:
  ```python
  try:
      from pgvector.sqlalchemy import Vector
  except ImportError:
      Vector = None
  ```
  - **Зачем:** Если pgvector не установлен (SQLite-only режим), не ломать импорт.
  - **AI NOTE:** Vector может быть None при SQLite. Все места где используется Vector должны проверять `if Vector is not None`.

- [ ] 3.3 Изменить `Embedding.embedding` (строка 73):
  **СЕЙЧАС:** `embedding = Column(Text, nullable=True)`
  **СТАНЕТ:**
  ```python
  embedding = Column(Vector(config.embedding.dimensions), nullable=True) if Vector else Column(Text, nullable=True)
  ```
  - **Зачем:** PostgreSQL → нативный Vector тип (1024 float32). SQLite → fallback на Text.
  - **AI NOTE:** `config.embedding.dimensions` = 1024 по умолчанию. Vector тип требует pgvector PostgreSQL extension, который уже включается в `init_db()` строка 170.

- [ ] 3.4 Добавить HNSW индекс в `__table_args__` Embedding (после строки 78):
  ```python
  __table_args__ = (
      Index("ix_embeddings_hnsw", "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"}),
  )
  ```
  - **Зачем:** Без индекса pgvector делает seq scan. HNSW — графовый индекс для ANN поиска, O(log n).
  - **AI NOTE:** Этот индекс создаётся только для PostgreSQL. Для SQLite он игнорируется (postgresql_* параметры просто не применяются).
  - **ВАЖНО:** HNSW индекс не создаётся через `create_all()`. Нужно явно создать через DDL в `init_db()`.

- [ ] 3.5 Удалить `MemoryCache` класс (строки 80-93)
  - **Зачем:** Мёртвая модель. Никто не пишет/читает из `memory_cache`. Кэш теперь в Redis.
  - **AI NOTE:** При удалении нужно также обновить тесты (section 7).

- [ ] 3.6 Удалить `serialize_embedding` и `deserialize_embedding` (строки 116-133)
  - **Зачем:** Vector тип SQLAlchemy принимает numpy.array/list напрямую. Сериализация больше не нужна.
  - **AI NOTE:** Эти функции импортируются в `ingest/processor.py` (строка 10) и `search/searcher.py` (строка 6). Нужно обновить оба файла.

- [ ] 3.7 В `init_db()` после `Base.metadata.create_all()` (строка 164) добавить DDL для PostgreSQL:
  ```python
  if _backend == "postgresql":
      try:
          with _engine.connect() as conn:
              # FTS: добавить tsvector колонку и GIN индекс
              conn.execute(text("""
                  ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector;
                  CREATE INDEX IF NOT EXISTS ix_chunks_fts ON chunks USING GIN(search_vector);
                  CREATE OR REPLACE FUNCTION chunks_tsvector_trigger() RETURNS trigger AS $$
                  BEGIN
                      NEW.search_vector := to_tsvector('russian', NEW.content);
                      RETURN NEW;
                  END;
                  $$ LANGUAGE plpgsql;
                  CREATE TRIGGER chunks_tsvector_refresh
                      BEFORE INSERT OR UPDATE ON chunks
                      FOR EACH ROW EXECUTE FUNCTION chunks_tsvector_trigger();
              """))
              conn.commit()
              logger.info("FTS search vector and trigger configured")
      except Exception as e:
          logger.warning(f"Could not configure FTS: {e}")

          # HNSW индекс для pgvector
          if config.db.use_pgvector and Vector is not None:
              try:
                  with _engine.connect() as conn:
                      conn.execute(text("""
                          CREATE INDEX IF NOT EXISTS ix_embeddings_hnsw ON embeddings
                          USING hnsw (embedding vector_cosine_ops)
                          WITH (m = 16, ef_construction = 64);
                      """))
                      conn.commit()
                      logger.info("HNSW vector index created")
              except Exception as e:
                  logger.warning(f"Could not create HNSW index: {e}")
  ```
  - **Зачем:**
    - `search_vector tsvector` — колонка для PostgreSQL FTS
    - GIN индекс — быстрый полнотекстовый поиск
    - Триггер — автоматическое обновление `search_vector` при INSERT/UPDATE `content`
    - HNSW индекс — быстрый векторный поиск (не создаётся через `create_all`)
  - **AI NOTE:** Триггер использует `'russian'` конфиг. Если документы на английском — можно добавить `'english'` или `'simple'`. Но текущий проект русскоязычный.
  - **AI NOTE:** Триггер срабатывает автоматически — не нужно обновлять `search_vector` вручную в `ingest/processor.py`.

- [ ] 3.8 Обновить docstring модуля (строка 1) — добавить описание pgvector и FTS

> **AI NOTE CRITICAL:** `Embedding.chunk_id` — это primary key (line 72). После удаления serialize_embedding, при сохранении нужно передавать list[float] напрямую: `Embedding(chunk_id=..., embedding=vector_list, ...)`. SQLAlchemy/pgvector сама сконвертирует.

---

## 4. `ingest/processor.py` — Убрать сериализацию

**FILE:** `/home/silentstorm/Documents/Projects/ActiveMemory/src/active_memory_mcp/ingest/processor.py`
**CURRENT STATE:** 202 lines. DocumentProcessor с process_file, ingest_document, extract methods.
**WHY CHANGE:** Сейчас сериализует эмбеддинги в JSON. С Vector-типом это не нужно. FTS триггер обновит search_vector автоматически.

### Что изменить

- [ ] 4.1 Удалить строку 10:
  ```python
  from ..storage.db import serialize_embedding
  ```
  **Станет:**
  ```python
  from ..storage.db import Document, Chunk, Embedding
  ```
  - **Зачем:** serialize_embedding удалена из db.py

- [ ] 4.2 Изменить строку 146 — убрать serialize_embedding:
  **СЕЙЧАС:** `embedding=serialize_embedding(embedding),`
  **СТАНЕТ:** `embedding=embedding,`
  - **Зачем:** Vector-тип принимает list[float] напрямую
  - **AI NOTE:** `embedding` здесь — это `self.embedder.embed(chunk.content)` который возвращает `list[float]` или `None`. Добавить check: `if embedding is None: continue`

- [ ] 4.3 Удалить строки 141-152 заменить на:
  ```python
  # Generate embedding
  embedding = self.embedder.embed(chunk.content)
  if embedding is None:
      logger.warning(f"Failed to embed chunk {chunk.id} (empty content)")
      continue
  emb = Embedding(
      chunk_id=chunk.id,
      embedding=embedding,
      model=self.embedder.model_name,
      dimensions=len(embedding),
  )
  session.add(emb)
  ```
  - **Зачем:** Убрана try/except обёртка вокруг embed — embedder сам логирует ошибки. Добавлен check на None.
  - **AI NOTE:** search_vector обновится автоматически через триггер в БД (см. пункт 3.7). Не нужно вручную вызывать `to_tsvector`.

---

## 5. `search/embedder.py` — Remote API + Redis cache + N-gram fallback

**FILE:** `/home/silentstorm/Documents/Projects/ActiveMemory/src/active_memory_mcp/search/embedder.py`
**CURRENT STATE:** 85 lines. Embedder класс с Local FastEmbed + hash-based fallback. config.embedding.endpoint сконфигурирован но НЕ используется.
**WHY CHANGE:** Добавить 3-уровневый стек: Redis cache → Remote API → Local FastEmbed → N-gram fallback. Сейчас endpoint — dead config, fallback — семантически бесполезный.

### Что изменить

- [ ] 5.1 Добавить импорты (после строки 4):
  ```python
  import hashlib
  import json
  import httpx
  ```
  - **Зачем:** `hashlib` для cache key, `httpx` для Remote API (уже в deps), `json` для Redis сериализации
  - **AI NOTE:** httpx уже в pyproject.toml строка 15.

- [ ] 5.2 Добавить `_init_redis` метод:
  ```python
  def _init_redis(self):
      """Initialize Redis connection for embedding cache."""
      try:
          import redis
          if config.cache.use_redis:
              self._redis = redis.from_url(config.cache.redis_url, decode_responses=True)
              self._redis.ping()
              logger.info("Redis embedding cache initialized")
              return True
      except Exception as e:
          logger.warning(f"Redis not available, using in-memory cache: {e}")
      self._redis = None
      self._memory_cache = {}  # Fallback in-memory cache
      return False
  ```
  - **Зачем:** Lazy-init Redis с ping-проверкой. Fallback на dict если Redis недоступен.
  - **AI NOTE:** Вызывать в `__init__` после `_init_local_model`.

- [ ] 5.3 Добавить `_cache_key` метод:
  ```python
  def _cache_key(self, text: str) -> str:
      """Generate cache key from text."""
      return "emb:" + hashlib.md5(text.encode()).hexdigest()
  ```
  - **Зачем:** MD5 хеш → стабильный короткий ключ для Redis. Одинаковый текст → одинаковый ключ.

- [ ] 5.4 Добавить `_get_cached` и `_set_cache` методы:
  ```python
  def _get_cached(self, text: str):
      """Get cached embedding from Redis or in-memory cache."""
      key = self._cache_key(text)
      if self._redis:
          data = self._redis.get(key)
          if data:
              return json.loads(data)
      else:
          return self._memory_cache.get(key)
      return None

  def _set_cache(self, text: str, embedding: list):
      """Store embedding in cache."""
      key = self._cache_key(text)
      data = json.dumps(embedding)
      if self._redis:
          self._redis.setex(key, config.cache.ttl_seconds, data)
      else:
          # In-memory: limit to 10000 entries (LRU-like)
          if len(self._memory_cache) > 10000:
              oldest = next(iter(self._memory_cache))
              del self._memory_cache[oldest]
          self._memory_cache[key] = data
  ```
  - **Зачем:** Redis SETEX — атомарный set + TTL. In-memory — простой dict с лимитом.
  - **AI NOTE:** Для in-memory это не настоящий LRU (удаляет первый ключ), но достаточно для fallback.

- [ ] 5.5 Добавить `_remote_embedding` метод:
  ```python
  def _remote_embedding(self, text: str):
      """Generate embedding via external API (OpenAI-compatible format)."""
      if not config.embedding.endpoint:
          return None
      response = httpx.post(
          config.embedding.endpoint,
          json={"input": text, "model": config.embedding.model},
          timeout=30.0,
      )
      response.raise_for_status()
      data = response.json()
      return data["data"][0]["embedding"]
  ```
  - **Зачем:** Вызов внешнего embedding API. Формат совместим с OpenAI embeddings API.
  - **AI NOTE:** endpoint default = `http://192.168.1.199:8899/v1/embeddings`. Это может быть Ollama, vLLM, TGI.

- [ ] 5.6 Добавить `_remote_embed_batch` метод:
  ```python
  def _remote_embed_batch(self, texts: list):
      """Batch embedding via remote API (individual calls)."""
      return [self._remote_embedding(t) for t in texts]
  ```
  - **Зачем:** Batch для Remote API. Можно улучшить до одного HTTP запроса если API поддерживает batch.

- [ ] 5.7 Переписать `__init__`:
  ```python
  def __init__(self):
      self.model_name = config.embedding.model
      self.dimensions = config.embedding.dimensions
      self._local_model = None
      self._redis = None
      self._memory_cache = {}

      if config.embedding.use_local:
          self._init_local_model()
      self._init_redis()
  ```

- [ ] 5.8 Переписать `embed()` — новый порядок приоритетов:
  ```python
  def embed(self, text: str):
      """Generate embedding for a single text."""
      if not text or not text.strip():
          return None

      # 1. Check cache
      cached = self._get_cached(text)
      if cached:
          return cached

      # 2. Remote API
      try:
          embedding = self._remote_embedding(text)
          if embedding:
              self._set_cache(text, embedding)
              return embedding
      except Exception as e:
          logger.warning(f"Remote embedding failed: {e}")

      # 3. Local FastEmbed
      if self._local_model:
          try:
              embeddings = list(self._local_model.embed([text]))
              if embeddings:
                  embedding = embeddings[0].tolist()
                  self._set_cache(text, embedding)
                  return embedding
          except Exception as e:
              logger.warning(f"Local embedding failed: {e}")

      # 4. N-gram hash fallback
      embedding = self._fallback_embedding(text)
      self._set_cache(text, embedding)
      return embedding
  ```

- [ ] 5.9 Переписать `embed_batch()` — тот же порядок:
  ```python
  def embed_batch(self, texts: list):
      """Generate embeddings for multiple texts."""
      results = []
      for text in texts:
          results.append(self.embed(text))
      return results
  ```
  - **Зачем:** Делегирует `embed()` — который уже проверяет cache → remote → local → fallback.
  - **AI NOTE:** Можно оптимизировать batch для Remote API (один запрос вместо N), но это позже.

- [ ] 5.10 Переписать `_fallback_embedding` — N-gram hashing:
  ```python
  def _fallback_embedding(self, text: str) -> list:
      """Deterministic N-gram character hashing fallback."""
      ngrams = self._char_ngrams(text.lower(), n=4)
      vector = np.zeros(self.dimensions, dtype=np.float32)
      for ngram in ngrams:
          idx = abs(hash(ngram)) % self.dimensions
          vector[idx] += 1.0
      norm = np.linalg.norm(vector)
      if norm > 0:
          vector = vector / norm
      else:
          # Edge case: empty or very short text
          vector[0] = 1.0
      return vector.tolist()
  ```
  - **Зачем:** N-gram hashing даёт семантическую близость: "catch" и "cat" имеют общий n-gram "cat" → их вектора ближе.
  - **AI NOTE:** `abs(hash())` — hash() может быть отрицательным. `n=4` — хороший баланс (3-5 символов).

- [ ] 5.11 Добавить `_char_ngrams` хелпер:
  ```python
  def _char_ngrams(self, text: str, n: int = 4) -> list:
      """Extract character n-grams from text."""
      if len(text) < n:
          return [text] if text else []
      return [text[i:i+n] for i in range(len(text) - n + 1)]
  ```

> **AI NOTE CRITICAL:** Новый порядок: Cache → Remote → Local → N-gram. Это значит что `_fallback_embedding` теперь ВСЕГДА кэшируется после вычисления. Второй вызов с тем же текстом вернёт из кэша, а не вычислит заново.

---

## 6. `search/searcher.py` — pgvector query + FTS query

**FILE:** `/home/silentstorm/Documents/Projects/ActiveMemory/src/active_memory_mcp/search/searcher.py`
**CURRENT STATE:** 152 lines. HybridSearcher с brute-force vector search (загружает ВСЕ эмбеддинги в Python) и ilike keyword search.
**WHY CHANGE:** Vector search теперь через pgvector HNSW индекс в БД (O(log n) вместо O(n)). Keyword search через PostgreSQL FTS вместо ilike.

### Что изменить

- [ ] 6.1 Изменить импорты (строка 6):
  **СЕЙЧАС:** `from ..storage.db import Chunk, Document, Embedding, deserialize_embedding`
  **СТАНЕТ:**
  ```python
  from ..storage.db import Chunk, Document, Embedding, get_backend
  ```
  - **Зачем:** deserialize_embedding удалена. get_backend нужна для FTS fallback.

- [ ] 6.2 Переписать `_vector_search` (строки 53-84):
  **СЕЙЧАС:** Загружает все эмбеддинги, десериализует, cosine similarity в Python.
  **СТАНЕТ:**
  ```python
  def _vector_search(self, session, query_embedding, limit: int, filters):
      """Vector search using pgvector cosine distance."""
      if not query_embedding:
          return []
      try:
          from sqlalchemy import text

          base_query = (
              session.query(Chunk, Document, Embedding)
              .join(Document, Chunk.document_id == Document.id)
              .join(Embedding, Chunk.id == Embedding.chunk_id)
              .filter(Embedding.embedding.isnot(None))
          )
          if filters:
              base_query = self._apply_filters(base_query, filters)

          # PostgreSQL: use pgvector cosine_distance
          if get_backend() == "postgresql":
              distance_expr = Embedding.embedding.cosine_distance(query_embedding)
              results = base_query.order_by(distance_expr).limit(limit).all()
              return [
                  SearchResult(
                      chunk.id,
                      chunk.content,
                      1.0 - distance,  # distance -> score
                      "vector",
                      self._metadata(document, chunk),
                  )
                  for chunk, document, embedding in results
                  for distance in [embedding.embedding.cosine_distance(query_embedding)]
              ]
          else:
              # SQLite fallback: load embeddings and compute in Python
              results = base_query.limit(max(limit * 10, 100)).all()
              search_results = []
              for chunk, document, embedding in results:
                  vector = embedding.embedding  # list[float]
                  if not vector:
                      continue
                  score = self.embedder.cosine_similarity(query_embedding, vector)
                  search_results.append(
                      SearchResult(
                          chunk.id, chunk.content, score, "vector",
                          self._metadata(document, chunk),
                      )
                  )
              search_results.sort(key=lambda r: r.score, reverse=True)
              return search_results[:limit]
      except Exception as e:
          logger.warning(f"Vector search failed: {e}")
          return []
  ```
  - **Зачем:** PostgreSQL → pgvector через HNSW индекс. SQLite → старый brute-force (приемлемо для маленьких БД).
  - **AI NOTE:** В pgvector подходе `embedding.embedding.cosine_distance(query_embedding)` вызывается дважды — один раз для ORDER BY, второй для score. Можно оптимизировать через `text()` subquery, но это сложнее. Для текущих объёмов не критично.

- [ ] 6.3 Переписать `_keyword_search` (строки 86-107):
  **СЕЙЧАС:** `ilike('%token%')` для каждого токена.
  **СТАНЕТ:**
  ```python
  def _keyword_search(self, session, query: str, limit: int, filters):
      """Keyword search using PostgreSQL FTS or SQLite ilike fallback."""
      try:
          tokens = [t for t in query.lower().split() if len(t) >= 2]
          if not tokens:
              return []

          base_query = session.query(Chunk, Document).join(
              Document, Chunk.document_id == Document.id
          )
          if filters:
              base_query = self._apply_filters(base_query, filters)

          if get_backend() == "postgresql":
              # PostgreSQL FTS: tsquery with AND logic
              tsquery = " & ".join(tokens)
              results = base_query.filter(
                  text("chunks.search_vector @@ to_tsquery('russian', :q)")
              ).params(q=tsquery).add_columns(
                  text("ts_rank(chunks.search_vector, to_tsquery('russian', :q)) AS rank")
              ).params(q=tsquery).order_by(
                  text("rank DESC")
              ).limit(limit).all()

              return [
                  SearchResult(
                      chunk.id, chunk.content, float(rank or 0.0), "keyword",
                      self._metadata(document, chunk),
                  )
                  for chunk, document, rank in results
              ]
          else:
              # SQLite fallback: ilike substring matching
              from sqlalchemy import or_
              conditions = [Chunk.content.ilike(f"%{t}%") for t in tokens]
              results = base_query.filter(or_(*conditions)).limit(limit).all()
              return [
                  SearchResult(chunk.id, chunk.content, 0.5, "keyword", self._metadata(document, chunk))
                  for chunk, document in results
              ]
      except Exception as e:
          logger.error(f"Keyword search failed: {e}")
          return []
  ```
  - **Зачем:**
    - PostgreSQL FTS: понимает морфологию русского языка, ts_rank даёт релевантный score 0-1
    - SQLite fallback: старый ilike подход (без ранжирования, fixed score 0.5)
  - **AI NOTE:** `to_tsquery('russian', :q)` использует & (AND) логику — все токены должны быть в тексте. Для OR логики заменить `&` на `|`.
  - **AI NOTE:** `ts_rank` может вернуть NULL для пустых search_vector. `float(rank or 0.0)` обрабатывает это.

- [ ] 6.3 Удалить импорт `or_` (строка 3) — он теперь только в SQLite fallback ветке

> **AI NOTE CRITICAL:** `searcher.py` теперь зависит от `get_backend()`. При SQLite fallback оба метода деградируют gracefully. Но при PostgreSQL — используется полный стек: pgvector + FTS.

---

## 7. Тесты — Обновить под новые типы

### `tests/test_active_memory.py` (368 lines)

**WHY CHANGE:** Удаляем MemoryCache, serialize/deserialize. Добавляем тесты на Vector тип, Redis cache, N-gram fallback.

- [ ] 7.1 В `TestStorage.test_models_import` (строка 253):
  **СЕЙЧАС:** `from ... import Document, Chunk, Embedding, MemoryCache`
  **СТАНЕТ:** `from ... import Document, Chunk, Embedding`
  - Удалить строку 257: `assert MemoryCache is not None`

- [ ] 7.2 Удалить `test_memory_cache_table_name` (строки 274-277) целиком

- [ ] 7.3 Добавить новый тест в `TestStorage`:
  ```python
  def test_embedding_column_is_vector_type(self):
      """Embedding column uses Vector type when pgvector is available."""
      try:
          from pgvector.sqlalchemy import Vector
          from active_memory_mcp.storage.db import Embedding
          assert isinstance(Embedding.__table__.c.embedding.type, Vector)
      except ImportError:
          pass  # pgvector not installed, skip
  ```

- [ ] 7.4 Добавить тест в `TestStorage`:
  ```python
  def test_serialize_deserialize_removed(self):
      """Serialization helpers are removed (Vector handles it natively)."""
      from active_memory_mcp.storage import db
      assert not hasattr(db, "serialize_embedding")
      assert not hasattr(db, "deserialize_embedding")
  ```

- [ ] 7.5 В `TestEmbedder` добавить тест:
  ```python
  def test_char_ngrams(self):
      """N-gram extraction works correctly."""
      from active_memory_mcp.search.embedder import Embedder
      emb = Embedder()
      assert emb._char_ngrams("hello", n=3) == ["hel", "ell", "llo"]
      assert emb._char_ngrams("hi", n=4) == ["hi"]  # Shorter than n
      assert emb._char_ngrams("", n=4) == []
  ```

- [ ] 7.6 В `TestEmbedder` добавить тест:
  ```python
  def test_ngram_fallback_semantic_similarity(self):
      """N-gram fallback produces higher similarity for similar texts."""
      from active_memory_mcp.search.embedder import Embedder
      emb = Embedder()
      # Disable local model and remote to test pure fallback
      emb._local_model = None
      vec1 = emb._fallback_embedding("python programming")
      vec2 = emb._fallback_embedding("python code")
      vec3 = emb._fallback_embedding("ocean waves")
      sim_similar = emb.cosine_similarity(vec1, vec2)
      sim_different = emb.cosine_similarity(vec1, vec3)
      assert sim_similar > sim_different
  ```

- [ ] 7.7 Добавить новый тест класс:
  ```python
  class TestEmbeddingCache:
      """Test embedding caching layer."""

      def test_cache_key_deterministic(self):
          """Same text produces same cache key."""
          from active_memory_mcp.search.embedder import Embedder
          emb = Embedder()
          k1 = emb._cache_key("hello world")
          k2 = emb._cache_key("hello world")
          assert k1 == k2

      def test_cache_key_different_texts(self):
          """Different texts produce different cache keys."""
          from active_memory_mcp.search.embedder import Embedder
          emb = Embedder()
          k1 = emb._cache_key("hello")
          k2 = emb._cache_key("world")
          assert k1 != k2
  ```

### `tests/test_basic.py` (51 lines)

- [ ] 7.8 Обновить `test_cosine_similarity` (строки 34-44):
  - **СЕЙЧАС:** `sim_same > 0.9` — это работает только потому что hash-based fallback детерминированный (одинаковый текст → одинаковый вектор → similarity = 1.0)
  - **НОВЫЙ ПОВЕДЕНИЕ:** То же самое, но теперь это тест для N-gram fallback. Дополнительно проверить что похожие тексты ближе:
  ```python
  def test_cosine_similarity():
      embedder = Embedder()
      embedder._local_model = None  # Force fallback
      vec1 = embedder.embed("hello world")
      vec2 = embedder.embed("hello world")
      vec3 = embedder.embed("completely different")
      sim_same = embedder.cosine_similarity(vec1, vec2)
      sim_diff = embedder.cosine_similarity(vec1, vec3)
      assert sim_same > 0.99  # Same text → nearly identical
      assert sim_same > sim_diff
      # Similar texts should be closer than completely different
      vec4 = embedder.embed("hello there")
      sim_similar = embedder.cosine_similarity(vec1, vec4)
      assert sim_similar > sim_diff
      print(f"✓ Cosine similarity OK (same={sim_same:.3f}, similar={sim_similar:.3f}, diff={sim_diff:.3f})")
  ```

---

## 8. Верификация

- [ ] 8.1 `ruff check src tests` — без ошибок
  - **AI NOTE:** Ruff настроен на line-length=100, py312 target. Если длинные строки — разбить.

- [ ] 8.2 `python3 -m pytest` — все тесты проходят
  - **AI NOTE:** Тесты не требуют живой БД (используют mocks или SQLite). Если тесты падают из-за БД — проверить что `get_backend()` корректно определяет SQLite fallback.

- [ ] 8.3 Проверить что MCP server запускается: `python3 -m active_memory_mcp.main`
  - **AI NOTE:** Может упасть если PostgreSQL недоступен и SQLite fallback не работает. Проверить `AM_STORAGE_BACKEND=sqlite` для тестирования.

- [ ] 8.4 Проверить что Dashboard запускается: `web-dashboard`
  - **AI NOTE:** Аналогично — может требовать БД.

---

## Зависимости между шагами

```
1 (deps: +pgvector, +redis) ───────────────┐
2 (config: CacheConfig update) ────────────┤
3 (db: Vector, FTS DDL, -MemoryCache) ──→ 4 (processor: -serialize)
                                           └─→ 6 (searcher: pgvector/FTS queries)
5 (embedder: Redis cache, Remote, N-gram) ┘
7 (tests) ─────────────────────────────────→ зависит от 2, 3, 5
8 (verify) ────────────────────────────────→ после 1-7
```

## Критические заметки для AI

1. **pgvector extension** — должен быть включён в PostgreSQL ДО создания таблиц. `init_db()` уже делает `CREATE EXTENSION IF NOT EXISTS vector` (строка 170).
2. **HNSW индекс** — НЕ создаётся через `create_all()`. Нужно явно через DDL (пункт 3.7).
3. **FTS триггер** — обновляет `search_vector` автоматически при INSERT/UPDATE. Не нужно вручную вызывать `to_tsvector` в processor.
4. **SQLite fallback** — ВСЕ pgvector и FTS фичи должны graceful degrade. Vector → Text, FTS → ilike, Redis → in-memory dict.
5. **Embedder возвращает list[float]** — не numpy array. `tolist()` вызывается в FastEmbed и в fallback.
6. **Redis кэш** — ключи формата `emb:{md5hash}`. TTL по умолчанию 3600 секунд (1 час).
7. **Remote API** — OpenAI-совместимый формат: POST `/v1/embeddings` с `{"input": text, "model": "..."}`. Ответ: `{"data": [{"embedding": [...]}]}`.
