"""Lineage tests: by_run / by_dataset / by_strategy."""

from conftest import make_import_rows, write_import_manifest

from data_factory import lineage


def _seed_run(factory, tmp_path, stages=None):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=5))
    ds = factory.create_dataset(
        "qa", source_type="import", import_manifest=str(manifest)
    )
    domain = factory.create_capability_domain("chart_fact_qa")
    strategy = factory.create_strategy("fact-qa", domain.id)
    wf = factory.define_workflow(
        strategy.id,
        "chain",
        stages
        or [
            ("schema_check", None),
            ("publish", {"dataset_id": ds.id}),
        ],
    )
    run = factory.create_run(wf.id, ds.id)
    factory.run_workflow(run.id)
    return {"domain": domain, "strategy": strategy, "wf": wf, "run": run, "ds": ds}


def test_by_run(factory, tmp_path):
    seed = _seed_run(factory, tmp_path)
    view = lineage.by_run(factory._db, seed["run"].id)
    assert view["strategy_name"] == "fact-qa"
    assert view["input_dataset_id"] == seed["ds"].id
    assert len(view["stages"]) == 2
    assert len(view["artifacts"]) == 3  # input + schema_check + publish
    assert len(view["produced_versions"]) == 1


def test_by_dataset(factory, tmp_path):
    seed = _seed_run(factory, tmp_path)
    view = lineage.by_dataset(factory._db, seed["ds"].id)
    assert len(view["versions"]) == 1
    version = view["versions"][0]
    assert version["produced_by"]["run_id"] == seed["run"].id
    assert version["produced_by"]["strategy_id"] == seed["strategy"].id


def test_by_dataset_version_filter(factory, tmp_path):
    seed = _seed_run(factory, tmp_path)
    view = lineage.by_dataset(factory._db, seed["ds"].id, 1)
    assert view["versions"][0]["version"] == 1
    assert lineage.by_dataset(factory._db, seed["ds"].id, 99)["versions"] == []


def test_by_strategy(factory, tmp_path):
    seed = _seed_run(factory, tmp_path)
    view = lineage.by_strategy(factory._db, seed["strategy"].id)
    assert view["capability_domain"] == "chart_fact_qa"
    assert view["workflows"] == [seed["wf"].id]
    assert view["runs"][0]["produced"] == [{"dataset_id": seed["ds"].id, "version": 1}]


def test_unknown_ids_raise(factory):
    for fn, arg in (
        (lineage.by_run, "run_x"),
        (lineage.by_dataset, "ds_x"),
        (lineage.by_strategy, "st_x"),
    ):
        try:
            fn(factory._db, arg)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
