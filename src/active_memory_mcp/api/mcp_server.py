"""MCP server exposing ActiveMemory tools."""

import logging
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

from mcp.server import Server
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions

from ..storage.db import get_backend, get_session, Document, Chunk, Embedding
from ..ingest.processor import DocumentProcessor
from ..search.searcher import HybridSearcher
from ..core.config import config

logger = logging.getLogger(__name__)

# Global processors
processor = DocumentProcessor()
searcher = HybridSearcher()

@asynccontextmanager
async def lifespan(app):
    """Server lifespan."""
    logger.info("ActiveMemory MCP server starting...")
    yield
    logger.info("ActiveMemory MCP server stopped")

app = Server("active-memory", lifespan=lifespan)

@app.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """List available tools."""
    return [
        types.Tool(
            name="search_memory",
            description="Search memory for relevant information using vector, keyword, multi-vector, or hybrid search.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "top_k": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Number of results to return (default: 5)",
                    },
                    "search_mode": {
                        "type": "string",
                        "enum": ["dense", "multi-vector", "hybrid"],
                        "description": "Search mode: dense (vector only), multi-vector (ColBERT), or hybrid (default: hybrid)",
                    },
                    "filetype": {
                        "type": "string",
                        "description": "Filter by file type (e.g., 'pdf', 'code', 'text')",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="store_document",
            description="Store a document in memory (supports PDF, text, code, markdown).",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to store",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata",
                    },
                },
                "required": ["file_path"],
            },
        ),
        types.Tool(
            name="list_documents",
            description="List all documents in memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filetype": {
                        "type": "string",
                        "description": "Filter by file type",
                    },
                    "limit": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Max number of results",
                    },
                },
            },
        ),
        types.Tool(
            name="get_document",
            description="Get a specific document and its chunks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "Document ID",
                    },
                },
                "required": ["document_id"],
            },
        ),
        types.Tool(
            name="delete_document",
            description="Delete a document and all its chunks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "Document ID",
                    },
                },
                "required": ["document_id"],
            },
        ),
        types.Tool(
            name="get_stats",
            description="Get memory statistics.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="export_memory",
            description="Export memory database to JSON or CSV format.",
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["json", "csv"],
                        "description": "Export format (default: json)",
                    },
                    "include_embeddings": {
                        "type": "boolean",
                        "description": "Include embedding vectors in JSON export (default: true)",
                    },
                },
            },
        ),
        types.Tool(
            name="import_memory",
            description="Import memory from JSON or CSV file. Supports merge (add new) and replace (clear all) modes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to JSON or CSV file to import",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["merge", "replace"],
                        "description": "Import mode: merge (add new) or replace (clear all first)",
                    },
                    "validate_only": {
                        "type": "boolean",
                        "description": "Only validate, do not actually import (default: false)",
                    },
                },
                "required": ["file_path"],
            },
        ),
        types.Tool(
            name="smart_context",
            description="Get smart context for a query with token budget management.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Query to get context for",
                    },
                    "max_tokens": {
                        "type": "number",
                        "description": "Maximum tokens in context (default: from config)",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["dense", "multi-vector", "hybrid"],
                        "description": "Search mode (default: hybrid)",
                    },
                    "prioritize": {
                        "type": "boolean",
                        "description": "Prioritize important/pinned docs (default: true)",
                    },
                    "filetype": {
                        "type": "string",
                        "description": "Filter by file type",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_context",
            description="Auto-collect important context based on criteria.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional query to include relevant results",
                    },
                    "max_tokens": {
                        "type": "number",
                        "description": "Maximum tokens in context",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["dense", "multi-vector", "hybrid"],
                        "description": "Search mode if query is provided (default: hybrid)",
                    },
                    "pinned": {
                        "type": "boolean",
                        "description": "Include pinned documents",
                    },
                    "important": {
                        "type": "boolean",
                        "description": "Include important documents (importance >= 3)",
                    },
                },
            },
        ),
        types.Tool(
            name="update_document",
            description="Update document metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "Document ID",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Metadata to update",
                    },
                },
                "required": ["document_id", "metadata"],
            },
        ),
        types.Tool(
            name="bulk_search",
            description="Perform multiple searches in one call with optional search mode.",
            inputSchema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of search queries",
                    },
                    "search_mode": {
                        "type": "string",
                        "enum": ["dense", "multi-vector", "hybrid"],
                        "description": "Search mode for all queries (default: hybrid)",
                    },
                },
                "required": ["queries"],
            },
        ),
        types.Tool(
            name="update_document_metadata",
            description="Update only document metadata (alias for update_document).",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "Document ID",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Metadata to update",
                    },
                },
                "required": ["document_id", "metadata"],
            },
        ),
        types.Tool(
            name="search_by_date",
            description="Search memory within a date range with optional search mode.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date (ISO format)",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date (ISO format)",
                    },
                    "top_k": {
                        "type": "number",
                        "description": "Number of results",
                    },
                    "search_mode": {
                        "type": "string",
                        "enum": ["dense", "multi-vector", "hybrid"],
                        "description": "Search mode (default: hybrid)",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="rename_document",
            description="Rename a document.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "Document ID",
                    },
                    "new_filename": {
                        "type": "string",
                        "description": "New filename",
                    },
                },
                "required": ["document_id", "new_filename"],
            },
        ),
        types.Tool(
            name="reindex_embeddings",
            description="Re-index embeddings for a document or all documents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "Document ID (optional, reindexes all if omitted)",
                    },
                },
            },
        ),
    ]

