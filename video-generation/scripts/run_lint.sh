#!/usr/bin/env bash
# Run all code-quality gates: ruff, radon (complexity), pylint, bandit.
# Usage: scripts/run_lint.sh
set -euo pipefail
cd "$(dirname "$0")/.."

uv run ruff check src tests
uv run ruff format --check src tests

# radon: fail when any block ranks C or worse (complexity >= 11)
if out=$(uv run radon cc src -s -n C 2>/dev/null); then
  if [[ -n "$out" ]]; then
    echo "radon: blocks at complexity C+ (must be <= B):" >&2
    echo "$out" >&2
    exit 1
  fi
fi

uv run pylint src tests
uv run bandit -r src -q
