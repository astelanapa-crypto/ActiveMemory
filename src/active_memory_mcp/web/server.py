"""Web dashboard for managing agent memory and context."""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import os
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import desc, func, or_

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

from ..core.config import config
from ..core.auth import (
    generate_token, hash_token,
    create_encryption_salt, encrypt_embedding, decrypt_embedding, get_encryption_key,
    VALID_SCOPES,
)
from ..ingest.chunker import Chunker
from ..ingest.processor import DocumentProcessor
from ..search.embedder import Embedder
from ..search.searcher import HybridSearcher
from ..storage.db import (
    AccessLog, ApiToken, Chunk, Document, Embedding, Vector,
    get_backend, get_session, init_db,
)
from ..core.exporter import (
    export_all_json, export_all_csv, export_document_json, export_category_json, get_export_stats,
)
from ..core.importer import (
    import_from_json, import_from_csv, validate_import_data,
)
from ..core.snapshot import (
    create_snapshot, list_snapshots, restore_snapshot, delete_snapshot, get_snapshot_stats,
)

processor = DocumentProcessor()
chunker = Chunker()
embedder = Embedder()
searcher = HybridSearcher()

logger = logging.getLogger(__name__)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

# Prometheus metrics
REQUEST_COUNT = Counter('activememory_requests_total', 'Total requests', ['method', 'endpoint', 'status'])
DOCUMENT_COUNT = Gauge('activememory_documents_total', 'Total number of documents')
CHUNK_COUNT = Gauge('activememory_chunks_total', 'Total number of chunks')
EMBEDDING_COUNT = Gauge('activememory_embeddings_total', 'Total number of embeddings')
SEARCH_LATENCY = Histogram('activememory_search_duration_seconds', 'Search latency')
IMPORT_COUNT = Counter('activememory_imports_total', 'Total imports', ['status'])

_ENCRYPTION_KEY = None


def _get_encryption_key():
    global _ENCRYPTION_KEY
    if _ENCRYPTION_KEY is None:
        master = os.getenv("AM_ENCRYPTION_KEY", "active_memory_default_key_change_me")
        _ENCRYPTION_KEY = get_encryption_key(master)
    return _ENCRYPTION_KEY


_SKIP_AUTH_PATHS = {"/", "/health", "/api/stats", "/api/documents", "/api/context",
                    "/api/search", "/api/activity", "/api/documents"}


def _should_skip_auth(path: str) -> bool:
    for skip in _SKIP_AUTH_PATHS:
        if path == skip or path.startswith(skip + "/") and not path.startswith("/api/tokens"):
            return True
    return False


def _log_access(action: str, token_label: str | None = None, document_id: int | None = None,
                ip: str | None = None, success: bool = True, details: dict | None = None):
    try:
        session = get_session()
        try:
            log = AccessLog(
                action=action, token_label=token_label, document_id=document_id,
                ip_address=ip, success=success, details=details or {},
            )
            session.add(log)
            session.commit()
        finally:
            session.close()
    except Exception:
        pass


def _extract_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_token_label_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        raw_token = auth[7:]
        token_hash = hash_token(raw_token)
        session = get_session()
        try:
            token = session.query(ApiToken).filter(ApiToken.token_hash == token_hash).first()
            return token.label if token else None
        finally:
            session.close()
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if config.security.require_auth:
        session = get_session()
        try:
            has_admin = session.query(func.count(ApiToken.id)).filter(
                ApiToken.active.is_(True), ApiToken.scopes.like("%admin%")
            ).scalar()
            if not has_admin:
                raw, token_hash = generate_token()
                session.add(ApiToken(
                    token_hash=token_hash, label="admin-auto-generated",
                    scopes="admin", active=True, created_by="system",
                ))
                session.commit()
                logger.info(f"Auto-generated admin token: {raw}")
        finally:
            session.close()
    yield


app = FastAPI(title="ActiveMemory Control Center", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_WRITE_ACTIONS = {"POST", "PUT", "DELETE", "PATCH"}
_AUTH_REQUIRED_PATHS = {"/api/memory", "/api/upload", "/api/tokens", "/api/access-log"}


@app.middleware("http")
async def auth_and_logging_middleware(request: Request, call_next):
    ip = _extract_client_ip(request)
    action_name = f"{request.method.lower()}_{request.url.path.strip('/').replace('/', '_')}"

    if config.security.require_auth and request.method in _WRITE_ACTIONS:
        needs_auth = any(request.url.path.startswith(p) for p in _AUTH_REQUIRED_PATHS)
        if needs_auth:
            auth = request.headers.get("authorization", "")
            if not auth.startswith("Bearer "):
                return JSONResponse(status_code=401, content={"error": "Authorization required"})
            raw_token = auth[7:]
            token_hash_val = hash_token(raw_token)
            session = get_session()
            try:
                token = session.query(ApiToken).filter(
                    ApiToken.token_hash == token_hash_val,
                    ApiToken.active.is_(True),
                ).first()
                if not token:
                    _log_access("auth_failed", ip=ip, success=False, details={"action": action_name})
                    return JSONResponse(status_code=401, content={"error": "Invalid or inactive token"})
                from datetime import datetime as _dt
                if token.expires_at and token.expires_at < _dt.utcnow():
                    _log_access("auth_expired", token_label=token.label, ip=ip, success=False)
                    return JSONResponse(status_code=401, content={"error": "Token expired"})
                token.last_used_at = datetime.utcnow()
                session.commit()
            finally:
                session.close()

    response = await call_next(request)

    if config.security.require_auth or request.method in _WRITE_ACTIONS:
        if not config.security.require_auth and request.method in _WRITE_ACTIONS:
            _log_access(action_name, token_label=None, ip=ip, success=response.status_code < 400)

    return response


def _document_payload(doc: Document, preview: str = "") -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "filetype": doc.filetype,
        "filesize": doc.filesize,
        "title": doc.title or doc.filename,
        "category": doc.category,
        "source": doc.source,
        "importance": doc.importance,
        "pinned": doc.pinned,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "chunks": len(doc.chunks),
        "preview": preview,
        "metadata": doc.metadata_ or {},
    }


def _store_text_memory(
    content: str,
    title: str,
    category: str,
    importance: int,
    pinned: bool,
    tags: str,
    source: str,
) -> dict:
    if not content.strip():
        raise HTTPException(status_code=400, detail="Content is required")
    session = get_session()
    try:
        doc = Document(
            filename=title.strip() or "manual-memory",
            filetype="text",
            filesize=len(content.encode("utf-8")),
            title=title.strip() or "Manual memory",
            category=category,
            source=source,
            importance=importance,
            pinned=pinned,
            metadata_={"tags": tags, "manual": True},
        )
        session.add(doc)
        session.flush()
        chunks = chunker.chunk_text(content, doc.id, {"tags": tags, "source": source})
        for item in chunks:
            chunk = Chunk(
                document_id=doc.id,
                content=item["content"],
                token_count=item["token_count"],
                chunk_index=item["chunk_index"],
                metadata_=item.get("metadata", {}),
            )
            session.add(chunk)
            session.flush()
            vector = embedder.embed(chunk.content)
            session.add(
                Embedding(
                    chunk_id=chunk.id,
                    embedding=vector,
                    model=embedder.model_name,
                    dimensions=len(vector) if vector else config.embedding.dimensions,
                )
            )
        session.commit()
        return {"id": doc.id, "chunks": len(chunks)}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _get_dashboard_context(session) -> dict:
    return {
        "documents": session.query(func.count(Document.id)).scalar() or 0,
        "chunks": session.query(func.count(Chunk.id)).scalar() or 0,
        "embeddings": session.query(func.count(Embedding.id)).scalar() or 0,
        "total_tokens": session.query(func.coalesce(func.sum(Chunk.tokens), 0)).scalar() or 0,
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    session = get_session()
    try:
        stats = _get_dashboard_context(session)
        recent_docs = session.query(Document).order_by(desc(Document.created_at)).limit(10).all()
    finally:
        session.close()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "stats": stats, "recent_docs": recent_docs},
    )


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = ""):
    results = searcher.search(q, top_k=10) if q else []
    return templates.TemplateResponse("search.html", {"request": request, "query": q, "results": results})


@app.get("/ingest", response_class=HTMLResponse)
async def ingest_page(request: Request):
    return templates.TemplateResponse("ingest.html", {"request": request})


@app.get("/documents", response_class=HTMLResponse)
async def documents_page(request: Request):
    session = get_session()
    try:
        docs = session.query(Document).order_by(desc(Document.created_at)).limit(200).all()
    finally:
        session.close()
    return templates.TemplateResponse("documents.html", {"request": request, "documents": docs})


