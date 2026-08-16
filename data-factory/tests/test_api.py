"""Facade (open_factory) tests, incl. snapshot + derived dataset paths."""

import os

import pytest
from conftest import make_import_rows, write_import_manifest

from data_factory.api import DataFactory, open_factory
from data_factory.storage import LocalStorageBackend


def test_open_factory_local(tmp_path):
    with open_factory(
        data_dir=tmp_path / "d",
        backend=LocalStorageBackend(tmp_path / "art"),
        models_dir=tmp_path / "m",
    ) as factory:
        assert isinstance(factory, DataFactory)
        assert factory.db_path.is_file()


def test_open_factory_requires_valid_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DFAC_STORAGE_BACKEND", "s3")
    monkeypatch.delenv("RUSTFS_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="RUSTFS_ENDPOINT"):
        open_factory(data_dir=tmp_path / "d")


def test_snapshot_dataset_consumes_asset_layer(tmp_path, monkeypatch):
    """Snapshot datasets read the llava-instruct asset layer via its API."""
    asset_dir = tmp_path / "assets"
    monkeypatch.setenv("LLAVA_DATA_DIR", str(asset_dir))
    monkeypatch.setenv("LLAVA_STORAGE_BACKEND", "local")

    from llava_instruct.assets.api import open_store as open_asset_store

    with open_asset_store(data_dir=asset_dir) as store:
        store.import_dir(
            _blob_dir(tmp_path),
            labels={"a.png": "chart", "b.png": "chart", "c.png": "doc"},
        )
        store.tag_asset(store.list_assets()[0].id, "chart", group="task")
        store.tag_asset(store.list_assets()[1].id, "chart", group="task")
        snapshot = store.create_snapshot(name="v1")

    with open_factory(
        data_dir=tmp_path / "d",
        backend=LocalStorageBackend(tmp_path / "art"),
        models_dir=tmp_path / "m",
    ) as factory:
        ds = factory.create_dataset(
            "snap",
            source_type="snapshot",
            snapshot_id=snapshot["id"],
            tag_filters=[{"group": "task", "name": "chart"}],
        )
        domain = factory.create_capability_domain("d")
        strategy = factory.create_strategy("s", domain.id)
        wf = factory.define_workflow(
            strategy.id,
            "w",
            [
                ("schema_check", {"fields": [{"name": "asset_id", "type": "string"}]}),
                ("publish", {"dataset_id": ds.id}),
            ],
        )
        run = factory.create_run(wf.id, ds.id)
        final = factory.run_workflow(run.id)
        assert final.status == "succeeded"
        versions = factory._db.list_dataset_versions(ds.id)
        assert versions[0].row_count == 2  # c.png filtered out


def test_derived_dataset_chains_versions(factory, tmp_path):
    manifest = write_import_manifest(tmp_path, make_import_rows(count=4))
    ds1 = factory.create_dataset(
        "qa1", source_type="import", import_manifest=str(manifest)
    )
    domain = factory.create_capability_domain("d")
    strategy = factory.create_strategy("s", domain.id)
    wf = factory.define_workflow(
        strategy.id,
        "w1",
        [
            ("schema_check", None),
            ("publish", {"dataset_id": ds1.id}),
        ],
    )
    factory.run_workflow(factory.create_run(wf.id, ds1.id).id)

    ds2 = factory.create_dataset(
        "qa2", source_type="derived", derived_from=f"{ds1.id}@1"
    )
    wf2 = factory.define_workflow(
        strategy.id,
        "w2",
        [
            ("dedup", None),
            ("publish", {"dataset_id": ds2.id}),
        ],
    )
    factory.run_workflow(factory.create_run(wf2.id, ds2.id).id)
    assert factory._db.list_dataset_versions(ds2.id)[0].row_count == 4


def test_dataset_validation(factory):
    with pytest.raises(ValueError, match="source_type"):
        factory.create_dataset("x", source_type="wat")
    with pytest.raises(ValueError, match="snapshot_id"):
        factory.create_dataset("x", source_type="snapshot")
    with pytest.raises(ValueError, match="import_manifest"):
        factory.create_dataset("x", source_type="import")


def test_workflow_definition_validates_stages(factory):
    domain = factory.create_capability_domain("d")
    strategy = factory.create_strategy("s", domain.id)
    with pytest.raises(ValueError, match="unknown stage"):
        factory.define_workflow(strategy.id, "w", [("nope", None)])


def _blob_dir(tmp_path: "pytest.TempPathFactory") -> "os.PathLike":

    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    for name in ("a.png", "b.png", "c.png"):
        # distinct content: the asset layer content-dedups identical blobs
        (src / name).write_bytes(f"fake-png-{name}".encode())
    return src
