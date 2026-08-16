"""Pipeline executor tests: E2E chain run, resume, cancel, error isolation."""

import json

import pytest
from conftest import make_import_rows, write_import_manifest

from data_factory.meta import models as m
from data_factory.strategies.executor import PipelineExecutor


def _build_workflow(factory, ds_id, stages):
    domain = factory.create_capability_domain("chart_fact_qa")
    strategy = factory.create_strategy("fact-qa", domain.id)
    return factory.define_workflow(strategy.id, "qc-chain", stages)


def test_full_chain_run_produces_version(factory, tmp_path):
    manifest = write_import_manifest(
        tmp_path, make_import_rows(count=10, dup=2, bad_len=1)
    )
    ds = factory.create_dataset(
        "qa", source_type="import", import_manifest=str(manifest)
    )
    wf = _build_workflow(
        factory,
        ds.id,
        [
            ("schema_check", None),
            ("dedup", None),
            ("field_range", {"fields": {"answer": {"max": 200}}}),
            ("filter", None),
            ("publish", {"dataset_id": ds.id}),
        ],
    )
    run = factory.create_run(wf.id, ds.id)
    final = factory.run_workflow(run.id)
    assert final.status == m.RUN_SUCCEEDED
    # 13 in; dedup marks 2 dup; field_range rejects the 300-char answer;
    # filter drops both -> publish gets 10 clean rows
    assert final.stats["rows_out"] == 10
    assert final.stats["failed_rows"] == 0

    versions = factory._db.list_dataset_versions(ds.id)
    assert len(versions) == 1
    assert versions[0].row_count == 10

    manifest_payload = json.loads(
        factory.backend.open_stream(versions[0].manifest_key).read()
    )
    assert manifest_payload["version"] == 1
    assert manifest_payload["lineage"]["run_id"] == run.id
    assert manifest_payload["rejected_count"] == 0

    # the published rows are the clean ones (no long answers, no dups)
    object_key = manifest_payload["files"][0]["object_key"]
    raw = factory.backend.open_stream(object_key).read()
    published = [json.loads(line) for line in raw.splitlines()]
    assert len(published) == 10
    assert all(len(r["answer"]) <= 200 for r in published)
    assert len({r["question"] for r in published}) == 10


def test_run_persists_stage_stats(factory, tmp_path):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=4))
    ds = factory.create_dataset(
        "qa", source_type="import", import_manifest=str(manifest)
    )
    wf = _build_workflow(factory, ds.id, [("schema_check", None)])
    run = factory.create_run(wf.id, ds.id)
    factory.run_workflow(run.id)
    stages = factory.show_run(run.id)["stages"]
    assert len(stages) == 1
    assert stages[0]["rows_in"] == 4
    assert stages[0]["rows_out"] == 4


def test_resume_skips_completed_nodes(factory, tmp_path):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=5))
    ds = factory.create_dataset(
        "qa", source_type="import", import_manifest=str(manifest)
    )
    wf = _build_workflow(
        factory,
        ds.id,
        [
            ("schema_check", None),
            ("dedup", None),
            ("publish", {"dataset_id": ds.id}),
        ],
    )
    run = factory.create_run(wf.id, ds.id)

    executor = PipelineExecutor(factory._db, factory.backend)
    first = executor.run(run.id)
    assert first.status == m.RUN_SUCCEEDED

    # simulate an interrupted rerun: re-mark the run as failed, re-execute
    factory._db.update_run(run.id, {"status": m.RUN_FAILED, "error": "test"})
    second = executor.run(run.id)
    assert second.status == m.RUN_SUCCEEDED
    # stage attempts stay at 1 (skipped on resume)
    assert all(rs.attempts == 1 for rs in factory._db.list_run_stages(run.id))


def test_cancel_before_execution(factory, tmp_path):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=3))
    ds = factory.create_dataset(
        "qa", source_type="import", import_manifest=str(manifest)
    )
    wf = _build_workflow(
        factory,
        ds.id,
        [
            ("schema_check", None),
            ("publish", {"dataset_id": ds.id}),
        ],
    )
    run = factory.create_run(wf.id, ds.id)
    factory.cancel_run(run.id)
    with pytest.raises(RuntimeError, match="cancel"):
        factory.run_workflow(run.id)
    assert factory._db.get_run(run.id).status == m.RUN_CANCELLED


def test_unknown_run_rejected(factory):
    with pytest.raises(ValueError, match="unknown run"):
        factory.run_workflow("run_missing")


def test_chain_run_with_qc_llm_judge(factory, tmp_path, mock_llm):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=4))
    ds = factory.create_dataset(
        "qa", source_type="import", import_manifest=str(manifest)
    )
    judge = factory.register_model(
        "judge", backend="api", base_url=mock_llm, model_id="mock"
    )
    factory.check_model(judge.id)
    wf = _build_workflow(
        factory,
        ds.id,
        [
            ("schema_check", None),
            ("qc_llm", {"judge_model_id": judge.id, "threshold": 0.5}),
            ("publish", {"dataset_id": ds.id}),
        ],
    )
    run = factory.create_run(wf.id, ds.id)
    final = factory.run_workflow(run.id)
    assert final.status == m.RUN_SUCCEEDED
    versions = factory._db.list_dataset_versions(ds.id)
    assert versions[0].row_count == 4


def test_unknown_judge_model_fails_run(factory, tmp_path):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=2))
    ds = factory.create_dataset(
        "qa", source_type="import", import_manifest=str(manifest)
    )
    wf = _build_workflow(
        factory,
        ds.id,
        [
            ("qc_llm", {"judge_model_id": "m_missing"}),
        ],
    )
    run = factory.create_run(wf.id, ds.id)
    with pytest.raises(ValueError, match="judge model not found"):
        factory.run_workflow(run.id)
    assert factory._db.get_run(run.id).status == m.RUN_FAILED


def test_stage_run_single_stage_debug(factory, tmp_path):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=3, dup=1))
    out = factory.stage_run("dedup", manifest)
    assert len(out) == 4  # 3 unique + 1 duplicate of the first row
    assert out[3]["_qc"]["checks"]["dedup"]["duplicate"] is True