@app.get("/health")
async def health():
    session = get_session()
    try:
        total = session.query(func.count(Document.id)).scalar() or 0
        return {"status": "ok", "backend": get_backend(), "documents": total}
    finally:
        session.close()


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    session = get_session()
    try:
        DOCUMENT_COUNT.set(session.query(func.count(Document.id)).scalar() or 0)
        CHUNK_COUNT.set(session.query(func.count(Chunk.id)).scalar() or 0)
        EMBEDDING_COUNT.set(session.query(func.count(Embedding.chunk_id)).scalar() or 0)
    finally:
        session.close()
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/stats")
async def stats(request: Request):
    _log_access("stats_read", token_label=_get_token_label_from_request(request), ip=_extract_client_ip(request))
    session = get_session()
    try:
        documents = session.query(func.count(Document.id)).scalar() or 0
        chunks = session.query(func.count(Chunk.id)).scalar() or 0
        embeddings = session.query(func.count(Embedding.chunk_id)).scalar() or 0
        pinned = session.query(func.count(Document.id)).filter(Document.pinned.is_(True)).scalar() or 0
        critical = (
            session.query(func.count(Document.id))
            .filter(or_(Document.pinned.is_(True), Document.importance <= 2))
            .scalar()
            or 0
        )
        tokens = session.query(func.sum(Chunk.token_count)).scalar() or 0
        categories = session.query(Document.category, func.count(Document.id)).group_by(Document.category).all()
        return {
            "backend": get_backend(),
            "documents": documents,
            "chunks": chunks,
            "embeddings": embeddings,
            "pinned": pinned,
            "critical": critical,
            "tokens": tokens,
            "categories": {name or "general": count for name, count in categories},
        }
    finally:
        session.close()


@app.get("/api/documents")
async def documents(
    limit: int = Query(50, ge=1, le=300),
    q: str = "",
    category: str = "",
    filetype: str = "",
    critical: bool = False,
):
    session = get_session()
    try:
        query = session.query(Document)
        if q:
            query = query.filter(or_(Document.filename.ilike(f"%{q}%"), Document.title.ilike(f"%{q}%")))
        if category:
            query = query.filter(Document.category == category)
        if filetype:
            query = query.filter(Document.filetype == filetype)
        if critical:
            query = query.filter(or_(Document.pinned.is_(True), Document.importance <= 2))
        docs = query.order_by(desc(Document.pinned), Document.importance, desc(Document.created_at)).limit(limit).all()
        items = []
        for doc in docs:
            preview = doc.chunks[0].content[:220] if doc.chunks else ""
            items.append(_document_payload(doc, preview))
        return {"items": items, "count": len(items)}
    finally:
        session.close()


@app.get("/api/context")
async def context(limit: int = Query(12, ge=1, le=50)):
    session = get_session()
    try:
        docs = (
            session.query(Document)
            .filter(or_(Document.pinned.is_(True), Document.importance <= 2))
            .order_by(desc(Document.pinned), Document.importance, desc(Document.updated_at))
            .limit(limit)
            .all()
        )
        lines = []
        for doc in docs:
            preview = doc.chunks[0].content[:500].replace("\n", " ") if doc.chunks else ""
            lines.append(
                {
                    "id": doc.id,
                    "title": doc.title or doc.filename,
                    "category": doc.category,
                    "importance": doc.importance,
                    "pinned": doc.pinned,
                    "content": preview,
                }
            )
        return {"items": lines}
    finally:
        session.close()


@app.post("/api/memory")
@limiter.limit("30/minute")  # Rate limit: 30 requests per minute
async def store_memory(
    request: Request,
    content: str = Form(...),
    title: str = Form("Manual memory"),
    category: str = Form("agent"),
    importance: int = Form(2),
    pinned: bool = Form(False),
    tags: str = Form(""),
    source: str = Form("dashboard"),
):
    token_label = _get_token_label_from_request(request)
    ip = _extract_client_ip(request)
    try:
        result = _store_text_memory(content, title, category, importance, pinned, tags, source)
        _log_access("store_memory", token_label=token_label, document_id=result["id"], ip=ip)
        return {"status": "stored", **result}
    except Exception as e:
        _log_access("store_memory", token_label=token_label, ip=ip, success=False, details={"error": str(e)})
        raise


@app.post("/api/upload")
@limiter.limit("10/minute")  # Rate limit: 10 uploads per minute
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("knowledge"),
    importance: int = Form(3),
    pinned: bool = Form(False),
    tags: str = Form(""),
):
    token_label = _get_token_label_from_request(request)
    ip = _extract_client_ip(request)
    suffix = os.path.splitext(file.filename or "")[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = processor.ingest_document(
            tmp_path,
            metadata={
                "title": file.filename,
                "filename": file.filename,
                "category": category,
                "importance": importance,
                "pinned": pinned,
                "tags": tags,
                "source": "upload",
            },
        )
        _log_access("upload", token_label=token_label, ip=ip, details={"filename": file.filename})
        return result
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.get("/api/search")
async def search(
    request: Request,
    q: str = Query(..., min_length=1),
    k: int = Query(8, ge=1, le=30),
    category: str = "",
    filetype: str = "",
):
    token_label = _get_token_label_from_request(request)
    _log_access("search", token_label=token_label, ip=_extract_client_ip(request), details={"query": q[:100]})
    filters = {}
    if category:
        filters["category"] = category
    if filetype:
        filters["filetype"] = filetype
    results = searcher.search(q, top_k=k, filters=filters)
    return {"query": q, "results": [item.to_dict() for item in results], "total": len(results)}


@app.patch("/api/documents/{document_id}")
@limiter.limit("60/minute")
async def update_document(
    request: Request,
    document_id: int,
    pinned: bool | None = None,
    importance: int | None = Query(None, ge=1, le=5),
    category: str | None = None,
):
    token_label = _get_token_label_from_request(request)
    session = get_session()
    try:
        doc = session.query(Document).filter(Document.id == document_id).first()
        if not doc:
            _log_access("update_document", token_label=token_label, document_id=document_id, success=False, details={"error": "not found"})
            raise HTTPException(status_code=404, detail="Document not found")
        if pinned is not None:
            doc.pinned = pinned
        if importance is not None:
            doc.importance = importance
        if category is not None:
            doc.category = category
        session.commit()
        _log_access("update_document", token_label=token_label, document_id=document_id)
        return {"updated": True}
    finally:
        session.close()


@app.delete("/api/documents/{document_id}")
@limiter.limit("30/minute")
async def delete_document(request: Request, document_id: int):
    token_label = _get_token_label_from_request(request)
    session = get_session()
    try:
        doc = session.query(Document).filter(Document.id == document_id).first()
        if not doc:
            _log_access("delete_document", token_label=token_label, document_id=document_id, success=False, details={"error": "not found"})
            raise HTTPException(status_code=404, detail="Document not found")
        session.delete(doc)
        session.commit()
        _log_access("delete_document", token_label=token_label, document_id=document_id)
        return {"deleted": True}
    finally:
        session.close()


@app.get("/api/documents/{document_id}/content")
async def get_document_content(document_id: int):
    """Get full content of a document with all chunks concatenated."""
    session = get_session()
    try:
        doc = session.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        chunks = sorted(doc.chunks, key=lambda c: c.chunk_index)
        full_content = "\n\n---\n\n".join(c.content for c in chunks)
        chunk_details = [
            {
                "index": c.chunk_index,
                "token_count": c.token_count,
                "content_preview": c.content[:300],
                "full_length": len(c.content),
            }
            for c in chunks
        ]
        return {
            "id": doc.id,
            "filename": doc.filename,
            "filetype": doc.filetype,
            "title": doc.title or doc.filename,
            "category": doc.category,
            "importance": doc.importance,
            "pinned": doc.pinned,
            "encrypted": doc.encrypted,
            "chunks_count": len(chunks),
            "total_tokens": sum(c.token_count for c in chunks),
            "content": full_content,
            "chunk_details": chunk_details,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        }
    finally:
        session.close()


@app.get("/api/activity")
async def get_activity_data():
    """Get activity data for charts: documents per day, category sizes, importance distribution."""
    session = get_session()
    try:
        from sqlalchemy import extract as sa_extract

        daily = (
            session.query(
                sa_extract("year", Document.created_at).label("year"),
                sa_extract("month", Document.created_at).label("month"),
                sa_extract("day", Document.created_at).label("day"),
                func.count(Document.id),
                func.sum(Document.filesize).label("total_size"),
            )
            .group_by("year", "month", "day")
            .order_by("year", "month", "day")
            .all()
        )
        daily_chart = [
            {
                "date": f"{int(y):04d}-{int(m):02d}-{int(d):02d}",
                "count": cnt,
                "size_bytes": int(total_size or 0),
            }
            for y, m, d, cnt, total_size in daily
        ]

        category_sizes = (
            session.query(
                Document.category,
                func.count(Document.id),
                func.sum(Document.filesize),
                func.avg(Document.filesize),
            )
            .group_by(Document.category)
            .all()
        )
        category_chart = [
            {
                "name": cat or "general",
                "count": cnt,
                "total_bytes": int(total or 0),
                "avg_bytes": int(avg or 0),
            }
            for cat, cnt, total, avg in category_sizes
        ]

        importance_dist = (
            session.query(Document.importance, func.count(Document.id))
            .group_by(Document.importance)
            .order_by(Document.importance)
            .all()
        )
        importance_chart = [{"level": lvl, "count": cnt} for lvl, cnt in importance_dist]

        filetype_dist = (
            session.query(Document.filetype, func.count(Document.id))
            .group_by(Document.filetype)
            .all()
        )
        filetype_chart = [{"type": ft, "count": cnt} for ft, cnt in filetype_dist]

        return {
            "daily": daily_chart,
            "categories": category_chart,
            "importance": importance_chart,
            "filetypes": filetype_chart,
        }
    finally:
        session.close()


# ============== SECURITY API ==============

@app.get("/api/tokens")
async def list_tokens(request: Request):
    _log_access("token_list", token_label=_get_token_label_from_request(request), ip=_extract_client_ip(request))
    session = get_session()
    try:
        tokens = session.query(ApiToken).order_by(desc(ApiToken.created_at)).all()
        return {
            "items": [
                {
                    "id": t.id,
                    "label": t.label,
                    "scopes": t.scopes,
                    "active": t.active,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                    "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
                    "created_by": t.created_by,
                }
                for t in tokens
            ]
        }
    finally:
        session.close()


@app.post("/api/tokens")
@limiter.limit("5/minute")  # Token creation is sensitive
async def create_token(request: Request):
    ip = _extract_client_ip(request)
    _log_access("token_create", ip=ip, details={"ip": ip})
    label = request.query_params.get("label", "unnamed-token")
    scopes = request.query_params.get("scopes", "read")
    days = request.query_params.get("days", None)
    if days:
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(days=int(days))
    else:
        expires_at = None
    scopes_list = {s.strip() for s in scopes.split(",")}
    invalid = scopes_list - VALID_SCOPES
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid scopes: {invalid}")
    raw_token, token_hash = generate_token()
    session = get_session()
    try:
        token = ApiToken(
            token_hash=token_hash,
            label=label,
            scopes=",".join(sorted(scopes_list)),
            active=True,
            expires_at=expires_at,
            created_by=_get_token_label_from_request(request) or "dashboard",
        )
        session.add(token)
        session.commit()
        _log_access("token_create", token_label=label, success=True, details={"scopes": token.scopes})
        return {
            "token": raw_token,
            "label": token.label,
            "scopes": token.scopes,
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
            "warning": "Save this token now — it will not be shown again",
        }
    finally:
        session.close()


@app.patch("/api/tokens/{token_id}")
async def update_token(token_id: int, request: Request):
    _log_access("token_update", token_label=_get_token_label_from_request(request), ip=_extract_client_ip(request))
    session = get_session()
    try:
        token = session.query(ApiToken).filter(ApiToken.id == token_id).first()
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")
        body = await request.json()
        if "active" in body:
            token.active = bool(body["active"])
        if "label" in body:
            token.label = body["label"]
        if "scopes" in body:
            scopes_list = {s.strip() for s in body["scopes"].split(",")}
            invalid = scopes_list - VALID_SCOPES
            if invalid:
                raise HTTPException(status_code=400, detail=f"Invalid scopes: {invalid}")
            token.scopes = ",".join(sorted(scopes_list))
        session.commit()
        return {"updated": True}
    finally:
        session.close()


@app.delete("/api/tokens/{token_id}")
async def delete_token(token_id: int, request: Request):
    label = _get_token_label_from_request(request)
    _log_access("token_delete", token_label=label, ip=_extract_client_ip(request))
    session = get_session()
    try:
        token = session.query(ApiToken).filter(ApiToken.id == token_id).first()
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")
        session.delete(token)
        session.commit()
        return {"deleted": True}
    finally:
        session.close()


@app.get("/api/access-log")
async def get_access_log(
    limit: int = Query(100, ge=1, le=500),
    action: str = "",
    success: bool | None = None,
):
    session = get_session()
    try:
        query = session.query(AccessLog).order_by(desc(AccessLog.timestamp))
        if action:
            query = query.filter(AccessLog.action == action)
        if success is not None:
            query = query.filter(AccessLog.success == success)
        logs = query.limit(limit).all()
        return {
            "items": [
                {
                    "id": log_entry.id,
                    "timestamp": log_entry.timestamp.isoformat() if log_entry.timestamp else None,
                    "action": log_entry.action,
                    "token_label": log_entry.token_label,
                    "document_id": log_entry.document_id,
                    "ip_address": log_entry.ip_address,
                    "success": log_entry.success,
                    "details": log_entry.details or {},
                }
                for log_entry in logs
            ],
            "count": len(logs),
        }
    finally:
        session.close()


@app.delete("/api/access-log")
@limiter.limit("10/minute")
async def clear_access_log(request: Request):
    _log_access("log_clear", token_label=_get_token_label_from_request(request), ip=_extract_client_ip(request))
    older_than_days = int(request.query_params.get("older_than_days", 0))
    session = get_session()
    try:
        if older_than_days > 0:
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=older_than_days)
            deleted = session.query(AccessLog).filter(AccessLog.timestamp < cutoff).delete()
        else:
            deleted = session.query(AccessLog).delete()
        session.commit()
        return {"deleted": deleted}
    finally:
        session.close()


