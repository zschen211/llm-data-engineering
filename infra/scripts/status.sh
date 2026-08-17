#!/usr/bin/env bash
# Show the state of the middleware stack and the Ray cluster (if up).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== compose =="
docker compose ps

echo
echo "== ray =="
if command -v ray >/dev/null 2>&1 && ray status >/dev/null 2>&1; then
  ray status
else
  echo "no ray cluster reachable (start with ./scripts/ray-start.sh)"
fi
