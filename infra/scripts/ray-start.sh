#!/usr/bin/env bash
# Start a standalone Ray head node with the contract ports (configurable):
#   GCS 127.0.0.1:$RAY_GCS_PORT (default 26379 — 6379 may be taken by redis)
#   Dashboard :8265 · metrics agent :$ASSET_RAY_METRICS_PORT (default 8080)
# All services attach via $RAY_ADDRESS (see infra/docs/contract.md).
# GPU workers (mm-rag / video-generation, phase 2): on each GPU host run
#   ray start --address=127.0.0.1:$RAY_GCS_PORT --num-gpus=N
set -euo pipefail

gcs_port="${RAY_GCS_PORT:-26379}"
metrics_port="${ASSET_RAY_METRICS_PORT:-8080}"

if ray status >/dev/null 2>&1; then
  echo "a ray cluster is already reachable; nothing to do" >&2
  ray status
  exit 0
fi

ray start --head \
  --port "$gcs_port" \
  --dashboard-port 8265 \
  --metrics-export-port "$metrics_port" \
  --dashboard-host 0.0.0.0

echo
echo "cluster up. export for services:"
echo "  export RAY_ADDRESS=127.0.0.1:$gcs_port"
