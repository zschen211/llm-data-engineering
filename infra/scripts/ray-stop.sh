#!/usr/bin/env bash
# Stop the Ray cluster started by ray-start.sh.
set -euo pipefail

ray stop --force
echo "ray stopped"
