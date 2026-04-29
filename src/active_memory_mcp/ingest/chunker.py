"""Intelligent chunking for documents and code."""

import re
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class Chunker:
    """Split text into chunks with optional overlap."""
    
    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
    
    def chunk_text(self, text: str, doc_id: int, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Chunk plain text."""
        if not text or not text.strip():
            return []
        paragraphs = re.split(r'\n\s*\n', text)
        chunks = []
        current_chunk = []
        current_length = 0
        chunk_index = 0
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            para_tokens = len(para.split())
            if current_length + para_tokens <= self.chunk_size:
                current_chunk.append(para)
                current_length += para_tokens
            else:
                if current_chunk:
                    chunks.append({
                        "document_id": doc_id,
                        "content": "\n\n".join(current_chunk),
                        "token_count": current_length,
                        "chunk_index": chunk_index,
                        "metadata": metadata or {},
                    })
                    chunk_index += 1
                    overlap_tokens = 0
                    overlap_paras = []
                    for p in reversed(current_chunk):
                        p_tokens = len(p.split())
                        if overlap_tokens + p_tokens <= self.chunk_overlap:
                            overlap_paras.insert(0, p)
                            overlap_tokens += p_tokens
                        else:
                            break
                    current_chunk = overlap_paras
                    current_length = overlap_tokens
                current_chunk.append(para)
                current_length += para_tokens
        if current_chunk:
            chunks.append({
                "document_id": doc_id,
                "content": "\n\n".join(current_chunk),
                "token_count": current_length,
                "chunk_index": chunk_index,
                "metadata": metadata or {},
            })
        return chunks
    
    def chunk_code(self, code: str, doc_id: int, file_path: str = "") -> List[Dict[str, Any]]:
        """Chunk code by logical blocks."""
        if not code or not code.strip():
            return []
        pattern = r'^(\s*(?:def|class)\s+\w+)'
        lines = code.split("\n")
        chunks = []
        current_block = []
        current_length = 0
        chunk_index = 0
        for line in lines:
            if re.match(pattern, line, re.MULTILINE) and current_block:
                block_text = "\n".join(current_block)
                chunks.append({
                    "document_id": doc_id,
                    "content": block_text,
                    "token_count": len(block_text.split()),
                    "chunk_index": chunk_index,
                    "metadata": {"type": "code", "file_path": file_path, "block_type": "function_or_class"},
                })
                chunk_index += 1
                current_block = []
                current_length = 0
            current_block.append(line)
            current_length += len(line.split())
            if current_length >= self.chunk_size:
                block_text = "\n".join(current_block)
                chunks.append({
                    "document_id": doc_id,
                    "content": block_text,
                    "token_count": current_length,
                    "chunk_index": chunk_index,
                    "metadata": {"type": "code", "file_path": file_path, "block_type": "large_block_split"},
                })
                chunk_index += 1
                current_block = []
                current_length = 0
        if current_block:
            block_text = "\n".join(current_block)
            chunks.append({
                "document_id": doc_id,
                "content": block_text,
                "token_count": len(block_text.split()),
                "chunk_index": chunk_index,
                "metadata": {"type": "code", "file_path": file_path, "block_type": "remaining"},
            })
        return chunks
