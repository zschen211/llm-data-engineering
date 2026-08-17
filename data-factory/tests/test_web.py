"""Web API tests: the data-factory FastAPI routes under /api/*."""

import pytest
from conftest import make_import_rows, write_import_manifest
from fastapi.testclient import TestClient

from data_factory.routes import create_app
from data_factory.routes.common import reset_metrics


@pytest.fixture()
def client(factory):
    reset_metrics()
    app = create_app(factory)
    with TestClient(app) as test_client:
        yield test_client
    factory.close()


def _seed_strategy(factory):
    domain = factory.create_capability_domain("chart-facts")
    return factory.create_strategy("fact-qa", domain.id)


def test_factory_info(client, factory):
    resp = client.get("/api/factory-info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "local"
    assert body["capability_count"] == 0


def test_capabilities_and_strategies(client, factory):
    cap = client.post(
        "/api/capabilities", json={"name": "chart-facts", "description": "图表事实"}
    )
    assert cap.status_code == 201
    cap_id = cap.json()["id"]

    dup = client.post("/api/capabilities", json={"name": "chart-facts"})
    assert dup.status_code == 400

    strat = client.post(
        "/api/strategies", json={"name": "fact-qa", "capability_domain_id": cap_id}
    )
    assert strat.status_code == 201
    assert len(client.get("/api/strategies").json()) == 1


def test_datasets_validation(client):
    ok = client.post(
        "/api/datasets",
        json={"name": "qa", "source_type": "import", "import_manifest": "rows.jsonl"},
    )
    assert ok.status_code == 201
    bad = client.post("/api/datasets", json={"name": "x", "source_type": "snapshot"})
    assert bad.status_code == 400
    bad_type = client.post("/api/datasets", json={"name": "x", "source_type": "wat"})
    assert bad_type.status_code == 422


def test_workflow_define_and_validate(client, factory):
    strategy = _seed_strategy(factory)
    wf = client.post(
        "/api/workflows",
        json={
            "strategy_id": strategy.id,
            "name": "qc-chain",
            "stages": [{"stage": "schema_check"}, {"stage": "dedup"}],
        },
    )
    assert wf.status_code == 201
    wf_id = wf.json()["id"]

    unknown = client.post(
        "/api/workflows",
        json={"strategy_id": strategy.id, "name": "bad", "stages": [{"stage": "nope"}]},
    )
    assert unknown.status_code == 400

    validate = client.post(f"/api/workflows/{wf_id}/validate")
    assert validate.status_code == 200
    assert len(validate.json()["order"]) == 2

    show = client.get(f"/api/workflows/{wf_id}")
    assert show.status_code == 200
    assert len(show.json()["order"]) == 2


def test_run_executes_chain(client, factory, tmp_path):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=4))
    ds = client.post(
        "/api/datasets",
        json={"name": "qa", "source_type": "import", "import_manifest": str(manifest)},
    ).json()
    strategy = _seed_strategy(factory)
    wf = client.post(
        "/api/workflows",
        json={
            "strategy_id": strategy.id,
            "name": "chain",
            "stages": [
                {"stage": "schema_check"},
                {"stage": "publish", "config": {"dataset_id": ds["id"]}},
            ],
        },
    ).json()

    run = client.post(
        "/api/runs", json={"workflow_id": wf["id"], "input_dataset_id": ds["id"]}
    )
    assert run.status_code == 201
    run_id = run.json()["id"]

    executed = client.post(f"/api/runs/{run_id}/run")
    assert executed.status_code == 202
    assert executed.json()["status"] == "succeeded"

    show = client.get(f"/api/runs/{run_id}")
    assert show.status_code == 200
    assert len(show.json()["stages"]) == 2

    lineage = client.get("/api/lineage", params={"run_id": run_id})
    assert lineage.status_code == 200


def test_stages_registry(client):
    stages = client.get("/api/stages").json()
    names = {s["name"] for s in stages}
    assert {"schema_check", "dedup", "filter", "publish", "qc_llm"}.issubset(names)
    assert all(s["kind"] in ("transform", "sink", "qc_rule", "qc_llm") for s in stages)


def test_models_register_and_check(client, factory, mock_llm):
    model = client.post(
        "/api/models",
        json={"name": "mock", "backend": "api", "base_url": mock_llm},
    )
    assert model.status_code == 201
    model_id = model.json()["id"]

    checked = client.post(f"/api/models/{model_id}/check")
    assert checked.status_code == 200
    assert checked.json()["status"] == "ready"

    bad = client.post("/api/models", json={"name": "x", "backend": "api"})
    assert bad.status_code == 400

    deleted = client.delete(f"/api/models/{model_id}")
    assert deleted.status_code == 204


def test_eval_roundtrip_and_reports(client, factory, mock_llm):
    model = client.post(
        "/api/models",
        json={"name": "mock", "backend": "api", "base_url": mock_llm},
    ).json()
    client.post(f"/api/models/{model['id']}/check")

    items = [
        {
            "question": "q1: what is the bar height?",
            "expected": "answer:q1: what is the bar height?",
        },
        {
            "question": "q2: what is the bar height?",
            "expected": "answer:q2: what is the bar height?",
        },
    ]
    evs = client.post("/api/eval-sets", json={"name": "chart-qa", "items": items})
    assert evs.status_code == 201
    evs_id = evs.json()["id"]
    assert evs.json()["item_count"] == 2

    show = client.get(f"/api/eval-sets/{evs_id}")
    assert len(show.json()["items"]) == 2

    empty = client.post("/api/eval-sets", json={"name": "empty", "items": []})
    assert empty.status_code == 400

    evr = client.post(
        "/api/eval-runs", json={"eval_set_id": evs_id, "model_id": model["id"]}
    )
    assert evr.status_code == 201
    evr_id = evr.json()["id"]

    not_ready = client.post(
        "/api/models",
        json={"name": "unchecked", "backend": "api", "base_url": mock_llm},
    ).json()
    conflict = client.post(
        "/api/eval-runs", json={"eval_set_id": evs_id, "model_id": not_ready["id"]}
    )
    assert conflict.status_code == 409

    executed = client.post(f"/api/eval-runs/{evr_id}/run")
    assert executed.status_code == 202
    assert executed.json()["status"] == "succeeded"

    reports = client.get("/api/reports", params={"eval_run_id": evr_id})
    assert reports.status_code == 200
    assert len(reports.json()) == 1
    report = reports.json()[0]

    payload = client.get(f"/api/reports/{report['id']}/payload")
    assert payload.status_code == 200
    assert payload.json()["aggregate"]["overall"]["avg_score"] == 1.0

    show_run = client.get(f"/api/eval-runs/{evr_id}")
    assert len(show_run.json()["results"]) == 2


def test_metrics_endpoint(client):
    client.get("/api/factory-info")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "asset_http_requests_total" in resp.text
