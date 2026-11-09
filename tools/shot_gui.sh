#!/bin/sh
# Drive the GUI in a headless browser and screenshot the owner's workflow.
#
#     sh tools/shot_gui.sh "http://127.0.0.1:8741/?token=..."
#
# **The driver lives in `web/` deliberately.** Node resolves an imported package relative to the FILE
# that imports it, and Playwright is installed in `web/node_modules` - so a driver in `tools/` cannot
# find it however the caller sets the working directory. This wrapper is the entry point; the script
# beside the dependency is the one that runs.
set -eu
cd "$(dirname "$0")/.."
if [ $# -ne 1 ]; then
    echo "usage: sh tools/shot_gui.sh <url-with-token>" >&2
    exit 2
fi
if [ ! -d web/node_modules/playwright ]; then
    echo "playwright is not installed: cd web && npm i -D playwright && npx playwright install chromium" >&2
    exit 1
fi
cd web
exec node shot_gui.mjs "$1"
