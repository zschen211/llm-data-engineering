"""Unified public API of data-factory (spec §12).

Everything else (CLI, examples, notebooks, tests) accesses the factory
exclusively through this module::

    from data_factory.api import open_factory

    with open_factory() as factory:              # env-configured backend
        domain = factory.create_capability_domain("chart_fact_qa")
        strategy = factory.create_strategy("fact-qa", domain.id)
        ds = factory.create_dataset("qa-import", source_type="import",
                                    import_manifest="rows.jsonl")
        wf = factory.define_workflow(strategy.id, "qc-chain",
                                     [("schema_check", None), ("dedup", None),
                                      ("publish", {"dataset_id": ds.id})])
        run = factory.run_workflow(factory.create_run(wf.id, ds.id).id)

        model = factory.register_model("qwen", backend="api", base_url=...)
        factory.check_model(model.id)
        es = factory.import_eval_set("chart-qa-10", Path("eval.jsonl"))
        er = factory.run_eval(factory.create_eval_run(es.id, model.id).id)

``DataFactory`` is a composition facade over the per-domain service classes
(``PipelineService`` / ``ModelRegistryService`` / ``EvalService``); only its
public methods and ``open_factory`` are stable across versions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Self

from .eval.registry import ModelRegistryService
from .eval.service import EvalService
from .log import get_logger
from .meta.db import Database
from .pipeline import PipelineService
from .storage import StorageBackend, resolve_backend

__all__ = ["DataFactory", "open_factory"]

logger = get_logger("api")

DEFAULT_DATA_DIR = Path(os.environ.get("DFAC_DATA_DIR", "data"))
DEFAULT_MODELS_DIR = Path(os.environ.get("DFAC_MODELS_DIR", "data/models"))


def open_factory(
    data_dir: Path | None = None,
    backend: StorageBackend | None = None,
    models_dir: Path | None = None,
) -> DataFactory:
    """Build a DataFactory from configuration (env or explicit backend).

    ``data_dir``: factory root (SQLite + tmp); defaults to ``$DFAC_DATA_DIR``
    or ``data/``. Backend resolution: explicit ``backend`` wins, else
    ``DFAC_STORAGE_BACKEND`` selects (see ``storage.resolve_backend``).
    ``models_dir``: local model checkpoint discovery root (``$DFAC_MODELS_DIR``).
    """
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    return DataFactory(
        data_dir,
        resolve_backend(data_dir, backend),
        models_dir=Path(models_dir or DEFAULT_MODELS_DIR),
    )


class DataFactory(PipelineService, ModelRegistryService, EvalService):
    """Composition facade; owns the DB, storage backend and shared dirs.

    Service mixins read ``self._db`` / ``self.backend`` / ``self.tmp_dir`` /
    ``self._models_dir``, all initialized here.
    """

    def __init__(
        self,
        data_dir: Path,
        backend: StorageBackend,
        models_dir: Path | None = None,
        db_path: Path | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db = Database(db_path or self.data_dir / "datafactory.db")
        self.backend = backend
        self.tmp_dir = self.data_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._models_dir = Path(models_dir or self.data_dir / "models")
        self._models_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self._db.close()

    @property
    def db_path(self) -> Path:
        return self._db.path

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", type(self.backend).__name__)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
