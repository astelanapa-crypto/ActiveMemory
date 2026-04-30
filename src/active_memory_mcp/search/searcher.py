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

    def search(self, query: str, top_k: int = None, mode: str = "hybrid", filters: Dict[str, Any] = None, session=None) -> List[SearchResult]:
        from ..storage.db import get_session
        top_k = top_k or self.top_k
        should_close = False
        if session is None:
            session = get_session()
            should_close = True
        try:
            if mode == "dense":
                query_embedding = self.embedder.embed_dense(query)
                results = self._vector_search(session, query_embedding, top_k * 3, filters)
            elif mode == "multi-vector":
                query_multi = self.embedder.embed_multi_vector(query)
                results = self._search_multi_vector(session, query_multi, top_k * 3)
            elif mode == "hybrid":
                query_embedding = self.embedder.embed_dense(query)
                query_multi = self.embedder.embed_multi_vector(query)
                vector_results = self._vector_search(session, query_embedding, top_k * 3, filters)
                multi_results = self._search_multi_vector(session, query_multi, top_k * 3)
                keyword_results = self._keyword_search(session, query, top_k * 3, filters)
                results = self._combine_results_with_mode(
                    vector_results, multi_results, keyword_results, top_k
                )
            else:
                raise ValueError(f"Unknown search mode: {mode}")

            results = [r for r in results if r.score >= self.min_score][:top_k]
            return results
        finally:
            if should_close:
                session.close()

    def bulk_search(self, queries: List[str], top_k: int = None, filters: Dict[str, Any] = None, mode: str = "hybrid") -> Dict[str, List[SearchResult]]:
        """Execute multiple searches in one call, reusing the same session."""
        from ..storage.db import get_session
        top_k = top_k or self.top_k
        session = get_session()
        try:
            results = {}
            for q in queries:
                results[q] = self.search(q, top_k=top_k, filters=filters, session=session, mode=mode)
            return results
        finally:
            session.close()

    def _search_multi_vector(self, session, query_vecs: List[List[float]], limit: int) -> List[SearchResult]:
        """ColBERT-style late interaction search."""
        if not query_vecs:
            return []
        try:
            embeddings = session.query(Embedding).filter(
                Embedding.multi_vector.isnot(None)
            ).limit(1000).all()
            
            results = []
            for emb in embeddings:
                doc_multi = emb.multi_vector  # JSON: [tokens][1024]
                if not doc_multi:
                    continue
                score = self.embedder.colbert_score(query_vecs, doc_multi)
                chunk = session.query(Chunk).filter(Chunk.id == emb.chunk_id).first()
                document = chunk.document if chunk else None
                results.append(
                    SearchResult(
                        emb.chunk_id,
                        chunk.content if chunk else "",
                        score,
                        "multi-vector",
                        self._metadata(document, chunk) if document else {}
                    )
                )
            results.sort(key=lambda r: r.score, reverse=True)
            return results[:limit]
        except Exception as e:
            logger.warning(f"Multi-vector search failed: {e}")
            return []

    def _combine_results_with_mode(
        self, vector_results, multi_results, keyword_results, top_k
    ):
        """Combine results from multiple search modes."""
        combined_dict = {}
        for r in vector_results:
            if r.chunk_id not in combined_dict:
                combined_dict[r.chunk_id] = r
            else:
                combined_dict[r.chunk_id].score = (1 - self.hybrid_alpha) * r.score + self.hybrid_alpha * combined_dict[r.chunk_id].score
                combined_dict[r.chunk_id].source = "hybrid"
        for r in multi_results:
            if r.chunk_id not in combined_dict:
                combined_dict[r.chunk_id] = r
            else:
                combined_dict[r.chunk_id].score = (1 - self.hybrid_alpha) * combined_dict[r.chunk_id].score + self.hybrid_alpha * r.score
                combined_dict[r.chunk_id].source = "hybrid"
        for r in keyword_results:
            if r.chunk_id not in combined_dict:
                combined_dict[r.chunk_id] = r
            else:
                combined_dict[r.chunk_id].score = self.hybrid_alpha * r.score + (1 - self.hybrid_alpha) * combined_dict[r.chunk_id].score
                combined_dict[r.chunk_id].source = "hybrid"
        results = list(combined_dict.values())
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def search_by_date(
        self,
        query: str,
        date_from: str = None,
        date_to: str = None,
        top_k: int = None,
        filters: Dict[str, Any] = None,
        mode: str = "hybrid",
    ) -> List[SearchResult]:
        """Search with date range filter. Dates in ISO format (YYYY-MM-DD)."""
        date_filters = dict(filters or {})
        if date_from:
            date_filters["date_from"] = date_from
        if date_to:
            date_filters["date_to"] = date_to
        return self.search(query, top_k=top_k, filters=date_filters, mode=mode)

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
                import numpy as np
                results = base_query.limit(max(limit * 10, 100)).all()
                search_results = []
                for chunk, document, embedding in results:
                    vec = embedding.embedding
                    if vec is None:
                        continue
                    if isinstance(vec, np.ndarray):
                        if vec.size == 0:
                            continue
                    elif isinstance(vec, list):
                        if len(vec) == 0:
                            continue
                    else:
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

    def _apply_priority_boost(self, results: List[SearchResult]) -> List[SearchResult]:
        """Boost scores for pinned and high-importance documents."""
        boost_importance = config.search.context_boost_importance
        boost_pinned = config.search.context_boost_pinned

        for r in results:
            importance = r.metadata.get("importance", 3)
            pinned = r.metadata.get("pinned", False)

            importance_boost = boost_importance * (4 - importance) / 3.0
            pinned_boost = boost_pinned if pinned else 0.0

            r.score = min(1.0, r.score + importance_boost + pinned_boost)
            if pinned:
                r.source = f"{r.source}+boost"

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _count_tokens(self, text: str) -> int:
        """Rough token count: ~4 chars per token for English, ~2 for CJK."""
        cjk_count = sum(1 for c in text if ord(c) > 0x2E80)
        ascii_count = len(text) - cjk_count
        return max(1, cjk_count // 2 + ascii_count // 4)

    def smart_context(
        self,
        query: str,
        max_tokens: int = None,
        mode: str = "hybrid",
        filters: Dict[str, Any] = None,
        prioritize: bool = True,
    ) -> List[SearchResult]:
        """Search with token budget limit instead of count.

        Returns results that fit within max_tokens, prioritizing
        pinned and high-importance documents.
        """
        max_tokens = max_tokens or config.search.context_max_tokens
        results = self.search(query, top_k=50, mode=mode, filters=filters)


        if prioritize:
            results = self._apply_priority_boost(results)

        budget_results = []
        used_tokens = 0

        for r in results:
            chunk_tokens = self._count_tokens(r.content)
            if used_tokens + chunk_tokens > max_tokens:
                break
            used_tokens += chunk_tokens
            r.metadata["token_count"] = chunk_tokens
            budget_results.append(r)

        return budget_results

    def get_context(
        self,
        query: str = None,
        max_tokens: int = None,
        mode: str = "hybrid",
        include_pinned: bool = True,
        include_important: bool = True,
        importance_threshold: int = 2,
    ) -> List[SearchResult]:
        """Auto-collect important context: pinned + high importance docs.

        If query is provided, combines relevant search results with
        important static context.
        """
        max_tokens = max_tokens or config.search.context_max_tokens
        from ..storage.db import get_session, Document
        session = get_session()
        try:
            important_chunks = []
            doc_filter = []
            if include_pinned:
                doc_filter.append(Document.pinned.is_(True))
            if include_important:
                doc_filter.append(Document.importance <= importance_threshold)

            if doc_filter:
                from sqlalchemy import or_
                docs = session.query(Document).filter(or_(*doc_filter)).all()

                for doc in docs:
                    for chunk in doc.chunks:
                        sr = SearchResult(
                            chunk.id,
                            chunk.content,
                            1.0 if doc.pinned else 0.8,
                            "context+pinned" if doc.pinned else "context+important",
                            self._metadata(doc, chunk),
                        )
                        sr.metadata["token_count"] = self._count_tokens(chunk.content)
                        important_chunks.append(sr)

                important_chunks.sort(key=lambda r: r.score, reverse=True)

            if query:
                search_results = self.search(query, top_k=50, mode=mode)
                search_results = self._apply_priority_boost(search_results)
                seen = {r.chunk_id for r in important_chunks}
                for sr in search_results:
                    if sr.chunk_id not in seen:
                        sr.metadata["token_count"] = self._count_tokens(sr.content)
                        important_chunks.append(sr)
                        seen.add(sr.chunk_id)

            budget_results = []
            used_tokens = 0
            for r in important_chunks:
                chunk_tokens = r.metadata.get("token_count", self._count_tokens(r.content))
                if used_tokens + chunk_tokens > max_tokens:
                    break
                used_tokens += chunk_tokens
                budget_results.append(r)

            return budget_results
        finally:
            session.close()

    def rerank(
        self,
        results: List[SearchResult],
        query: str,
        method: str = "recency",
    ) -> List[SearchResult]:
        """Re-rank search results using different strategies."""
        if not results:
            return []

        if method == "recency":
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for r in results:
                created = r.metadata.get("created_at")
                if created and isinstance(created, datetime):
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    age_hours = max(0.01, (now - created).total_seconds() / 3600)
                    recency_score = 1.0 / (1.0 + age_hours / 168.0)
                    r.score = r.score * 0.7 + recency_score * 0.3
                    r.source = f"{r.source}+recency"

        elif method == "length":
            avg_len = sum(len(r.content) for r in results) / len(results)
            for r in results:
                length_ratio = min(len(r.content) / max(avg_len, 1), 2.0) / 2.0
                r.score = r.score * 0.7 + length_ratio * 0.3
                r.source = f"{r.source}+length"

        elif method == "diversity":
            diverse = [results[0]]
            for r in results[1:]:
                max_sim = max(
                    self._text_similarity(r.content, d.content)
                    for d in diverse
                )
                if max_sim < 0.7:
                    r.score *= (1.0 - max_sim * 0.5)
                    r.source = f"{r.source}+diversity"
                    diverse.append(r)
            results = diverse

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def _text_similarity(self, text1: str, text2: str) -> float:
        """Quick text similarity using Jaccard on word sets."""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union)
