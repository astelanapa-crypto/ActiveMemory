"""Embedding generation with BGE-M3 multi-vector (ColBERT) support.

Uses native /embedding endpoint for multi-vector and /v1/embeddings for dense.
Implements chunking strategy for optimal performance (30-50 tokens per chunk).
"""

import logging
import json
import hashlib
import httpx
from typing import List, Dict, Any, Optional
from collections import OrderedDict
from ..core.config import config

logger = logging.getLogger(__name__)


class LRUCache:
    """LRU cache with capacity limit."""
    
    def __init__(self, capacity: int = 10000):
        self.cache = OrderedDict()
        self.capacity = capacity
    
    def get(self, key: str) -> Optional[str]:
        """Get item from cache (moves to end if found)."""
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]
    
    def set(self, key: str, value: str):
        """Set item in cache (evicts LRU if at capacity)."""
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
    
    def __contains__(self, key: str) -> bool:
        return key in self.cache
    
    def __len__(self) -> int:
        return len(self.cache)


class Embedder:
    """BGE-M3 embedder with multi-vector (ColBERT) and chunking support."""

    def __init__(self):
        self.config = config.embedding
        self.session = httpx.Client(timeout=60.0)
        self._memory_cache = LRUCache(capacity=10000)
        self._init_redis()

    def _init_redis(self):
        """Initialize Redis connection for embedding cache."""
        self._redis = None
        try:
            import redis
            if config.cache.use_redis:
                self._redis = redis.from_url(config.cache.redis_url, decode_responses=True)
                self._redis.ping()
                logger.info("Redis embedding cache initialized")
        except Exception as e:
            logger.warning(f"Redis not available, using in-memory cache: {e}")
            self._redis = None

    def _cache_key(self, text: str) -> str:
        """Generate cache key from text."""
        import hashlib
        return "emb:" + hashlib.md5(text.encode()).hexdigest()

    def _get_cached(self, text: str) -> Optional[Dict]:
        """Get cached embedding from Redis or in-memory cache."""
        key = self._cache_key(text)
        if self._redis:
            try:
                data = self._redis.get(key)
                if data:
                    return json.loads(data)
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")
                self._redis = None
        else:
            if key in self._memory_cache:
                return json.loads(self._memory_cache.get(key))
        return None

    def _set_cache(self, text: str, data: Dict):
        """Store embedding data in cache."""
        key = self._cache_key(text)
        serialized = json.dumps(data)
        if self._redis:
            try:
                self._redis.setex(key, config.cache.ttl_seconds, serialized)
            except Exception as e:
                logger.warning(f"Redis set failed: {e}")
                self._redis = None
                self._memory_cache.set(key, serialized)
        else:
            self._memory_cache.set(key, serialized)

    def embed_dense(self, text: str) -> List[float]:
        """Get dense embedding via native /embedding endpoint.
        Averages multi-vector tokens to produce a single dense vector.
        """
        if not text or not text.strip():
            return []
        # Use native endpoint with pooling=none, then average tokens
        response = self.session.post(
            self.config.endpoint_native,
            json={"content": text}
        )
        response.raise_for_status()
        data = response.json()
        # data: [{"index": 0, "embedding": [[v,v,...], ...]}]
        if isinstance(data, list) and len(data) > 0:
            multi_vec = data[0].get("embedding", [])
            if multi_vec and isinstance(multi_vec[0], list):
                # Average all token vectors to get dense (mean pooling)
                import numpy as np
                arr = np.array(multi_vec, dtype=np.float32)
                dense = np.mean(arr, axis=0).tolist()
                return dense
        return []

    def embed_multi_vector(self, text: str) -> List[List[float]]:
        """Get multi-vector (ColBERT) via native /embedding endpoint.
        Returns matrix: [tokens][1024] for ColBERT late interaction.
        """
        if not text or not text.strip():
            return []
        response = self.session.post(
            self.config.endpoint_native,
            json={"content": text}
        )
        response.raise_for_status()
        data = response.json()
        # Response: [{"index": 0, "embedding": [[v,v,...], ...]}]
        if isinstance(data, list) and len(data) > 0:
            return data[0].get("embedding", [])
        return []

    def embed_with_chunking(self, text: str) -> Dict[str, Any]:
        """Smart embedding: chunk if needed, return optimal representation."""
        if not text or not text.strip():
            return {"dense": [], "multi_vector": [], "token_count": 0, "chunk_count": 0}

        # Check cache first
        cached = self._get_cached(text)
        if cached:
            return cached

        estimated_tokens = len(text.split()) * 1.3

        if estimated_tokens <= self.config.optimal_chunk_tokens:
            # Short text: return multi-vector directly
            multi_vec = self.embed_multi_vector(text)
            result = {
                "dense": self.embed_dense(text),
                "multi_vector": multi_vec,
                "token_count": len(multi_vec),
                "chunk_count": 1
            }
        else:
            # Long text: chunk into optimal sizes
            chunks = self._chunk_text(text)
            chunk_embeddings = [self.embed_multi_vector(chunk) for chunk in chunks]
            result = {
                "dense": self.embed_dense(text),  # Fallback to full text dense
                "multi_vector_chunks": chunk_embeddings,
                "token_count": sum(len(v) for v in chunk_embeddings),
                "chunk_count": len(chunks)
            }

        self._set_cache(text, result)
        return result

    def _chunk_text(self, text: str, max_tokens: int = None) -> List[str]:
        """Chunk text into optimal sizes."""
        if max_tokens is None:
            max_tokens = self.config.optimal_chunk_tokens
        words = text.split()
        chunks = []
        for i in range(0, len(words), max_tokens):
            chunks.append(" ".join(words[i:i + max_tokens]))
        return chunks

    def cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        import numpy as np
        a = np.array(vec_a, dtype=np.float32)
        b = np.array(vec_b, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def colbert_score(self, query_vecs: List[List[float]], doc_vecs: List[List[float]]) -> float:
        """Compute ColBERT late interaction score."""
        if not query_vecs or not doc_vecs:
            return 0.0
        scores = []
        for q_vec in query_vecs:
            # Max similarity with any document token
            max_sim = max(self.cosine_similarity(q_vec, d_vec) for d_vec in doc_vecs)
            scores.append(max_sim)
        return sum(scores) / len(scores) if scores else 0.0