@app.post("/api/documents/{document_id}/encrypt")
@limiter.limit("20/minute")
async def encrypt_document(document_id: int, request: Request):
    _log_access("document_encrypt", token_label=_get_token_label_from_request(request), document_id=document_id, ip=_extract_client_ip(request))
    session = get_session()
    try:
        doc = session.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc.encrypted:
            return {"encrypted": True, "warning": "Already encrypted"}
        from cryptography.fernet import Fernet
        key = _get_encryption_key()
        f = Fernet(key)
        salt = create_encryption_salt()
        for chunk in doc.chunks:
            content_bytes = chunk.content.encode("utf-8")
            encrypted = f.encrypt(content_bytes).decode()
            chunk.content = encrypted
            emb = session.query(Embedding).filter(Embedding.chunk_id == chunk.id).first()
            if emb and emb.embedding is not None:
                import json
                if hasattr(emb.embedding, 'tolist'):
                    emb_vec = emb.embedding.tolist()
                elif isinstance(emb.embedding, str):
                    emb_vec = json.loads(emb.embedding)
                else:
                    emb_vec = list(emb.embedding)
                emb.encrypted_embedding = encrypt_embedding(json.dumps(emb_vec).encode(), key)
                if not Vector:
                    emb.embedding = None
        doc.encrypted = True
        doc.encryption_salt = salt
        session.commit()
        return {"encrypted": True, "document_id": document_id}
    finally:
        session.close()


@app.post("/api/documents/{document_id}/decrypt")
async def decrypt_document(document_id: int, request: Request):
    _log_access("document_decrypt", token_label=_get_token_label_from_request(request), document_id=document_id, ip=_extract_client_ip(request))
    session = get_session()
    try:
        doc = session.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if not doc.encrypted:
            return {"decrypted": True, "warning": "Not encrypted"}
        from cryptography.fernet import Fernet
        key = _get_encryption_key()
        f = Fernet(key)
        for chunk in doc.chunks:
            try:
                chunk.content = f.decrypt(chunk.content.encode()).decode()
            except Exception:
                pass
            emb = session.query(Embedding).filter(Embedding.chunk_id == chunk.id).first()
            if emb and emb.encrypted_embedding:
                try:
                    import json
                    decrypted_bytes = decrypt_embedding(emb.encrypted_embedding, key)
                    emb_vec = json.loads(decrypted_bytes)
                    if Vector:
                        emb.embedding = emb_vec
                    else:
                        emb.embedding = json.dumps(emb_vec)
                    emb.encrypted_embedding = None
                except Exception:
                    pass
        doc.encrypted = False
        doc.encryption_salt = None
        session.commit()
        return {"decrypted": True, "document_id": document_id}
    finally:
        session.close()


def main():
    import uvicorn

    uvicorn.run(app, host=config.web.host, port=config.web.port)


