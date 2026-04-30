"""Export functionality for ActiveMemory.

Export documents, chunks, and embeddings to JSON or CSV format.
Supports full export, single document export, and category-based export.
"""

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from ..storage.db import (
    Document, Chunk, Embedding,
    get_session, get_backend,
)


def _serialize_embedding(embedding) -> Optional[list]:
    """Convert embedding to list of floats."""
    if embedding is None:
        return None
    if hasattr(embedding, 'tolist'):
        return embedding.tolist()
    if isinstance(embedding, str):
        import json as _json
        try:
            return _json.loads(embedding)
        except Exception:
            return None
    return list(embedding) if embedding else None


def export_all_json(include_embeddings: bool = True) -> dict:
    """Export all documents, chunks, and optionally embeddings to JSON.

    Args:
        include_embeddings: If True, include full embedding vectors.

    Returns:
        dict ready for JSON serialization.
    """
    session = get_session()
    try:
        docs = session.query(Document).order_by(Document.id).all()
        result = {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "backend": get_backend(),
            "documents": []
        }

        for doc in docs:
            doc_data = {
                "id": doc.id,
                "filename": doc.filename,
                "filetype": doc.filetype,
                "filesize": doc.filesize,
                "title": doc.title,
                "author": doc.author,
                "category": doc.category,
                "source": doc.source,
                "importance": doc.importance,
                "pinned": doc.pinned,
                "encrypted": doc.encrypted,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
                "metadata": doc.metadata_ or {},
                "chunks": []
            }

            for chunk in sorted(doc.chunks, key=lambda c: c.chunk_index):
                chunk_data = {
                    "index": chunk.chunk_index,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                    "metadata": chunk.metadata_ or {},
                }

                if include_embeddings:
                    emb = session.query(Embedding).filter(Embedding.chunk_id == chunk.id).first()
                    if emb:
                        chunk_data["embedding"] = _serialize_embedding(emb.embedding)
                        chunk_data["embedding_model"] = emb.model
                        chunk_data["embedding_dimensions"] = emb.dimensions
                        chunk_data["encrypted_embedding"] = emb.encrypted_embedding

                doc_data["chunks"].append(chunk_data)

            result["documents"].append(doc_data)

        return result
    finally:
        session.close()


def export_all_csv() -> str:
    """Export all documents and chunks to CSV (no embeddings).

    Returns:
        CSV string with columns:
        document_id, filename, filetype, title, category, importance, pinned,
        encrypted, created_at, chunk_index, content, token_count
    """
    session = get_session()
    try:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "document_id", "filename", "filetype", "title", "category",
            "importance", "pinned", "encrypted", "created_at",
            "chunk_index", "content", "token_count"
        ])

        docs = session.query(Document).order_by(Document.id).all()
        for doc in docs:
            for chunk in sorted(doc.chunks, key=lambda c: c.chunk_index):
                writer.writerow([
                    doc.id,
                    doc.filename,
                    doc.filetype,
                    doc.title or "",
                    doc.category,
                    doc.importance,
                    doc.pinned,
                    doc.encrypted,
                    doc.created_at.isoformat() if doc.created_at else "",
                    chunk.chunk_index,
                    chunk.content,
                    chunk.token_count,
                ])

        return output.getvalue()
    finally:
        session.close()


def export_document_json(document_id: int, include_embeddings: bool = True) -> Optional[dict]:
    """Export a single document with all its chunks.

    Returns:
        dict for JSON serialization, or None if document not found.
    """
    session = get_session()
    try:
        doc = session.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return None

        doc_data = {
            "id": doc.id,
            "filename": doc.filename,
            "filetype": doc.filetype,
            "filesize": doc.filesize,
            "title": doc.title,
            "author": doc.author,
            "category": doc.category,
            "source": doc.source,
            "importance": doc.importance,
            "pinned": doc.pinned,
            "encrypted": doc.encrypted,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            "metadata": doc.metadata_ or {},
            "chunks": []
        }

        for chunk in sorted(doc.chunks, key=lambda c: c.chunk_index):
            chunk_data = {
                "index": chunk.chunk_index,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "metadata": chunk.metadata_ or {},
            }

            if include_embeddings:
                emb = session.query(Embedding).filter(Embedding.chunk_id == chunk.id).first()
                if emb:
                    chunk_data["embedding"] = _serialize_embedding(emb.embedding)
                    chunk_data["embedding_model"] = emb.model
                    chunk_data["embedding_dimensions"] = emb.dimensions
                    chunk_data["encrypted_embedding"] = emb.encrypted_embedding

            doc_data["chunks"].append(chunk_data)

        return doc_data
    finally:
        session.close()


def export_category_json(category: str, include_embeddings: bool = True) -> dict:
    """Export all documents in a specific category.

    Returns:
        dict with category name and list of documents.
    """
    session = get_session()
    try:
        docs = session.query(Document).filter(Document.category == category).order_by(Document.id).all()

        result = {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "count": len(docs),
            "documents": []
        }

        for doc in docs:
            doc_data = {
                "id": doc.id,
                "filename": doc.filename,
                "filetype": doc.filetype,
                "filesize": doc.filesize,
                "title": doc.title,
                "author": doc.author,
                "category": doc.category,
                "source": doc.source,
                "importance": doc.importance,
                "pinned": doc.pinned,
                "encrypted": doc.encrypted,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
                "metadata": doc.metadata_ or {},
                "chunks": []
            }

            for chunk in sorted(doc.chunks, key=lambda c: c.chunk_index):
                chunk_data = {
                    "index": chunk.chunk_index,
                    "content": chunk.content,
                    "token_count": chunk.token_count,
                    "metadata": chunk.metadata_ or {},
                }

                if include_embeddings:
                    emb = session.query(Embedding).filter(Embedding.chunk_id == chunk.id).first()
                    if emb:
                        chunk_data["embedding"] = _serialize_embedding(emb.embedding)
                        chunk_data["embedding_model"] = emb.model
                        chunk_data["embedding_dimensions"] = emb.dimensions
                        chunk_data["encrypted_embedding"] = emb.encrypted_embedding

                doc_data["chunks"].append(chunk_data)

            result["documents"].append(doc_data)

        return result
    finally:
        session.close()


def get_export_stats() -> dict:
    """Get statistics about exportable data."""
    session = get_session()
    try:
        doc_count = session.query(func.count(Document.id)).scalar() or 0
        chunk_count = session.query(func.count(Chunk.id)).scalar() or 0
        embedding_count = session.query(func.count(Embedding.chunk_id)).scalar() or 0

        categories = session.query(
            Document.category,
            func.count(Document.id)
        ).group_by(Document.category).all()

        return {
            "documents": doc_count,
            "chunks": chunk_count,
            "embeddings": embedding_count,
            "categories": {cat or "general": count for cat, count in categories},
        }
    finally:
        session.close()
