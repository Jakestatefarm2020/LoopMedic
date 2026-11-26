#!/usr/bin/env bash
# LoopMedic for Linux - by Jacob Stiltner, (c) 2026
# Removes the service, tray icon and all LoopMedic files. Run as your normal user.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
exec python3 loopmedic/setup_loopmedic.py --uninstall