DASHBOARD_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ActiveMemory</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@14.1.3/marked.min.js"></script>
<style>
:root,[data-theme="light"]{
  --bg:#f4f7fb;--surface:#ffffff;--surface-2:#f8fafc;--ink:#101828;--muted:#667085;
  --line:#d9e2ec;--line-strong:#b8c4d2;--brand:#0f766e;--brand-2:#2563eb;
  --accent:#b7791f;--danger:#b42318;--ok:#067647;--shadow:0 18px 44px rgba(16,24,40,.10);
  --radius:8px;--sidebar:#0d1726;--sidebar-2:#162235;--sidebar-text:#e8eef7;
  --drop-bg:#f0fdf4;--drop-border:#86efac;--drop-hover:#bbf7d0;
  --preview-bg:#ffffff;--code-bg:#f3f4f6;
}
[data-theme="dark"]{
  --bg:#0a0f1a;--surface:#1a2235;--surface-2:#1f2942;--ink:#e5e7eb;--muted:#9ca3af;
  --line:#2a3548;--line-strong:#3a4a60;--brand:#10b981;--brand-2:#06b6d4;
  --accent:#f59e0b;--danger:#ef4444;--ok:#22c55e;--shadow:0 18px 44px rgba(0,0,0,.4);
  --radius:8px;--sidebar:#0d1726;--sidebar-2:#111827;--sidebar-text:#e8eef7;
  --drop-bg:#0f1a0f;--drop-border:#166534;--drop-hover:#14532d;
  --preview-bg:#1a2235;--code-bg:#111827;
}
*{box-sizing:border-box}html{min-width:320px}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;line-height:1.45;letter-spacing:0;transition:background .3s,color .3s}
button,input,select,textarea{font:inherit;letter-spacing:0}button{cursor:pointer}h1,h2,h3,p{margin:0}.app{display:grid;grid-template-columns:272px minmax(0,1fr);min-height:100vh}
.sidebar{background:var(--sidebar);color:var(--sidebar-text);padding:18px;position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:18px;transition:background .3s}
.brand{display:flex;gap:10px;align-items:center}.mark{width:34px;height:34px;border-radius:8px;background:var(--brand);display:grid;place-items:center;font-weight:800;color:white;transition:background .3s}.brand-title{font-weight:800;font-size:18px}.brand-sub{color:#a9b6c8;font-size:12px}
.status{border:1px solid rgba(255,255,255,.12);background:var(--sidebar-2);border-radius:var(--radius);padding:12px;display:grid;gap:8px}.status-line{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:#b9c6d7}.status strong{color:white;font-size:13px}.dot{width:8px;height:8px;border-radius:50%;background:#28c76f;display:inline-block;margin-right:6px}
.nav{display:grid;gap:6px}.nav button{border:1px solid transparent;background:transparent;color:#bdc8d8;border-radius:var(--radius);padding:11px 12px;text-align:left;display:flex;gap:10px;align-items:center}.nav button:hover,.nav button.active{background:#1c2a40;border-color:#30435f;color:white}.nav .ico{width:18px;text-align:center;color:#8bc4ff}
.theme-toggle{display:flex;align-items:center;gap:8px;padding:10px 12px;border-radius:var(--radius);background:var(--sidebar-2);border:1px solid rgba(255,255,255,.1);cursor:pointer;color:#bdc8d8;font-size:13px;font-weight:600}.theme-toggle:hover{background:#1c2a40;color:white}.theme-toggle .icon{font-size:16px}
.side-foot{margin-top:auto;font-size:12px;color:#9dadc2;border-top:1px solid rgba(255,255,255,.10);padding-top:14px}
.main{padding:24px;max-width:1520px;width:100%;margin:0 auto}.command{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:20px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:center;margin-bottom:16px;transition:background .3s,border-color .3s}
.eyebrow{color:var(--brand);font-size:12px;text-transform:uppercase;font-weight:800;margin-bottom:6px}.command h1{font-size:28px;line-height:1.12}.command p{color:var(--muted);max-width:760px;margin-top:8px}.actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.btn{border:1px solid var(--line-strong);background:var(--surface);color:var(--ink);border-radius:var(--radius);padding:10px 12px;font-weight:700;min-height:40px;display:inline-flex;gap:8px;align-items:center;justify-content:center;transition:all .2s}.btn:hover{border-color:var(--brand);color:var(--brand)}.btn.primary{background:var(--brand);border-color:var(--brand);color:white}.btn.blue{background:var(--brand-2);border-color:var(--brand-2);color:white}.btn.danger{color:var(--danger);border-color:#f2b8b5;background:#fff7f7}.btn.danger:hover{background:var(--danger);color:white}.btn.slim{padding:7px 10px;min-height:34px;font-size:13px}
.view{display:none}.view.active{display:block}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px;min-height:116px;transition:background .3s,border-color .3s}.metric-label{font-size:12px;color:var(--muted);font-weight:800;text-transform:uppercase}.metric-value{font-size:32px;font-weight:850;margin-top:8px;color:var(--ink)}.metric-note{font-size:12px;color:var(--muted);margin-top:4px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:14px;margin-top:14px}.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;transition:background .3s,border-color .3s}.panel-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}.panel h2{font-size:17px}.panel p{color:var(--muted);font-size:13px;margin-top:4px}
.list{display:grid;gap:10px}.memory-row{border:1px solid var(--line);background:var(--surface-2);border-radius:var(--radius);padding:13px;display:grid;gap:10px;transition:background .3s,border-color .3s}.row-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.memory-row h3{font-size:15px;line-height:1.25;cursor:pointer;color:var(--brand)}.memory-row h3:hover{text-decoration:underline}.chips{display:flex;gap:6px;flex-wrap:wrap}.chip{border:1px solid var(--line);background:var(--surface);color:var(--muted);border-radius:999px;padding:3px 8px;font-size:12px;font-weight:700}.chip.pin{color:#8a5a00;background:#fff7df;border-color:#eed28a}.preview{color:#475467;font-size:13px;overflow-wrap:anywhere}.row-actions{display:flex;gap:8px;flex-wrap:wrap}
.category-row{display:grid;grid-template-columns:1fr auto;gap:8px;border-bottom:1px solid var(--line);padding:9px 0}.category-row:last-child{border-bottom:0}.category-name{font-weight:700}.category-count{color:var(--brand);font-weight:800}
.searchbar{display:grid;grid-template-columns:minmax(0,1fr) 170px 140px auto;gap:10px;margin-bottom:14px}.field{display:grid;gap:6px;margin-bottom:12px}.field span{font-size:12px;color:var(--muted);font-weight:800}input,select,textarea{width:100%;border:1px solid var(--line-strong);border-radius:var(--radius);background:var(--surface);color:var(--ink);padding:10px 11px;min-height:40px;transition:background .3s,border-color .3s}textarea{min-height:160px;resize:vertical}input:focus,select:focus,textarea:focus{outline:2px solid rgba(15,118,110,.18);border-color:var(--brand)}
.forms{display:grid;grid-template-columns:minmax(0,1fr) 420px;gap:14px}.split{display:grid;grid-template-columns:1fr 150px;gap:10px}.check{display:flex;gap:8px;align-items:center;color:var(--muted);font-weight:700;margin-bottom:12px}.check input{width:16px;min-height:16px}.context-box{background:#0e1624;color:#e8eef7;border-radius:var(--radius);padding:16px;white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;min-height:360px;max-height:520px;overflow:auto}
.empty{border:1px dashed var(--line-strong);border-radius:var(--radius);padding:22px;text-align:center;color:var(--muted);background:var(--surface-2)}.toast{position:fixed;right:18px;bottom:18px;background:#101828;color:white;border-radius:var(--radius);box-shadow:var(--shadow);padding:12px 14px;display:none;max-width:360px;z-index:20}.toast.show{display:block}.toast.error{background:#7a271a}

/* Drag-n-drop upload zone */
.dropzone{border:2px dashed var(--line-strong);border-radius:var(--radius);padding:40px 20px;text-align:center;transition:all .2s;cursor:pointer;position:relative}.dropzone:hover,.dropzone.drag-over{border-color:var(--brand);background:var(--drop-bg)}.dropzone.drag-over{transform:scale(1.01)}.dropzone-icon{font-size:48px;margin-bottom:12px;opacity:.6}.dropzone-text{font-size:14px;color:var(--muted);font-weight:600}.dropzone-hint{font-size:12px;color:var(--muted);margin-top:6px;opacity:.7}.dropzone input[type="file"]{position:absolute;inset:0;opacity:0;cursor:pointer}
.upload-queue{display:grid;gap:8px;margin-top:12px}.upload-item{display:flex;align-items:center;gap:12px;padding:10px 14px;background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius);font-size:13px}.upload-item .name{flex:1;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.upload-item .status{font-size:12px;color:var(--muted);min-width:70px;text-align:right}.upload-progress{height:4px;background:var(--line);border-radius:2px;overflow:hidden;width:120px}.upload-progress-bar{height:100%;background:var(--brand);border-radius:2px;transition:width .3s}.upload-item.done .status{color:var(--ok)}.upload-item.error .status{color:var(--danger)}

/* Preview modal */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;display:none;align-items:center;justify-content:center;padding:24px}.modal-overlay.open{display:flex}.modal{background:var(--surface);border:1px solid var(--line);border-radius:12px;max-width:900px;width:100%;max-height:85vh;display:flex;flex-direction:column;box-shadow:var(--shadow);overflow:hidden}.modal-header{display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-bottom:1px solid var(--line);flex-shrink:0}.modal-header h3{font-size:16px;font-weight:700}.modal-close{background:none;border:none;font-size:22px;color:var(--muted);cursor:pointer;padding:4px 8px;border-radius:6px}.modal-close:hover{background:var(--surface-2);color:var(--ink)}.modal-body{padding:20px;overflow-y:auto;flex:1}.modal-body .rendered-markdown{line-height:1.7}.modal-body .rendered-markdown pre{background:var(--code-bg);padding:14px;border-radius:8px;overflow-x:auto;font-size:13px}.modal-body .rendered-markdown code{background:var(--code-bg);padding:2px 6px;border-radius:4px;font-size:13px}.modal-body .rendered-markdown pre code{background:none;padding:0}.modal-body .plain-text{white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;color:var(--muted)}.modal-meta{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}.modal-meta .chip{font-size:11px}
.chunk-list{margin-top:20px;border-top:1px solid var(--line);padding-top:16px}.chunk-item{padding:10px 0;border-bottom:1px solid var(--line)}.chunk-item:last-child{border-bottom:none}.chunk-label{font-size:11px;color:var(--muted);font-weight:700;margin-bottom:4px}.chunk-preview{font-size:12px;color:var(--muted);white-space:pre-wrap}

/* Charts */
.charts-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.chart-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;transition:background .3s,border-color .3s}.chart-card h3{font-size:14px;font-weight:700;margin-bottom:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}.chart-canvas-wrap{position:relative;height:240px}

@media(max-width:1020px){.app{grid-template-columns:1fr}.sidebar{position:static;height:auto;display:grid;grid-template-columns:1fr;gap:12px}.nav{grid-template-columns:repeat(4,1fr)}.nav button{justify-content:center;text-align:center}.side-foot{display:none}.command{grid-template-columns:1fr}.actions{justify-content:flex-start}.layout,.forms{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.searchbar{grid-template-columns:1fr 1fr}.charts-grid{grid-template-columns:1fr}}
@media(max-width:520px){.main{padding:10px}.sidebar{padding:10px}.brand-title{font-size:16px}.brand-sub{display:none}.nav{grid-template-columns:repeat(4,minmax(0,1fr));gap:5px}.nav button{font-size:11px;padding:8px 4px;min-height:44px;display:grid;gap:3px}.nav .ico{width:auto}.command{padding:14px;margin-bottom:10px}.command h1{font-size:22px}.command p{font-size:13px}.actions .btn{flex:1 1 130px}.metrics{gap:8px}.metric-card{padding:10px;min-height:96px}.metric-value{font-size:24px}.layout{gap:10px;margin-top:10px}.panel{padding:12px}.panel-head{display:grid}.searchbar{grid-template-columns:1fr}.split{grid-template-columns:1fr}.row-top{display:grid}.row-actions .btn{flex:1 1 100px}.toast{left:10px;right:10px;bottom:10px;max-width:none}}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <div class="mark">AM</div>
      <div><div class="brand-title">ActiveMemory</div><div class="brand-sub">Agent memory OS</div></div>
    </div>
    <div class="status">
      <strong><span class="dot"></span><span id="statusText">Инициализация</span></strong>
      <div class="status-line"><span>Хранилище</span><span id="backendText">-</span></div>
      <div class="status-line"><span>Контекст</span><span id="criticalText">-</span></div>
    </div>
    <nav class="nav">
      <button class="active" data-view="overview"><span class="ico">01</span><span>Обзор</span></button>
      <button data-view="search"><span class="ico">02</span><span>Поиск</span></button>
      <button data-view="ingest"><span class="ico">03</span><span>Запись</span></button>
      <button data-view="context"><span class="ico">04</span><span>Контекст</span></button>
      <button data-view="charts"><span class="ico">05</span><span>Графики</span></button>
      <button data-view="security"><span class="ico">06</span><span>Безопасность</span></button>
    </nav>
    <button class="theme-toggle" onclick="toggleTheme()">
      <span class="icon" id="themeIcon">🌙</span>
      <span id="themeLabel">Тёмная тема</span>
    </button>
    <div class="side-foot">Локальный центр знаний для агента. Критичная память закрепляется в рабочем контексте.</div>
  </aside>
  <main class="main">
    <section class="command">
      <div>
        <div class="eyebrow">Memory Control Center</div>
        <h1>Оперативная память агента</h1>
        <p>Управляйте знаниями, приоритетами и контекстом из одного интерфейса. PostgreSQL остаётся основным хранилищем, SQLite хранит резерв критичных данных.</p>
      </div>
      <div class="actions">
        <button class="btn primary" onclick="quickFocus()">Найти память</button>
        <button class="btn blue" onclick="switchView('ingest')">Добавить</button>
        <button class="btn" onclick="refreshAll()">Обновить</button>
      </div>
    </section>

    <section id="overview" class="view active">
      <div class="metrics" id="stats"></div>
      <div class="layout">
        <div class="panel">
          <div class="panel-head">
            <div><h2>Приоритетная память</h2><p>Закреплённые записи и всё с важностью 1-2.</p></div>
            <button class="btn slim" onclick="loadDocs(true)">Критичное</button>
          </div>
          <div class="list" id="docs"></div>
        </div>
        <div class="panel">
          <div class="panel-head"><div><h2>Сегменты знаний</h2><p>Распределение памяти по категориям.</p></div></div>
          <div id="categories"></div>
        </div>
      </div>
    </section>

    <section id="search" class="view">
      <div class="panel">
        <div class="panel-head"><div><h2>Поиск по памяти</h2><p>Гибридный поиск по embeddings и ключевым словам.</p></div></div>
        <div class="searchbar">
          <input id="q" placeholder="Что агент должен вспомнить?">
          <select id="cat"><option value="">Все категории</option><option>agent</option><option>project</option><option>knowledge</option><option>decision</option></select>
          <select id="type"><option value="">Все типы</option><option value="text">text</option><option value="pdf">pdf</option><option value="code">code</option><option value="markdown">markdown</option></select>
          <button class="btn primary" onclick="doSearch()">Искать</button>
        </div>
        <div class="list" id="results"></div>
      </div>
    </section>

    <section id="ingest" class="view">
      <div class="forms">
        <form class="panel" onsubmit="storeMemory(event)">
          <div class="panel-head"><div><h2>Новая память</h2><p>Сохраняйте правила, решения и важный контекст вручную.</p></div></div>
          <label class="field"><span>Заголовок</span><input id="title" value="Agent memory"></label>
          <label class="field"><span>Содержимое</span><textarea id="content" required placeholder="Факт, правило, решение или контекст для будущих запусков агента"></textarea></label>
          <div class="split">
            <label class="field"><span>Категория</span><select id="newCat"><option>agent</option><option>project</option><option>decision</option><option>knowledge</option></select></label>
            <label class="field"><span>Важность</span><select id="importance"><option value="1">1 critical</option><option value="2" selected>2 high</option><option value="3">3 normal</option><option value="4">4 low</option><option value="5">5 archive</option></select></label>
          </div>
          <label class="field"><span>Теги</span><input id="tags" placeholder="mcp, agent, rule"></label>
          <label class="check"><input id="pinned" type="checkbox"> Закрепить в рабочем контексте</label>
          <button class="btn primary" type="submit">Сохранить память</button>
        </form>
        <div class="panel">
          <div class="panel-head"><div><h2>Индексировать документы</h2><p>Перетащите файлы сюда или нажмите для выбора.</p></div></div>
          <div class="dropzone" id="dropzone">
            <div class="dropzone-icon">📁</div>
            <div class="dropzone-text">Перетащите файлы сюда</div>
            <div class="dropzone-hint">PDF, TXT, MD, PY, JS, TS и другие</div>
            <input id="fileInput" type="file" multiple>
          </div>
          <div class="upload-queue" id="uploadQueue"></div>
        </div>
      </div>
    </section>

    <section id="context" class="view">
      <div class="panel">
        <div class="panel-head">
          <div><h2>Рабочий контекст</h2><p>Готовая выжимка критичной памяти для передачи агенту.</p></div>
          <button class="btn" onclick="loadContext()">Собрать заново</button>
        </div>
        <div class="context-box" id="ctx"></div>
      </div>
    </section>

    <section id="charts" class="view">
      <div class="metrics" id="chartMetrics"></div>
      <div class="charts-grid">
        <div class="chart-card"><h3>Активность по дням</h3><div class="chart-canvas-wrap"><canvas id="chartActivity"></canvas></div></div>
        <div class="chart-card"><h3>Категории</h3><div class="chart-canvas-wrap"><canvas id="chartCategories"></canvas></div></div>
        <div class="chart-card"><h3>Распределение важности</h3><div class="chart-canvas-wrap"><canvas id="chartImportance"></canvas></div></div>
        <div class="chart-card"><h3>Типы файлов</h3><div class="chart-canvas-wrap"><canvas id="chartFiletypes"></canvas></div></div>
        <div class="chart-card"><h3>Векторное пространство</h3><div class="chart-canvas-wrap"><canvas id="chartVectorSpace"></canvas></div></div>
        <div class="chart-card"><h3>Тепловая карта доступа</h3><div class="chart-canvas-wrap"><canvas id="chartHeatmap"></canvas></div></div>
      </div>
    </section>

    <section id="security" class="view">
      <div class="layout">
        <div class="panel">
          <div class="panel-head"><div><h2>API токены</h2><p>Управление доступом к ActiveMemory API.</p></div></div>
          <div class="field"><span>Имя токена</span><input id="newTokenLabel" placeholder="my-app"></div>
          <div class="split">
            <label class="field"><span>Scope</span><select id="newTokenScopes"><option value="read">read</option><option value="write">write</option><option value="read,write">read+write</option><option value="admin">admin</option></select></label>
            <label class="field"><span>Дней (0 = ∞)</span><input id="newTokenDays" type="number" value="0" min="0"></label>
          </div>
          <button class="btn primary" onclick="createToken()">Создать токен</button>
          <div class="field" id="newTokenResultWrap" style="display:none;margin-top:12px">
            <span>Токен (сохраните сейчас!)</span>
            <input id="newTokenResult" readonly onclick="this.select()">
          </div>
          <div class="list" id="tokenList" style="margin-top:16px"></div>
        </div>
        <div class="panel">
          <div class="panel-head">
            <div><h2>Лог доступа</h2><p>Аудит всех запросов к API.</p></div>
            <button class="btn danger slim" onclick="clearLog()">Очистить</button>
          </div>
          <div style="overflow-x:auto">
            <table class="log-table" id="logTable"></table>
          </div>
        </div>
      </div>
    </section>
  </main>
</div>

<!-- Preview Modal -->
<div class="modal-overlay" id="previewModal">
  <div class="modal">
    <div class="modal-header">
      <h3 id="previewTitle">Preview</h3>
      <button class="modal-close" onclick="closePreview()">✕</button>
    </div>
    <div class="modal-body" id="previewBody"></div>
  </div>
</div>

<div class="toast" id="toast"></div>
<script>
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
}[c]));
async function api(endpoint, opts) {
  const res = await fetch(endpoint, opts);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
function toast(message, type='ok') {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast show' + (type === 'error' ? ' error' : '');
  setTimeout(() => el.classList.remove('show'), 3200);
}
function switchView(name) {
  document.querySelectorAll('.nav button,.view').forEach(el => el.classList.remove('active'));
  document.querySelector(`[data-view="${name}"]`)?.classList.add('active');
  $(name).classList.add('active');
  if (name === 'charts') loadCharts();
  if (name === 'security') { loadTokens(); loadAccessLog(); }
}
function quickFocus() {
  switchView('search');
  setTimeout(() => $('q').focus(), 40);
}
document.querySelectorAll('.nav button').forEach(btn => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});
function metric(label, value, note) {
  return `<div class="metric-card"><div class="metric-label">${esc(label)}</div><div class="metric-value">${esc(value)}</div><div class="metric-note">${esc(note)}</div></div>`;
}
async function loadStats() {
  const s = await api('/api/stats');
  $('statusText').textContent = 'Подключено';
  $('backendText').textContent = s.backend;
  $('criticalText').textContent = `${s.critical} записей`;
  $('stats').innerHTML = [
    metric('Документы', s.documents, 'в долговременной памяти'),
    metric('Чанки', s.chunks, 'индексированные фрагменты'),
    metric('Embeddings', s.embeddings, 'готово к поиску'),
    metric('Критичное', s.critical, `${s.pinned} закреплено`)
  ].join('');
  const cats = Object.entries(s.categories || {});
  $('categories').innerHTML = cats.length ? cats.map(([name, count]) =>
    `<div class="category-row"><span class="category-name">${esc(name)}</span><span class="category-count">${esc(count)}</span></div>`
  ).join('') : '<div class="empty">Категории появятся после добавления памяти</div>';
}
function renderDocs(items, target='docs') {
  $(target).innerHTML = items.length ? items.map(d => `
    <article class="memory-row">
      <div class="row-top">
        <h3 onclick="openPreview(${d.id})">${d.pinned ? '<span class="chip pin">PIN</span> ' : ''}${esc(d.title)}</h3>
        <button class="btn danger slim" onclick="delDoc(${d.id})">Удалить</button>
      </div>
      <div class="chips">
        <span class="chip">${esc(d.category)}</span>
        <span class="chip">${esc(d.filetype)}</span>
        <span class="chip">важность ${esc(d.importance)}</span>
        <span class="chip">${esc(d.chunks)} чанков</span>
      </div>
      <div class="preview">${esc(d.preview || 'Предпросмотр пока пуст')}</div>
      <div class="row-actions">
        <button class="btn slim" onclick="patchDoc(${d.id},${!d.pinned},null)">${d.pinned ? 'Открепить' : 'Закрепить'}</button>
        <button class="btn slim" onclick="patchDoc(${d.id},null,1)">Сделать critical</button>
        <button class="btn slim" onclick="openPreview(${d.id})">Предпросмотр</button>
      </div>
    </article>`).join('') : '<div class="empty">Память пуста. Добавьте первую запись или загрузите документ.</div>';
}
async function loadDocs(critical=false) {
  const d = await api('/api/documents?limit=80' + (critical ? '&critical=true' : ''));
  renderDocs(d.items || []);
}
async function doSearch() {
  const q = $('q').value.trim();
  if (!q) return toast('Введите поисковый запрос', 'error');
  $('results').innerHTML = '<div class="empty">Идёт поиск по памяти...</div>';
  try {
    const url = `/api/search?q=${encodeURIComponent(q)}&category=${encodeURIComponent($('cat').value)}&filetype=${encodeURIComponent($('type').value)}`;
    const d = await api(url);
    $('results').innerHTML = (d.results || []).length ? d.results.map(r => `
      <article class="memory-row">
        <div class="chips"><span class="chip">${Math.round(r.score * 100)}%</span><span class="chip">${esc(r.source)}</span><span class="chip">${esc(r.metadata.category || 'general')}</span></div>
        <div class="preview">${esc(r.content)}</div>
      </article>`).join('') : '<div class="empty">Ничего не найдено. Попробуйте другой запрос или категорию.</div>';
  } catch (err) {
    toast(err.message, 'error');
  }
}
async function storeMemory(e) {
  e.preventDefault();
  const form = new FormData();
  ['content','tags'].forEach(id => form.append(id, $(id).value));
  form.append('title', $('title').value);
  form.append('category', $('newCat').value);
  form.append('importance', $('importance').value);
  form.append('pinned', $('pinned').checked ? 'true' : 'false');
  await api('/api/memory', {method:'POST', body:form});
  $('content').value = '';
  toast('Память сохранена');
  refreshAll();
}

/* Drag-n-drop multiupload */
const dropzone = $('dropzone');
const fileInput = $('fileInput');
const uploadQueue = $('uploadQueue');
['dragenter','dragover'].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add('drag-over'); }));
['dragleave','drop'].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove('drag-over'); }));
dropzone.addEventListener('drop', e => handleFiles(e.dataTransfer.files));
fileInput.addEventListener('change', e => handleFiles(e.target.files));

