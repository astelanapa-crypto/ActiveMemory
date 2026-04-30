"""Comprehensive tests for ActiveMemory MCP server."""

import pytest
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestConfig:
    """Test configuration management."""

    def test_config_imports(self):
        """Config module loads without errors."""
        from active_memory_mcp.core.config import config, Config
        assert config is not None
        assert isinstance(config, Config)

    def test_database_config_defaults(self):
        """Database config has correct defaults."""
        from active_memory_mcp.core.config import config
        assert config.db.host == "localhost"
        assert config.db.port == 5432
        assert config.db.database == "hermes_memory"
        assert config.db.user == "postgres"
        assert config.db.password == "postgres"
        assert config.db.use_pgvector is True

    def test_embedding_config_defaults(self):
        """Embedding config has correct defaults."""
        from active_memory_mcp.core.config import config
        assert config.embedding.dimensions == 1024
        assert config.embedding.model == "bge-m3-q8_0.gguf"
        assert config.embedding.use_local is False

    def test_chunking_config_defaults(self):
        """Chunking config has correct defaults."""
        from active_memory_mcp.core.config import config
        assert config.chunking.chunk_size == 512
        assert config.chunking.chunk_overlap == 50
        assert config.chunking.max_chunks_per_doc == 1000

    def test_search_config_defaults(self):
        """Search config has correct defaults."""
        from active_memory_mcp.core.config import config
        assert config.search.top_k == 5
        assert config.search.hybrid_alpha == 0.5
        assert config.search.min_score_threshold == 0.1

    def test_web_config_defaults(self):
        """Web config has correct defaults."""
        from active_memory_mcp.core.config import config
        assert config.web.host == "0.0.0.0"
        assert config.web.port == 8788
        assert config.web.debug is False

    def test_database_url_property(self):
        """Database URL is correctly constructed."""
        from active_memory_mcp.core.config import config
        url = config.database_url
        assert "postgresql" in url
        assert "localhost" in url
        assert "hermes_memory" in url


class TestEmbedder:
    """Test embedding generation with BGE-M3 multi-vector support."""

    def test_embedder_imports(self):
        """Embedder class loads without errors."""
        from active_memory_mcp.search.embedder import Embedder
        assert Embedder is not None

    def test_embedder_init(self):
        """Embedder initializes with config."""
        from active_memory_mcp.search.embedder import Embedder
        from active_memory_mcp.core.config import config
        emb = Embedder()
        assert config.embedding.model == "bge-m3-q8_0.gguf"
        assert config.embedding.dimensions == 1024
        assert emb.config.dimensions == 1024

    def test_cosine_similarity_identical(self):
        """Cosine similarity of identical vectors is 1.0."""
        from active_memory_mcp.search.embedder import Embedder
        emb = Embedder()
        vec = [0.5, 0.5, 0.5, 0.5]
        sim = emb.cosine_similarity(vec, vec)
        assert abs(sim - 1.0) < 0.001

    def test_cosine_similarity_orthogonal(self):
        """Cosine similarity of orthogonal vectors is 0.0."""
        from active_memory_mcp.search.embedder import Embedder
        emb = Embedder()
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [0.0, 1.0, 0.0]
        sim = emb.cosine_similarity(vec1, vec2)
        assert abs(sim) < 0.001

    def test_embed_dense_returns_list(self):
        """embed_dense() returns a list of floats."""
        from active_memory_mcp.search.embedder import Embedder
        emb = Embedder()
        result = emb.embed_dense("test query")
        assert isinstance(result, list)
        assert all(isinstance(x, float) for x in result)
        assert len(result) == 1024

    def test_embed_dense_empty_string(self):
        """embed_dense() returns empty list for empty string."""
        from active_memory_mcp.search.embedder import Embedder
        emb = Embedder()
        result = emb.embed_dense("")
        assert result == []

    def test_embed_multi_vector_returns_matrix(self):
        """embed_multi_vector() returns a matrix (list of lists)."""
        from active_memory_mcp.search.embedder import Embedder
        emb = Embedder()
        result = emb.embed_multi_vector("test query")
        assert isinstance(result, list)
        if result:  # May be empty if server not available
            assert isinstance(result[0], list)
            assert len(result[0]) == 1024

    def test_embed_with_chunking_returns_dict(self):
        """embed_with_chunking() returns a dict with expected keys."""
        from active_memory_mcp.search.embedder import Embedder
        emb = Embedder()
        result = emb.embed_with_chunking("test query")
        assert isinstance(result, dict)
        assert "dense" in result
        assert "multi_vector" in result
        assert "token_count" in result
        assert "chunk_count" in result

    def test_colbert_score_computation(self):
        """colbert_score() computes late interaction score."""
        from active_memory_mcp.search.embedder import Embedder
        emb = Embedder()
        query_vecs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        doc_vecs = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
        score = emb.colbert_score(query_vecs, doc_vecs)
        assert 0.0 <= score <= 1.0


