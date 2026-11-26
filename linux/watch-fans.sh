#!/usr/bin/env bash
# LoopMedic for Linux - live fan speeds, to find which header your pump is on. Ctrl+C to stop.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
exec python3 loopmedic/setup_loopmedic.py --watch
