"""Publish sink: persist the final rows as an immutable dataset version.

Accepted rows (no per-row error, ``_qc.ok`` true) go to a content-addressed
JSONL object; rejected rows are archived next to the run for audit. The
version manifest pins content + lineage so downstream consumers (fine-tuning
scripts) can reproduce exactly what was produced.
"""

from __future__ import annotations

from typing import ClassVar

from ... import jsonl
from ...meta import models as m
from ...meta.db import new_id
from ...storage.base import artifact_key_for, manifest_key_for
from .base import SinkStage, has_error, register


@register
class PublishStage(SinkStage):
    """sink: write accepted rows as a new dataset version + lineage manifest.

    Config: ``{"dataset_id": "...", "drop_failed": true, "note": "..."}``.
    """

    name = "publish"
    kind = "sink"
    description = "Publish accepted rows as an immutable dataset version"
    config_schema: ClassVar[dict] = {"dataset_id": "", "drop_failed": True, "note": ""}

    def write(self, rows: list[dict], ctx) -> dict:
        dataset_id = self.config.get("dataset_id")
        if not dataset_id:
            raise ValueError("publish stage requires config dataset_id")
        dataset = ctx.db.get_dataset(dataset_id)
        if dataset is None:
            raise ValueError(f"unknown dataset: {dataset_id}")
        accepted, rejected = self._split(rows)
        result = jsonl.write_rows_ca(ctx.backend, accepted, ctx.tmp_dir)
        self._archive_rejected(ctx, rejected)

        version = ctx.db.next_dataset_version(dataset_id)
        version_id = new_id("dv_")
        artifact_id = self._record_result(ctx, result, version)
        manifest_key = manifest_key_for(dataset_id, version)
        jsonl.write_manifest(
            ctx.backend,
            manifest_key,
            self._build_manifest(ctx, dataset, result, version, len(rejected)),
        )
        ctx.db.create_dataset_version(
            m.DatasetVersion(
                id=version_id,
                dataset_id=dataset_id,
                version=version,
                artifact_id=artifact_id,
                manifest_key=manifest_key,
                row_count=result["row_count"],
            )
        )
        return {
            "dataset_version_id": version_id,
            "version": version,
            "manifest_key": manifest_key,
            "object_key": result["key"],
            "row_count": result["row_count"],
            "rejected_count": len(rejected),
        }

    def _split(self, rows: list[dict]) -> tuple[list[dict], list[dict]]:
        """Partition rows into accepted (clean) vs rejected (QC/error)."""
        drop_failed = self.config.get("drop_failed", True)
        bad = lambda r: (
            drop_failed and (has_error(r) or not r.get("_qc", {}).get("ok", True))
        )
        return [r for r in rows if not bad(r)], [r for r in rows if bad(r)]

    def _archive_rejected(self, ctx, rejected: list[dict]) -> None:
        if rejected:
            jsonl.write_rows(
                ctx.backend,
                artifact_key_for(ctx.run_id, ctx.node_id, "rejected.jsonl"),
                rejected,
                ctx.tmp_dir,
            )

    def _record_result(self, ctx, result: dict, version: int) -> str:
        artifact_id = new_id("art_")
        ctx.db.create_artifact(
            m.Artifact(
                id=artifact_id,
                run_id=ctx.run_id,
                node_id=ctx.node_id,
                kind="result",
                object_key=result["key"],
                sha256=result["sha256"],
                size=result["size"],
                row_count=result["row_count"],
            )
        )
        return artifact_id

    def _build_manifest(
        self,
        ctx,
        dataset,
        result: dict,
        version: int,
        rejected_count: int,
    ) -> dict:
        run = ctx.db.get_run(ctx.run_id)
        lineage = {
            "run_id": ctx.run_id,
            "node_id": ctx.node_id,
            "workflow_id": run.workflow_id if run else "",
            "strategy_id": "",
            "input_dataset_id": run.input_dataset_id if run else "",
            "input_dataset_version": run.input_dataset_version if run else 1,
        }
        if run:
            workflow = ctx.db.get_workflow(run.workflow_id)
            if workflow:
                lineage["strategy_id"] = workflow.strategy_id
        return {
            "dataset_id": dataset.id,
            "dataset_name": dataset.name,
            "version": version,
            "created_at": m._now(),
            "note": self.config.get("note", ""),
            "row_count": result["row_count"],
            "rejected_count": rejected_count,
            "lineage": lineage,
            "files": [
                {
                    "object_key": result["key"],
                    "sha256": result["sha256"],
                    "size": result["size"],
                    "row_count": result["row_count"],
                }
            ],
        }