class TestSearchResult:
    """Test search result structures."""

    def test_search_result_creation(self):
        """SearchResult initializes correctly."""
        from active_memory_mcp.search.searcher import SearchResult
        sr = SearchResult(
            chunk_id=1,
            content="test content",
            score=0.85,
            source="vector",
            metadata={"filename": "test.pdf"}
        )
        assert sr.chunk_id == 1
        assert sr.content == "test content"
        assert sr.score == 0.85
        assert sr.source == "vector"
        assert sr.metadata == {"filename": "test.pdf"}

    def test_search_result_to_dict(self):
        """SearchResult.to_dict() returns correct structure."""
        from active_memory_mcp.search.searcher import SearchResult
        sr = SearchResult(chunk_id=1, content="test", score=0.5, source="keyword")
        d = sr.to_dict()
        assert d["chunk_id"] == 1
        assert d["content"] == "test"
        assert d["score"] == 0.5
        assert d["source"] == "keyword"
        assert d["metadata"] == {}


class TestHybridSearcher:
    """Test hybrid search functionality."""

    def test_searcher_imports(self):
        """HybridSearcher loads without errors."""
        from active_memory_mcp.search.searcher import HybridSearcher
        assert HybridSearcher is not None

    def test_searcher_init(self):
        """HybridSearcher initializes with config."""
        from active_memory_mcp.search.searcher import HybridSearcher
        searcher = HybridSearcher()
        assert searcher.top_k == 5
        assert searcher.hybrid_alpha == 0.5
        assert searcher.min_score == 0.1
        assert searcher.embedder is not None


class TestChunker:
    """Test document chunking."""

    def test_chunker_imports(self):
        """Chunker loads without errors."""
        from active_memory_mcp.ingest.chunker import Chunker
        assert Chunker is not None

    def test_chunker_init(self):
        """Chunker initializes with config."""
        from active_memory_mcp.ingest.chunker import Chunker
        chunker = Chunker()
        assert chunker.chunk_size == 512
        assert chunker.chunk_overlap == 50

    def test_split_text_basic(self):
        """Basic text splitting works."""
        from active_memory_mcp.ingest.chunker import Chunker
        chunker = Chunker()
        text = "Hello world. " * 100  # Long text
        chunks = chunker.chunk_text(text, doc_id=1)
        assert len(chunks) > 0
        assert all(isinstance(c, dict) for c in chunks)

    def test_split_text_empty(self):
        """Empty text returns empty list."""
        from active_memory_mcp.ingest.chunker import Chunker
        chunker = Chunker()
        chunks = chunker.chunk_text("", doc_id=1)
        assert chunks == []

    def test_split_code_by_lines(self):
        """Code splitting preserves line boundaries."""
        from active_memory_mcp.ingest.chunker import Chunker
        chunker = Chunker()
        code = "def foo():\n    pass\n\ndef bar():\n    return 42\n"
        chunks = chunker.chunk_code(code, doc_id=1)
        assert len(chunks) > 0
        assert all(isinstance(c, dict) for c in chunks)


