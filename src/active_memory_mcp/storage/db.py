"""Database models and operations."""

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, ForeignKey, Index, JSON, LargeBinary
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import numpy as np
from typing import Optional, List
import logging

from ..core.config import config

logger = logging.getLogger(__name__)

Base = declarative_base()

class Document(Base):
    """Document metadata."""
    __tablename__ = "documents"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(500), nullable=False)
    filetype = Column(String(50), nullable=False)
    filesize = Column(Integer)
    title = Column(String(200))
    author = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_ = Column(JSON, default=dict)
    
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_documents_filename", "filename"),
        Index("ix_documents_filetype", "filetype"),
        Index("ix_documents_created_at", "created_at"),
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
    search_vector = Column(String, nullable=True)  # String for SQLite, VARCHAR for PG
    
    document = relationship("Document", back_populates="chunks")
    embedding = relationship("Embedding", uselist=False, back_populates="chunk", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_search_vector", "search_vector"),
        Index("ix_chunks_token_count", "token_count"),
    )

# Embedding table - simplified for SQLite compatibility
class Embedding(Base):
    """Vector embedding for a chunk."""
    __tablename__ = "embeddings"
    
    chunk_id = Column(Integer, ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(LargeBinary, nullable=True)  # BLOB for SQLite, could use VECTOR for PG
    model = Column(String(200), default="bge-m3")
    dimensions = Column(Integer, default=1024)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    chunk = relationship("Chunk", back_populates="embedding")

class MemoryCache(Base):
    """Local cache for frequently accessed data."""
    __tablename__ = "memory_cache"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(500), unique=True, nullable=False)
    cache_value = Column(Text, nullable=False)
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index("ix_memory_cache_key", "cache_key"),
        Index("ix_memory_cache_expires", "expires_at"),
    )

_engine = None
_SessionLocal = None

def init_db():
    """Initialize database engine and create tables."""
    global _engine, _SessionLocal
    import os
    if os.getenv("AM_WEB_SQLITE", "false").lower() == "true":
        from pathlib import Path
        from sqlalchemy import create_engine
        data_dir = Path(__file__).parent.parent.parent / "data"
        data_dir.mkdir(exist_ok=True)
        db_path = data_dir / "active_memory.db"
        logger.info(f"Web dashboard using SQLite: {db_path}")
        _engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(_engine)
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
        logger.info("SQLite database initialized")
        return
    # Use PostgreSQL
    logger.info(f"Connecting to database: {config.database_url}")
    _engine = create_engine(
        config.database_url,
        echo=config.web.debug,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    Base.metadata.create_all(_engine)
    if config.db.use_pgvector:
        try:
            from sqlalchemy import text
            with _engine.connect() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                conn.commit()
            logger.info("pgvector extension enabled")
        except Exception as e:
            logger.warning(f"Could not enable pgvector: {e}")
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    logger.info("Database initialized successfully")

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
