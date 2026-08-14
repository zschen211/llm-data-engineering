#!/usr/bin/env bash
# Start the asset-manager Web UI locally (uvicorn + default_app factory).
# Usage: scripts/serve.sh [--host 127.0.0.1] [--port 8000] [--data-dir data]
#                          [--storage rustfs|local]
#
# Storage backend: `--storage rustfs` (default) targets the RustFS container
# from docker-compose (localhost:9000, rustfsadmin/rustfsadmin) unless the
# RUSTFS_* env vars say otherwise; `--storage local` forces the local
# content-addressed directory (data/blobs/). A `.env` file in the project
# root (see .env.example) is loaded first and wins over the built-in
# defaults, but is overridden by already-exported environment variables.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a && . ./.env && set +a
fi

host="127.0.0.1"
port=8000
storage="rustfs"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --data-dir) export LLAVA_DATA_DIR="$2"; shift 2 ;;
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
  export RUSTFS_BUCKET="${RUSTFS_BUCKET:-llava-assets}"
  export LLAVA_STORAGE_BACKEND="${LLAVA_STORAGE_BACKEND:-rustfs}"
  echo "storage: rustfs (${RUSTFS_ENDPOINT}, bucket ${RUSTFS_BUCKET})" >&2
else
  unset RUSTFS_ENDPOINT RUSTFS_ACCESS_KEY RUSTFS_SECRET_KEY RUSTFS_BUCKET
  export LLAVA_STORAGE_BACKEND=local
  echo "storage: local (data/blobs/)" >&2
fi

echo "serving asset manager on http://${host}:${port} (data: ${LLAVA_DATA_DIR:-data})" >&2
exec uv run uvicorn llava_instruct.assets.routes:default_app --factory --host "$host" --port "$port"