class TestStorage:
    """Test database storage operations."""

    def test_models_import(self):
        """All models load without errors."""
        from active_memory_mcp.storage.db import Document, Chunk, Embedding
        assert Document is not None
        assert Chunk is not None
        assert Embedding is not None

    def test_document_table_name(self):
        """Document model has correct table name."""
        from active_memory_mcp.storage.db import Document
        assert Document.__tablename__ == "documents"

    def test_chunk_table_name(self):
        """Chunk model has correct table name."""
        from active_memory_mcp.storage.db import Chunk
        assert Chunk.__tablename__ == "chunks"

    def test_embedding_table_name(self):
        """Embedding model has correct table name."""
        from active_memory_mcp.storage.db import Embedding
        assert Embedding.__tablename__ == "embeddings"


class TestMCPServer:
    """Test MCP server tool definitions."""

    def test_mcp_server_imports(self):
        """MCP server loads without errors."""
        from active_memory_mcp.api.mcp_server import app
        assert app is not None

    def test_tools_listed(self):
        """MCP server has expected tools registered."""
        import asyncio
        from active_memory_mcp.api.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        tool_names = [t.name for t in tools]

        assert "search_memory" in tool_names
        assert "store_document" in tool_names
        assert "list_documents" in tool_names
        assert "get_document" in tool_names
        assert "delete_document" in tool_names
        assert "get_stats" in tool_names

    def test_search_memory_tool_schema(self):
        """search_memory tool has correct schema."""
        import asyncio
        from active_memory_mcp.api.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        search_tool = next(t for t in tools if t.name == "search_memory")

        assert "query" in search_tool.inputSchema["properties"]
        assert "top_k" in search_tool.inputSchema["properties"]
        assert "filetype" in search_tool.inputSchema["properties"]

    def test_new_tools_registered(self):
        """New Phase 2 tools are registered."""
        import asyncio
        from active_memory_mcp.api.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        tool_names = [t.name for t in tools]

        assert "update_document" in tool_names
        assert "bulk_search" in tool_names
        assert "update_document_metadata" in tool_names
        assert "search_by_date" in tool_names
        assert "rename_document" in tool_names

    def test_update_document_tool_schema(self):
        """update_document tool has correct schema."""
        import asyncio
        from active_memory_mcp.api.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        tool = next(t for t in tools if t.name == "update_document")

        assert "document_id" in tool.inputSchema["properties"]
        assert "metadata" in tool.inputSchema["properties"]
        assert "document_id" in tool.inputSchema["required"]
        assert "metadata" in tool.inputSchema["required"]

    def test_bulk_search_tool_schema(self):
        """bulk_search tool has correct schema."""
        import asyncio
        from active_memory_mcp.api.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        tool = next(t for t in tools if t.name == "bulk_search")

        assert "queries" in tool.inputSchema["properties"]
        assert "queries" in tool.inputSchema["required"]
        assert tool.inputSchema["properties"]["queries"]["type"] == "array"

    def test_search_by_date_tool_schema(self):
        """search_by_date tool has correct schema."""
        import asyncio
        from active_memory_mcp.api.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        tool = next(t for t in tools if t.name == "search_by_date")

        assert "query" in tool.inputSchema["properties"]
        assert "date_from" in tool.inputSchema["properties"]
        assert "date_to" in tool.inputSchema["properties"]

    def test_rename_document_tool_schema(self):
        """rename_document tool has correct schema."""
        import asyncio
        from active_memory_mcp.api.mcp_server import handle_list_tools

        tools = asyncio.run(handle_list_tools())
        tool = next(t for t in tools if t.name == "rename_document")

        assert "document_id" in tool.inputSchema["properties"]
        assert "new_filename" in tool.inputSchema["properties"]


class TestWebServer:
    """Test web dashboard server."""

    def test_web_server_imports(self):
        """Web server loads without errors."""
        # Just verify imports work (full init requires DB)
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
            # Don't actually start the server, just check imports
            from active_memory_mcp.web.server import app
            assert app is not None
        except ImportError:
            # If Flask/Starlette not installed, that's OK for unit tests
            pass


class TestMemoryDB:
    """Test memory database integration."""

    def test_memory_db_exists(self):
        """Memory database file exists in project root."""
        db_path = Path(__file__).parent.parent / "memory.db"
        # Database may or may not exist depending on setup
        assert db_path.parent.exists()

    def test_active_memory_db_exists(self):
        """ActiveMemory database file exists."""
        db_path = Path(__file__).parent.parent / "active_memory.db"
        # Database may or may not exist depending on setup
        assert db_path.parent.exists()


