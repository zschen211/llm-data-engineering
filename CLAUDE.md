# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

The repo is a container of **independent sub-projects** in a four-layer stack:

```
frontend/           pure static SPA (vite+react+ts); talks to backends over HTTP only
asset/              platform service: digital asset layer + management API
data_factory/       business service: data production & eval closed loop
  mm_rag/           business service: multimodal RAG assistant (phase-2 shared-stack)
  video_generation/ business service: T2V video data pipeline (phase-2)
infra/              middleware & ops: RustFS/Ray/Prometheus/Grafana/nginx + lifecycle scripts
```

Each Python sub-project is a completely separate package (own `pyproject.toml`,
own dependencies, own tests, own `uv.lock`) and can be built/run on its own.
There is no shared workspace — do NOT run `uv sync` from the repo root; always
`cd` into the sub-project folder. `infra/` is declarative (compose + configs +
bash scripts, no Python code, never imported). The sub-projects mirror projects
3/5/14 of 《大模型数据工程》
(datascale-ai.github.io/data_engineering_book/part14/).

- **`asset/`** — platform service, generic digital asset layer:
  sources / HF download pipeline (Ray Data, sliding-window concurrency, crash
  auto-retry) / content-addressed storage (local or RustFS) / tags / versions /
  snapshots / management API. Programmatic entry: `asset_management.assets.api`
  (the only stable entry point). Management UI lives in `frontend/`.
- **`data_factory/`** — data production & eval closed loop on top of the asset
  layer: capability domains / strategies / workflows / lineage / model registry /
  eval / reports + a FastAPI management API (`data_factory.routes`). Consumes
  assets only via `asset_management.assets.api` (path dependency); own SQLite +
  storage; `dfac` CLI; programmatic entry `data_factory.api`.
- **`data_factory/mm_rag/`** — multimodal RAG assistant for financial report
  PDFs.
- **`data_factory/video_generation/`** — T2V video data pipeline with six
  resumable, shardable stages.
- **`infra/`** — middleware & ops: docker compose (RustFS/Prometheus/Grafana/
  node-exporter), Ray standalone cluster scripts, backup/clean/status scripts,
  nginx gateway config. The **contract** (ports/env vars/metric names/API paths/
  data dirs) lives in `infra/docs/contract.md` — services connect to middleware
  only through it, never by importing infra.

## Commands

```bash
# Python sub-projects (run from inside the sub-project folder)
uv sync --extra dev
uv run pytest
uv build
uv run ruff check src tests        # or scripts/run_lint.sh: ruff+radon+pylint+bandit

# frontend (run from frontend/)
npm install
npm run dev                        # http://localhost:5173 (proxies /api to both backends)
npm run lint && npm run typecheck

# infra (run from infra/)
./scripts/up.sh                    # RustFS + Prometheus + Grafana + node-exporter
./scripts/ray-start.sh             # standalone Ray cluster (export RAY_ADDRESS)
./scripts/obs_check.sh             # observability smoke
./scripts/backup.sh                # consistent SQLite snapshots
```

## Conventions

- Follow the established structure when adding a new sub-project: `pyproject.toml`
  (hatchling, src/ layout, `[project.scripts]` entry), `src/<package>/`, `tests/`,
  README with an end-to-end runnable example.
- Keep heavy ML dependencies (torch, transformers, byaldi, clip) in
  `[project.optional-dependencies] gpu` so the CPU path stays light; guard
  imports at call time and raise a clear `RuntimeError` mentioning the `gpu`
  extra.
- Never put comments in code unless they carry design intent (sub-projects use
  docstrings for that).
- `pytest` runs from each sub-project's own folder; there is no root-level test
  config.
- Ray: attach the shared cluster via `RAY_ADDRESS` (infra contract); when unset
  an embedded local cluster is the dev fallback (loud warning). Tests always use
  a session-scoped embedded cluster, never the shared one.
