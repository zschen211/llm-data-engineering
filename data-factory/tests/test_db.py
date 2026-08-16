"""Database CRUD tests: schema, JSON columns, workflow edges, versions."""

import sqlite3

import pytest

from data_factory.meta import models as m
from data_factory.meta.db import Database, new_id


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "db.sqlite")


def test_schema_initialized(db):
    tables = {
        r["name"]
        for r in db._fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table in (
        "capability_domains",
        "strategies",
        "datasets",
        "stages",
        "workflows",
        "workflow_nodes",
        "workflow_edges",
        "runs",
        "run_stages",
        "artifacts",
        "dataset_versions",
        "models",
        "eval_sets",
        "eval_items",
        "eval_runs",
        "eval_results",
        "reports",
    ):
        assert table in tables


def test_stages_seeded(db):
    names = {s.name for s in db.list_stage_types()}
    assert {
        "schema_check",
        "dedup",
        "field_range",
        "filter",
        "publish",
        "qc_llm",
    } <= names


def test_capability_domain_roundtrip(db):
    domain = m.CapabilityDomain(id="cd_1", name="chart_fact_qa")
    db.create_capability_domain(domain)
    got = db.get_capability_domain("cd_1")
    assert got.name == "chart_fact_qa"
    assert db.get_capability_domain_by_name("chart_fact_qa").id == "cd_1"


def test_dataset_json_columns(db):
    ds = m.DatasetDefinition(
        id="ds_1",
        name="imp",
        source_type="import",
        tag_filters=[{"group": "task", "name": "chart"}],
    )
    db.create_dataset(ds)
    got = db.get_dataset("ds_1")
    assert got.tag_filters == [{"group": "task", "name": "chart"}]


def test_workflow_nodes_and_edges(db):
    db.create_capability_domain(m.CapabilityDomain(id="cd_1", name="cd"))
    db.create_strategy(m.Strategy(id="st_1", name="st", capability_domain_id="cd_1"))
    wf = m.Workflow(id="wf_1", name="w", strategy_id="st_1")
    db.create_workflow(wf)
    n1 = m.WorkflowNode(
        id="nd_1", workflow_id="wf_1", stage_name="schema_check", position=0
    )
    n2 = m.WorkflowNode(
        id="nd_2",
        workflow_id="wf_1",
        stage_name="dedup",
        position=1,
        config={"k": [1, 2]},
    )
    db.create_workflow_nodes([n1, n2])
    db.replace_workflow_edges("wf_1", [("nd_1", "nd_2")])
    assert [n.id for n in db.list_workflow_nodes("wf_1")] == ["nd_1", "nd_2"]
    assert db.get_workflow_node("nd_2").config == {"k": [1, 2]}
    edges = db.list_workflow_edges("wf_1")
    assert (edges[0].from_node, edges[0].to_node) == ("nd_1", "nd_2")


def test_run_stages_upsert_and_versions(db):
    db.create_capability_domain(m.CapabilityDomain(id="cd_1", name="cd"))
    db.create_strategy(m.Strategy(id="st_1", name="st", capability_domain_id="cd_1"))
    db.create_dataset(m.DatasetDefinition(id="ds_1", name="ds", source_type="import"))
    db.create_workflow(m.Workflow(id="wf_1", name="w", strategy_id="st_1"))
    db.create_workflow_nodes(
        [
            m.WorkflowNode(
                id="nd_1", workflow_id="wf_1", stage_name="schema_check", position=0
            )
        ]
    )
    run = m.Run(id="run_1", workflow_id="wf_1", input_dataset_id="ds_1")
    db.create_run(run)
    rs = m.RunStage(run_id="run_1", node_id="nd_1", status=m.RUN_RUNNING, rows_in=5)
    db.upsert_run_stage(rs)
    db.upsert_run_stage(
        m.RunStage(
            run_id="run_1",
            node_id="nd_1",
            status=m.RUN_SUCCEEDED,
            rows_in=5,
            rows_out=4,
            failed_rows=1,
            attempts=2,
        )
    )
    got = db.get_run_stage("run_1", "nd_1")
    assert got.status == m.RUN_SUCCEEDED
    assert got.attempts == 2

    assert db.next_dataset_version("ds_1") == 1
    db.create_dataset_version(
        m.DatasetVersion(
            id="dv_1", dataset_id="ds_1", version=1, manifest_key="k1", row_count=4
        )
    )
    assert db.next_dataset_version("ds_1") == 2
    assert db.get_dataset_version("ds_1", 1).row_count == 4


def test_stale_runs_marked_interrupted(tmp_path):
    path = tmp_path / "db.sqlite"
    db = Database(path)
    db.create_capability_domain(m.CapabilityDomain(id="cd_1", name="cd"))
    db.create_strategy(m.Strategy(id="st_1", name="st", capability_domain_id="cd_1"))
    db.create_dataset(m.DatasetDefinition(id="ds_1", name="ds", source_type="import"))
    db.create_workflow(m.Workflow(id="wf_1", name="w", strategy_id="st_1"))
    db.create_model(m.Model(id="m_1", name="m", backend="api"))
    db.create_eval_set(m.EvalSet(id="es_1", name="es"))
    db.create_run(
        m.Run(
            id="run_1",
            workflow_id="wf_1",
            input_dataset_id="ds_1",
            status=m.RUN_RUNNING,
        )
    )
    db.create_eval_run(
        m.EvalRun(id="evr_1", eval_set_id="es_1", model_id="m_1", status=m.EVAL_RUNNING)
    )
    db.close()

    db2 = Database(path)
    assert db2.get_run("run_1").status == m.RUN_FAILED
    assert "interrupted" in db2.get_run("run_1").error
    assert db2.get_eval_run("evr_1").status == m.EVAL_FAILED
    db2.close()


def test_unique_constraints(db):
    db.create_capability_domain(m.CapabilityDomain(id="cd_1", name="dup"))
    with pytest.raises(sqlite3.IntegrityError):
        db.create_capability_domain(m.CapabilityDomain(id="cd_2", name="dup"))


def test_new_id_prefixes():
    assert new_id("cd_").startswith("cd_")
