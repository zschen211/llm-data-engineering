"""Model client adapters: one inference contract, three backends.

- ``api`` / ``vllm``: OpenAI-compatible HTTP (``/v1/chat/completions``);
  vllm serves the same protocol, so one adapter covers both.
- ``local``: transformers offline inference (optional ``gpu`` extra — the
  documented guard-import exception from AGENTS.md, same as mm-rag).

Clients are built from plain dicts so they survive Ray's serialization: the
pipeline stage configs carry a JSON-safe model snapshot, and workers build
clients inside the task function. ``lru_cache`` keys on the immutable config
so a worker reuses one connection per model.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from functools import lru_cache

import httpx

from ..meta import models as m

DEFAULT_TIMEOUT = 120.0


class ModelClient(ABC):
    """Uniform inference contract used by eval runner and QC stages."""

    @abstractmethod
    def generate(self, question: str, images: list[str] | None = None) -> str:
        """Answer ``question`` (images are data URLs or URLs)."""

    def close(self) -> None:
        pass

    @abstractmethod
    def describe(self) -> dict: ...


class OpenAIModelClient(ModelClient):
    """HTTP client for OpenAI-compatible endpoints (api / vllm backends)."""

    def __init__(self, base_url: str, model_name: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.api_key = api_key
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers, timeout=DEFAULT_TIMEOUT
        )

    def _payload(self, question: str, images: list[str] | None) -> dict:
        content: list[dict] = [{"type": "text", "text": question}]
        for image in images or []:
            content.append({"type": "image_url", "image_url": {"url": image}})
        return {
            "model": self.model_name,
            "messages": [{"role": "user", "content": content}],
        }

    def generate(self, question: str, images: list[str] | None = None) -> str:
        resp = self._client.post(
            "/v1/chat/completions", json=self._payload(question, images)
        )
        resp.raise_for_status()
        choices = resp.json().get("choices", [])
        if not choices:
            raise RuntimeError("model returned no choices")
        return choices[0].get("message", {}).get("content", "")

    def close(self) -> None:
        self._client.close()

    def describe(self) -> dict:
        return {
            "backend": "openai",
            "base_url": self.base_url,
            "model": self.model_name,
        }


class LocalModelClient(ModelClient):
    """transformers offline inference (``gpu`` extra required).

    Weights are loaded once per process and kept in a module-level cache
    (the ``lru_cache`` factory below), so a Ray worker reuses the loaded
    checkpoint across rows of one stage.
    """

    def __init__(self, model_id: str, weights_dir: str | None = None):
        self.model_id = model_id
        self.weights_dir = weights_dir or model_id
        self._loaded = None

    def _load(self):
        if self._loaded is not None:
            return self._loaded
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "local inference requires the optional 'gpu' extra"
                " (torch + transformers)"
            ) from exc
        # B615: weights_dir is operator-chosen at registration time; a
        # commit-hash pin is not possible for arbitrary local checkpoints.
        tokenizer = AutoTokenizer.from_pretrained(self.weights_dir)  # nosec B615
        model = AutoModelForCausalLM.from_pretrained(
            self.weights_dir,
            device_map="auto",  # nosec B615
        )
        self._loaded = (model, tokenizer)
        return self._loaded

    def generate(self, question: str, images: list[str] | None = None) -> str:
        model, tokenizer = self._load()
        inputs = tokenizer(question, return_tensors="pt").to(model.device)
        output = model.generate(**inputs, max_new_tokens=256, do_sample=False)
        return tokenizer.decode(
            output[0][inputs.input_ids.shape[1] :], skip_special_tokens=True
        ).strip()

    def describe(self) -> dict:
        return {"backend": "local", "model_id": self.model_id}


def model_to_cfg(model: m.Model) -> dict:
    """JSON-safe snapshot of a model row (survives Ray serialization)."""
    return {
        "backend": model.backend,
        "model_id": model.model_id,
        "weights_dir": model.weights_dir,
        "base_url": model.base_url,
        "api_key_env": model.api_key_env,
        "name": model.name,
    }


@lru_cache(maxsize=8)
def _build_cached(cfg_key: str) -> ModelClient:
    cfg = json.loads(cfg_key)
    backend = cfg["backend"]
    if backend in ("api", "vllm"):
        api_key = os.environ.get(cfg.get("api_key_env", ""), "")
        return OpenAIModelClient(cfg["base_url"], cfg["model_id"], api_key)
    if backend == "local":
        return LocalModelClient(cfg["model_id"], cfg.get("weights_dir"))
    raise ValueError(f"unknown backend: {backend}")


def build_client(cfg: dict) -> ModelClient:
    """Build (or reuse a cached) client from a JSON-safe config dict."""
    return _build_cached(json.dumps(cfg, ensure_ascii=False, sort_keys=True))


def build_client_for_model(model: m.Model) -> ModelClient:
    """Build a client from a registered model row."""
    return build_client(model_to_cfg(model))
