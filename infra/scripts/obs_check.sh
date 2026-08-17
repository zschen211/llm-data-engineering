#!/usr/bin/env bash
# Observability smoke check: compose config, Prometheus targets, /metrics.
# Usage: ./scripts/obs_check.sh   (start the stack + both serve.sh first)
set -euo pipefail

docker compose config -q

# Wait for Prometheus, then check every scrape target is up.
targets=$(curl -fsS http://localhost:9090/api/v1/targets)
for job in asset-management data-factory ray-metrics node-exporter; do
  if ! echo "$targets" | grep -q "\"job\":\"$job\"\|\"job\": \"$job\""; then
    echo "error: no scrape target for job $job" >&2
    exit 1
  fi
done
echo "prometheus targets: ok"

# asset-management self metrics must carry process and Ray gauges.
metrics=$(curl -fsS http://localhost:8000/metrics)
for metric in asset_process_rss_bytes asset_ray_total_cpus asset_ray_metrics_up; do
  echo "$metrics" | grep -q "^$metric " || { echo "error: missing $metric" >&2; exit 1; }
done
echo "asset-management /metrics: ok"

# data-factory self metrics (HTTP counters).
df_metrics=$(curl -fsS http://localhost:8001/metrics)
echo "$df_metrics" | grep -q "^asset_http_requests_total " || {
  echo "error: missing asset_http_requests_total on data-factory" >&2; exit 1; }
echo "data-factory /metrics: ok"
