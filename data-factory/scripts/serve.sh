#!/usr/bin/env bash
# Start the data-factory management API locally (uvicorn + default_app factory).
# Usage: scripts/serve.sh [--host 127.0.0.1] [--port 8001] [--data-dir data]
#                          [--storage rustfs|local]
#
# Storage backend: `--storage rustfs` (default) targets the RustFS container
# started by infra/scripts/up.sh (localhost:9000, rustfsadmin/rustfsadmin)
# unless the RUSTFS_* env vars say otherwise; `--storage local` forces the
# local artifacts directory (data/artifacts/). Ray: attach the shared cluster
# via $RAY_ADDRESS (infra/scripts/ray-start.sh); when unset an embedded local
# cluster is started as a dev fallback (loud warning).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a && . ./.env && set +a
fi

host="127.0.0.1"
port=8001
storage="rustfs"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --data-dir) export DFAC_DATA_DIR="$2"; shift 2 ;;
    --storage)
      case "$2" in
        rustfs|local) storage="$2" ;;
        *) echo "error: --storage must be rustfs|local" >&2; exit 1 ;;
      esac
      shift 2 ;;
    *) echo "error: unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ "$storage" == "rustfs" ]]; then
  export RUSTFS_ENDPOINT="${RUSTFS_ENDPOINT:-http://localhost:9000}"
  export RUSTFS_ACCESS_KEY="${RUSTFS_ACCESS_KEY:-rustfsadmin}"
  export RUSTFS_SECRET_KEY="${RUSTFS_SECRET_KEY:-rustfsadmin}"
  export RUSTFS_BUCKET="${RUSTFS_BUCKET:-dfac-datasets}"
  export DFAC_STORAGE_BACKEND="${DFAC_STORAGE_BACKEND:-rustfs}"
  echo "storage: rustfs (${RUSTFS_ENDPOINT}, bucket ${RUSTFS_BUCKET})" >&2
else
  unset RUSTFS_ENDPOINT RUSTFS_ACCESS_KEY RUSTFS_SECRET_KEY RUSTFS_BUCKET
  export DFAC_STORAGE_BACKEND=local
  echo "storage: local (data/artifacts/)" >&2
fi

echo "serving data factory on http://${host}:${port} (data: ${DFAC_DATA_DIR:-data})" >&2
exec uv run uvicorn data_factory.routes:default_app --factory --host "$host" --port "$port"
