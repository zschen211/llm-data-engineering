#!/usr/bin/env bash
# Stop the middleware stack. Volumes (RustFS data, Prometheus/Grafana state)
# are kept; use `down.sh --volumes` to wipe them.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose down "$@"
