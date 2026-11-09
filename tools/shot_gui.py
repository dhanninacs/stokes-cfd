"""Drive the GUI in a headless browser and screenshot the owner's workflow.

    cd web && node ../tools/shot_gui.mjs <url-with-token>

**Why a browser and not the socket.** Everything below the DOM has been checked over `/ws` and
over HTTP
(rounds 64-68). What has never run is the page itself: React, three.js, the panels, the
buttons. This is
the only check of the layer above the protocol, and it is the layer the owner will actually use.

**Software GL.** There is no GPU on this host, so Chromium is launched with
`--use-gl=swiftshader --enable-unsafe-swiftshader`. Without them WebGL fails and the viewport
is blank,
which would look like a product bug rather than a missing driver.

**It reports the page's own errors.** `console.error` and every non-2xx response are collected and
printed, because a GUI that renders but throws is not a GUI that works. Screenshots go to
`$HOME/stokes-shots/`, and are copied to `.coordination/shots/` for Claude to read from the share.
"""

from __future__ import annotations

import sys
from pathlib import Path

# : Where the PNGs go. Under `$HOME`, never `/tmp` — this host's `/tmp` is a small tmpfs with
# a user quota
#: and filling it has already cost this project two rounds of a dead shell.
SHOTS = Path.home() / "stokes-shots"
#: A copy Claude can read from the Windows share.
MIRROR = Path(__file__).parent.parent / ".coordination" / "shots"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: shot_gui.py <url-with-token>", file=sys.stderr)
        return 2
    SHOTS.mkdir(parents=True, exist_ok=True)
    MIRROR.mkdir(parents=True, exist_ok=True)
    print(f"url: {sys.argv[1]}")
    print(f"shots: {SHOTS}")
    print("drive it with:  cd web && node ../tools/shot_gui.mjs <url>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