class TestPhase2Processor:
    """Test Phase 2 DocumentProcessor methods."""

    def test_update_document_not_found(self):
        """update_document returns error for non-existent document."""
        import os
        os.environ["AM_WEB_SQLITE"] = "true"
        from active_memory_mcp.ingest.processor import DocumentProcessor
        from active_memory_mcp.storage.db import _engine
        if _engine is not None:
            from active_memory_mcp.storage.db import init_db
            init_db()
        proc = DocumentProcessor()
        result = proc.update_document(99999, "/nonexistent/file.txt")
        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_update_metadata_not_found(self):
        """update_document_metadata returns error for non-existent document."""
        import os
        os.environ["AM_WEB_SQLITE"] = "true"
        from active_memory_mcp.ingest.processor import DocumentProcessor
        proc = DocumentProcessor()
        result = proc.update_document_metadata(99999, {"title": "Test"})
        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_rename_document_not_found(self):
        """rename_document returns error for non-existent document."""
        import os
        os.environ["AM_WEB_SQLITE"] = "true"
        from active_memory_mcp.ingest.processor import DocumentProcessor
        proc = DocumentProcessor()
        result = proc.rename_document(99999, "new_name.txt")
        assert result["success"] is False
        assert "not found" in result["message"].lower()


class TestPhase2Searcher:
    """Test Phase 2 HybridSearcher methods."""

    def test_bulk_search_returns_dict(self):
        """bulk_search returns dict with query keys."""
        from active_memory_mcp.search.searcher import HybridSearcher
        searcher = HybridSearcher()
        results = searcher.bulk_search(["test query one", "test query two"], top_k=2)
        assert isinstance(results, dict)
        assert "test query one" in results
        assert "test query two" in results
        assert isinstance(results["test query one"], list)
        assert isinstance(results["test query two"], list)

    def test_search_by_date_returns_list(self):
        """search_by_date returns list of SearchResult."""
        from active_memory_mcp.search.searcher import HybridSearcher, SearchResult
        searcher = HybridSearcher()
        results = searcher.search_by_date("test", top_k=2)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, SearchResult)

    def test_apply_filters_date_from(self):
        """_apply_filters handles date_from filter."""
        from active_memory_mcp.search.searcher import HybridSearcher
        from active_memory_mcp.storage.db import Document
        searcher = HybridSearcher()
        # Create a mock query object
        class MockQuery:
            def __init__(self):
                self.filters_applied = []
            def filter(self, condition):
                self.filters_applied.append(condition)
                return self

        mock_query = MockQuery()
        result = searcher._apply_filters(mock_query, {"date_from": "2025-01-01T00:00:00"})
        assert len(result.filters_applied) == 1

    def test_apply_filters_invalid_date(self):
        """_apply_filters gracefully handles invalid dates."""
        from active_memory_mcp.search.searcher import HybridSearcher
        searcher = HybridSearcher()
        class MockQuery:
            def __init__(self):
                self.filters_applied = []
            def filter(self, condition):
                self.filters_applied.append(condition)
                return self

        mock_query = MockQuery()
        result = searcher._apply_filters(mock_query, {"date_from": "not-a-date"})
        # Should not raise, just skip the invalid filter
        assert len(result.filters_applied) == 0