async function handleFiles(files) {
  if (!files.length) return;
  for (const file of files) {
    const item = document.createElement('div');
    item.className = 'upload-item';
    item.innerHTML = `<span class="name">${esc(file.name)}</span><div class="upload-progress"><div class="upload-progress-bar" style="width:0%"></div></div><span class="status">Загрузка...</span>`;
    uploadQueue.prepend(item);
    const bar = item.querySelector('.upload-progress-bar');
    const status = item.querySelector('.status');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('category', $('upCat')?.value || 'knowledge');
      const xhr = new XMLHttpRequest();
      xhr.upload.addEventListener('progress', e => { if (e.lengthComputable) bar.style.width = (e.loaded/e.total*100)+'%'; });
      const promise = new Promise((resolve, reject) => {
        xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve(JSON.parse(xhr.responseText)) : reject(new Error(xhr.responseText));
        xhr.onerror = () => reject(new Error('Network error'));
      });
      xhr.open('POST', '/api/upload');
      xhr.send(form);
      const result = await promise;
      item.classList.add('done');
      bar.style.width = '100%';
      status.textContent = `${result.chunks || '?'} чанков`;
      toast(`${file.name} загружен`);
    } catch (err) {
      item.classList.add('error');
      status.textContent = 'Ошибка';
      toast(`${file.name}: ${err.message}`, 'error');
    }
  }
  refreshAll();
}

