"""FastAPI management UI for the asset layer (optional ``web`` extra).

Endpoints: sources CRUD, asset listing/filtering, tagging, snapshots, sync
trigger and image preview (streamed from the storage backend).
"""
from __future__ import annotations

import mimetypes
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .store import AssetStore

DEFAULT_HTML = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8"><title>llava-instruct 资产管理</title>
<style>
body{font-family:system-ui,sans-serif;margin:2rem;background:#fafafa}
table{border-collapse:collapse;width:100%;background:#fff}
th,td{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:14px}
input,select,button{padding:4px 8px;margin:2px}
img.preview{max-width:160px;max-height:120px;object-fit:contain}
.badge{background:#eef;border-radius:8px;padding:1px 6px;font-size:12px}
</style>
</head>
<body>
<h2>llava-instruct 数据资产管理</h2>
<section>
  <h3>数据源</h3>
  <div id="sources"></div>
  <form id="sourceForm">
    <input name="name" placeholder="名称" required>
    <select name="kind">
      <option>local</option><option>http</option><option>huggingface</option>
    </select>
    <input name="url" placeholder="url / 本地路径" size="40">
    <input name="license" placeholder="license">
    <button type="submit">添加数据源</button>
  </form>
</section>
<section>
  <h3>资产 <span id="count"></span></h3>
  <label>类型 <select id="fType"><option value="">全部</option>
    <option>general_image</option><option>document_image</option>
    <option>chart_image</option><option>interleaved_pair</option></select></label>
  <label>状态 <select id="fStatus"><option value="">全部</option>
    <option>ready</option><option>pending</option><option>failed</option></select></label>
  <input id="fTag" placeholder="标签过滤 group=name" size="24">
  <button onclick="loadAssets()">筛选</button>
  <table><thead><tr><th>ID</th><th>名称</th><th>类型</th><th>状态</th><th>标签</th><th>预览</th><th>操作</th></tr></thead>
  <tbody id="assets"></tbody></table>
</section>
<script>
async function j(url, opts){const r=await fetch(url,opts);if(!r.ok)throw await r.text();return r.json();}
function esc(s){const d=document.createElement('div');d.textContent=s??'';return d.innerHTML;}
async function loadSources(){
  const list=await j('/api/sources');
  document.getElementById('sources').innerHTML=list.map(s=>
    `<div>${esc(s.name)} <span class="badge">${esc(s.kind)}</span> ${esc(s.url)}
     <button onclick="syncSource('${s.id}')">同步</button>
     <button onclick="rmSource('${s.id}')">删除</button></div>`).join('');
}
async function loadAssets(){
  const fType=document.getElementById('fType').value,fStatus=document.getElementById('fStatus').value;
  const fTag=document.getElementById('fTag').value;
  const q=new URLSearchParams();
  if(fType)q.set('type',fType); if(fStatus)q.set('status',fStatus); if(fTag)q.set('tag',fTag);
  const list=await j('/api/assets?'+q);
  document.getElementById('count').textContent=`(${list.length})`;
  document.getElementById('assets').innerHTML=list.map(a=>
    `<tr><td>${esc(a.id)}</td><td>${esc(a.name)}</td><td>${esc(a.asset_type)}</td>
     <td>${esc(a.status)}</td>
     <td>${(a.tags||[]).map(t=>`<span class="badge">${esc(t[0]+'='+t[1])}</span>`).join(' ')}</td>
     <td><img class="preview" src="/api/assets/${a.id}/preview" onerror="this.style.display='none'"></td>
     <td><input placeholder="标签名" id="tg${a.id}" size="10">
         <button onclick="tagAsset('${a.id}')">打标</button></td></tr>`).join('');
}
async function syncSource(id){await j(`/api/sources/${id}/sync`,{method:'POST'});alert('同步完成');loadSources();loadAssets();}
async function rmSource(id){await j(`/api/sources/${id}`,{method:'DELETE'});loadSources();}
async function tagAsset(id){const name=document.getElementById('tg'+id).value;if(!name)return;
  await j(`/api/assets/${id}/tags`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});loadAssets();}
document.getElementById('sourceForm').addEventListener('submit',async e=>{
  e.preventDefault();const fd=new FormData(e.target);
  await j('/api/sources',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.fromEntries(fd))});e.target.reset();loadSources();});