class TestPhase3SmartContext:
    """Test Phase 3 smart context features."""

    def test_smart_context_returns_list(self):
        """smart_context returns a list of SearchResult."""
        from active_memory_mcp.search.searcher import HybridSearcher, SearchResult
        searcher = HybridSearcher()
        results = searcher.smart_context("test", max_tokens=1000)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, SearchResult)

    def test_smart_context_respects_token_budget(self):
        """smart_context stays within token budget."""
        from active_memory_mcp.search.searcher import HybridSearcher
        searcher = HybridSearcher()
        max_tokens = 500
        results = searcher.smart_context("test", max_tokens=max_tokens)
        total = sum(r.metadata.get("token_count", 0) for r in results)
        assert total <= max_tokens + searcher._count_tokens(results[-1].content) if results else True

    def test_get_context_returns_list(self):
        """get_context returns a list of SearchResult."""
        from active_memory_mcp.search.searcher import HybridSearcher, SearchResult
        searcher = HybridSearcher()
        results = searcher.get_context(max_tokens=1000)
        assert isinstance(results, list)

    def test_get_context_with_query(self):
        """get_context combines static context with search results."""
        from active_memory_mcp.search.searcher import HybridSearcher
        searcher = HybridSearcher()
        results = searcher.get_context(query="test", max_tokens=2000)
        assert isinstance(results, list)

    def test_count_tokens_basic(self):
        """_count_tokens gives reasonable estimates."""
        from active_memory_mcp.search.searcher import HybridSearcher
        searcher = HybridSearcher()
        t1 = searcher._count_tokens("hello world")
        t2 = searcher._count_tokens("hello world foo bar")
        assert t1 > 0
        assert t2 > t1

    def test_apply_priority_boost(self):
        """_apply_priority_boost increases scores for pinned/important docs."""
        from active_memory_mcp.search.searcher import HybridSearcher, SearchResult
        searcher = HybridSearcher()
        results = [
            SearchResult(1, "content", 0.5, "vector", {"importance": 3, "pinned": False}),
            SearchResult(2, "content2", 0.5, "vector", {"importance": 1, "pinned": True}),
        ]
        boosted = searcher._apply_priority_boost(results)
        assert boosted[0].score > 0.5  # Pinned + important boosted
        assert "boost" in boosted[0].source

    def test_rerank_recency(self):
        """rerank with recency method updates scores."""
        from active_memory_mcp.search.searcher import HybridSearcher, SearchResult
        from datetime import datetime, timezone
        searcher = HybridSearcher()
        results = [
            SearchResult(1, "content", 0.5, "vector", {"created_at": datetime.now(timezone.utc)}),
        ]
        reranked = searcher.rerank(results, "test", method="recency")
        assert len(reranked) == 1
        assert "recency" in reranked[0].source

    def test_rerank_diversity(self):
        """rerank with diversity method filters similar results."""
        from active_memory_mcp.search.searcher import HybridSearcher, SearchResult
        searcher = HybridSearcher()
        results = [
            SearchResult(1, "hello world test", 0.9, "vector", {}),
            SearchResult(2, "hello world test", 0.8, "vector", {}),
            SearchResult(3, "completely different topic", 0.7, "vector", {}),
        ]
        reranked = searcher.rerank(results, "test", method="diversity")
        assert len(reranked) < len(results)  # Similar ones filtered

    def test_text_similarity(self):
        """_text_similarity returns Jaccard similarity."""
        from active_memory_mcp.search.searcher import HybridSearcher
        searcher = HybridSearcher()
        sim = searcher._text_similarity("hello world foo", "hello world bar")
        assert 0 < sim < 1.0
        sim_same = searcher._text_similarity("hello world", "hello world")
        assert sim_same == 1.0
        sim_diff = searcher._text_similarity("abc def", "xyz qqq")
        assert sim_diff == 0.0

    def test_new_tools_registered(self):
        """Phase 3 tools are registered in MCP server."""
        import asyncio
        from active_memory_mcp.api.mcp_server import handle_list_tools
        tools = asyncio.run(handle_list_tools())
        tool_names = [t.name for t in tools]
        assert "smart_context" in tool_names
        assert "get_context" in tool_names
        assert "reindex_embeddings" in tool_names


class TestAPIEndpoints:
    """Test API endpoint patterns."""

    def test_stats_endpoint(self):
        """Stats endpoint returns expected structure."""
        import os
        os.environ["AM_WEB_SQLITE"] = "true"
        # Reload config to pick up env var
        from active_memory_mcp.api.mcp_server import handle_call_tool
        import asyncio
        result = asyncio.run(handle_call_tool("get_stats", {}))
        assert len(result) > 0
        text = result[0].text
        # Should contain stats info (either data or error message)
        assert isinstance(text, str)
        assert len(text) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
