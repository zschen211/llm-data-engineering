#!/usr/bin/env bash
# Start the middleware stack (RustFS + Prometheus + Grafana + node-exporter).
# Usage: ./scripts/up.sh [service...]      (service names pass through to compose)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a && . ./.env && set +a
fi

docker compose up -d "$@"