loadSources();loadAssets();
</script>
</body></html>
"""


class SourceIn(BaseModel):
    name: str
    kind: str
    url: str = ""
    license: str = ""
    description: str = ""
    params: dict = Field(default_factory=dict)


class TagIn(BaseModel):
    name: str
    group: str = "default"


class SnapshotIn(BaseModel):
    name: str = ""


def create_app(store: AssetStore) -> FastAPI:
    app = FastAPI(title="llava-instruct asset manager", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return DEFAULT_HTML

    # ------------------------------------------------------------- sources
    @app.get("/api/sources")
    def list_sources():
        return [asdict(s) for s in store.list_sources()]

    @app.post("/api/sources", status_code=201)
    def add_source(body: SourceIn):
        try:
            return asdict(store.add_source(**body.model_dump()))
        except Exception as exc:
            raise HTTPException(400, str(exc))

    @app.put("/api/sources/{source_id}")
    def update_source(source_id: str, body: SourceIn):
        source = store.update_source(source_id, **body.model_dump())
        if source is None:
            raise HTTPException(404, "source not found")
        return asdict(source)

    @app.delete("/api/sources/{source_id}", status_code=204)
    def delete_source(source_id: str):
        store.delete_source(source_id)

    @app.post("/api/sources/{source_id}/sync")
    def sync_source(source_id: str):
        from dataclasses import asdict as _ad

        try:
            return _ad(store.sync_source(source_id))
        except Exception as exc:
            raise HTTPException(400, str(exc))

    # -------------------------------------------------------------- assets
    @app.get("/api/assets")
    def list_assets(type: str | None = None, status: str | None = None,
                    source: str | None = None, tag: str | None = Query(default=None)):
        tags = [tag] if tag else None
        assets = store.list_assets(asset_type=type, status=status,
                                   source_id=source, tags=tags)
        return [
            {**asdict(a), "tags": a.tags}
            for a in assets
        ]

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str):
        asset = store.get_asset(asset_id)
        if asset is None:
            raise HTTPException(404, "asset not found")
        return {
            **asdict(asset),
            "tags": asset.tags,
            "versions": store.version_history(asset_id),
        }

    @app.delete("/api/assets/{asset_id}", status_code=204)
    def delete_asset(asset_id: str):
        store.delete_asset(asset_id)

    @app.post("/api/assets/{asset_id}/tags", status_code=201)
    def tag_asset(asset_id: str, body: TagIn):
        try:
            store.tag_asset(asset_id, body.name, body.group)
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    @app.delete("/api/assets/{asset_id}/tags/{tag_name}", status_code=204)
    def untag_asset(asset_id: str, tag_name: str):
        store.untag_asset(asset_id, tag_name)

    @app.get("/api/assets/{asset_id}/preview")
    def preview(asset_id: str):
        asset = store.get_asset(asset_id)
        if asset is None or not asset.object_key:
            raise HTTPException(404, "asset not found")
        if not store.backend.exists(asset.object_key):
            raise HTTPException(404, "object missing on backend")
        stream = store.backend.open_stream(asset.object_key)
        media_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
        return StreamingResponse(stream, media_type=media_type)

    # ----------------------------------------------------------- snapshots
    @app.post("/api/snapshots", status_code=201)
    def create_snapshot(body: SnapshotIn):
        return store.create_snapshot(name=body.name)

    @app.get("/api/snapshots")
    def list_snapshots():
        return store.list_snapshots()

    return app


def default_app(data_dir: Path | None = None) -> FastAPI:
    """Build an app wired to the default store (env-configured backend)."""
    import os

    from .storage import LocalStorageBackend, S3StorageBackend

    data_dir = Path(data_dir or os.environ.get("LLAVA_DATA_DIR", "data"))
    endpoint = os.environ.get("RUSTFS_ENDPOINT")
    if endpoint:
        backend = S3StorageBackend(
            endpoint,
            os.environ["RUSTFS_ACCESS_KEY"],
            os.environ["RUSTFS_SECRET_KEY"],
            os.environ.get("RUSTFS_BUCKET", "llava-assets"),
        )
    else:
        backend = LocalStorageBackend(data_dir / "blobs")
    store = AssetStore(data_dir / "assets.db", backend, tmp_dir=data_dir / "tmp")
    return create_app(store)
