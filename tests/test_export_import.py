"""
Tests for export/import functionality.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import json
import csv
import io
from unittest import mock
from datetime import datetime

import pytest


class TestExporter:
    """Test export functions."""

    def _setup_mock_session(self, docs):
        """Setup mock session with documents."""
        session = mock.MagicMock()
        
        # Mock the query chain: session.query(Document).order_by(...).all()
        query_mock = mock.MagicMock()
        query_mock.order_by.return_value.all.return_value = docs
        session.query.return_value = query_mock
        
        return session

    def test_export_all_json_structure(self):
        """Test JSON export returns correct structure."""
        from active_memory_mcp.core.exporter import export_all_json
        
        # Create mock doc
        doc = mock.MagicMock()
        doc.id = 1
        doc.filename = "test.txt"
        doc.filetype = "text"
        doc.filesize = 100
        doc.content = "Test content"
        doc.metadata_ = json.dumps({"source": "test"})
        doc.created_at = datetime(2026, 4, 30)
        doc.updated_at = datetime(2026, 4, 30)
        doc.chunks = []
        doc.title = None
        doc.author = None
        doc.category = None
        doc.source = None
        doc.importance = 0
        doc.pinned = False
        doc.encrypted = False
        
        session = self._setup_mock_session([doc])
        
        with mock.patch("active_memory_mcp.core.exporter.get_session", return_value=session):
            data = export_all_json(include_embeddings=True)
            
            assert isinstance(data, dict)
            assert "documents" in data
            assert "exported_at" in data
            assert "version" in data

    def test_export_all_json_content(self):
        """Test JSON export contains correct data."""
        from active_memory_mcp.core.exporter import export_all_json
        
        # Create mock doc with chunk
        chunk = mock.MagicMock()
        chunk.id = 1
        chunk.chunk_index = 0
        chunk.content = "Chunk content"
        chunk.token_count = 10
        chunk.metadata_ = json.dumps({})
        
        doc = mock.MagicMock()
        doc.id = 1
        doc.filename = "test.txt"
        doc.filetype = "text"
        doc.filesize = 100
        doc.content = "Test content"
        doc.metadata_ = json.dumps({"source": "test"})
        doc.created_at = datetime(2026, 4, 30)
        doc.updated_at = datetime(2026, 4, 30)
        doc.chunks = [chunk]
        doc.title = None
        doc.author = None
        doc.category = None
        doc.source = None
        doc.importance = 0
        doc.pinned = False
        doc.encrypted = False
        
        session = self._setup_mock_session([doc])
        
        # Mock embedding query
        emb_query = mock.MagicMock()
        emb_query.filter.return_value.first.return_value = None
        session.query.side_effect = [emb_query] if False else [session.query.return_value, emb_query]
        
        with mock.patch("active_memory_mcp.core.exporter.get_session", return_value=session):
            data = export_all_json(include_embeddings=True)
            
            assert len(data["documents"]) == 1
            exported_doc = data["documents"][0]
            assert exported_doc["filename"] == "test.txt"
            assert exported_doc["filetype"] == "text"

    def test_export_all_csv_structure(self):
        """Test CSV export returns correct structure."""
        from active_memory_mcp.core.exporter import export_all_csv
        
        # Create mock doc
        doc = mock.MagicMock()
        doc.id = 1
        doc.filename = "test.txt"
        doc.filetype = "text"
        doc.filesize = 100
        doc.content = "Test content"
        doc.metadata_ = json.dumps({"source": "test"})
        doc.created_at = datetime(2026, 4, 30)
        doc.updated_at = datetime(2026, 4, 30)
        doc.chunks = []
        doc.title = None
        doc.author = None
        doc.category = None
        doc.source = None
        doc.importance = 0
        doc.pinned = False
        doc.encrypted = False
        
        session = self._setup_mock_session([doc])
        
        with mock.patch("active_memory_mcp.core.exporter.get_session", return_value=session):
            csv_content = export_all_csv()
            
            assert isinstance(csv_content, str)
            assert len(csv_content) > 0

    def test_export_all_csv_content(self):
        """Test CSV export contains correct data."""
        from active_memory_mcp.core.exporter import export_all_csv
        
        # Create mock doc with chunk
        chunk = mock.MagicMock()
        chunk.chunk_index = 0
        chunk.content = "Chunk content"
        chunk.token_count = 10
        
        doc = mock.MagicMock()
        doc.id = 1
        doc.filename = "test.txt"
        doc.filetype = "text"
        doc.filesize = 100
        doc.content = "Test content"
        doc.metadata_ = json.dumps({"source": "test"})
        doc.created_at = datetime(2026, 4, 30)
        doc.updated_at = datetime(2026, 4, 30)
        doc.chunks = [chunk]
        doc.title = None
        doc.author = None
        doc.category = None
        doc.source = None
        doc.importance = 0
        doc.pinned = False
        doc.encrypted = False
        
        session = self._setup_mock_session([doc])
        
        with mock.patch("active_memory_mcp.core.exporter.get_session", return_value=session):
            csv_content = export_all_csv()
            
            reader = csv.DictReader(io.StringIO(csv_content))
            rows = list(reader)
            
            assert len(rows) >= 1
            assert rows[0]["filename"] == "test.txt"


class TestImporter:
    """Test import functions."""

    @pytest.fixture
    def sample_json_data(self):
        return {
            "documents": [
                {
                    "filename": "imported.txt",
                    "filetype": "text",
                    "filesize": 50,
                    "content": "Imported content",
                    "metadata": {"source": "import"},
                    "chunks": [
                        {
                            "content": "Imported content",
                            "embedding": [0.1] * 1024,
                            "embedding_model": "test",
                            "chunk_index": 0,
                            "token_count": 5,
                            "metadata": {},
                        }
                    ],
                }
            ]
        }

    @pytest.fixture
    def temp_json_file(self, tmp_path, sample_json_data):
        file_path = tmp_path / "import.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(sample_json_data, f)
        return file_path

    def test_validate_import_data_valid(self, sample_json_data):
        from active_memory_mcp.core.importer import validate_import_data
        
        result = validate_import_data(sample_json_data)
        
        assert result["valid"] is True
        assert "stats" in result

    def test_validate_import_data_invalid(self):
        from active_memory_mcp.core.importer import validate_import_data
        
        result = validate_import_data({"documents": "not a list"})
        
        assert result["valid"] is False
        assert "errors" in result

    def test_import_from_file_json_merge(self, temp_json_file):
        from active_memory_mcp.core.importer import import_from_file
        
        with mock.patch("active_memory_mcp.core.importer.get_session") as mock_get:
            session = mock.MagicMock()
            mock_get.return_value = session
            
            result = import_from_file(str(temp_json_file), mode="merge")
            
            assert "imported_documents" in result
            session.add.assert_called()
            session.commit.assert_called()

    def test_import_from_file_json_replace(self, temp_json_file):
        from active_memory_mcp.core.importer import import_from_file
        
        with mock.patch("active_memory_mcp.core.importer.get_session") as mock_get:
            session = mock.MagicMock()
            mock_get.return_value = session
            
            result = import_from_file(str(temp_json_file), mode="replace")
            
            assert "imported_documents" in result
            session.query.return_value.delete.assert_called()

    def test_import_from_file_not_found(self):
        from active_memory_mcp.core.importer import import_from_file
        
        result = import_from_file("/nonexistent/file.json")
        
        assert "errors" in result
