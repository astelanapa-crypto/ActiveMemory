"""Web dashboard for managing agent memory and context."""

from contextlib import asynccontextmanager
import os
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, or_

from ..core.config import config
from ..ingest.chunker import Chunker
from ..ingest.processor import DocumentProcessor
from ..search.embedder import Embedder
from ..search.searcher import HybridSearcher
from ..storage.db import Chunk, Document, Embedding, get_backend, get_session, init_db
from ..storage.db import serialize_embedding


processor = DocumentProcessor()
chunker = Chunker()
embedder = Embedder()
searcher = HybridSearcher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ActiveMemory Control Center", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
                    embedding=serialize_embedding(vector),
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


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/health")
async def health():
    session = get_session()
    try:
        total = session.query(func.count(Document.id)).scalar() or 0
        return {"status": "ok", "backend": get_backend(), "documents": total}
    finally:
        session.close()


@app.get("/api/stats")
async def stats():
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
async def store_memory(
    content: str = Form(...),
    title: str = Form("Manual memory"),
    category: str = Form("agent"),
    importance: int = Form(2),
    pinned: bool = Form(False),
    tags: str = Form(""),
    source: str = Form("dashboard"),
):
    result = _store_text_memory(content, title, category, importance, pinned, tags, source)
    return {"status": "stored", **result}


@app.post("/api/upload")
async def upload_document(
    file: UploadFile = File(...),
    category: str = Form("knowledge"),
    importance: int = Form(3),
    pinned: bool = Form(False),
    tags: str = Form(""),
):
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
        return result
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1),
    k: int = Query(8, ge=1, le=30),
    category: str = "",
    filetype: str = "",
):
    filters = {}
    if category:
        filters["category"] = category
    if filetype:
        filters["filetype"] = filetype
    results = searcher.search(q, top_k=k, filters=filters)
    return {"query": q, "results": [item.to_dict() for item in results], "total": len(results)}


