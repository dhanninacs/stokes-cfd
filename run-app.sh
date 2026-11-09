#!/usr/bin/env bash
# Launch the Stokes desktop app on this Linux box.
#
# This machine has no GPU, so Chromium blocklists WebGL2 and the viewport shows
# "3D view unavailable". The QtWebEngine flags below force ANGLE's SwiftShader
# (CPU) backend so the 3D view renders here. On a machine with a real GPU they
# are unnecessary and you can drop the QTWEBENGINE_CHROMIUM_FLAGS line.
#
# Usage:  ./run-app.sh [--project PATH] [--theme dark] [...]
set -euo pipefail
cd "$(dirname "$0")"

export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:---ignore-gpu-blocklist --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader}"

exec .venv/bin/python -m stokes.app "$@"