/* Document preview modal */
async function openPreview(id) {
  try {
    const d = await api(`/api/documents/${id}/content`);
    $('previewTitle').textContent = d.title || d.filename;
    let meta = `<div class="modal-meta">
      <span class="chip">${esc(d.filetype)}</span>
      <span class="chip">${esc(d.category)}</span>
      <span class="chip">важность ${esc(d.importance)}</span>
      <span class="chip">${esc(d.chunks_count)} чанков</span>
      <span class="chip">~${esc(d.total_tokens)} токенов</span>
      ${d.pinned ? '<span class="chip pin">PIN</span>' : ''}
    </div>`;
    let bodyContent = '';
    if (d.filetype === 'markdown' || d.filetype === 'md') {
      bodyContent = `<div class="rendered-markdown">${marked.parse(d.content)}</div>`;
    } else if (d.filetype === 'code' || d.filetype === 'py' || d.filetype === 'js' || d.filetype === 'ts') {
      bodyContent = `<pre><code class="plain-text">${esc(d.content)}</code></pre>`;
    } else {
      bodyContent = `<div class="plain-text">${esc(d.content)}</div>`;
    }
    let chunksHtml = '<div class="chunk-list"><h3 style="margin-bottom:12px;font-size:14px;">Чанки:</h3>';
    for (const ch of d.chunk_details) {
      chunksHtml += `<div class="chunk-item"><div class="chunk-label">Чанк #${ch.index} • ${ch.token_count} токенов • ${ch.full_length} символов</div><div class="chunk-preview">${esc(ch.content_preview)}${ch.full_length > 300 ? '...' : ''}</div></div>`;
    }
    chunksHtml += '</div>';
    $('previewBody').innerHTML = meta + bodyContent + chunksHtml;
    $('previewModal').classList.add('open');
  } catch (err) {
    toast('Не удалось загрузить превью: ' + err.message, 'error');
  }
}
function closePreview() { $('previewModal').classList.remove('open'); }
$('previewModal').addEventListener('click', e => { if (e.target === $('previewModal')) closePreview(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePreview(); });

/* Theme toggle */
function toggleTheme() {
  const current = localStorage.getItem('am-theme') || 'light';
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
}
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('am-theme', theme);
  const isDark = theme === 'dark';
  $('themeIcon').textContent = isDark ? '☀️' : '🌙';
  $('themeLabel').textContent = isDark ? 'Светлая тема' : 'Тёмная тема';
  if (window._chartInstances) {
    Object.values(window._chartInstances).forEach(c => {
      if (c.options) {
        c.options.scales.x.ticks.color = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
        c.options.scales.y.ticks.color = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
        c.options.plugins.legend.labels.color = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
        c.update('none');
      }
    });
  }
}
applyTheme(localStorage.getItem('am-theme') || 'light');

