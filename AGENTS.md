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

- **`llava-instruct/`** — LLaVA multimodal instruction data factory. Pipeline:
  asset pool -> template-driven supervision construction -> QA -> bbox reverse
  rendering -> train/val/smoke split + manifest. `sync_source` runs on Ray
  (one task per file, sliding-window concurrency, crash auto-retry).
- **`mm-rag/`** — multimodal RAG assistant for financial report PDFs.
- **`video-generation/`** — T2V video data pipeline with six resumable,
  shardable stages.

## Commands (run from inside a sub-project folder)

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run all tests
uv run pytest

# Run the sub-project CLI
uv run llava-instruct --help   # llava-instruct prepare-assets|generate|qa|render|split|asset
uv run mm-rag --help
uv run video-generation --help

# Build a standalone package
uv build
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
