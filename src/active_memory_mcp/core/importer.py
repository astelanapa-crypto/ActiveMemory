"""Import functionality for ActiveMemory.

Import documents, chunks, and embeddings from JSON or CSV format.
Supports merge (add new) and replace (replace all) modes.
"""

import hashlib
import json
import csv
from datetime import datetime


from ..storage.db import (
    Document, Chunk, Embedding,
    get_session, get_backend,
)
from ..core.config import config


def _content_hash(content: str) -> str:
    """Generate hash for content to detect duplicates."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def validate_import_data(data: dict) -> dict:
    """Validate import data structure without actually importing.

    Returns:
        dict with "valid" (bool), "errors" (list), "warnings" (list), "stats" (dict)
    """
    errors = []
    warnings = []

    if not isinstance(data, dict):
        return {"valid": False, "errors": ["Data must be a JSON object"], "warnings": [], "stats": {}}

    if "documents" not in data:
        return {"valid": False, "errors": ["Missing 'documents' key"], "warnings": [], "stats": {}}

    docs = data.get("documents", [])
    if not isinstance(docs, list):
        return {"valid": False, "errors": ["'documents' must be a list"], "warnings": [], "stats": {}}

    stats = {"documents": len(docs), "chunks": 0, "embeddings": 0, "errors": []}

    for i, doc in enumerate(docs):
        prefix = f"Document[{i}]"

        if not isinstance(doc, dict):
            errors.append(f"{prefix}: must be an object")
            continue

        required = ["filename", "filetype"]
        for field in required:
            if field not in doc:
                errors.append(f"{prefix}: missing '{field}'")

        chunks = doc.get("chunks", [])
        if not isinstance(chunks, list):
            errors.append(f"{prefix}: 'chunks' must be a list")
            continue

        stats["chunks"] += len(chunks)

        for j, chunk in enumerate(chunks):
            cprefix = f"{prefix} chunk[{j}]"

            if not isinstance(chunk, dict):
                errors.append(f"{cprefix}: must be an object")
                continue

            if "content" not in chunk:
                errors.append(f"{cprefix}: missing 'content'")

            if "embedding" in chunk and chunk["embedding"]:
                emb = chunk["embedding"]
                if isinstance(emb, list) and len(emb) > 0:
                    if len(emb) != config.embedding.dimensions:
                        warnings.append(
                            f"{cprefix}: embedding dimension {len(emb)} "
                            f"!= expected {config.embedding.dimensions}"
                        )
                    stats["embeddings"] += 1

            if "encrypted_embedding" in chunk and chunk["encrypted_embedding"]:
                stats["embeddings"] += 1

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }


def import_from_json(data: dict, mode: str = "merge") -> dict:
    """Import documents from JSON data.

    Args:
        data: JSON data with documents/chunks structure.
        mode: "merge" (add new) or "replace" (clear all first).

    Returns:
        dict with import statistics.
    """
    session = get_session()
    try:
        stats = {"imported_documents": 0, "imported_chunks": 0, "imported_embeddings": 0, "skipped": 0, "errors": []}

        if mode == "replace":
            session.query(Embedding).delete()
            session.query(Chunk).delete()
            session.query(Document).delete()
            session.commit()

        docs = data.get("documents", [])
        existing_hashes = set()
        if mode == "merge":
            # Build hash set for deduplication
            for doc in session.query(Document).all():
                for chunk in doc.chunks:
                    existing_hashes.add(_content_hash(chunk.content))

        for doc_data in docs:
            try:
                # Check for duplicates in merge mode
                chunks_data = doc_data.get("chunks", [])
                if mode == "merge" and chunks_data:
                    first_content = chunks_data[0].get("content", "")
                    if first_content and _content_hash(first_content) in existing_hashes:
                        stats["skipped"] += 1
                        continue

                doc = Document(
                    filename=doc_data.get("filename", "imported.txt"),
                    filetype=doc_data.get("filetype", "text"),
                    filesize=doc_data.get("filesize", 0),
                    title=doc_data.get("title", doc_data.get("filename")),
                    author=doc_data.get("author"),
                    category=doc_data.get("category", "imported"),
                    source=doc_data.get("source", "import"),
                    importance=doc_data.get("importance", 3),
                    pinned=doc_data.get("pinned", False),
                    encrypted=doc_data.get("encrypted", False),
                    encryption_salt=doc_data.get("encryption_salt"),
                    metadata_=doc_data.get("metadata", {}),
                )
                if "created_at" in doc_data:
                    try:
                        doc.created_at = datetime.fromisoformat(doc_data["created_at"])
                    except Exception:
                        pass
                if "updated_at" in doc_data:
                    try:
                        doc.updated_at = datetime.fromisoformat(doc_data["updated_at"])
                    except Exception:
                        pass

                session.add(doc)
                session.flush()  # Get doc.id

                stats["imported_documents"] += 1

                for chunk_data in chunks_data:
                    content = chunk_data.get("content", "")
                    if not content:
                        continue

                    chunk = Chunk(
                        document_id=doc.id,
                        content=content,
                        token_count=chunk_data.get("token_count", len(content) // 4),
                        chunk_index=chunk_data.get("chunk_index", 0),
                        metadata_=chunk_data.get("metadata", {}),
                    )
                    session.add(chunk)
                    session.flush()

                    stats["imported_chunks"] += 1

                    # Import embedding if present
                    emb_data = None
                    if "embedding" in chunk_data and chunk_data["embedding"]:
                        emb_data = chunk_data["embedding"]
                    elif "embedding" in chunk_data and chunk_data["embedding"] is None:
                        emb_data = None

                    if emb_data and isinstance(emb_data, list) and len(emb_data) > 0:
                        try:
                            dimensions = chunk_data.get("embedding_dimensions", len(emb_data))
                            model = chunk_data.get("embedding_model", "imported")

                            if get_backend() == "postgresql" and __import_vector__():
                                embedding_col = emb_data
                            else:
                                embedding_col = json.dumps(emb_data)

                            session.add(Embedding(
                                chunk_id=chunk.id,
                                embedding=embedding_col,
                                model=model,
                                dimensions=dimensions,
                            ))
                            stats["imported_embeddings"] += 1
                        except Exception as e:
                            stats["errors"].append(f"Chunk {chunk.id}: embedding import failed: {e}")

                    # Import encrypted embedding if present
                    if "encrypted_embedding" in chunk_data and chunk_data["encrypted_embedding"]:
                        try:
                            emb = session.query(Embedding).filter(Embedding.chunk_id == chunk.id).first()
                            if emb:
                                emb.encrypted_embedding = chunk_data["encrypted_embedding"]
                            stats["imported_embeddings"] += 1
                        except Exception as e:
                            stats["errors"].append(f"Chunk {chunk.id}: encrypted embedding import failed: {e}")

            except Exception as e:
                stats["errors"].append(f"Document import failed: {e}")
                session.rollback()
                session.begin()

        session.commit()
        return stats

    except Exception as e:
        session.rollback()
        return {"imported_documents": 0, "imported_chunks": 0, "imported_embeddings": 0, "skipped": 0, "errors": [str(e)]}
    finally:
        session.close()


def import_from_csv(file_path: str, mode: str = "merge") -> dict:
    """Import documents from CSV file.

    CSV format: document_id, filename, filetype, title, category, importance, pinned,
                  created_at, chunk_index, content, token_count

    Note: embeddings are NOT imported from CSV (too large).

    Args:
        file_path: Path to CSV file.
        mode: "merge" or "replace"

    Returns:
        dict with import statistics.
    """
    session = get_session()
    try:
        stats = {"imported_documents": 0, "imported_chunks": 0, "imported_embeddings": 0, "skipped": 0, "errors": []}

        if mode == "replace":
            session.query(Embedding).delete()
            session.query(Chunk).delete()
            session.query(Document).delete()
            session.commit()

        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)

            for row in reader:
                try:
                    int(row.get("document_id", 0))
                    filename = row.get("filename", "imported.txt")
                    filetype = row.get("filetype", "text")
                    title = row.get("title", filename)
                    category = row.get("category", "imported")
                    importance = int(row.get("importance", 3))
                    pinned = row.get("pinned", "false").lower() == "true"
                    content = row.get("content", "")
                    chunk_index = int(row.get("chunk_index", 0))
                    token_count = int(row.get("token_count", len(content) // 4))

                    # Create new document for each row (CSV is flat)
                    doc = Document(
                        filename=filename,
                        filetype=filetype,
                        filesize=len(content.encode('utf-8')),
                        title=title,
                        category=category,
                        source="csv-import",
                        importance=importance,
                        pinned=pinned,
                    )
                    session.add(doc)
                    session.flush()
                    stats["imported_documents"] += 1

                    chunk = Chunk(
                        document_id=doc.id,
                        content=content,
                        token_count=token_count,
                        chunk_index=chunk_index,
                    )
                    session.add(chunk)
                    stats["imported_chunks"] += 1

                except Exception as e:
                    stats["errors"].append(f"Row import failed: {e}")
                    session.rollback()
                    session.begin()

        session.commit()
        return stats

    except Exception as e:
        session.rollback()
        return {"imported_documents": 0, "imported_chunks": 0, "imported_embeddings": 0, "skipped": 0, "errors": [str(e)]}
    finally:
        session.close()


def import_from_file(file_path: str, mode: str = "merge") -> dict:
    """Auto-detect format and import from file.

    Args:
        file_path: Path to JSON or CSV file.
        mode: "merge" or "replace"

    Returns:
        dict with import statistics.
    """
    import os
    if not os.path.exists(file_path):
        return {"errors": [f"File not found: {file_path}"]}

    if file_path.endswith('.csv'):
        return import_from_csv(file_path, mode)
    elif file_path.endswith('.json'):
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return import_from_json(data, mode)
    else:
        return {"errors": [f"Unsupported file format: {file_path}"]}


def __import_vector__():
    """Check if pgvector Vector type is available."""
    try:
        import importlib
        return importlib.util.find_spec("pgvector") is not None
    except ImportError:
        return False
