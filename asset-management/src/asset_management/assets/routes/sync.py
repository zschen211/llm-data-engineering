"""Sync-run resource: run state, pause/resume control, event stream."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..api import AssetStore


def make_router(store: AssetStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sync/runs")
    def list_sync_runs(limit: int = Query(default=20, le=200)):
        return store.list_sync_runs(limit=limit)

    @router.get("/api/sync/{run_id}")
    def get_sync_run(run_id: str):
        run = store.get_sync_run(run_id)
        if run is None:
            raise HTTPException(404, "sync run not found")
        return run

    @router.post("/api/sync/{run_id}/pause")
    def pause_sync(run_id: str):
        try:
            return store.pause_sync(run_id)
        except ValueError as exc:
            raise HTTPException(
                404 if store.get_sync_run(run_id) is None else 400, str(exc)
            ) from exc

    @router.post("/api/sync/{run_id}/resume")
    def resume_sync(run_id: str):
        try:
            return store.resume_sync(run_id)
        except ValueError as exc:
            raise HTTPException(
                404 if store.get_sync_run(run_id) is None else 400, str(exc)
            ) from exc

    @router.get("/api/sync/{run_id}/events")
    def get_sync_events(
        run_id: str, after: int = 0, limit: int = Query(default=200, le=1000)
    ):
        return store.get_sync_events(run_id, after_id=after, limit=limit)

    @router.get("/api/sync/{run_id}/stages")
    def get_sync_stages(run_id: str):
        """Per-stage records of the run: wall time, item/failure counts and
        app/Ray retry counters (durable mirror of the Prometheus metrics)."""
        if store.get_sync_run(run_id) is None:
            raise HTTPException(404, "sync run not found")
        return store.get_sync_stages(run_id)

    @router.get("/api/sync/{run_id}/tasks")
    def get_sync_tasks(
        run_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=200),
    ):
        """Paginated per-file task rows: {"items", "total", "offset", "limit"}.

        Offset pagination is safe here: a run's task table is populated at
        resolve time and no rows are inserted while the run is in flight.
        """
        if store.get_sync_run(run_id) is None:
            raise HTTPException(404, "sync run not found")
        return {
            "items": store.get_sync_tasks_page(run_id, offset=offset, limit=limit),
            "total": store.count_sync_tasks(run_id),
            "offset": offset,
            "limit": limit,
        }

    return router
