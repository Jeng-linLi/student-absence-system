#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/leave-system"
export LEAVE_DEBUG=0
exec "C:/Users/Johnny/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" app.py