/* Charts */
.log-table{width:100%;border-collapse:collapse;font-size:12px}.log-table th{text-align:left;padding:6px 8px;border-bottom:2px solid var(--line-strong);color:var(--muted);font-weight:700;text-transform:uppercase;font-size:11px}.log-table td{padding:5px 8px;border-bottom:1px solid var(--line)}.log-table code{background:var(--code-bg);padding:1px 4px;border-radius:3px;font-size:11px}.token-row{border:1px solid var(--line);background:var(--surface-2);border-radius:var(--radius);padding:11px;display:grid;gap:8px}.token-meta{display:flex;gap:16px;font-size:11px;color:var(--muted)}.chip-inactive{color:#999;background:#eee;border-color:#ccc}
window._chartInstances = {};
async function loadCharts() {
  try {
    const stats = await api('/api/stats');
    $('chartMetrics').innerHTML = [
      metric('Документы', stats.documents, 'всего'),
      metric('Токены', stats.tokens.toLocaleString(), 'общий объём'),
      metric('Категории', Object.keys(stats.categories || {}).length, 'уникальных'),
      metric('Закреплено', stats.pinned, 'в контексте')
    ].join('');

    const activity = await api('/api/activity');
    const textColor = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
    const gridColor = getComputedStyle(document.documentElement).getPropertyValue('--line').trim();
    const brandColor = getComputedStyle(document.documentElement).getPropertyValue('--brand').trim();
    const catColors = [brandColor, '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#10b981', '#f97316'];

    window._chartInstances.activity = new Chart($('chartActivity'), {
      type: 'line',
      data: { labels: activity.activity.map(a => a.date), datasets: [{ data: activity.activity.map(a => a.count), borderColor: brandColor, backgroundColor: brandColor + '20', fill: true, tension: 0.4, pointRadius: 3 }] },
      options: { ...chartOpts(), scales: { x: { ticks: { color: textColor, maxRotation: 45 }, grid: { display: false } }, y: { ticks: { color: textColor }, grid: { color: gridColor }, beginAtZero: true } }, plugins: { title: { display: true, text: 'Активность по дням', color: textColor } } }
    });
    window._chartInstances.categories = new Chart($('chartCategories'), {
      type: 'doughnut',
      data: { labels: activity.categories.map(c => c.name), datasets: [{ data: activity.categories.map(c => c.count), backgroundColor: catColors.slice(0, activity.categories.length), borderWidth: 0 }] },
      options: { ...chartOpts(), plugins: { title: { display: true, text: 'Категории', color: textColor } } }
    });
    window._chartInstances.importance = new Chart($('chartImportance'), {
      type: 'bar',
      data: { labels: activity.importance.map(i => i.label), datasets: [{ data: activity.importance.map(i => i.count), backgroundColor: catColors, borderRadius: 6, barPercentage: 0.6 }] },
      options: { ...chartOpts(), scales: { x: { ticks: { color: textColor }, grid: { display: false } }, y: { ticks: { color: textColor }, grid: { color: gridColor }, beginAtZero: true } }, plugins: { title: { display: true, text: 'Распределение важности', color: textColor } } }
    });
    window._chartInstances.filetypes = new Chart($('chartFiletypes'), {
      type: 'bar',
      data: { labels: activity.filetypes.map(f => f.type), datasets: [{ data: activity.filetypes.map(f => f.count), backgroundColor: catColors.slice(0, activity.filetypes.length), borderRadius: 6, barPercentage: 0.6 }] },
      options: { ...chartOpts(), indexAxis: 'y', scales: { x: { ticks: { color: textColor }, grid: { color: gridColor }, beginAtZero: true }, y: { ticks: { color: textColor, font: { weight: 600 } }, grid: { display: false } } }, plugins: { title: { display: true, text: 'Типы файлов', color: textColor } } }
    });

    // Load new visualization charts
    await loadVectorSpace();
    await loadHeatmap();
  } catch (err) {
    toast('Ошибка загрузки графиков: ' + err.message, 'error');
  }
}
    });

    if (window._chartInstances.activity) window._chartInstances.activity.destroy();
    window._chartInstances.activity = new Chart($('chartActivity'), {
      type: 'line',
      data: {
        labels: activity.daily.map(d => d.date.slice(5)),
        datasets: [{
          label: 'Документы',
          data: activity.daily.map(d => d.count),
          borderColor: brandColor, backgroundColor: brandColor + '22',
          fill: true, tension: 0.3, pointRadius: 3
        }]
      },
      options: { ...chartOpts(), plugins: { legend: { display: false } } }
    });

    if (window._chartInstances.categories) window._chartInstances.categories.destroy();
    const catColors = ['#10b981','#06b6d4','#8b5cf6','#f59e0b','#ef4444','#ec4899','#3b82f6','#84cc16'];
    window._chartInstances.categories = new Chart($('chartCategories'), {
      type: 'doughnut',
      data: {
        labels: activity.categories.map(c => c.name),
        datasets: [{ data: activity.categories.map(c => c.count), backgroundColor: catColors, borderWidth: 0 }]
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right', labels: { color: textColor, font: { weight: 600 }, padding: 12 } } } }
    });

    if (window._chartInstances.importance) window._chartInstances.importance.destroy();
    const impLabels = {1:'Critical',2:'High',3:'Normal',4:'Low',5:'Archive'};
    const impColors = ['#ef4444','#f59e0b','#10b981','#3b82f6','#9ca3af'];
    window._chartInstances.importance = new Chart($('chartImportance'), {
      type: 'bar',
      data: {
        labels: activity.importance.map(i => impLabels[i.level] || i.level),
        datasets: [{ data: activity.importance.map(i => i.count), backgroundColor: activity.importance.map((_,i) => impColors[i.level-1] || '#9ca3af'), borderRadius: 6, barPercentage: 0.6 }]
      },
      options: chartOpts()
    });

    if (window._chartInstances.filetypes) window._chartInstances.filetypes.destroy();
    window._chartInstances.filetypes = new Chart($('chartFiletypes'), {
      type: 'bar',
      data: {
        labels: activity.filetypes.map(f => f.type),
        datasets: [{ data: activity.filetypes.map(f => f.count), backgroundColor: catColors.slice(0, activity.filetypes.length), borderRadius: 6, barPercentage: 0.6 }]
      },
      options: { ...chartOpts(), indexAxis: 'y', scales: { x: { ticks: { color: textColor }, grid: { color: gridColor }, beginAtZero: true }, y: { ticks: { color: textColor, font: { weight: 600 } }, grid: { display: false } } } }
    });
  } catch (err) {
    toast('Ошибка загрузки графиков: ' + err.message, 'error');
  }
}

/* Visualization — Vector Space & Heatmap */
async function loadVectorSpace() {
  try {
    const d = await api('/api/visualization/vector-space?method=tsne');
    if (!d.points || !d.points.length) {
      $('chartVectorSpace').parentElement.innerHTML = '<div class="empty">Нет данных для визуализации</div>';
      return;
    }
    
    const ctx = $('chartVectorSpace').getContext('2d');
    if (window._chartInstances.vectorSpace) window._chartInstances.vectorSpace.destroy();
    
    window._chartInstances.vectorSpace = new Chart(ctx, {
      type: 'scatter',
      data: {
        datasets: [{
          label: 'Documents',
          data: d.points.map((p, i) => ({ x: p[0], y: p[1], doc: d.documents[i] })),
          backgroundColor: catColors,
          radius: 6,
          hoverRadius: 9,
        }]
      },
      options: {
        ...chartOpts(),
        scales: {
          x: { display: false },
          y: { display: false }
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: function(ctx) {
                const doc = ctx.raw.doc;
                return doc ? `${doc.filename} (${doc.filetype})` : '';
              }
            }
          },
          title: {
            display: true,
            text: `Векторное пространство (${d.method.toUpperCase()})`,
            color: textColor,
          }
        }
      }
    });
  } catch (err) {
    console.error('Vector space error:', err);
  }
}

async function loadHeatmap() {
  try {
    const d = await api('/api/analytics/heatmap?days=7');
    if (!d.data) return;
    
    const ctx = $('chartHeatmap').getContext('2d');
    if (window._chartInstances.heatmap) window._chartInstances.heatmap.destroy();
    
    const dates = Object.keys(d.data);
    const types = ['search', 'view', 'context', 'update'];
    const colors = ['rgba(15,118,110,0.8)', 'rgba(59,130,246,0.8)', 'rgba(245,158,11,0.8)', 'rgba(239,68,68,0.8)'];
    
    window._chartInstances.heatmap = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: dates.reverse(),
        datasets: types.map((type, i) => ({
          label: type === 'search' ? 'Поиск' : type === 'view' ? 'Просмотр' : type === 'context' ? 'Контекст' : 'Обновление',
          data: dates.map(date => d.data[date][type] || 0).reverse(),
          backgroundColor: colors[i],
          stack: 'Stack 0',
        }))
      },
      options: {
        ...chartOpts(),
        scales: {
          x: { ticks: { color: textColor, maxRotation: 45 }, grid: { display: false } },
          y: { ticks: { color: textColor }, grid: { color: gridColor }, beginAtZero: true }
        },
        plugins: {
          title: {
            display: true,
            text: 'Тепловая карта активности (7 дней)',
            color: textColor,
          }
        }
      }
    });
  } catch (err) {
    console.error('Heatmap error:', err);
  }
}

