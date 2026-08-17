#!/usr/bin/env bash
# Clean transient data: service tmp dirs + Ray session logs. Never touches
# sqlite dbs or blob storage. Requires confirmation.
# Usage: ./scripts/clean.sh [--data-dir ../data]
set -euo pipefail
cd "$(dirname "$0")/.."

data_dir="${1:-../data}"
data_dir="$(cd "$data_dir" && pwd)"

targets=("$data_dir"/tmp)
for t in "${targets[@]}"; do
  [[ -d "$t" ]] || continue
  du -sh "$t"
done
read -r -p "remove the tmp dirs above? [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "aborted"; exit 1; }

for t in "${targets[@]}"; do
  rm -rf "$t"
  echo "cleaned $t"
done
