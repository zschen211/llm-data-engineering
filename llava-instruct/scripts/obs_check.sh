#!/usr/bin/env bash
# Observability smoke check: compose config, Prometheus targets, /metrics.
# Usage: ./scripts/obs_check.sh   (start the stack + serve.sh first)
set -euo pipefail

docker compose config -q

# Wait for Prometheus, then check every scrape target is up.
targets=$(curl -fsS http://localhost:9090/api/v1/targets)
for job in llava-instruct ray-metrics node-exporter; do
  if ! echo "$targets" | grep -q "\"job\":\"$job\"\|\"job\": \"$job\""; then
    echo "error: no scrape target for job $job" >&2
    exit 1
  fi
done
echo "prometheus targets: ok"

# llava-instruct self metrics must carry process and Ray gauges.
metrics=$(curl -fsS http://localhost:8000/metrics)
for metric in llava_process_rss_bytes llava_ray_total_cpus llava_ray_metrics_up; do
  echo "$metrics" | grep -q "^$metric " || { echo "error: missing $metric" >&2; exit 1; }
done
echo "llava-instruct /metrics: ok"
