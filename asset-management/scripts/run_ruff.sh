#!/usr/bin/env bash
# Run ruff lint + format checks over the package and tests.
# Usage: scripts/run_ruff.sh            (lint only)
#        scripts/run_ruff.sh --fix      (auto-fix what ruff can fix)
set -euo pipefail
cd "$(dirname "$0")/.."

args=("$@")
uv run ruff check src tests ${args[@]+"${args[@]}"}
uv run ruff format --check src tests
