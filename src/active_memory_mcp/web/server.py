import os, json, sqlite3
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Query, Form, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path("/home/silentstorm/Documents/Projects/ActiveMemory")
DB_PATH = os.getenv("MEMORY_DB", str(BASE_DIR / "memory.db"))
EMBED_URL = os.getenv("EMBED_URL", "http://192.168.1.199:8899/v1/embeddings")

app = FastAPI(title="ActiveMemory Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Static files
static_dir = BASE_DIR / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        importance INTEGER DEFAULT 3,
        type TEXT DEFAULT 'text',
        tags TEXT,
        source TEXT,
        embedding BLOB,
        chunk_index INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cat ON memories(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON memories(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imp ON memories(importance)")
    conn.commit()
    conn.close()


def human_size(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def get_embedding(text: str) -> Optional[list]:
    """Call BGE-M3 embedding service"""
    try:
        import httpx
        resp = httpx.post(EMBED_URL, json={"input": text}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", [{}])[0].get("embedding")
    except Exception as e:
        print(f"Embedding error: {e}")
    return None


def cosine_sim(a: list, b: list) -> float:
    if not a or not b: return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(x * x for x in b) ** 0.5)
    return dot / norm if norm > 0 else 0.0


def chunk_text(text: str, size: int = 512, overlap: int = 50) -> list:
    words = text.split()
    chunks = []
    step = max(1, size - overlap)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks or [text]


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse(BASE_DIR / "web" / "templates" / "index.html")


@app.get("/health")
async def health():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    with_emb = conn.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL").fetchone()[0]
    conn.close()
    return {"status": "ok", "db": str(DB_PATH), "total": total, "with_embedding": with_emb}


@app.get("/api/stats")
async def stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    with_emb = conn.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL").fetchone()[0]
    cats = conn.execute("SELECT category, COUNT(*) as c FROM memories GROUP BY category").fetchall()
    types = conn.execute("SELECT type, COUNT(*) as c FROM memories GROUP BY type").fetchall()
    imp = conn.execute("SELECT importance, COUNT(*) as c FROM memories GROUP BY importance ORDER BY importance").fetchall()
    conn.close()
    return {
        "documents": total,
        "chunks": total,
        "size_bytes": size,
        "size_human": human_size(size),
        "embeddings": with_emb,
        "categories": {r["category"]: r["c"] for r in cats},
        "types": {r["type"]: r["c"] for r in types},
        "importance": {r["importance"]: r["c"] for r in imp}
    }


@app.get("/api/memories")
async def memories(limit: int = Query(50, le=500), category: str = None, type: str = None, search: str = None):
    conn = get_conn()
    sql = "SELECT id, content, category, importance, type, source, tags, created_at FROM memories WHERE 1=1"
    params = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    if type:
        sql += " AND type = ?"
        params.append(type)
    if search:
        sql += " AND content LIKE ?"
        params.append(f"%{search}%")
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@app.post("/api/store")
async def store(
    content: str = Form(...),
    category: str = Form("general"),
    importance: int = Form(3),
    type: str = Form("text"),
    tags: str = Form(""),
    source: str = Form("manual")
):
    if not content.strip():
        raise HTTPException(status_code=400, detail="Content required")
    
    # Get embedding
    emb = get_embedding(content)
    
    # Chunk if too long
    chunks = chunk_text(content, 512, 50)
    conn = get_conn()
    ids = []
    for idx, chunk in enumerate(chunks):
        # Generate embedding for each chunk
        chunk_emb = get_embedding(chunk) if not emb else emb  # Use same or generate new
        conn.execute(
            "INSERT INTO memories (content, category, importance, type, tags, source, embedding, chunk_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chunk, category, importance, type, tags, source, json.dumps(chunk_emb) if chunk_emb else None, idx)
        )
        last = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        ids.append(last)
    conn.commit()
    conn.close()
    return {"status": "stored", "count": len(chunks), "ids": ids}


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1), k: int = Query(5, ge=1, le=20), category: str = None, type: str = None, min_imp: int = None):
    # Get query embedding
    query_emb = get_embedding(q)
    
    conn = get_conn()
    sql = "SELECT id, content, category, importance, type, embedding FROM memories WHERE embedding IS NOT NULL"
    params = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    if type:
        sql += " AND type = ?"
        params.append(type)
    if min_imp:
        sql += " AND importance <= ?"
        params.append(min_imp)
    
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    
    # Score by cosine similarity
    scored = []
    for r in rows:
        emb = json.loads(r["embedding"]) if r["embedding"] else None
        sim = cosine_sim(query_emb, emb) if emb and query_emb else 0.0
        scored.append({"id": r["id"], "content": r["content"], "category": r["category"], "importance": r["importance"], "type": r["type"], "similarity": sim})
    
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return {"query": q, "results": scored[:k], "total": len(rows)}


@app.delete("/api/delete")
async def delete(id: int):
    conn = get_conn()
    conn.execute("DELETE FROM memories WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return {"deleted": True}


@app.post("/api/clear")
async def clear():
    conn = get_conn()
    conn.execute("DELETE FROM memories")
    conn.commit()
    conn.close()
    return {"cleared": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8788)