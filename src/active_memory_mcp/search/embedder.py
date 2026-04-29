"""Embedding generation with Redis cache, remote API, local model, and N-gram fallback."""

import logging
import hashlib
import json
import numpy as np
from typing import List, Optional
import httpx

from ..core.config import config

logger = logging.getLogger(__name__)


class Embedder:
    """Generate embeddings with 4-tier priority: Redis cache → Remote API → Local FastEmbed → N-gram fallback."""

    def __init__(self):
        self.model_name = config.embedding.model
        self.dimensions = config.embedding.dimensions
        self._local_model = None
        self._redis = None
        self._memory_cache = {}

        if config.embedding.use_local:
            self._init_local_model()
        self._init_redis()

    def _init_local_model(self):
        """Initialize local embedding model."""
        try:
            from fastembed import TextEmbedding
            self._local_model = TextEmbedding(
                model_name="BAAI/bge-m3",
                max_length=512,
            )
            logger.info("Local FastEmbed model initialized")
        except ImportError:
            logger.warning("FastEmbed not available, will use fallback")
            self._local_model = None
        except Exception as e:
            logger.warning(f"Local model init failed: {e}, using fallback")
            self._local_model = None

    def _init_redis(self):
        """Initialize Redis connection for embedding cache."""
        try:
            import redis
            if config.cache.use_redis:
                self._redis = redis.from_url(config.cache.redis_url, decode_responses=True)
                self._redis.ping()
                logger.info("Redis embedding cache initialized")
                return True
        except Exception as e:
            logger.warning(f"Redis not available, using in-memory cache: {e}")
        self._redis = None
        self._memory_cache = {}
        return False

    def _cache_key(self, text: str) -> str:
        """Generate cache key from text."""
        return "emb:" + hashlib.md5(text.encode()).hexdigest()

    def _get_cached(self, text: str):
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
            raw = self._memory_cache.get(key)
            if raw:
                return json.loads(raw)
        return None

    def _set_cache(self, text: str, embedding: list):
        """Store embedding in cache."""
        key = self._cache_key(text)
        data = json.dumps(embedding)
        if self._redis:
            try:
                self._redis.setex(key, config.cache.ttl_seconds, data)
            except Exception as e:
                logger.warning(f"Redis set failed: {e}")
                self._redis = None
                self._memory_cache[key] = data
        else:
            # In-memory: limit to 10000 entries
            if len(self._memory_cache) > 10000:
                oldest = next(iter(self._memory_cache))
                del self._memory_cache[oldest]
            self._memory_cache[key] = data

    def _remote_embedding(self, text: str):
        """Generate embedding via external API (OpenAI-compatible format)."""
        if not config.embedding.endpoint:
            return None
        response = httpx.post(
            config.embedding.endpoint,
            json={"input": text, "model": config.embedding.model},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]

    def _remote_embed_batch(self, texts: list):
        """Batch embedding via remote API (individual calls)."""
        return [self._remote_embedding(t) for t in texts]

    def embed(self, text: str) -> Optional[List[float]]:
        """Generate embedding for a single text."""
        if not text or not text.strip():
            return None

        # 1. Check cache
        cached = self._get_cached(text)
        if cached:
            return cached

        # 2. Remote API
        try:
            embedding = self._remote_embedding(text)
            if embedding:
                self._set_cache(text, embedding)
                return embedding
        except Exception as e:
            logger.warning(f"Remote embedding failed: {e}")

        # 3. Local FastEmbed
        if self._local_model:
            try:
                embeddings = list(self._local_model.embed([text]))
                if embeddings:
                    embedding = embeddings[0].tolist()
                    self._set_cache(text, embedding)
                    return embedding
            except Exception as e:
                logger.warning(f"Local embedding failed: {e}")

        # 4. N-gram hash fallback
        embedding = self._fallback_embedding(text)
        self._set_cache(text, embedding)
        return embedding

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Generate embeddings for multiple texts."""
        return [self.embed(t) for t in texts]

    def _fallback_embedding(self, text: str) -> List[float]:
        """Deterministic N-gram character hashing fallback."""
        ngrams = self._char_ngrams(text.lower(), n=4)
        vector = np.zeros(self.dimensions, dtype=np.float32)
        for ngram in ngrams:
            idx = abs(hash(ngram)) % self.dimensions
            vector[idx] += 1.0
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        else:
            # Edge case: empty or very short text
            vector[0] = 1.0
        return vector.tolist()

    def _char_ngrams(self, text: str, n: int = 4) -> list:
        """Extract character n-grams from text."""
        if len(text) < n:
            return [text] if text else []
        return [text[i:i+n] for i in range(len(text) - n + 1)]

    def cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        a = np.array(vec_a, dtype=np.float32)
        b = np.array(vec_b, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