@app.patch("/api/documents/{document_id}")
async def update_document(
    document_id: int,
    pinned: bool | None = None,
    importance: int | None = Query(None, ge=1, le=5),
    category: str | None = None,
):
    session = get_session()
    try:
        doc = session.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if pinned is not None:
            doc.pinned = pinned
        if importance is not None:
            doc.importance = importance
        if category is not None:
            doc.category = category
        session.commit()
        return {"updated": True}
    finally:
        session.close()


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: int):
    session = get_session()
    try:
        doc = session.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        session.delete(doc)
        session.commit()
        return {"deleted": True}
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
<title>ActiveMemory Control Center</title>
<style>
:root{--bg:#0b0d10;--panel:#15191f;--panel2:#1c222a;--line:#2a323d;--text:#eef2f6;--muted:#95a0ae;--accent:#20c997;--warn:#ffcf5a;--danger:#ff6b6b;--blue:#71b7ff;--radius:8px}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Arial,Helvetica,sans-serif;line-height:1.45}
button,input,select,textarea{font:inherit}button{cursor:pointer}.app{display:grid;grid-template-columns:248px 1fr;min-height:100vh}
.side{border-right:1px solid var(--line);background:#101419;padding:16px;position:sticky;top:0;height:100vh}.brand{font-weight:700;font-size:18px;margin:4px 0 18px}.status{font-size:12px;color:var(--muted);padding:10px;border:1px solid var(--line);border-radius:var(--radius)}
.nav{display:grid;gap:8px;margin-bottom:18px}.nav button{background:transparent;color:var(--muted);border:1px solid transparent;border-radius:var(--radius);padding:10px;text-align:left}.nav button.active,.nav button:hover{color:var(--text);background:var(--panel);border-color:var(--line)}
.main{padding:18px;max-width:1440px;width:100%;margin:0 auto}.top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:16px}.top h1{font-size:22px;margin:0 0 4px}.top p{margin:0;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:14px}.metric{font-size:26px;font-weight:700;color:var(--accent)}.label{font-size:12px;color:var(--muted);text-transform:uppercase}
.tabs{display:none}.view{display:none}.view.active{display:block}.two{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:12px;margin-top:12px}.stack{display:grid;gap:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.field{display:grid;gap:6px;margin-bottom:10px}.field span{font-size:12px;color:var(--muted)}input,select,textarea{width:100%;background:#0f1318;border:1px solid var(--line);border-radius:var(--radius);color:var(--text);padding:10px}textarea{min-height:128px;resize:vertical}
.btn{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:var(--radius);padding:10px 12px}.btn.primary{background:var(--accent);color:#06110d;border-color:var(--accent);font-weight:700}.btn.danger{background:transparent;color:var(--danger);border-color:rgba(255,107,107,.5)}
.list{display:grid;gap:8px}.item{border:1px solid var(--line);background:#11161c;border-radius:var(--radius);padding:12px}.item h3{margin:0 0 6px;font-size:15px}.meta{display:flex;gap:6px;flex-wrap:wrap;color:var(--muted);font-size:12px}.pill{border:1px solid var(--line);border-radius:999px;padding:2px 7px}.pin{color:var(--warn)}.preview{color:#cbd3dc;font-size:13px;margin-top:8px;overflow-wrap:anywhere}
.searchbar{display:grid;grid-template-columns:1fr 150px 110px auto;gap:8px;margin-bottom:12px}.context{white-space:pre-wrap;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:#d8dee6;max-height:420px;overflow:auto}
.toast{position:fixed;right:16px;bottom:16px;background:var(--panel2);border:1px solid var(--accent);border-radius:var(--radius);padding:10px 12px;display:none;max-width:320px}.toast.show{display:block}
@media(max-width:900px){.app{grid-template-columns:1fr}.side{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}.nav{grid-template-columns:repeat(4,1fr)}.nav button{text-align:center;padding:9px 6px}.two{grid-template-columns:1fr}.grid{grid-template-columns:repeat(2,1fr)}.searchbar{grid-template-columns:1fr 1fr}.top{display:block}}
@media(max-width:430px){.main{padding:10px}.side{padding:10px}.brand{font-size:16px;margin-bottom:10px}.nav{gap:6px}.nav button{font-size:12px;min-height:40px}.grid{grid-template-columns:1fr 1fr;gap:8px}.card{padding:10px}.metric{font-size:22px}.searchbar{grid-template-columns:1fr}.row .btn{flex:1 1 120px}.item{padding:10px}.top h1{font-size:19px}textarea{min-height:110px}.toast{left:10px;right:10px;bottom:10px;max-width:none}}
</style>
</head>
<body>
<div class="app">
<aside class="side">
  <div class="brand">ActiveMemory</div>
  <div class="nav">
    <button class="active" data-view="overview">Обзор</button>
    <button data-view="search">Поиск</button>
    <button data-view="ingest">Запись</button>
    <button data-view="context">Контекст</button>
  </div>
  <div class="status" id="health">Подключение...</div>
</aside>
<main class="main">
  <div class="top"><div><h1>Центр памяти агента</h1><p>Управление знаниями, критичным контекстом и резервной памятью.</p></div><button class="btn" onclick="refreshAll()">Обновить</button></div>
  <section id="overview" class="view active">
    <div class="grid" id="stats"></div>
    <div class="two"><div class="card"><div class="row" style="justify-content:space-between"><h2>Критичная память</h2><button class="btn" onclick="loadDocs(true)">Только критичное</button></div><div class="list" id="docs"></div></div><div class="card"><h2>Категории</h2><div id="categories" class="list"></div></div></div>
  </section>
  <section id="search" class="view">
    <div class="card"><div class="searchbar"><input id="q" placeholder="Запрос к памяти"><select id="cat"><option value="">Все категории</option><option>agent</option><option>project</option><option>knowledge</option><option>decision</option></select><select id="type"><option value="">Все типы</option><option value="text">text</option><option value="pdf">pdf</option><option value="code">code</option><option value="markdown">markdown</option></select><button class="btn primary" onclick="doSearch()">Искать</button></div><div class="list" id="results"></div></div>
  </section>
  <section id="ingest" class="view">
    <div class="two"><form class="card" onsubmit="storeMemory(event)"><h2>Добавить важную память</h2><label class="field"><span>Заголовок</span><input id="title" value="Agent memory"></label><label class="field"><span>Содержимое</span><textarea id="content" required></textarea></label><div class="row"><label class="field" style="flex:1"><span>Категория</span><select id="newCat"><option>agent</option><option>project</option><option>decision</option><option>knowledge</option></select></label><label class="field" style="width:120px"><span>Важность</span><select id="importance"><option value="1">1 critical</option><option value="2" selected>2 high</option><option value="3">3 normal</option><option value="4">4 low</option><option value="5">5 archive</option></select></label></div><label class="field"><span>Теги</span><input id="tags" placeholder="mcp, agent, rule"></label><label class="row"><input id="pinned" type="checkbox" style="width:auto"> Закрепить в рабочем контексте</label><button class="btn primary" type="submit">Сохранить</button></form><form class="card" onsubmit="uploadDoc(event)"><h2>Загрузить документ</h2><label class="field"><span>Файл</span><input id="file" type="file" required></label><label class="field"><span>Категория</span><select id="upCat"><option>knowledge</option><option>project</option><option>agent</option></select></label><button class="btn primary" type="submit">Загрузить и индексировать</button></form></div>
  </section>
  <section id="context" class="view"><div class="card"><div class="row" style="justify-content:space-between"><h2>Рабочий контекст агента</h2><button class="btn" onclick="loadContext()">Собрать</button></div><div class="context" id="ctx"></div></div></section>
</main></div><div class="toast" id="toast"></div>
<script>
const $=id=>document.getElementById(id);const api=(u,o)=>fetch(u,o).then(async r=>{if(!r.ok)throw new Error(await r.text());return r.json()});
function toast(t){const e=$('toast');e.textContent=t;e.classList.add('show');setTimeout(()=>e.classList.remove('show'),3000)}
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.nav button,.view').forEach(x=>x.classList.remove('active'));b.classList.add('active');$(b.dataset.view).classList.add('active')});
async function loadStats(){const s=await api('/api/stats');$('health').textContent=`${s.backend} | ${s.documents} документов | ${s.critical} критичных`;$('stats').innerHTML=[['Документы',s.documents],['Чанки',s.chunks],['Embeddings',s.embeddings],['Критичное',s.critical]].map(x=>`<div class="card"><div class="label">${x[0]}</div><div class="metric">${x[1]}</div></div>`).join('');$('categories').innerHTML=Object.entries(s.categories).map(([k,v])=>`<div class="item"><b>${k}</b><span class="pill" style="float:right">${v}</span></div>`).join('')||'<p class="preview">Нет данных</p>'}
function renderDocs(items,target='docs'){ $(target).innerHTML=items.map(d=>`<div class="item"><div class="row" style="justify-content:space-between"><h3>${d.pinned?'<span class="pin">PIN</span> ':''}${d.title}</h3><button class="btn danger" onclick="delDoc(${d.id})">Удалить</button></div><div class="meta"><span class="pill">${d.category}</span><span class="pill">${d.filetype}</span><span class="pill">важность ${d.importance}</span><span class="pill">${d.chunks} чанков</span></div><div class="preview">${d.preview||''}</div><div class="row" style="margin-top:8px"><button class="btn" onclick="patchDoc(${d.id},${!d.pinned},null)">PIN</button><button class="btn" onclick="patchDoc(${d.id},null,1)">Критично</button></div></div>`).join('')||'<p class="preview">Память пуста</p>'}
async function loadDocs(critical=false){const d=await api('/api/documents?limit=80'+(critical?'&critical=true':''));renderDocs(d.items)}
async function doSearch(){const q=$('q').value.trim();if(!q)return toast('Введите запрос');const u=`/api/search?q=${encodeURIComponent(q)}&category=${$('cat').value}&filetype=${$('type').value}`;const d=await api(u);$('results').innerHTML=d.results.map(r=>`<div class="item"><div class="meta"><span class="pill">${Math.round(r.score*100)}%</span><span class="pill">${r.source}</span><span class="pill">${r.metadata.category||''}</span></div><div class="preview">${r.content}</div></div>`).join('')||'<p class="preview">Ничего не найдено</p>'}
async function storeMemory(e){e.preventDefault();const f=new FormData();['content','tags'].forEach(id=>f.append(id,$(id).value));f.append('title',$('title').value);f.append('category',$('newCat').value);f.append('importance',$('importance').value);f.append('pinned',$('pinned').checked?'true':'false');await api('/api/memory',{method:'POST',body:f});$('content').value='';toast('Память сохранена');refreshAll()}
async function uploadDoc(e){e.preventDefault();const f=new FormData();f.append('file',$('file').files[0]);f.append('category',$('upCat').value);await api('/api/upload',{method:'POST',body:f});$('file').value='';toast('Документ загружен');refreshAll()}
async function patchDoc(id,pinned,importance){let u=`/api/documents/${id}?`;if(pinned!==null)u+='pinned='+pinned;if(importance!==null)u+='&importance='+importance;await api(u,{method:'PATCH'});refreshAll()}
async function delDoc(id){if(!confirm('Удалить запись памяти?'))return;await api('/api/documents/'+id,{method:'DELETE'});refreshAll()}
async function loadContext(){const d=await api('/api/context');$('ctx').textContent=d.items.map(x=>`[${x.category} | importance ${x.importance}${x.pinned?' | pinned':''}] ${x.title}\n${x.content}`).join('\n\n')||'Критичный контекст пока пуст'}
async function refreshAll(){await loadStats();await loadDocs();await loadContext()}
$('q').addEventListener('keydown',e=>{if(e.key==='Enter')doSearch()});refreshAll().catch(e=>toast(e.message));
</script>
</body></html>"""


if __name__ == "__main__":
    main()
