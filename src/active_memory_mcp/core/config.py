"""Configuration management for ActiveMemory.

Supports environment variables:
- AM_DB_HOST, AM_DB_PORT, AM_DB_NAME, AM_DB_USER, AM_DB_PASSWORD
- AM_USE_PGVECTOR (true/false)
- AM_EMBEDDING_ENDPOINT, AM_EMBEDDING_MODEL, AM_EMBEDDING_DIM
- AM_USE_LOCAL_EMBEDDING (true/false)
- AM_CHUNK_SIZE, AM_CHUNK_OVERLAP, AM_MAX_CHUNKS_PER_DOC
- AM_SEARCH_TOP_K, AM_HYBRID_ALPHA, AM_MIN_SCORE_THRESHOLD
- AM_REDIS_URL, AM_CACHE_TTL, AM_USE_REDIS
- AM_WEB_HOST, AM_WEB_PORT, AM_WEB_DEBUG
- AM_STORAGE_BACKEND (postgresql/sqlite), AM_ENABLE_SQLITE_FALLBACK (true/false)
- AM_SQLITE_PATH — path for critical SQLite fallback memory
- AM_WEB_SQLITE (true/false) — legacy alias for forcing SQLite storage
"""

from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent.parent


@dataclass
class DatabaseConfig:
    """Database configuration."""
    host: str = os.getenv("AM_DB_HOST", "localhost")
    port: int = int(os.getenv("AM_DB_PORT", "5432"))
    database: str = os.getenv("AM_DB_NAME", "hermes_memory")
    user: str = os.getenv("AM_DB_USER", "postgres")
    password: str = os.getenv("AM_DB_PASSWORD", "postgres")
    use_pgvector: bool = os.getenv("AM_USE_PGVECTOR", "true").lower() == "true"


@dataclass
class EmbeddingConfig:
    """Embedding model configuration."""
    endpoint: str = os.getenv("AM_EMBEDDING_ENDPOINT", "http://192.168.1.199:8899/v1/embeddings")
    model: str = os.getenv("AM_EMBEDDING_MODEL", "BAAI/bge-m3")
    dimensions: int = int(os.getenv("AM_EMBEDDING_DIM", "1024"))
    use_local: bool = os.getenv("AM_USE_LOCAL_EMBEDDING", "true").lower() == "true"
    local_model_path: str = os.getenv("AM_LOCAL_MODEL_PATH", "/models/bge-m3-q8_0.gguf")


@dataclass
class ChunkingConfig:
    """Chunking configuration for document processing."""
    chunk_size: int = int(os.getenv("AM_CHUNK_SIZE", "512"))
    chunk_overlap: int = int(os.getenv("AM_CHUNK_OVERLAP", "50"))
    max_chunks_per_doc: int = int(os.getenv("AM_MAX_CHUNKS_PER_DOC", "1000"))


@dataclass
class SearchConfig:
    """Search configuration."""
    top_k: int = int(os.getenv("AM_SEARCH_TOP_K", "5"))
    hybrid_alpha: float = float(os.getenv("AM_HYBRID_ALPHA", "0.5"))  # 0.0=vector only, 1.0=keyword only
    min_score_threshold: float = float(os.getenv("AM_MIN_SCORE_THRESHOLD", "0.1"))
    context_max_tokens: int = int(os.getenv("AM_CONTEXT_MAX_TOKENS", "4000"))
    context_boost_importance: float = float(os.getenv("AM_CONTEXT_BOOST_IMPORTANCE", "0.3"))
    context_boost_pinned: float = float(os.getenv("AM_CONTEXT_BOOST_PINNED", "0.5"))


@dataclass
class CacheConfig:
    """Cache configuration."""
    redis_url: str = os.getenv("AM_REDIS_URL", "redis://localhost:6379/0")
    ttl_seconds: int = int(os.getenv("AM_CACHE_TTL", "3600"))
    use_redis: bool = os.getenv("AM_USE_REDIS", "true").lower() == "true"


@dataclass
class SecurityConfig:
    """Security configuration."""
    require_auth: bool = os.getenv("AM_REQUIRE_AUTH", "false").lower() == "true"
    encryption_key: str = os.getenv("AM_ENCRYPTION_KEY", "active_memory_default_key_change_me")
    access_log_retention_days: int = int(os.getenv("AM_ACCESS_LOG_RETENTION", "30"))


@dataclass
class WebConfig:
    """Web dashboard configuration."""
    host: str = os.getenv("AM_WEB_HOST", "0.0.0.0")
    port: int = int(os.getenv("AM_WEB_PORT", "8788"))
    debug: bool = os.getenv("AM_WEB_DEBUG", "false").lower() == "true"


class Config:
    """Main configuration container."""

    def __init__(self):
        self.db = DatabaseConfig()
        self.embedding = EmbeddingConfig()
        self.chunking = ChunkingConfig()
        self.search = SearchConfig()
        self.cache = CacheConfig()
        self.web = WebConfig()
        self.security = SecurityConfig()

    @property
    def database_url(self) -> str:
        """SQLAlchemy database URL — PostgreSQL with pgvector by default."""
        return (
            f"postgresql+psycopg://{self.db.user}:{self.db.password}"
            f"@{self.db.host}:{self.db.port}/{self.db.database}"
        )


# Global singleton
config = Config()
