"""Visualization module for ActiveMemory.

Provides dimensionality reduction (t-SNE, UMAP) for vector space visualization.
"""

import numpy as np
from typing import List, Tuple, Optional
from ..storage.db import get_session, Document, Chunk, Embedding
from ..core.config import config


def get_document_vectors() -> Tuple[List[dict], np.ndarray]:
    """Fetch all document vectors for clustering.
    
    Returns:
        Tuple of (document metadata list, vectors array)
    """
    session = get_session()
    try:
        # Get documents with embeddings
        results = session.query(
            Document.id,
            Document.filename,
            Document.filetype,
            Document.category,
            Embedding.embedding,
        ).join(
            Chunk, Document.id == Chunk.document_id
        ).join(
            Embedding, Chunk.id == Embedding.chunk_id
        ).filter(
            Embedding.embedding.isnot(None)
        ).all()
        
        if not results:
            return [], np.array([])
        
        docs_meta = []
        vectors = []
        
        for doc_id, filename, filetype, category, embedding in results:
            # Handle both Vector type (list) and Text (parse JSON)
            if isinstance(embedding, str):
                import json
                vec = json.loads(embedding)
            else:
                vec = list(embedding) if embedding else None
            
            if vec:
                docs_meta.append({
                    "id": doc_id,
                    "filename": filename,
                    "filetype": filetype,
                    "category": category,
                })
                vectors.append(vec)
        
        return docs_meta, np.array(vectors)
    finally:
        session.close()


def compute_tsne(vectors: np.ndarray, perplexity: int = 30, n_iter: int = 1000) -> np.ndarray:
    """Compute t-SNE dimensionality reduction.
    
    Args:
        vectors: Input vectors (n_samples, n_features)
        perplexity: Perplexity parameter for t-SNE
        n_iter: Number of iterations
        
    Returns:
        Array of shape (n_samples, 2) with 2D coordinates
    """
    from sklearn.manifold import TSNE
    
    if len(vectors) < 2:
        # Not enough samples for t-SNE
        return np.zeros((len(vectors), 2))
    
    # Adjust perplexity if needed
    actual_perplexity = min(perplexity, len(vectors) - 1)
    
    tsne = TSNE(
        n_components=2,
        perplexity=actual_perplexity,
        max_iter=n_iter,
        random_state=42,
    )
    return tsne.fit_transform(vectors)


def compute_umap(vectors: np.ndarray, n_neighbors: int = 15, min_dist: float = 0.1) -> np.ndarray:
    """Compute UMAP dimensionality reduction.
    
    Args:
        vectors: Input vectors (n_samples, n_features)
        n_neighbors: Number of neighbors for UMAP
        min_dist: Minimum distance for UMAP
        
    Returns:
        Array of shape (n_samples, 2) with 2D coordinates
    """
    try:
        from umap import UMAP
    except ImportError:
        # Fallback to PCA if umap-learn not installed
        return compute_pca(vectors)
    
    if len(vectors) < 2:
        return np.zeros((len(vectors), 2))
    
    # Adjust n_neighbors if needed
    actual_neighbors = min(n_neighbors, len(vectors) - 1)
    
    umap = UMAP(
        n_components=2,
        n_neighbors=actual_neighbors,
        min_dist=min_dist,
        random_state=42,
    )
    return umap.fit_transform(vectors)


def compute_pca(vectors: np.ndarray, n_components: int = 2) -> np.ndarray:
    """Compute PCA dimensionality reduction (fallback).
    
    Args:
        vectors: Input vectors (n_samples, n_features)
        n_components: Number of components
        
    Returns:
        Array of shape (n_samples, n_components)
    """
    from sklearn.decomposition import PCA
    
    if len(vectors) < 2:
        return np.zeros((len(vectors), n_components))
    
    pca = PCA(n_components=n_components, random_state=42)
    return pca.fit_transform(vectors)


def get_vector_space_data(method: str = "tsne", **kwargs) -> dict:
    """Get vector space visualization data.
    
    Args:
        method: "tsne", "umap", or "pca"
        **kwargs: Method-specific parameters
        
    Returns:
        Dict with points, metadata, and method info
    """
    docs_meta, vectors = get_document_vectors()
    
    if len(vectors) == 0:
        return {
            "method": method,
            "points": [],
            "documents": [],
            "message": "No embeddings found",
        }
    
    # Compute dimensionality reduction
    if method == "tsne":
        perplexity = kwargs.get("perplexity", config.visualization.clustering_perplexity)
        points = compute_tsne(vectors, perplexity=perplexity)
    elif method == "umap":
        n_neighbors = kwargs.get("n_neighbors", 15)
        min_dist = kwargs.get("min_dist", 0.1)
        points = compute_umap(vectors, n_neighbors=n_neighbors, min_dist=min_dist)
    else:  # pca
        points = compute_pca(vectors)
    
    return {
        "method": method,
        "points": points.tolist(),
        "documents": docs_meta,
        "total_points": len(points),
    }
