#!/usr/bin/env bash
# Upgrade an existing B1 installation in place; never manages data services.
set -euo pipefail
B1_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$B1_ROOT/scripts/b1_parallel_runtime.py" "$@"
