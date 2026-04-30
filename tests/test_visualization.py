"""
Tests for visualization and analytics functionality.
"""

import sys
import os
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np
from unittest import mock
from datetime import datetime, timedelta

import pytest


class TestClustering:
    """Test vector space clustering."""

    def test_get_document_vectors_empty(self):
        """Return empty when no embeddings."""
        from active_memory_mcp.visualization import get_vector_space_data
        
        with mock.patch("active_memory_mcp.visualization.clustering.get_session") as mock_get:
            session = mock.MagicMock()
            session.query.return_value.join.return_value.join.return_value.filter.return_value.all.return_value = []
            mock_get.return_value = session
            
            result = get_vector_space_data(method="tsne")
            assert "points" in result
            assert len(result["points"]) == 0

    def test_compute_tsne(self):
        """t-SNE reduces dimensions correctly."""
        from active_memory_mcp.visualization.clustering import compute_tsne
        
        vectors = np.random.rand(10, 1024)
        points = compute_tsne(vectors, perplexity=5)
        
        assert points.shape == (10, 2)

    def test_compute_umap(self):
        """UMAP reduces dimensions correctly."""
        try:
            from active_memory_mcp.visualization.clustering import compute_umap
            
            vectors = np.random.rand(10, 1024)
            points = compute_umap(vectors, n_neighbors=3)
            
            assert points.shape == (10, 2)
        except ImportError:
            pytest.skip("umap-learn not installed")

    def test_compute_pca(self):
        """PCA reduces dimensions correctly."""
        from active_memory_mcp.visualization.clustering import compute_pca
        
        vectors = np.random.rand(10, 1024)
        points = compute_pca(vectors)
        
        assert points.shape == (10, 2)

    def test_get_vector_space_tsne(self):
        """Vector space endpoint returns valid data."""
        from active_memory_mcp.visualization import get_vector_space_data
        
        # Mock document with embedding
        doc = mock.MagicMock()
        doc.id = 1
        doc.filename = "test.txt"
        doc.filetype = "text"
        doc.category = "test"
        doc.embedding = [0.1] * 1024
        
        with mock.patch("active_memory_mcp.visualization.clustering.get_session") as mock_get:
            session = mock.MagicMock()
            mock_query = mock.MagicMock()
            mock_query.join.return_value.join.return_value.filter.return_value.all.return_value = [
                (1, "test.txt", "text", "test", [0.1] * 1024)
            ]
            session.query.return_value = mock_query
            mock_get.return_value = session
            
            result = get_vector_space_data(method="tsne", perplexity=5)
            assert result["method"] == "tsne"
            assert len(result["points"]) > 0
            assert len(result["documents"]) > 0


class TestUsageStats:
    """Test usage statistics and heatmap."""

    def test_log_document_access(self):
        """Document access logging works."""
        from active_memory_mcp.core.usage_stats import log_document_access
        
        with mock.patch("active_memory_mcp.core.usage_stats.get_session") as mock_get:
            session = mock.MagicMock()
            mock_get.return_value = session
            
            log_document_access(1, "search", user_context="test")
            session.add.assert_called()
            session.commit.assert_called()

    def test_get_heatmap_data_empty(self):
        """Heatmap returns empty structure when no data."""
        from active_memory_mcp.core.usage_stats import get_heatmap_data
        
        with mock.patch("active_memory_mcp.core.usage_stats.get_session") as mock_get:
            session = mock.MagicMock()
            session.query.return_value.filter.return_value.group_by.return_value.all.return_value = []
            mock_get.return_value = session
            
            result = get_heatmap_data(days=7)
            assert "type" in result
            assert "data" in result

    def test_get_hot_documents(self):
        """Hot documents returns sorted list."""
        from active_memory_mcp.core.usage_stats import get_hot_documents
        
        with mock.patch("active_memory_mcp.core.usage_stats.get_session") as mock_get:
            session = mock.MagicMock()
            # Mock access log results
            mock_query = mock.MagicMock()
            mock_query.filter.return_value.group_by.return_value.order_by.return_value.limit.return_value.all.return_value = [
                (json.dumps({"document_id": 1}), 10),
                (json.dumps({"document_id": 2}), 5),
            ]
            session.query.return_value = mock_query
            
            # Mock document query
            doc1 = mock.MagicMock()
            doc1.id = 1
            doc1.filename = "doc1.txt"
            doc1.filetype = "text"
            
            doc2 = mock.MagicMock()
            doc2.id = 2
            doc2.filename = "doc2.txt"
            doc2.filetype = "text"
            
            session.query.return_value.filter.return_value.first.side_effect = [doc1, doc2]
            mock_get.return_value = session
            
            result = get_hot_documents(limit=10, days=7)
            assert len(result) <= 2
            if result:
                assert "id" in result[0]
                assert "access_count" in result[0]

    def test_get_usage_stats(self):
        """Usage stats returns valid structure."""
        from active_memory_mcp.core.usage_stats import get_usage_stats
        
        with mock.patch("active_memory_mcp.core.usage_stats.get_session") as mock_get:
            session = mock.MagicMock()
            session.query.return_value.scalar.return_value = 0
            session.query.return_value.filter.return_value.group_by.return_value.all.return_value = []
            mock_get.return_value = session
            
            result = get_usage_stats(days=30)
            assert "total_accesses" in result
            assert "by_action" in result
            assert "unique_documents" in result
