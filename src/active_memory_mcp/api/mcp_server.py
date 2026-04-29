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
            description="Search memory for relevant information using hybrid (vector + keyword) search.",
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
            name="update_document",
            description="Update a document: replace content and re-embed. Old chunks are deleted.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "ID of the document to update",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the new file",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata overrides",
                    },
                },
                "required": ["document_id", "file_path"],
            },
        ),
        types.Tool(
            name="bulk_search",
            description="Execute multiple search queries in one call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of search queries",
                    },
                    "top_k": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Number of results per query (default: 5)",
                    },
                    "filetype": {
                        "type": "string",
                        "description": "Filter by file type",
                    },
                },
                "required": ["queries"],
            },
        ),
        types.Tool(
            name="update_document_metadata",
            description="Update metadata fields on an existing document.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "ID of the document",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Fields to update (title, author, category, importance, pinned, etc.)",
                    },
                },
                "required": ["document_id", "metadata"],
            },
        ),
        types.Tool(
            name="search_by_date",
            description="Search within a date range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Start date (ISO format: YYYY-MM-DD)",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date (ISO format: YYYY-MM-DD)",
                    },
                    "top_k": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Number of results (default: 5)",
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
            name="rename_document",
            description="Rename a document without changing its content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "number",
                        "description": "ID of the document",
                    },
                    "new_filename": {
                        "type": "string",
                        "description": "New filename",
                    },
                },
                "required": ["document_id", "new_filename"],
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
            filters = {}
            if "filetype" in arguments:
                filters["filetype"] = arguments["filetype"]
            
            results = searcher.search(query, top_k=top_k, filters=filters)
            
            if not results:
                return [types.TextContent(
                    type="text",
                    text="No results found.",
                )]
            
            lines = [f"Found {len(results)} results:\n"]
            for i, r in enumerate(results, 1):
                lines.append(
                    f"{i}. [Score: {r.score:.3f}, Source: {r.source}]\n{r.content[:500]}"
                )
                if len(r.content) > 500:
                    lines.append("...")
                lines.append("\n")
            
            return [types.TextContent(type="text", text="".join(lines))]
        
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
            filters = {}
            if "filetype" in arguments:
                filters["filetype"] = arguments["filetype"]
            
            results = searcher.bulk_search(queries, top_k=top_k, filters=filters)
            
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
            date_from = arguments.get("date_from")
            date_to = arguments.get("date_to")
            filters = {}
            if "filetype" in arguments:
                filters["filetype"] = arguments["filetype"]
            
            results = searcher.search_by_date(
                query, date_from=date_from, date_to=date_to,
                top_k=top_k, filters=filters,
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
