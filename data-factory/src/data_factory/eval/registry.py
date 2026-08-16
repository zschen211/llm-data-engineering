"""Model registry: registration, service discovery, heartbeat checks.

Three backends are registered uniformly (spec D8):

- ``local``: checkpoint directories under ``models_dir`` (a dir is a
  checkpoint when it contains ``config.json`` + weight files); discovered by
  directory scan, checked by filesystem probe. Inference needs the ``gpu``
  extra.
- ``vllm`` / ``api``: OpenAI-compatible HTTP endpoints; reachability is
  probed via ``GET {base_url}/v1/models``.

API keys are stored as *environment variable names* only (never the secret
itself). State machine: ``pending -> ready | failed``, refreshed by
``check_model`` / ``scan_models``.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ..meta import models as m
from ..meta.db import new_id

WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".ckpt")


def is_checkpoint_dir(path: Path) -> bool:
    if not (path / "config.json").is_file():
        return False
    return any(path.glob(f"*{s}") for s in WEIGHT_SUFFIXES)


def scan_checkpoints(models_dir: Path) -> list[Path]:
    if not Path(models_dir).is_dir():
        return []
    return [p for p in sorted(Path(models_dir).iterdir()) if is_checkpoint_dir(p)]


def probe_http(base_url: str, timeout: float = 5.0) -> bool:
    url = base_url.rstrip("/") + "/v1/models"
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


class ModelRegistryService:
    """Model registry operations; mixed into the DataFactory facade."""

    def register_model(
        self,
        name: str,
        backend: str,
        model_id: str = "",
        weights_dir: str = "",
        base_url: str = "",
        api_key_env: str = "",
    ) -> m.Model:
        if backend not in ("local", "vllm", "api"):
            raise ValueError(f"unknown backend: {backend}")
        if backend == "local" and not (weights_dir or model_id):
            raise ValueError("local backend needs weights_dir or model_id")
        if backend in ("vllm", "api") and not base_url:
            raise ValueError(f"{backend} backend needs base_url")
        existing = self._db.get_model_by_name(name)
        if existing:
            raise ValueError(f"model already registered: {name}")
        model = m.Model(
            id=new_id("m_"),
            name=name,
            backend=backend,
            model_id=model_id,
            weights_dir=weights_dir,
            base_url=base_url,
            api_key_env=api_key_env,
        )
        self._db.create_model(model)
        return model

    def scan_models(self, models_dir: Path | str | None = None) -> list[m.Model]:
        """Service discovery: register unseen checkpoints as pending local
        models and return the full local model list."""
        models_dir = Path(models_dir or self._models_dir)
        seen = {p.name for p in scan_checkpoints(models_dir)}
        for checkpoint in seen:
            if self._db.get_model_by_name(checkpoint) is None:
                self._db.create_model(
                    m.Model(
                        id=new_id("m_"),
                        name=checkpoint,
                        backend="local",
                        model_id=checkpoint,
                        weights_dir=str(models_dir / checkpoint),
                    )
                )
        for model in self._db.models_in_dir(str(models_dir)):
            if model.name not in seen:
                self._db.delete_model(model.id)
        return [m_ for m_ in self._db.list_models() if m_.backend == "local"]

    def check_model(self, model_id: str) -> m.Model:
        """Heartbeat probe; flips the model's status to ready/failed."""
        model = self._db.get_model(model_id)
        if model is None:
            raise ValueError(f"unknown model: {model_id}")
        if model.backend == "local":
            ok = is_checkpoint_dir(Path(model.weights_dir or model.model_id))
            error = "" if ok else "checkpoint not found"
        else:
            ok = probe_http(model.base_url)
            error = "" if ok else "endpoint unreachable"
        fields = {
            "status": m.MODEL_READY if ok else m.MODEL_FAILED,
            "last_check_at": m._now(),
            "last_error": error,
            "updated_at": m._now(),
        }
        self._db.update_model(model_id, fields)
        return self._db.get_model(model_id)

    def list_models(self) -> list[m.Model]:
        return self._db.list_models()

    def remove_model(self, model_id: str) -> int:
        return self._db.delete_model(model_id)
