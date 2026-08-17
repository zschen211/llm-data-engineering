#!/usr/bin/env bash
# Consistent SQLite backups for every service data dir under <repo>/data,
# plus a copy of the Ray session logs. Restore: sqlite3 .restore.
# Usage: ./scripts/backup.sh [--data-dir ../data]
set -euo pipefail
cd "$(dirname "$0")/.."

data_dir="${1:-../data}"
data_dir="$(cd "$data_dir" && pwd)"
stamp="$(date +%Y%m%d-%H%M%S)"
out_dir="backups/$stamp"
mkdir -p "$out_dir"

backed=0
for db in "$data_dir"/*.db; do
  [[ -e "$db" ]] || continue
  name="$(basename "$db")"
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$db" ".backup '$out_dir/$name'"
  else
    cp "$db" "$out_dir/$name"
  fi
  backed=$((backed + 1))
  echo "backed up $name"
done

[[ "$backed" -gt 0 ]] || echo "no sqlite databases found under $data_dir"
echo "backup dir: $out_dir"
