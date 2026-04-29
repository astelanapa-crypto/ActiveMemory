import logging
from typing import List, Dict, Any
from sqlalchemy import or_
from ..core.config import config
from .embedder import Embedder
from ..storage.db import Chunk, Document, Embedding, deserialize_embedding

logger = logging.getLogger(__name__)

class SearchResult:
    def __init__(self, chunk_id: int, content: str, score: float, source: str, metadata: Dict[str, Any] = None):
        self.chunk_id = chunk_id
        self.content = content
        self.score = score
        self.source = source
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "score": self.score,
            "source": self.source,
            "metadata": self.metadata,
        }

class HybridSearcher:
    def __init__(self):
        self.embedder = Embedder()
        self.top_k = config.search.top_k
        self.hybrid_alpha = config.search.hybrid_alpha
        self.min_score = config.search.min_score_threshold

    def search(self, query: str, top_k: int = None, filters: Dict[str, Any] = None, session=None) -> List[SearchResult]:
        from ..storage.db import get_session
        top_k = top_k or self.top_k
        should_close = False
        if session is None:
            session = get_session()
            should_close = True
        try:
            query_embedding = self.embedder.embed(query)
            vector_results = self._vector_search(session, query_embedding, top_k * 3, filters)
            keyword_results = self._keyword_search(session, query, top_k * 3, filters)
            combined = self._combine_results(vector_results, keyword_results, top_k)
            combined.sort(key=lambda r: r.score, reverse=True)
            final_results = [r for r in combined if r.score >= self.min_score][:top_k]
            return final_results
        finally:
            if should_close:
                session.close()

    def _vector_search(self, session, query_embedding, limit: int, filters):
        if not query_embedding:
            return []
        try:
            base_query = (
                session.query(Chunk, Document, Embedding)
                .join(Document, Chunk.document_id == Document.id)
                .join(Embedding, Chunk.id == Embedding.chunk_id)
                .filter(Embedding.embedding.isnot(None))
            )
            if filters:
                base_query = self._apply_filters(base_query, filters)
            search_results = []
            for chunk, document, embedding in base_query.limit(max(limit * 10, 100)).all():
                vector = deserialize_embedding(embedding.embedding)
                if not vector:
                    continue
                score = self.embedder.cosine_similarity(query_embedding, vector)
                search_results.append(
                    SearchResult(
                        chunk.id,
                        chunk.content,
                        score,
                        "vector",
                        self._metadata(document, chunk),
                    )
                )
            search_results.sort(key=lambda r: r.score, reverse=True)
            return search_results[:limit]
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def _keyword_search(self, session, query: str, limit: int, filters):
        try:
            tokens = query.lower().split()
            if not tokens:
                return []
            conditions = []
            for token in tokens:
                if len(token) >= 2:
                    conditions.append(Chunk.content.ilike(f"%{token}%"))
            if not conditions:
                return []
            base_query = session.query(Chunk, Document).join(Document, Chunk.document_id == Document.id)
            if filters:
                base_query = self._apply_filters(base_query, filters)
            results = base_query.filter(or_(*conditions)).limit(limit).all()
            return [
                SearchResult(chunk.id, chunk.content, 0.5, "keyword", self._metadata(document, chunk))
                for chunk, document in results
            ]
        except Exception as e:
            logger.error(f"Keyword search failed: {e}")
            return []

    def _apply_filters(self, query, filters):
        from ..storage.db import Document
        if not filters:
            return query
        if "filetype" in filters:
            query = query.filter(Document.filetype == filters["filetype"])
        if "filename" in filters:
            query = query.filter(Document.filename.ilike(f"%{filters['filename']}%"))
        if "category" in filters:
            query = query.filter(Document.category == filters["category"])
        if "pinned" in filters:
            query = query.filter(Document.pinned == bool(filters["pinned"]))
        return query

    def _metadata(self, document: Document, chunk: Chunk) -> Dict[str, Any]:
        metadata = dict(document.metadata_ or {})
        metadata.update(chunk.metadata_ or {})
        metadata.update(
            {
                "document_id": document.id,
                "filename": document.filename,
                "filetype": document.filetype,
                "category": document.category,
                "importance": document.importance,
                "pinned": document.pinned,
            }
        )
        return metadata

    def _combine_results(self, vector_results, keyword_results, top_k):
        combined_dict = {}
        for rank, result in enumerate(vector_results):
            if result.chunk_id not in combined_dict:
                combined_dict[result.chunk_id] = result
            else:
                combined_dict[result.chunk_id].score = (1 - self.hybrid_alpha) * result.score + self.hybrid_alpha * combined_dict[result.chunk_id].score
                combined_dict[result.chunk_id].source = "hybrid"
        for rank, result in enumerate(keyword_results):
            if result.chunk_id not in combined_dict:
                combined_dict[result.chunk_id] = result
            else:
                combined_dict[result.chunk_id].score = self.hybrid_alpha * result.score + (1 - self.hybrid_alpha) * combined_dict[result.chunk_id].score
                combined_dict[result.chunk_id].source = "hybrid"
        return list(combined_dict.values())
