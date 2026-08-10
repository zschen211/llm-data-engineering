# AGENTS.md

This file provides guidance for AI agents working with code in this repository.

## Repository layout

The repo is a container of **independent sub-projects**. Each sub-project lives
in its own top-level folder, is a completely separate Python package (own
`pyproject.toml`, own dependencies, own tests, own `uv.lock`) and can be
built/run on its own. There is no shared workspace — do NOT run `uv sync` from
the repo root and expect sub-project code to be importable; always `cd` into
the sub-project folder.

The three sub-projects mirror projects 3/5/14 of 《大模型数据工程》
(datascale-ai.github.io/data_engineering_book/part14/):

- **`llava-instruct/`** — LLaVA multimodal asset factory: unified asset
  layer (sources/download pipeline/storage/tags/versions/snapshots) with a
  FastAPI management UI. `sync_source` runs on Ray (one task per file,
  sliding-window concurrency, crash auto-retry). Programmatic entry:
  `llava_instruct.assets.api`.
- **`mm-rag/`** — multimodal RAG assistant for financial report PDFs.
- **`video-generation/`** — T2V video data pipeline with six resumable,
  shardable stages.

## Commands (run from inside a sub-project folder)

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run all tests
uv run pytest

# Run the sub-project CLI / Web UI
llava-instruct/scripts/serve.sh --port 8000   # asset-manager Web UI

# Build a standalone package
uv build

# Lint check (run after every code change, see Code quality)
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

## Conventions

- Each sub-project: `pyproject.toml` (hatchling, src/ layout,
  `[project.scripts]` entry), `src/<package>/`, `tests/`, README with an
  end-to-end runnable example.
- Never put comments in code unless they carry design intent (the sub-projects
  use docstrings for that).
- `pytest` runs from each sub-project's own folder; there is no root-level
  test config.

## Code quality (MUST follow)

Four lint gates guard every sub-project: **ruff**, **radon**, **pylint** and
**bandit**. They are configured in each sub-project's `pyproject.toml`
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
