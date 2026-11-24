#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/leave-system"
export LEAVE_DEBUG=0
export LEAVE_DEMO_QUICK=1
PY="C:/Users/Johnny/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
if [ ! -x "$PY" ]; then PY="python3"; fi
exec "$PY" app.py
