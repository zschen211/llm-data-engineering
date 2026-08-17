"""Sources resource: CRUD + background sync-run trigger."""

from __future__ import annotations

import threading
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...log import get_logger
from ..api import AssetStore

logger = get_logger("assets.web")


class SourceIn(BaseModel):
    name: str
    kind: str
    url: str = ""
    license: str = ""
    description: str = ""
    params: dict = Field(default_factory=dict)


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sources")
    def list_sources():
        """Sources with their currently-running sync run id and their
        crash-interrupted (resumable) run id (None when idle/finished)."""
        return [
            {
                **asdict(s),
                "running_run_id": (store.get_running_run(s.id) or {}).get("id"),
                "resumable_run_id": (store.get_interrupted_run(s.id) or {}).get("id"),
            }
            for s in store.list_sources()
        ]

    @router.post("/api/sources", status_code=201)
    def add_source(body: SourceIn):
        try:
            return asdict(store.add_source(**body.model_dump()))
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put("/api/sources/{source_id}")
    def update_source(source_id: str, body: SourceIn):
        source = store.update_source(source_id, **body.model_dump())
        if source is None:
            raise HTTPException(404, "source not found")
        return asdict(source)

    @router.delete("/api/sources/{source_id}", status_code=204)
    def delete_source(source_id: str):
        store.delete_source(source_id)

    @router.post("/api/sources/{source_id}/sync", status_code=202)
    def sync_source(source_id: str):
        """Start a sync run in the background; poll /api/sync/{run_id}.

        When the source has a crash-interrupted run, that run is resumed
        (file-level: persisted files are skipped, the rest continue), so the
        frontend keeps seeing the interrupted run's per-file progress.
        """
        try:
            if store.get_interrupted_run(source_id):
                run_id = store.resume_source(source_id)
                resumed = True
            else:
                run_id = store.start_sync(source_id)
                resumed = False
        except ValueError as exc:
            if "already syncing" in str(exc):
                raise HTTPException(409, str(exc)) from exc
            raise HTTPException(400, str(exc)) from exc

        def _run():
            try:
                store.sync_source(source_id, run_id=run_id)
            except Exception as exc:
                logger.error("background sync run=%s failed: %s", run_id, exc)

        threading.Thread(target=_run, daemon=True).start()
        return {"run_id": run_id, "resumed": resumed}

    @router.post("/api/sources/{source_id}/reprocess", status_code=202)
    def reprocess_source(source_id: str):
        """Re-run Phase B only (raw layer already populated, zero network)."""
        try:
            run_id = store.start_sync(source_id)
        except ValueError as exc:
            if "already syncing" in str(exc):
                raise HTTPException(409, str(exc)) from exc
            raise HTTPException(400, str(exc)) from exc

        def _run():
            try:
                store.reprocess_source(source_id, run_id=run_id)
            except Exception as exc:
                logger.error("background reprocess run=%s failed: %s", run_id, exc)

        threading.Thread(target=_run, daemon=True).start()
        return {"run_id": run_id, "reprocess": True}

    @router.get("/api/sources/{source_id}/raw")
    def list_raw_files(source_id: str):
        """Raw-layer files of the source (path-addressed mirror, one row per
        repo file, with upload status / sha256 / attempts)."""
        if store.get_source(source_id) is None:
            raise HTTPException(404, "source not found")
        return store.list_raw_files(source_id)

    return router
