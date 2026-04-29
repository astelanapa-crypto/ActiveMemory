import logging
from typing import List, Dict, Any
from ..core.config import config
from .embedder import Embedder
from ..storage.db import Chunk, Document, Embedding, get_backend, text

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

    def bulk_search(self, queries: List[str], top_k: int = None, filters: Dict[str, Any] = None) -> Dict[str, List[SearchResult]]:
        """Execute multiple searches in one call, reusing the same session."""
        from ..storage.db import get_session
        top_k = top_k or self.top_k
        session = get_session()
        try:
            results = {}
            for q in queries:
                results[q] = self.search(q, top_k=top_k, filters=filters, session=session)
            return results
        finally:
            session.close()

    def search_by_date(
        self,
        query: str,
        date_from: str = None,
        date_to: str = None,
        top_k: int = None,
        filters: Dict[str, Any] = None,
    ) -> List[SearchResult]:
        """Search with date range filter. Dates in ISO format (YYYY-MM-DD)."""
        date_filters = dict(filters or {})
        if date_from:
            date_filters["date_from"] = date_from
        if date_to:
            date_filters["date_to"] = date_to
        return self.search(query, top_k=top_k, filters=date_filters)

    def _vector_search(self, session, query_embedding, limit: int, filters):
        """Vector search using pgvector cosine distance or brute-force fallback."""
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

            if get_backend() == "postgresql":
                # PostgreSQL: use pgvector cosine_distance with HNSW index
                distance_expr = Embedding.embedding.cosine_distance(query_embedding)
                results = base_query.order_by(distance_expr).limit(limit).all()
                search_results = []
                for chunk, document, embedding in results:
                    # Recalculate distance for score
                    dist = session.scalar(
                        text("SELECT embedding <=> :qve FROM embeddings WHERE chunk_id = :cid"),
                        {"qve": query_embedding, "cid": embedding.chunk_id}
                    )
                    score = 1.0 - (dist if dist is not None else 0.0)
                    search_results.append(
                        SearchResult(
                            chunk.id,
                            chunk.content,
                            max(0.0, score),
                            "vector",
                            self._metadata(document, chunk),
                        )
                    )
                return search_results
            else:
                # SQLite fallback: load embeddings and compute cosine similarity in Python
                results = base_query.limit(max(limit * 10, 100)).all()
                search_results = []
                for chunk, document, embedding in results:
                    vec = embedding.embedding
                    if not vec:
                        continue
                    score = self.embedder.cosine_similarity(query_embedding, vec)
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
                # PostgreSQL FTS with ts_rank for relevance scoring
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

    def _apply_filters(self, query, filters):
        from datetime import datetime
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
        if "date_from" in filters:
            try:
                dt = datetime.fromisoformat(filters["date_from"])
                query = query.filter(Document.created_at >= dt)
            except (ValueError, TypeError):
                pass
        if "date_to" in filters:
            try:
                dt = datetime.fromisoformat(filters["date_to"])
                query = query.filter(Document.created_at <= dt)
            except (ValueError, TypeError):
                pass
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
