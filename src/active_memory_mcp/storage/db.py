"""Database models and operations.

PostgreSQL with pgvector for native vector storage and full-text search.
SQLite fallback with degraded capabilities (no Vector, no FTS).
"""

from pathlib import Path
import os

from sqlalchemy import Boolean, Column, create_engine, DateTime, ForeignKey, Index, Integer, JSON
from sqlalchemy import String, Text, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import logging

from ..core.config import config

logger = logging.getLogger(__name__)

Base = declarative_base()

# pgvector Vector type (conditional — not available without PostgreSQL)
try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None


class Document(Base):
    """Document metadata."""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(500), nullable=False)
    filetype = Column(String(50), nullable=False)
    filesize = Column(Integer)
    title = Column(String(200))
    author = Column(String(200))
    category = Column(String(100), default="general")
    source = Column(String(200), default="manual")
    importance = Column(Integer, default=3)
    pinned = Column(Boolean, default=False)
    encrypted = Column(Boolean, default=False)
    encryption_salt = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_ = Column(JSON, default=dict)

    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_documents_filename", "filename"),
        Index("ix_documents_filetype", "filetype"),
        Index("ix_documents_created_at", "created_at"),
        Index("ix_documents_encrypted", "encrypted"),
    )


class Chunk(Base):
    """Text chunk from a document."""
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)
    chunk_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    search_vector = Column(String, nullable=True)  # tsvector for PG, String for SQLite
    metadata_ = Column(JSON, default=dict)

    document = relationship("Document", back_populates="chunks")
    embedding = relationship("Embedding", uselist=False, back_populates="chunk", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_search_vector", "search_vector"),
        Index("ix_chunks_token_count", "token_count"),
    )


class Embedding(Base):
    """Vector embedding for a chunk.

    Uses pgvector Vector type when PostgreSQL is available,
    falls back to Text for SQLite.
    """
    __tablename__ = "embeddings"

    chunk_id = Column(Integer, ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(Vector(config.embedding.dimensions), nullable=True) if Vector else Column(Text, nullable=True)
    encrypted_embedding = Column(Text, nullable=True)  # Fernet-encrypted embedding for sensitive docs
    model = Column(String(200), default="bge-m3")
    dimensions = Column(Integer, default=1024)
    created_at = Column(DateTime, default=datetime.utcnow)

    chunk = relationship("Chunk", back_populates="embedding")

    __table_args__ = (
        Index("ix_embeddings_hnsw", "embedding",
              postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64},
              postgresql_ops={"embedding": "vector_cosine_ops"}),
        Index("ix_embeddings_encrypted", "encrypted_embedding"),
    )


class ApiToken(Base):
    """API token for authentication."""
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String(128), nullable=False, unique=True)
    label = Column(String(100), nullable=False)
    scopes = Column(String(50), nullable=False, default="read")  # read | write | admin (comma-separated)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_by = Column(String(100), default="system")

    __table_args__ = (
        Index("ix_api_tokens_hash", "token_hash"),
        Index("ix_api_tokens_active", "active"),
    )


class AccessLog(Base):
    """Access log for audit trail."""
    __tablename__ = "access_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    action = Column(String(50), nullable=False)  # search, store, read, delete, update, login, token_create, etc.
    token_label = Column(String(100), nullable=True)
    document_id = Column(Integer, nullable=True)
    ip_address = Column(String(45), nullable=True)  # IPv6 max length
    user_agent = Column(String(500), nullable=True)
    details = Column(JSON, default=dict)
    success = Column(Boolean, default=True)

    __table_args__ = (
        Index("ix_access_log_timestamp", "timestamp"),
        Index("ix_access_log_action", "action"),
        Index("ix_access_log_document_id", "document_id"),
        Index("ix_access_log_token_label", "token_label"),
    )


_engine = None
_SessionLocal = None
_backend = None


def _sqlite_url() -> str:
    data_dir = Path(__file__).resolve().parents[3] / "data"
    data_dir.mkdir(exist_ok=True)
    db_path = Path(os.getenv("AM_SQLITE_PATH", str(data_dir / "active_memory_fallback.db")))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def _create_sqlite_engine(reason: str):
    global _backend
    url = _sqlite_url()
    logger.warning("Using SQLite fallback for critical agent memory: %s", reason)
    _backend = "sqlite"
    return create_engine(url, connect_args={"check_same_thread": False})


def _configure_postgres_ddl():
    """Run DDL for pgvector and FTS after tables are created."""
    try:
        with _engine.connect() as conn:
            # Enable pgvector extension
            if config.db.use_pgvector:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

            # FTS: tsvector column, GIN index, and auto-update trigger
            conn.execute(text("""
                ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector;
                CREATE INDEX IF NOT EXISTS ix_chunks_fts ON chunks USING GIN(search_vector);
                CREATE OR REPLACE FUNCTION chunks_tsvector_trigger() RETURNS trigger AS $$
                BEGIN
                    NEW.search_vector := to_tsvector('russian', NEW.content);
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                DROP TRIGGER IF EXISTS chunks_tsvector_refresh ON chunks;
                CREATE TRIGGER chunks_tsvector_refresh
                    BEFORE INSERT OR UPDATE ON chunks
                    FOR EACH ROW EXECUTE FUNCTION chunks_tsvector_trigger();
            """))

            # HNSW index for fast vector search
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_embeddings_hnsw ON embeddings
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64);
            """))

            conn.commit()
            logger.info("PostgreSQL DDL configured: pgvector, FTS, HNSW index")
    except Exception as e:
        logger.warning(f"Could not configure PostgreSQL DDL: {e}")


def init_db():
    """Initialize database engine and create tables."""
    global _engine, _SessionLocal, _backend
    force_sqlite = (
        os.getenv("AM_STORAGE_BACKEND", "").lower() == "sqlite"
        or os.getenv("AM_WEB_SQLITE", "false").lower() == "true"
    )
    allow_fallback = os.getenv("AM_ENABLE_SQLITE_FALLBACK", "true").lower() == "true"
    if force_sqlite:
        _engine = _create_sqlite_engine("AM_STORAGE_BACKEND/AM_WEB_SQLITE requested")
        Base.metadata.create_all(_engine)
    else:
        logger.info(f"Connecting to database: {config.database_url}")
        try:
            _engine = create_engine(
                config.database_url,
                echo=config.web.debug,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
            )
            with _engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            _backend = "postgresql"
            Base.metadata.create_all(_engine)
            _configure_postgres_ddl()
        except Exception as e:
            if not allow_fallback:
                raise
            _engine = _create_sqlite_engine(f"PostgreSQL unavailable: {e}")
            Base.metadata.create_all(_engine)

    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    logger.info("Database initialized successfully using %s", _backend)


def get_session():
    """Get a database session."""
    global _SessionLocal
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()


def get_engine():
    """Get the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        init_db()
    return _engine


def get_backend() -> str:
    """Return the active storage backend name."""
    if _engine is None:
        init_db()
    return _backend or "unknown"
