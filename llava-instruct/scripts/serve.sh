#!/usr/bin/env bash
# Start the asset-manager Web UI locally (uvicorn + default_app factory).
# Usage: scripts/serve.sh [--host 127.0.0.1] [--port 8000] [--data-dir data]
set -euo pipefail
cd "$(dirname "$0")/.."

host="127.0.0.1"
port=8000
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --data-dir) export LLAVA_DATA_DIR="$2"; shift 2 ;;
    *) echo "error: unknown option: $1" >&2; exit 1 ;;
  esac
done

echo "serving asset manager on http://${host}:${port} (data: ${LLAVA_DATA_DIR:-data})" >&2
exec uv run uvicorn llava_instruct.assets.routes:default_app --factory --host "$host" --port "$port"