/* Security — Tokens */
async function loadTokens() {
  const d = await api('/api/tokens');
  const items = d.items || [];
  $('tokenList').innerHTML = items.length ? items.map(t => `
    <article class="token-row">
      <div class="row-top">
        <h3>${esc(t.label)}</h3>
        <div class="chips">
          <span class="chip ${t.active ? '' : 'chip-inactive'}">${t.active ? 'active' : 'revoked'}</span>
          <span class="chip">${esc(t.scopes)}</span>
        </div>
      </div>
      <div class="token-meta">
        <span>Создан: ${esc(t.created_at?.slice(0,10) || '—')}</span>
        <span>Истёк: ${esc(t.expires_at?.slice(0,10) || '∞')}</span>
        <span>Последнее: ${esc(t.last_used_at?.slice(0,16) || '—')}</span>
      </div>
      <div class="row-actions">
        ${t.active ? `<button class="btn slim danger" onclick="revokeToken(${t.id})">Отозвать</button>` : ''}
        <button class="btn slim danger" onclick="deleteToken(${t.id})">Удалить</button>
      </div>
    </article>
  `).join('') : '<div class="empty">Нет токенов. Создайте первый.</div>';
}
async function createToken() {
  const label = $('newTokenLabel').value.trim();
  const scopes = $('newTokenScopes').value;
  const days = $('newTokenDays').value;
  if (!label) return toast('Введите имя токена', 'error');
  let url = `/api/tokens?label=${encodeURIComponent(label)}&scopes=${encodeURIComponent(scopes)}`;
  if (days) url += `&days=${encodeURIComponent(days)}`;
  const d = await api(url, {method:'POST'});
  $('newTokenLabel').value = '';
  if (d.token) {
    $('newTokenResult').value = d.token;
    $('newTokenResultWrap').style.display = '';
    toast('Токен создан — сохраните его!');
  }
  loadTokens();
}
async function revokeToken(id) {
  await api(`/api/tokens/${id}`, {method:'PATCH', body:JSON.stringify({active:false}), headers:{'Content-Type':'application/json'}});
  toast('Токен отозван'); loadTokens();
}
async function deleteToken(id) {
  if (!confirm('Удалить токен?')) return;
  await api(`/api/tokens/${id}`, {method:'DELETE'});
  toast('Токен удалён'); loadTokens();
}

/* Security — Access Log */
async function loadAccessLog() {
  const d = await api('/api/access-log?limit=200');
  const items = d.items || [];
  $('logTable').innerHTML = items.length ? items.map(l => `
    <tr>
      <td>${esc(log_entry.timestamp?.slice(0,19) || '—')}</td>
      <td><code>${esc(log_entry.action)}</code></td>
      <td>${esc(log_entry.token_label || '—')}</td>
      <td>${esc(log_entry.ip_address || '—')}</td>
      <td>${log_entry.success ? '<span class="chip" style="color:var(--ok)">✓</span>' : '<span class="chip" style="color:var(--danger)">✗</span>'}</td>
    </tr>
  `).join('') : '<tr><td colspan="5" class="empty">Нет записей</td></tr>';
}
async function clearLog() {
  if (!confirm('Очистить весь лог доступа?')) return;
  await api('/api/access-log', {method:'DELETE'});
  toast('Лог очищен'); loadAccessLog();
}

/* Context */
async function loadContext() {
  const d = await api('/api/context');
  const items = d.items || [];
  $('ctx').textContent = items.length ? items.map(x =>
    `[${x.category} | importance ${x.importance}${x.pinned ? ' | pinned' : ''}] ${x.title}\n${x.content}`
  ).join('\n\n') : 'Критичный контекст пока пуст';
}

async function patchDoc(id, pinned, importance) {
  let url = `/api/documents/${id}?`;
  if (pinned !== null) url += `pinned=${pinned}`;
  if (importance !== null) url += `${pinned !== null ? '&' : ''}importance=${importance}`;
  await api(url, {method:'PATCH'});
  refreshAll();
}
async function delDoc(id) {
  if (!confirm('Удалить запись памяти?')) return;
  await api('/api/documents/' + id, {method:'DELETE'});
  refreshAll();
}
async function refreshAll() {
  await loadStats();
  await loadDocs();
  await loadContext();
}
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
refreshAll().catch(err => toast(err.message, 'error'));
</script>
</body></html>"""

# ============= EXPORT / IMPORT / SNAPSHOTS =============

@app.get("/api/export")
async def export_data(
    format: str = Query("json"),
    include_embeddings: bool = Query(True),
):
    """Export all data as JSON or CSV."""
    if format.lower() == "csv":
        content = export_all_csv()
        return PlainTextResponse(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=active_memory_export.csv"},
        )
    else:
        data = export_all_json(include_embeddings=include_embeddings)
        return JSONResponse(content=data)


@app.get("/api/export/documents/{document_id}")
async def export_document(document_id: int, include_embeddings: bool = Query(True)):
    """Export a single document with all chunks."""
    data = export_document_json(document_id, include_embeddings=include_embeddings)
    if data is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return data


@app.get("/api/export/categories/{category}")
async def export_category(category: str, include_embeddings: bool = Query(True)):
    """Export all documents in a category."""
    data = export_category_json(category, include_embeddings=include_embeddings)
    return data


@app.post("/api/import")
@limiter.limit("5/minute")  # Import is resource-intensive
async def import_data(
    file: UploadFile = File(...),
    mode: str = Form("merge"),
    request: Request = None,
):
    """Import data from JSON or CSV file."""
    token_label = _get_token_label_from_request(request) if request else None
    ip = _extract_client_ip(request) if request else "unknown"
    
    content = await file.read()
    try:
        if file.filename.endswith('.json'):
            data = json.loads(content)
            result = import_from_json(data, mode=mode)
        elif file.filename.endswith('.csv'):
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            result = import_from_csv(tmp_path, mode=mode)
            os.unlink(tmp_path)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file format. Use .json or .csv")
        
        _log_access("import_data", token_label=token_label, ip=ip, details={"mode": mode, "filename": file.filename})
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    except Exception as e:
        _log_access("import_data", token_label=token_label, ip=ip, success=False, details={"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/import/validate")
async def validate_import(file: UploadFile = File(...)):
    """Validate import file without actually importing."""
    content = await file.read()
    try:
        if file.filename.endswith('.json'):
            data = json.loads(content)
            return validate_import_data(data)
        elif file.filename.endswith('.csv'):
            return {"valid": True, "message": "CSV validation not required (simple format)",
                    "errors": [], "warnings": []}
        else:
            return {"valid": False, "errors": ["Unsupported format"], "warnings": []}
    except json.JSONDecodeError as e:
        return {"valid": False, "errors": [f"JSON decode error: {e}"], "warnings": []}


@app.post("/api/snapshots")
async def create_snapshot_endpoint(request: Request, name: str = Query(None)):
    """Create a new database snapshot."""
    token_label = _get_token_label_from_request(request)
    ip = _extract_client_ip(request)
    
    result = create_snapshot(name)
    if result.get("success"):
        _log_access("create_snapshot", token_label=token_label, ip=ip, details={"name": result.get("name")})
    else:
        _log_access("create_snapshot", token_label=token_label, ip=ip, success=False, details=result)
    return result


@app.get("/api/snapshots")
async def list_snapshots_endpoint():
    """List all available snapshots."""
    return {"items": list_snapshots(), "count": len(list_snapshots())}


@app.post("/api/snapshots/{snapshot_name}/restore")
async def restore_snapshot_endpoint(snapshot_name: str, request: Request):
    """Restore database from a snapshot."""
    token_label = _get_token_label_from_request(request)
    ip = _extract_client_ip(request)
    
    result = restore_snapshot(snapshot_name)
    _log_access("restore_snapshot", token_label=token_label, ip=ip, details={"name": snapshot_name, "success": result.get("success")})
    return result


@app.delete("/api/snapshots/{snapshot_name}")
async def delete_snapshot_endpoint(snapshot_name: str, request: Request):
    """Delete a specific snapshot."""
    token_label = _get_token_label_from_request(request)
    ip = _extract_client_ip(request)
    
    result = delete_snapshot(snapshot_name)
    _log_access("delete_snapshot", token_label=token_label, ip=ip, details={"name": snapshot_name, "success": result.get("success")})
    return result


@app.get("/api/export/stats")
async def export_stats():
    """Get statistics about exportable data."""
    return get_export_stats()


@app.get("/api/snapshots/stats")
async def snapshot_stats():
    """Get snapshot statistics."""
    return get_snapshot_stats()


# ========== Visualization & Analytics Endpoints ==========

@app.get("/api/visualization/vector-space")
async def vector_space_endpoint(
    method: str = "tsne",
    perplexity: int = Query(30),
    n_neighbors: int = Query(15),
):
    """Get vector space visualization data (t-SNE/UMAP/PCA)."""
    from ..visualization import get_vector_space_data
    
    kwargs = {}
    if method == "tsne":
        kwargs["perplexity"] = perplexity
    elif method == "umap":
        kwargs["n_neighbors"] = n_neighbors
    
    return get_vector_space_data(method=method, **kwargs)


@app.get("/api/analytics/heatmap")
async def heatmap_endpoint(
    days: int = Query(30),
    resolution: str = Query("daily"),
):
    """Get heatmap data for document access patterns."""
    from ..core.usage_stats import get_heatmap_data
    return get_heatmap_data(days=days, resolution=resolution)


@app.get("/api/analytics/hot-documents")
async def hot_documents_endpoint(
    limit: int = Query(10),
    days: int = Query(7),
):
    """Get most frequently accessed documents."""
    from ..core.usage_stats import get_hot_documents
    return {"items": get_hot_documents(limit=limit, days=days)}


@app.get("/api/analytics/usage-stats")
async def usage_stats_endpoint(days: int = Query(30)):
    """Get comprehensive usage statistics."""
    from ..core.usage_stats import get_usage_stats
    return get_usage_stats(days=days)


if __name__ == "__main__":
    main()
