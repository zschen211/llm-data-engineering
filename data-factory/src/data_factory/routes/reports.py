"""Report endpoints (list/show/payload)."""

from __future__ import annotations

from fastapi import APIRouter

from .. import jsonl
from ..api import DataFactory
from .common import guard


def make_router(factory: DataFactory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/reports")
    def list_reports(eval_run_id: str = ""):
        return factory.list_reports(eval_run_id)

    @router.get("/api/reports/{report_id}")
    def show_report(report_id: str):
        return guard(factory.show_report, report_id)

    @router.get("/api/reports/{report_id}/payload")
    def report_payload(report_id: str):
        report = guard(factory.show_report, report_id)
        return jsonl.read_object(factory.backend, report.json_key)

    return router