@app.call_tool()
async def handle_call_tool(
    name: str,
    arguments: Optional[Dict[str, Any]],
) -> List[types.TextContent]:
    """Handle tool calls."""
    arguments = arguments or {}
    
    try:
        if name == "search_memory":
            query = arguments["query"]
            top_k = int(arguments.get("top_k", 5))
            mode = arguments.get("search_mode", "hybrid")
            filters = {}
            if "filetype" in arguments:
                filters["filetype"] = arguments["filetype"]

            results = searcher.search(query, top_k=top_k, mode=mode, filters=filters)

            if not results:
                return [types.TextContent(
                    type="text",
                    text="No results found.",
                )]

            lines = [f"Found {len(results)} results for '{query}':\n\n"]
            for i, r in enumerate(results, 1):
                lines.append(
                    f"{i}. [Score: {r.score:.3f}, Source: {r.source}]\n"
                    f"    {r.content[:300]}\n\n"
                )

            return [types.TextContent(type="text", text="".join(lines))]
        
        elif name == "smart_context":
            query = arguments["query"]
            max_tokens = int(arguments.get("max_tokens", config.search.context_max_tokens))
            mode = arguments.get("mode", "hybrid")
            prioritize = arguments.get("prioritize", True)
            filters = {}
            if "filetype" in arguments:
                filters["filetype"] = arguments["filetype"]

            results = searcher.smart_context(
                query, max_tokens=max_tokens, mode=mode, filters=filters, prioritize=prioritize,
            )

            if not results:
                return [types.TextContent(
                    type="text",
                    text="No results found within token budget.",
                )]

            total_tokens = sum(r.metadata.get("token_count", 0) for r in results)
            lines = [
                f"Smart context: {len(results)} results, {total_tokens} tokens (budget: {max_tokens}):\n\n"
            ]
            for i, r in enumerate(results, 1):
                tokens = r.metadata.get("token_count", 0)
                lines.append(
                    f"{i}. [Score: {r.score:.3f}, ~{tokens} tokens, Source: {r.source}]\n"
                    f"    {r.content[:400]}\n\n"
                )

            return [types.TextContent(type="text", text="".join(lines))]

        elif name == "get_context":
            max_tokens = int(arguments.get("max_tokens", config.search.context_max_tokens))
            mode = arguments.get("mode", "hybrid")
            include_pinned = arguments.get("include_pinned", True)
            include_important = arguments.get("include_important", True)
            importance_threshold = int(arguments.get("importance_threshold", 2))
            query = arguments.get("query")

            results = searcher.get_context(
                query=query,
                max_tokens=max_tokens,
                mode=mode,
                include_pinned=include_pinned,
                include_important=include_important,
                importance_threshold=importance_threshold,
            )

            if not results:
                return [types.TextContent(
                    type="text",
                    text="No important context found.",
                )]

            total_tokens = sum(r.metadata.get("token_count", 0) for r in results)
            sources = {}
            for r in results:
                src = r.source
                sources[src] = sources.get(src, 0) + 1
            source_summary = ", ".join(f"{v} {k}" for k, v in sources.items())

            lines = [
                f"Context: {len(results)} chunks, {total_tokens} tokens ({source_summary}):\n\n"
            ]
            for i, r in enumerate(results, 1):
                tokens = r.metadata.get("token_count", 0)
                importance = r.metadata.get("importance", 3)
                pinned = "📌" if r.metadata.get("pinned") else ""
                lines.append(
                    f"{i}. {pinned}[Imp: {importance}, ~{tokens} tokens] {r.source}\n"
                    f"    {r.content[:400]}\n\n"
                )

            return [types.TextContent(type="text", text="".join(lines))]

        elif name == "reindex_embeddings":
            doc_id = arguments.get("document_id")
            if doc_id is not None:
                doc_id = int(doc_id)

            result = processor.reindex_embeddings(document_id=doc_id)

            if result["success"]:
                scope = result["scope"]
                failed_text = f", {result['failed']} failed" if result["failed"] else ""
                return [types.TextContent(
                    type="text",
                    text=(
                        f"Reindexed embeddings for {scope}: "
                        f"{result['reindexed']}/{result['total']} chunks re-embedded{failed_text}."
                    ),
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=f"Failed: {result.get('message', 'Unknown error')}",
                )]

        elif name == "export_memory":
            from ..core.exporter import export_all_json, export_all_csv
            import json
            format = arguments.get("format", "json")
            include_embeddings = arguments.get("include_embeddings", True)
            
            if format == "csv":
                content = export_all_csv()
                return [types.TextContent(
                    type="text",
                    text=f"CSV Export:\n{content[:3000]}..." if len(content) > 3000 else content,
                )]
            else:
                data = export_all_json(include_embeddings=include_embeddings)
                return [types.TextContent(
                    type="text",
                    text=json.dumps(data, indent=2)[:4000],
                )]

        elif name == "import_memory":
            from ..core.importer import import_from_file, validate_import_data
            import json
            file_path = arguments["file_path"]
            mode = arguments.get("mode", "merge")
            validate_only = arguments.get("validate_only", False)
            
            if validate_only:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        if file_path.endswith('.json'):
                            data = json.load(f)
                            result = validate_import_data(data)
                        else:
                            result = {"valid": True, "message": "CSV validation not required"}
                    return [types.TextContent(
                        type="text",
                        text=json.dumps(result, indent=2),
                    )]
                except Exception as e:
                    return [types.TextContent(
                        type="text",
                        text=f"Validation error: {e}",
                    )]
            else:
                result = import_from_file(file_path, mode=mode)
                return [types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2),
                )]

        elif name == "store_document":
            file_path = arguments["file_path"]
            metadata = arguments.get("metadata", {})
            
            result = processor.ingest_document(file_path, metadata=metadata)
            
            if result["success"]:
                return [types.TextContent(
                    type="text",
                    text=f"Stored document '{result['filename']}' with {result['chunks']} chunks.",
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=f"Failed: {result.get('message', 'Unknown error')}",
                )]
        
        elif name == "list_documents":
            from sqlalchemy import desc
            session = get_session()
            try:
                query = session.query(Document)
                
                if "filetype" in arguments:
                    query = query.filter(Document.filetype == arguments["filetype"])
                
                limit = int(arguments.get("limit", 20))
                docs = query.order_by(desc(Document.created_at)).limit(limit).all()
                
                if not docs:
                    return [types.TextContent(type="text", text="No documents found.")]
                
                lines = [f"Documents ({len(docs)}):\n"]
                for d in docs:
                    lines.append(
                        f"- ID {d.id}: {d.filename} ({d.filetype}, {d.filesize} bytes) "
                        f"chunks: {len(d.chunks)}\n"
                    )
                
                return [types.TextContent(type="text", text="".join(lines))]
            finally:
                session.close()
        
        elif name == "get_document":
            doc_id = int(arguments["document_id"])
            session = get_session()
            try:
                doc = session.query(Document).filter(Document.id == doc_id).first()
                if not doc:
                    return [types.TextContent(type="text", text="Document not found.")]
                
                lines = [
                    f"Document: {doc.filename}\n",
                    f"Type: {doc.filetype}\n",
                    f"Size: {doc.filesize} bytes\n",
                    f"Chunks: {len(doc.chunks)}\n",
                    f"Created: {doc.created_at}\n\n",
                    "Chunks:\n",
                ]
                for c in doc.chunks[:10]:
                    lines.append(f"  [{c.chunk_index}] ({c.token_count} tokens)\n")
                    lines.append(f"    {c.content[:200]}...\n\n")
                if len(doc.chunks) > 10:
                    lines.append(f"  ... and {len(doc.chunks) - 10} more\n")
                
                return [types.TextContent(type="text", text="".join(lines))]
            finally:
                session.close()
        
        elif name == "delete_document":
            doc_id = int(arguments["document_id"])
            session = get_session()
            try:
                doc = session.query(Document).filter(Document.id == doc_id).first()
                if not doc:
                    return [types.TextContent(type="text", text="Document not found.")]
                
                filename = doc.filename
                session.delete(doc)
                session.commit()
                return [types.TextContent(
                    type="text",
                    text=f"Deleted document '{filename}' and all its chunks.",
                )]
            finally:
                session.close()
        
        elif name == "get_stats":
            session = get_session()
            try:
                from sqlalchemy import func
                doc_count = session.query(func.count(Document.id)).scalar()
                chunk_count = session.query(func.count(Chunk.id)).scalar()
                emb_count = session.query(func.count(Embedding.chunk_id)).scalar()
                critical_count = (
                    session.query(func.count(Document.id))
                    .filter((Document.pinned.is_(True)) | (Document.importance <= 2))
                    .scalar()
                )
                total_tokens = session.query(func.sum(Chunk.token_count)).scalar() or 0
                
                return [types.TextContent(
                    type="text",
                    text=(
                        f"ActiveMemory Statistics:\n"
                        f"- Backend: {get_backend()}\n"
                        f"- Documents: {doc_count}\n"
                        f"- Chunks: {chunk_count}\n"
                        f"- Embeddings: {emb_count}\n"
                        f"- Critical memories: {critical_count}\n"
                        f"- Total tokens: {total_tokens:,}\n"
                        f"- Avg chunks per doc: {chunk_count/max(doc_count,1):.1f}\n"
                        f"- Avg tokens per chunk: {total_tokens/max(chunk_count,1):.0f}"
                    ),
                )]
            finally:
                session.close()
        
        elif name == "update_document":
            doc_id = int(arguments["document_id"])
            file_path = arguments["file_path"]
            metadata = arguments.get("metadata", {})
            
            result = processor.update_document(doc_id, file_path, metadata=metadata)
            
            if result["success"]:
                return [types.TextContent(
                    type="text",
                    text=f"Updated document '{result['filename']}': {result['new_chunks']} new chunks, {result['tokens']} tokens.",
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=f"Failed: {result.get('message', 'Unknown error')}",
                )]
        
        elif name == "bulk_search":
            queries = arguments["queries"]
            top_k = int(arguments.get("top_k", 5))
            search_mode = arguments.get("search_mode", "hybrid")
            filters = {}
            if "filetype" in arguments:
                filters["filetype"] = arguments["filetype"]

            results = searcher.bulk_search(queries, top_k=top_k, mode=search_mode, filters=filters)
            
            lines = []
            for q, res_list in results.items():
                lines.append(f"Query: '{q}' ({len(res_list)} results):\n")
                if not res_list:
                    lines.append("  No results.\n")
                else:
                    for i, r in enumerate(res_list, 1):
                        lines.append(f"  {i}. [Score: {r.score:.3f}] {r.content[:200]}...\n")
                lines.append("\n")
            
            return [types.TextContent(type="text", text="".join(lines))]
        
        elif name == "update_document_metadata":
            doc_id = int(arguments["document_id"])
            metadata = arguments["metadata"]
            
            result = processor.update_document_metadata(doc_id, metadata)
            
            if result["success"]:
                return [types.TextContent(
                    type="text",
                    text=f"Updated metadata for document {doc_id}.",
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=f"Failed: {result.get('message', 'Unknown error')}",
                )]
        
        elif name == "search_by_date":
            query = arguments["query"]
            top_k = int(arguments.get("top_k", 5))
            search_mode = arguments.get("search_mode", "hybrid")
            date_from = arguments.get("date_from")
            date_to = arguments.get("date_to")
            filters = {}
            if "filetype" in arguments:
                filters["filetype"] = arguments["filetype"]

            from datetime import datetime
            valid_from = None
            valid_to = None
            if date_from:
                try:
                    valid_from = datetime.fromisoformat(date_from)
                except (ValueError, TypeError):
                    date_from = None
            if date_to:
                try:
                    datetime.fromisoformat(date_to)
                    valid_to = date_to
                except (ValueError, TypeError):
                    date_to = None

            results = searcher.search_by_date(
                query, date_from=date_from, date_to=date_to,
                top_k=top_k, filters=filters, mode=search_mode,
            )
            
            if not results:
                return [types.TextContent(
                    type="text",
                    text="No results found for the given date range.",
                )]
            
            date_info = []
            if date_from:
                date_info.append(f"from {date_from}")
            if date_to:
                date_info.append(f"to {date_to}")
            date_str = f" ({' '.join(date_info)})" if date_info else ""
            
            lines = [f"Found {len(results)} results{date_str}:\n"]
            for i, r in enumerate(results, 1):
                lines.append(
                    f"{i}. [Score: {r.score:.3f}, Source: {r.source}]\n{r.content[:500]}"
                )
                if len(r.content) > 500:
                    lines.append("...")
                lines.append("\n")
            
            return [types.TextContent(type="text", text="".join(lines))]
        
        elif name == "rename_document":
            doc_id = int(arguments["document_id"])
            new_filename = arguments["new_filename"]

            if not new_filename or not new_filename.strip():
                return [types.TextContent(
                    type="text",
                    text="Failed: filename cannot be empty.",
                )]
            
            result = processor.rename_document(doc_id, new_filename)
            
            if result["success"]:
                return [types.TextContent(
                    type="text",
                    text=f"Renamed document {doc_id}: '{result['old_name']}' -> '{result['new_name']}'.",
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=f"Failed: {result.get('message', 'Unknown error')}",
                )]
        
        else:
            raise ValueError(f"Unknown tool: {name}")
    
    except Exception as e:
        logger.error(f"Tool error [{name}]: {e}", exc_info=True)
        return [types.TextContent(
            type="text",
            text=f"Error: {e}",
        )]

async def main():
    """Run the MCP server via stdio transport."""
    from mcp.server.stdio import stdio_server
    
    async with stdio_server() as streams:
        await app.run(
            streams[0],
            streams[1],
            InitializationOptions(
                server_name="active-memory",
                server_version="1.0.0",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
