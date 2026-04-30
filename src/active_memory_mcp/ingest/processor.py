"""Document ingestion and processing."""

import logging
from pathlib import Path
from typing import Any, Dict

from ..core.config import config
from .chunker import Chunker
from ..search.embedder import Embedder
from ..storage.db import Document, Chunk, Embedding

logger = logging.getLogger(__name__)

class DocumentProcessor:
    """Process and ingest documents into the memory system."""
    
    def __init__(self):
        self.chunker = Chunker(
            chunk_size=config.chunking.chunk_size,
            chunk_overlap=config.chunking.chunk_overlap,
        )
        self.embedder = Embedder()
    
    def process_file(self, file_path: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process a file and return extracted data."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        ext = file_path.suffix.lower()
        metadata = metadata or {}
        metadata["original_path"] = str(file_path)
        metadata.setdefault("filename", file_path.name)
        metadata["filesize"] = file_path.stat().st_size
        
        # Determine file type
        if ext in [".txt"]:
            filetype = "text"
            text = self._extract_text(file_path)
        elif ext in [".pdf"]:
            filetype = "pdf"
            text = self._extract_pdf(file_path)
        elif ext in [".md"]:
            filetype = "markdown"
            text = self._extract_markdown(file_path)
        elif ext in [".py", ".js", ".ts", ".java", ".cpp", ".c", ".h", ".go", ".rs"]:
            filetype = "code"
            text = self._extract_text(file_path)
        else:
            # Try as text
            try:
                text = self._extract_text(file_path)
                filetype = "text"
            except Exception as exc:
                raise ValueError(f"Unsupported file type: {ext}") from exc
        
        return {
            "filename": metadata["filename"],
            "filetype": filetype,
            "filesize": metadata["filesize"],
            "text": text,
            "metadata": metadata,
        }
    
    def ingest_document(
        self,
        file_path: str,
        metadata: Dict[str, Any] = None,
        session=None,
    ) -> Dict[str, Any]:
        """Ingest a document into the database."""
        from ..storage.db import get_session, Document, Chunk, Embedding
        
        should_close = False
        if session is None:
            session = get_session()
            should_close = True
        
        try:
            # Process file
            doc_data = self.process_file(file_path, metadata=metadata)
            doc_metadata = doc_data["metadata"]
            
            # Create document record
            doc = Document(
                filename=doc_data["filename"],
                filetype=doc_data["filetype"],
                filesize=doc_data["filesize"],
                title=doc_metadata.get("title"),
                author=doc_metadata.get("author"),
                category=doc_metadata.get("category", "general"),
                source=doc_metadata.get("source", "document"),
                importance=int(doc_metadata.get("importance", 3)),
                pinned=bool(doc_metadata.get("pinned", False)),
                metadata_=doc_metadata,
            )
            session.add(doc)
            session.flush()  # Get doc.id
            
            # Chunk text
            if doc_data["filetype"] == "code":
                chunks_data = self.chunker.chunk_code(
                    doc_data["text"],
                    doc.id,
                    doc_data["metadata"].get("original_path", ""),
                )
            else:
                chunks_data = self.chunker.chunk_text(
                    doc_data["text"],
                    doc.id,
                    doc_data["metadata"],
                )
            
            if not chunks_data:
                logger.warning(f"No chunks extracted from {file_path}")
                if should_close:
                    session.close()
                return {"success": False, "message": "No chunks extracted"}
            
            # Limit chunks
            max_chunks = config.chunking.max_chunks_per_doc
            if len(chunks_data) > max_chunks:
                logger.warning(f"Truncating {len(chunks_data)} chunks to {max_chunks}")
                chunks_data = chunks_data[:max_chunks]
            
            # Create chunks and embeddings
            total_tokens = 0
            for chunk_data in chunks_data:
                chunk = Chunk(
                    document_id=doc.id,
                    content=chunk_data["content"],
                    token_count=chunk_data["token_count"],
                    chunk_index=chunk_data["chunk_index"],
                    metadata_=chunk_data.get("metadata", {}),
                )
                session.add(chunk)
                session.flush()  # Get chunk.id
                
                total_tokens += chunk.token_count
                
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
            
            session.commit()
            
            logger.info(
                f"Ingested document '{doc.filename}' with {len(chunks_data)} chunks "
                f"({total_tokens} tokens)"
            )
            
            return {
                "success": True,
                "document_id": doc.id,
                "filename": doc.filename,
                "chunks": len(chunks_data),
                "tokens": total_tokens,
            }
            
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to ingest document {file_path}: {e}")
            raise
        finally:
            if should_close:
                session.close()
    
    def _extract_text(self, file_path: Path) -> str:
        """Extract text from a text file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="latin-1") as f:
                return f.read()
    
    def _extract_pdf(self, file_path: Path) -> str:
        """Extract text from a PDF file."""
        try:
            import PyPDF2
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text
        except ImportError:
            raise ImportError("PyPDF2 not installed. Install with: pip install PyPDF2")
        except Exception as e:
            logger.warning(f"PDF extraction failed, falling back to text: {e}")
            return self._extract_text(file_path)
    
    def _extract_markdown(self, file_path: Path) -> str:
        """Extract text from a markdown file."""
        return self._extract_text(file_path)

    def update_document(
        self,
        document_id: int,
        file_path: str,
        metadata: Dict[str, Any] = None,
        session=None,
    ) -> Dict[str, Any]:
        """Replace document content: delete old chunks+embeddings, re-ingest."""
        from ..storage.db import get_session, get_engine
        from sqlalchemy import delete

        should_close = False
        if session is None:
            session = get_session()
            should_close = True

        try:
            doc = session.query(Document).filter(Document.id == document_id).first()
            if not doc:
                return {"success": False, "message": "Document not found"}

            get_engine()
            session.execute(delete(Embedding).where(
                Embedding.chunk_id.in_(
                    session.query(Chunk.id).filter(Chunk.document_id == document_id)
                )
            ))
            session.execute(delete(Chunk).filter(Chunk.document_id == document_id))
            session.flush()

            doc_data = self.process_file(file_path, metadata=metadata)
            doc_metadata = doc_data["metadata"]

            doc.filename = doc_data["filename"]
            doc.filetype = doc_data["filetype"]
            doc.filesize = doc_data["filesize"]
            doc.title = doc_metadata.get("title", doc.title)
            doc.author = doc_metadata.get("author", doc.author)
            doc.category = doc_metadata.get("category", doc.category)
            if "importance" in doc_metadata:
                doc.importance = int(doc_metadata["importance"])
            if "pinned" in doc_metadata:
                doc.pinned = bool(doc_metadata["pinned"])
            doc.metadata_ = doc_metadata

            if doc_data["filetype"] == "code":
                chunks_data = self.chunker.chunk_code(
                    doc_data["text"],
                    doc.id,
                    doc_data["metadata"].get("original_path", ""),
                )
            else:
                chunks_data = self.chunker.chunk_text(
                    doc_data["text"],
                    doc.id,
                    doc_data["metadata"],
                )

            if not chunks_data:
                session.rollback()
                return {"success": False, "message": "No chunks extracted"}

            max_chunks = config.chunking.max_chunks_per_doc
            if len(chunks_data) > max_chunks:
                chunks_data = chunks_data[:max_chunks]

            total_tokens = 0
            for chunk_data in chunks_data:
                chunk = Chunk(
                    document_id=doc.id,
                    content=chunk_data["content"],
                    token_count=chunk_data["token_count"],
                    chunk_index=chunk_data["chunk_index"],
                    metadata_=chunk_data.get("metadata", {}),
                )
                session.add(chunk)
                session.flush()
                total_tokens += chunk.token_count

                embedding = self.embedder.embed(chunk.content)
                if embedding is None:
                    logger.warning(f"Failed to embed chunk {chunk.id}")
                    continue
                emb = Embedding(
                    chunk_id=chunk.id,
                    embedding=embedding,
                    model=self.embedder.model_name,
                    dimensions=len(embedding),
                )
                session.add(emb)

            session.commit()

            return {
                "success": True,
                "document_id": doc.id,
                "filename": doc.filename,
                "old_chunks_deleted": True,
                "new_chunks": len(chunks_data),
                "tokens": total_tokens,
            }

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update document {document_id}: {e}")
            return {"success": False, "message": str(e)}
        finally:
            if should_close:
                session.close()

    def update_document_metadata(
        self,
        document_id: int,
        metadata: Dict[str, Any],
        session=None,
    ) -> Dict[str, Any]:
        """Update metadata fields on an existing document."""
        from ..storage.db import get_session

        should_close = False
        if session is None:
            session = get_session()
            should_close = True

        try:
            doc = session.query(Document).filter(Document.id == document_id).first()
            if not doc:
                return {"success": False, "message": "Document not found"}

            current = dict(doc.metadata_ or {})
            current.update(metadata)
            doc.metadata_ = current

            for key in ["title", "author", "category", "importance", "pinned"]:
                if key in metadata:
                    setattr(doc, key, metadata[key])

            session.commit()
            return {"success": True, "document_id": doc.id}

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update metadata for {document_id}: {e}")
            return {"success": False, "message": str(e)}
        finally:
            if should_close:
                session.close()

    def rename_document(
        self,
        document_id: int,
        new_filename: str,
        session=None,
    ) -> Dict[str, Any]:
        """Rename a document without changing its content."""
        from ..storage.db import get_session

        should_close = False
        if session is None:
            session = get_session()
            should_close = True

        try:
            doc = session.query(Document).filter(Document.id == document_id).first()
            if not doc:
                return {"success": False, "message": "Document not found"}

            old_name = doc.filename
            doc.filename = new_filename

            current_meta = dict(doc.metadata_ or {})
            current_meta["original_filename"] = old_name
            doc.metadata_ = current_meta

            session.commit()
            return {"success": True, "document_id": doc.id, "old_name": old_name, "new_name": new_filename}

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to rename document {document_id}: {e}")
            return {"success": False, "message": str(e)}
        finally:
            if should_close:
                session.close()

    def reindex_embeddings(
        self,
        document_id: int = None,
        session=None,
    ) -> Dict[str, Any]:
        """Recalculate embeddings for all chunks (or one document).

        Useful after model change or when N-gram fallback improves.
        """
        from ..storage.db import get_session, Document, Chunk, Embedding
        from sqlalchemy import delete

        should_close = False
        if session is None:
            session = get_session()
            should_close = True

        try:
            chunks_query = session.query(Chunk)
            if document_id:
                doc = session.query(Document).filter(Document.id == document_id).first()
                if not doc:
                    return {"success": False, "message": "Document not found"}
                chunks_query = chunks_query.filter(Chunk.document_id == document_id)
                total_chunks = session.query(Chunk).filter(Chunk.document_id == document_id).count()
            else:
                total_chunks = session.query(Chunk).count()

            if total_chunks == 0:
                return {"success": False, "message": "No chunks to reindex"}

            chunks = chunks_query.all()
            chunk_ids = [c.id for c in chunks]

            session.execute(delete(Embedding).where(Embedding.chunk_id.in_(chunk_ids)))
            session.flush()

            reindexed = 0
            failed = 0
            for chunk in chunks:
                embedding = self.embedder.embed(chunk.content)
                if embedding is None:
                    failed += 1
                    logger.warning(f"Failed to re-embed chunk {chunk.id}")
                    continue
                emb = Embedding(
                    chunk_id=chunk.id,
                    embedding=embedding,
                    model=self.embedder.model_name,
                    dimensions=len(embedding),
                )
                session.add(emb)
                reindexed += 1

            session.commit()
            scope = f"document {document_id}" if document_id else "all documents"
            return {
                "success": True,
                "scope": scope,
                "total": total_chunks,
                "reindexed": reindexed,
                "failed": failed,
            }

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to reindex embeddings: {e}")
            return {"success": False, "message": str(e)}
        finally:
            if should_close:
                session.close()
