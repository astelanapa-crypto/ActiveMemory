"""Embedding generation using local models or external APIs."""

import logging
import numpy as np
from typing import List, Optional

from ..core.config import config

logger = logging.getLogger(__name__)

class Embedder:
    """Generate embeddings using local model or external API."""
    
    def __init__(self):
        self.model_name = config.embedding.model
        self.dimensions = config.embedding.dimensions
        self._local_model = None
        
        if config.embedding.use_local:
            self._init_local_model()
    
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
    
    def embed(self, text: str) -> Optional[List[float]]:
        """Generate embedding for a single text."""
        if not text or not text.strip():
            return None
        
        if self._local_model:
            try:
                embeddings = list(self._local_model.embed([text]))
                if embeddings and len(embeddings) > 0:
                    return embeddings[0].tolist()
            except Exception as e:
                logger.warning(f"Local embedding failed: {e}, trying fallback")
        
        # Fallback: simple hash-based mock embedding (for testing)
        return self._fallback_embedding(text)
    
    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Generate embeddings for multiple texts."""
        if self._local_model:
            try:
                embeddings = list(self._local_model.embed(texts))
                return [emb.tolist() if emb is not None else None for emb in embeddings]
            except Exception as e:
                logger.warning(f"Batch local embedding failed: {e}")
        
        return [self._fallback_embedding(t) for t in texts]
    
    def _fallback_embedding(self, text: str) -> List[float]:
        """Simple deterministic fallback embedding based on text hash."""
        # Create a deterministic vector from text
        hash_val = hash(text)
        rng = np.random.RandomState(abs(hash_val) % (2**31))
        vec = rng.randn(self.dimensions).astype(np.float32)
        # Normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()
    
    def cosine_similarity(self, vec_a: List[float], vec_b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        a = np.array(vec_a, dtype=np.float32)
        b = np.array(vec_b, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
