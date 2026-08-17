# AGENTS.md

This file provides guidance for AI agents working with code in this repository.

## Repository layout

The repo is a **four-layer stack**:

```
frontend/           pure static SPA (vite+react+ts); UI only, talks to backends over HTTP
asset-management/   platform service: digital asset layer + management API
data-factory/       business service: data production & eval closed loop
mm-rag/             business service: multimodal RAG assistant (phase-2 shared-stack)
video-generation/   business service: T2V video data pipeline (phase-2)
infra/              middleware & ops: RustFS/Ray/Prometheus/Grafana/nginx + scripts (declarative)
```

- Every Python sub-project lives in its own top-level folder and is a
  completely separate package (own `pyproject.toml`, own dependencies, own
  tests, own `uv.lock`), built/run on its own. There is no shared workspace —
  do NOT run `uv sync` from the repo root; always `cd` into the sub-project
  folder.
- `infra/` has **no Python code and is never imported**; services connect to
  middleware only through the contract in `infra/docs/contract.md` (ports /
  env vars / metric names / API paths / data dirs).
- `frontend/` is a JS project (npm); it must never import Python packages —
  it consumes the two backends' `/api/*` endpoints over HTTP only
  (dev: vite proxy, prod: infra nginx gateway, same path split).

The sub-projects mirror projects 3/5/14 of 《大模型数据工程》
(datascale-ai.github.io/data_engineering_book/part14/):

- **`asset-management/`** — platform service, generic digital asset layer:
  sources / HF download pipeline / content-addressed storage (local or
  RustFS) / tags / versions / snapshots, plus a FastAPI management API.
  `sync_source` runs on Ray (one task per file, sliding-window concurrency,
  crash auto-retry). Programmatic entry: `asset_management.assets.api`.
- **`data-factory/`** — data production & eval closed loop on top of the
  asset-management asset layer (strategies/workflows/lineage/model registry/
  eval/reports, spec in `data-factory/docs/spec/`). Consumes assets only via
  `asset_management.assets.api` (path dependency); own SQLite + storage,
  Ray Data executor; FastAPI management API (`data_factory.routes`); `dfac`
  CLI; programmatic entry `data_factory.api`.
- **`mm-rag/`** — multimodal RAG assistant for financial report PDFs.
- **`video-generation/`** — T2V video data pipeline with six resumable,
  shardable stages.

## Commands

```bash
# Python sub-projects (run from inside the sub-project folder)
uv sync --extra dev
uv run pytest
uv build

# Serve the backends
asset-management/scripts/serve.sh --port 8000   # asset-management API
data-factory/scripts/serve.sh --port 8001       # data-factory API

# frontend (from frontend/)
npm install && npm run dev       # http://localhost:5173

# infra (from infra/)
./scripts/up.sh && ./scripts/ray-start.sh   # middleware + Ray cluster (export RAY_ADDRESS)
./scripts/obs_check.sh                     # observability smoke

# Lint check (run after every Python code change, see Code quality)
scripts/run_lint.sh                          # all gates: ruff + radon + pylint + bandit
uv run ruff check src tests                  # or run them individually
uv run radon cc src -s -n C                  # must print nothing (complexity <= B)
uv run pylint src tests
uv run bandit -r src -q
```

## Dependency & import rules (MUST follow)

- **No dynamic imports.** Never `try/except ImportError` around imports, never
  import a dependency inside a function to defer its availability, and never
  ship `_require_*` helpers that probe installed packages at runtime.
- **No runtime dependency installation.** Never instruct users to install
  packages at runtime; never `pip install` / `subprocess` installs from code.
- **All dependencies are declared in `pyproject.toml`.** Runtime dependencies
  go into `[project.dependencies]` (core — installed with every `uv sync`);
  test tooling only (pytest, moto, httpx, …) goes into the `dev` extra. All
  imports must be static top-level imports.
- **Documented exception:** `mm-rag` and `video-generation` keep their `gpu`
  extra (torch/transformers/byaldi/clip — multi-GB, CPU paths must stay light);
  their runtime guard-imports are an accepted, deliberate exception. Do not
  extend this exception to other code and do not introduce new occurrences
  without explicit approval.
- **Ray cluster:** services attach the shared cluster via `RAY_ADDRESS`
  (infra contract); when unset they start an embedded local cluster as a dev
  fallback (loud warning). Tests always use a session-scoped embedded cluster
  and never touch the shared one. `RAY_ENABLE_UV_RUN_RUNTIME_ENV=0` is forced
  by both packages at import (uv-run path-dependency packaging guard).

## Conventions

- Each sub-project: `pyproject.toml` (hatchling, src/ layout,
  `[project.scripts]` entry), `src/<package>/`, `tests/`, README with an
  end-to-end runnable example.
- Never put comments in code unless they carry design intent (the sub-projects
  use docstrings for that).
- `pytest` runs from each sub-project's own folder; there is no root-level
  test config.
- Cross-service changes that alter the contract (ports/env/metric names/API
  paths) must update `infra/docs/contract.md` in the same change.

## Code quality (MUST follow)

Four lint gates guard every Python sub-project: **ruff**, **radon**, **pylint**
and **bandit**. They are configured in each sub-project's `pyproject.toml`
(`[tool.ruff.lint]`, `[tool.pylint.*]`); `scripts/run_lint.sh` runs all four
(the same checks are documented in the Commands section).

- **Run all four gates after every code change.** After any modification to a
  sub-project, execute `scripts/run_lint.sh` from that sub-project's folder:
  1. `uv run ruff check src tests` — zero findings
  2. `uv run ruff format --check src tests` — zero files to reformat
  3. `uv run radon cc src -s -n C` — must print nothing (no block ranked
     C or worse, i.e. cyclomatic complexity must stay <= B, < 11)
  4. `uv run pylint src tests` — exit code 0
  5. `uv run bandit -r src -q` — exit code 0
  The change is only done when every gate passes with zero findings.
- **Fix whatever any gate reports.** When a tool reports errors, fix the code
  according to the reported messages and re-run that tool; repeat until it
  passes with zero findings. Do not stop after applying only the auto-fixable
  subset.
- Do not silence findings with `# noqa` / `# nosec` / inline
  `# pylint: disable=` to make a gate pass; fix the code instead. The only
  sanctioned inline suppressions are ones justified by a design-intent
  comment (e.g. verified-safe f-string SQL for bandit B608, the documented
  gpu-extra guard imports). The shared `disable`/`ignore` lists in
  `pyproject.toml` cover the repo-wide deliberate patterns (e.g. BLE001
  blind-catch, composition-facade `no-member`, pytest fixture warnings); do
  not extend them per-finding without asking.
- Complexity: keep every function/method at radon rank B or better (<= 10).
  When a function drifts above B, extract phase helpers instead of raising
  the radon threshold.
