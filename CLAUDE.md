# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

The repo is a container of **independent sub-projects**. Each sub-project lives in its own top-level folder, is a completely separate Python package (own `pyproject.toml`, own dependencies, own tests, own `uv.lock`) and can be built/run on its own. There is no shared workspace — do NOT run `uv sync` from the repo root and expect sub-project code to be importable; always `cd` into the sub-project folder.

The three sub-projects mirror projects 3/5/14 of 《大模型数据工程》 (datascale-ai.github.io/data_engineering_book/part14/):

- **`llava-instruct/`** — LLaVA multimodal instruction data factory. Pipeline: asset pool (general/document/chart images) -> template-driven supervision construction (caption, counting, OCR summary, doc QA, chart reading/comparison, region grounding, multi-image comparison) -> QA (structure/semantic/bbox checks) -> bbox reverse rendering -> train/val/smoke split + manifest. Deps: Pillow only; sample schema in `src/llava_instruct/schema.py`.
- **`mm-rag/`** — multimodal RAG assistant for financial report PDFs. Pipeline: page rendering (PyMuPDF) -> visual index (Byaldi, optional `gpu` extra) or lexical fallback -> Top-K retrieval with table-of-contents page suppression -> evidence-organized answers (fallback or VLM) -> hit@k/evidence/directory-suppression evaluation. Deps: pymupdf; `gpu` extra adds torch/transformers/byaldi.
- **`video-generation/`** — T2V video data pipeline with six resumable, shardable stages: source loading (ffprobe) -> PySceneDetect shot segmentation -> Farneback optical-flow motion filter -> CLIP + LAION-Aesthetic scoring -> multi-frame VLM captioning -> shot-language tagging (controlled vocab + camera-motion classification) -> final manifest joined on `shot_id`. Deps: numpy/scenedetect/opencv-python-headless; `gpu` extra adds torch/transformers.

## Commands (run from inside a sub-project folder)

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run all tests
uv run pytest

# Run the sub-project CLI
uv run llava-instruct --help   # llava-instruct prepare-assets|generate|qa|render|split
uv run mm-rag --help           # mm-rag render-pdf|build-index|ask|evaluate
uv run video-generation --help # video-generation load-sources|scene-detect|motion-filter|aesthetic-filter|caption|tag-shot-language|build-manifest

# Build a standalone package
uv build

# Optional GPU capabilities (visual indexing, VLM captioning, aesthetic scoring)
uv sync --extra gpu
```

## Conventions

- Follow the established structure when adding a new sub-project: `pyproject.toml` (hatchling, src/ layout, `[project.scripts]` entry), `src/<package>/`, `tests/`, README with an end-to-end runnable example.
- Keep heavy ML dependencies (torch, transformers, byaldi, clip) in `[project.optional-dependencies] gpu` so the CPU path stays light; guard imports at call time and raise a clear `RuntimeError` mentioning the `gpu` extra.
- Never put comments in code unless they carry design intent (all three sub-projects use docstrings for that).
- `pytest` runs from each sub-project's own folder; there is no root-level test config.
