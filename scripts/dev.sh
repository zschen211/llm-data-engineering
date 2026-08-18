#!/usr/bin/env bash
# One-command local dev stack: infra middleware (compose) + Ray cluster +
# both backends + the frontend SPA. Idempotent: re-running `up` skips what is
# already running and only tops up the rest.
#
#   ./scripts/dev.sh up             start everything (waits for health)
#   ./scripts/dev.sh down           stop everything (apps -> ray -> compose)
#   ./scripts/dev.sh status         per-service state + HTTP probes
#   ./scripts/dev.sh logs           tail all service logs
#   ./scripts/dev.sh logs asset     tail one service's log
#   ./scripts/dev.sh restart [svc]  restart everything (or one service)
#
# Requirements: docker daemon, uv, npm. Ray is reused from the service venv
# (asset/.venv) when not on PATH. Service logs: .run/logs/<name>.log
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$RUN_DIR/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

RAY_ADDRESS="${RAY_ADDRESS:-127.0.0.1:26379}"
# Exported up front so EVERY subcommand (up / restart <svc> / ...) hands the
# shared-cluster address to the backends — an unset RAY_ADDRESS would make
# the services fail or (previously) spawn a duplicate embedded cluster.
export RAY_ADDRESS
RAY_BIN="$(command -v ray || echo "$ROOT/asset/.venv/bin/ray")"
APP_SERVICES=(asset data_factory frontend)
APP_PORTS=(8000 8001 5173)
APP_URLS=(
  "http://localhost:8000/api/info"
  "http://localhost:8001/api/factory-info"
  "http://localhost:5173/"
)

_pidfile() { echo "$RUN_DIR/$1.pid"; }

_running() {
  local pid
  pid="$(cat "$(_pidfile "$1")" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_port_busy() {
  ss -ltn 2>/dev/null | grep -q ":$1 "
}

_start_bg() {
  # name workdir command...  (launched as its own session for group-kill)
  local name="$1" wd="$2"
  shift 2
  if _running "$name"; then
    echo "[skip ] $name already running (pid $(cat "$(_pidfile "$name")"))"
    return
  fi
  setsid bash -c 'cd "$1" && shift && exec "$@"' bash "$wd" "$@" \
    >"$LOG_DIR/$name.log" 2>&1 < /dev/null &
  echo $! > "$(_pidfile "$name")"
  echo "[start] $name (pid $!)"
}

_stop() {
  local name="$1" pid
  pid="$(cat "$(_pidfile "$name")" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    # negative pid = the whole session (setsid) process group
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    echo "[stop ] $name"
  else
    echo "[skip ] $name not running"
  fi
  rm -f "$(_pidfile "$name")"
}

_wait_http() {
  local url="$1" what="$2" log="$3" t=0
  echo -n "[wait ] $what "
  until curl -fsS -m 2 "$url" >/dev/null 2>&1; do
    t=$((t + 1))
    if [[ $t -gt 90 ]]; then
      echo " TIMEOUT"
      echo "[error] $what did not come up (log: $LOG_DIR/$log.log)" >&2
      return 1
    fi
    echo -n "."
    sleep 1
  done
  echo " up"
}

_launch() {
  local name="$1"
  case "$name" in
    asset)        _start_bg asset "$ROOT/asset" ./scripts/serve.sh --port 8000 ;;
    data_factory) _start_bg data_factory "$ROOT/data_factory" ./scripts/serve.sh --port 8001 ;;
    frontend)     _start_bg frontend "$ROOT/frontend" npm run dev ;;
    *) echo "[error] unknown service: $name (${APP_SERVICES[*]})" >&2; return 1 ;;
  esac
}

_preflight() {
  for i in "${!APP_SERVICES[@]}"; do
    local name="${APP_SERVICES[$i]}" port="${APP_PORTS[$i]}"
    if ! _running "$name" && _port_busy "$port"; then
      echo "[error] port $port is busy but $name is not managed here;" >&2
      echo "        free it or start via this script" >&2
      exit 1
    fi
  done
}

cmd_up() {
  echo "== dev stack up =="
  _preflight
  echo "[infra] compose (rustfs / prometheus / grafana / node-exporter)"
  (cd infra && ./scripts/up.sh)
  echo "[infra] ray cluster"
  export PATH="$(dirname "$RAY_BIN"):$PATH"
  (cd infra && ./scripts/ray-start.sh)
  _wait_http "http://localhost:9000/health" "rustfs" "infra"
  echo "[apps ] backends + frontend"
  _launch asset
  _launch data_factory
  _launch frontend
  for i in "${!APP_SERVICES[@]}"; do
    _wait_http "${APP_URLS[$i]}" "${APP_SERVICES[$i]} (${APP_PORTS[$i]})" "${APP_SERVICES[$i]}"
  done
  echo
  echo "== dev stack up =="
  echo "  console http://localhost:5173   (frontend)"
  echo "  asset   http://localhost:8000   data_factory http://localhost:8001"
  echo "  grafana http://localhost:3000   ray dashboard http://localhost:8265"
  echo "next: ./scripts/dev.sh status | logs | down"
}

cmd_down() {
  echo "== dev stack down =="
  for s in "${APP_SERVICES[@]}"; do _stop "$s"; done
  export PATH="$(dirname "$RAY_BIN"):$PATH"
  (cd infra && ./scripts/ray-stop.sh) || true
  (cd infra && ./scripts/down.sh)
  echo "== dev stack down =="
}

cmd_status() {
  echo "== infra (docker compose) =="
  (cd infra && docker compose ps 2>/dev/null || echo "compose stack not up")
  echo
  echo "== ray =="
  export PATH="$(dirname "$RAY_BIN"):$PATH"
  if "$RAY_BIN" status >/dev/null 2>&1; then
    "$RAY_BIN" status | head -8
  else
    echo "ray not reachable"
  fi
  echo
  echo "== app services =="
  for name in "${APP_SERVICES[@]}"; do
    if _running "$name"; then
      echo "[up  ] $name (pid $(cat "$(_pidfile "$name")"))"
    else
      echo "[down] $name"
    fi
  done
  echo
  echo "== health =="
  for i in "${!APP_SERVICES[@]}"; do
    local url="${APP_URLS[$i]}" name="${APP_SERVICES[$i]}"
    if curl -fsS -m 2 "$url" >/dev/null 2>&1; then
      echo "[ok  ] $name $url"
    else
      echo "[fail] $name $url"
    fi
  done
}

cmd_logs() {
  local svc="${1:-}"
  if [[ -n "$svc" ]]; then
    [[ -f "$LOG_DIR/$svc.log" ]] || {
      echo "no log for '$svc' (services: ${APP_SERVICES[*]})" >&2
      exit 1
    }
    tail -f "$LOG_DIR/$svc.log"
  else
    tail -f "$LOG_DIR"/*.log
  fi
}

cmd_restart() {
  local svc="${1:-}"
  if [[ -n "$svc" ]]; then
    _stop "$svc"
    _launch "$svc"
  else
    cmd_down
    cmd_up
  fi
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  logs) cmd_logs "${2:-}" ;;
  restart) cmd_restart "${2:-}" ;;
  *)
    echo "usage: $0 up|down|status|logs [svc]|restart [svc]" >&2
    echo "  up       start infra + ray + backends + frontend (idempotent)" >&2
    echo "  down     stop everything" >&2
    echo "  status   per-service state + health probes" >&2
    echo "  logs     tail service logs (.run/logs/)" >&2
    echo "  restart  restart everything or one service" >&2
    exit 1
    ;;
esac
