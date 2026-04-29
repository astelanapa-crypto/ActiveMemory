"""Basic tests for ActiveMemory."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from active_memory_mcp.core.config import config
from active_memory_mcp.ingest.chunker import Chunker
from active_memory_mcp.search.embedder import Embedder

def test_config():
    """Test configuration loads."""
    assert config.chunking.chunk_size == 512
    assert config.chunking.chunk_overlap == 50
    print("✓ Config OK")

def test_chunker():
    """Test text chunking."""
    chunker = Chunker(chunk_size=100, chunk_overlap=10)
    text = "This is a test. " * 20
    chunks = chunker.chunk_text(text, doc_id=1)
    assert len(chunks) > 0
    assert all(c["document_id"] == 1 for c in chunks)
    print(f"✓ Chunker OK ({len(chunks)} chunks)")

def test_embedder():
    """Test embedding generation."""
    embedder = Embedder()
    vec = embedder.embed("test document")
    assert vec is not None
    assert len(vec) == config.embedding.dimensions
    print(f"✓ Embedder OK (dim={len(vec)})")

def test_cosine_similarity():
    """Test cosine similarity."""
    embedder = Embedder()
    vec1 = embedder.embed("hello world")
    vec2 = embedder.embed("hello world")
    vec3 = embedder.embed("completely different")
    sim_same = embedder.cosine_similarity(vec1, vec2)
    sim_diff = embedder.cosine_similarity(vec1, vec3)
    assert sim_same > 0.9
    assert sim_same > sim_diff
    print(f"✓ Cosine similarity OK (same={sim_same:.3f}, diff={sim_diff:.3f})")

if __name__ == "__main__":
    test_config()
    test_chunker()
    test_embedder()
    test_cosine_similarity()
    print("\n✅ All tests passed!")
