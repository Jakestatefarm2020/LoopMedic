#!/usr/bin/env bash
# LoopMedic for Linux - by Jacob Stiltner, (c) 2026
# Guided setup + install. Run as your normal user:  ./install.sh   (it asks for sudo when needed)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [ "$(id -u)" -eq 0 ]; then
    echo "Run ./install.sh as your normal user (not with sudo) - it asks for sudo itself."
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is missing. On CachyOS/Arch: sudo pacman -S python"
    exit 1
fi
exec python3 loopmedic/setup_loopmedic.py "$@"
