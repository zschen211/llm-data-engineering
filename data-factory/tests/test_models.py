"""Model registry tests: registration, discovery scan, heartbeat checks."""

import pytest

from data_factory.eval.registry import is_checkpoint_dir, scan_checkpoints
from data_factory.meta import models as m


def _make_checkpoint(models_dir, name: str):
    ckpt = models_dir / name
    ckpt.mkdir(parents=True, exist_ok=True)
    (ckpt / "config.json").write_text("{}")
    (ckpt / "model.safetensors").write_text("w")
    return ckpt


def test_register_validation(factory):
    with pytest.raises(ValueError, match="unknown backend"):
        factory.register_model("x", backend="wat")
    with pytest.raises(ValueError, match="base_url"):
        factory.register_model("x", backend="api")
    with pytest.raises(ValueError, match="weights_dir"):
        factory.register_model("x", backend="local")
    model = factory.register_model(
        "m1", backend="api", base_url="http://x", model_id="qwen"
    )
    assert model.status == m.MODEL_PENDING
    with pytest.raises(ValueError, match="already registered"):
        factory.register_model("m1", backend="api", base_url="http://x")


def test_scan_models_discovery(factory):
    _make_checkpoint(factory._models_dir, "qwen-vl-sft")
    _make_checkpoint(factory._models_dir, "llava-sft")
    factory.scan_models()
    names = {mo.name for mo in factory.list_models()}
    assert {"qwen-vl-sft", "llava-sft"} <= names
    # re-scan is idempotent
    factory.scan_models()
    assert len([mo for mo in factory.list_models() if mo.backend == "local"]) == 2


def test_checkpoint_helpers(tmp_path):
    assert scan_checkpoints(tmp_path) == []
    ckpt = _make_checkpoint(tmp_path, "a")
    assert is_checkpoint_dir(ckpt)
    assert is_checkpoint_dir(tmp_path / "b") is False
    assert [p.name for p in scan_checkpoints(tmp_path)] == ["a"]


def test_check_local_model(factory):
    ckpt = _make_checkpoint(factory._models_dir, "local-ckpt")
    factory.scan_models()
    model = factory._db.get_model_by_name("local-ckpt")
    checked = factory.check_model(model.id)
    assert checked.status == m.MODEL_READY
    assert checked.last_error == ""

    broken = factory.register_model(
        "broken", backend="local", weights_dir=str(ckpt / "nope")
    )
    checked = factory.check_model(broken.id)
    assert checked.status == m.MODEL_FAILED
    assert "checkpoint not found" in checked.last_error


def test_check_api_model(factory, mock_llm):
    model = factory.register_model(
        "svc", backend="vllm", base_url=mock_llm, model_id="mock"
    )
    checked = factory.check_model(model.id)
    assert checked.status == m.MODEL_READY

    dead = factory.register_model("dead", backend="api", base_url="http://127.0.0.1:1")
    checked = factory.check_model(dead.id)
    assert checked.status == m.MODEL_FAILED
    assert "unreachable" in checked.last_error


def test_remove_model(factory):
    model = factory.register_model("m1", backend="api", base_url="http://x")
    assert factory.remove_model(model.id) == 1
    assert factory._db.get_model(model.id) is None


def test_eval_requires_ready_model(factory, tmp_path):
    from conftest import write_eval_items

    es = factory.import_eval_set(
        "es", write_eval_items(tmp_path), capability_domain_id=""
    )
    model = factory.register_model("m1", backend="api", base_url="http://x")
    with pytest.raises(RuntimeError, match="pending"):
        factory.create_eval_run(es.id, model.id)
