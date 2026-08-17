"""Lineage queries: who produced what, with which strategy, from which input.

Three entry points mirror the three production questions: ``by_run`` (what
did this run touch), ``by_dataset`` (which run/strategy produced a given
dataset version), ``by_strategy`` (everything a strategy ever produced).
"""

from __future__ import annotations

from .meta import models as m


def by_run(db, run_id: str) -> dict:
    run = db.get_run(run_id)
    if run is None:
        raise ValueError(f"unknown run: {run_id}")
    workflow = db.get_workflow(run.workflow_id)
    strategy = db.get_strategy(workflow.strategy_id) if workflow else None
    dataset = db.get_dataset(run.input_dataset_id)
    return {
        "run_id": run.id,
        "workflow_id": run.workflow_id,
        "strategy_id": workflow.strategy_id if workflow else "",
        "strategy_name": strategy.name if strategy else "",
        "input_dataset_id": run.input_dataset_id,
        "input_dataset_version": run.input_dataset_version,
        "input_dataset_name": dataset.name if dataset else "",
        "status": run.status,
        "stages": [
            {
                "node_id": rs.node_id,
                "status": rs.status,
                "rows_in": rs.rows_in,
                "rows_out": rs.rows_out,
                "failed_rows": rs.failed_rows,
            }
            for rs in db.list_run_stages(run_id)
        ],
        "artifacts": [
            {
                "node_id": a.node_id,
                "kind": a.kind,
                "object_key": a.object_key,
                "row_count": a.row_count,
                "sha256": a.sha256,
            }
            for a in db.list_artifacts(run_id)
        ],
        "produced_versions": [
            _version_view(db, dv) for dv in _versions_of_run(db, run_id)
        ],
    }


def by_dataset(db, dataset_id: str, version: int | None = None) -> dict:
    dataset = db.get_dataset(dataset_id)
    if dataset is None:
        raise ValueError(f"unknown dataset: {dataset_id}")
    if version is None:
        versions = db.list_dataset_versions(dataset_id)
    else:
        dv = db.get_dataset_version(dataset_id, version)
        versions = [dv] if dv else []
    return {
        "dataset_id": dataset.id,
        "name": dataset.name,
        "source_type": dataset.source_type,
        "versions": [_version_view(db, dv) for dv in versions],
    }


def by_strategy(db, strategy_id: str) -> dict:
    strategy = db.get_strategy(strategy_id)
    if strategy is None:
        raise ValueError(f"unknown strategy: {strategy_id}")
    domain = db.get_capability_domain(strategy.capability_domain_id)
    workflows = [wf for wf in db.list_workflows() if wf.strategy_id == strategy_id]
    runs = [r for wf in workflows for r in db.list_runs(wf.id)]
    return {
        "strategy_id": strategy.id,
        "name": strategy.name,
        "capability_domain_id": strategy.capability_domain_id,
        "capability_domain": domain.name if domain else "",
        "workflows": [wf.id for wf in workflows],
        "runs": [
            {
                "run_id": r.id,
                "workflow_id": r.workflow_id,
                "input_dataset_id": r.input_dataset_id,
                "status": r.status,
                "produced": [
                    {"dataset_id": dv.dataset_id, "version": dv.version}
                    for dv in _versions_of_run(db, r.id)
                ],
            }
            for r in runs
        ],
    }


def _versions_of_run(db, run_id: str) -> list[m.DatasetVersion]:
    return [
        dv
        for dv in _all_versions(db)
        if dv.artifact_id
        and (art := db.get_artifact(dv.artifact_id)) is not None
        and art.run_id == run_id
    ]


def _all_versions(db) -> list[m.DatasetVersion]:
    seen: dict[str, m.DatasetVersion] = {}
    for ds in db.list_datasets():
        for dv in db.list_dataset_versions(ds.id):
            seen[dv.id] = dv
    return list(seen.values())


def _version_view(db, dv: m.DatasetVersion) -> dict:
    artifact = db.get_artifact(dv.artifact_id) if dv.artifact_id else None
    run = db.get_run(artifact.run_id) if artifact else None
    workflow = db.get_workflow(run.workflow_id) if run else None
    return {
        "dataset_id": dv.dataset_id,
        "version": dv.version,
        "manifest_key": dv.manifest_key,
        "row_count": dv.row_count,
        "artifact": {
            "id": artifact.id,
            "object_key": artifact.object_key,
            "sha256": artifact.sha256,
        }
        if artifact
        else None,
        "produced_by": {
            "run_id": run.id,
            "workflow_id": run.workflow_id,
            "strategy_id": workflow.strategy_id if workflow else "",
        }
        if run
        else {},
    }
