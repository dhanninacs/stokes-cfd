"""Drive the viewport's own interaction benchmark on the 4,500-part pin grid.

``tools/bench_viewport.py`` measures the *server* half of the many-part benchmark (import, mesh
transfer). This tool is its *client* half: it builds the same 4,500-solid project, serves it with
``python -m stokes.server``, drives a headless Chrome over the DevTools protocol and reads the
figures the page's own probe publishes on ``window.__stokesScene`` (any build opened with
``?debug=1``; see ``web/src/viewport/Viewport.tsx``): the orbit's frame times, the cost of a box or
a lasso selection at ``pointerup``, and hover and click latency. Every figure the tool prints comes
from that probe or from timing the page's own pointer handlers; nothing is estimated.

Why a browser at all: frame cost, the id-buffer pass and the store update do not exist outside one.
Why Chrome and not the Qt host: the desktop window cannot be driven from a script, and the DevTools
protocol dispatches the *trusted* pointer events that OrbitControls needs for its pointer capture —
a script-built ``PointerEvent`` throws inside ``setPointerCapture`` and rotates nothing.

**Renderer honesty.** The WebGL renderer string is read back from the context and printed with
every run, and a software renderer (SwiftShader, llvmpipe) prints a loud warning: headless Chrome
falls back to software unless the machine's GPU is available, and a software frame time says
nothing about the budget. Software numbers are a regression baseline only. The budget verdict
belongs to a run on the owner's GPU, and this tool prints the exact command for it at the end.

**The model.** ``tests.geometry_fixtures.pin_grid(nx, ny)`` is a box and a cylinder per pin, nine
faces and two solids each, so ``50 x 45`` pins is the 4,500-solid, 20,250-face, 585k-triangle
benchmark ADR-0019, ``tests/perf/test_geometry_transfer.py`` and ``docs/HANDOVER.md`` §5b use.
``75 x 60`` would be 9,000 solids. ``build`` reports the counts it actually got.

Usage:
    python tools/bench_scene.py build --project <folder.stokes> [--pins 50x45]
    python tools/bench_scene.py measure --project <folder.stokes> [--renderer swiftshader]
    python tools/bench_scene.py measure --project <folder.stokes> --json out.json

``build`` tessellates the solids in this process and writes the project's mesh cache the way an
import leaves it (``step.tessellate_bodies`` over the same batches the worker uses), so it needs no
STEP file and takes a minute or two rather than the import's several. It needs OCP, which the
desktop build has.

**Freshness.** The page ``measure`` drives is not this tree's source: it is whatever bundle sits in
``web/dist``, which is gitignored and is therefore invisible to ``git status``. A run once measured
a ``dist`` holding a build of a rejected attempt and published its numbers for code that was never
in the tree. ``measure`` therefore refuses to start unless ``web/dist`` is at least as new as
``web/src`` (``dist_freshness``); ``--allow-stale-dist`` overrides the refusal and the report still
carries the fact. The check runs once at t=0 and never aborts a run that has begun measuring.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_viewport import Server, free_port  # noqa: E402  (a sibling tool, reused on purpose)

PARTS = 4500
"""Solids of the benchmark pin grid: 2 per pin of ``pin_grid(50, 45)``, 9 faces each."""
BROWSER_ENV = ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
"""Where a Windows Chrome install is looked for when ``--browser`` is not given."""
MOVE_FRAMES = 20
"""Orbit moves, one per two animation frames, so each move's frame is timed on its own."""
STILL_FRAMES = 20
"""Forced frames of `measureRender`: the still cost of the loaded view, nothing moving."""
SETTLE_MS = 80
"""How long a hover waits for the scene's own 30 ms hover interval before its pick is read."""
CLICK_GAP_MS = 600
"""Between clicks: longer than the scene's 400 ms double-click window, so each is one click."""
TWO_FRAMES = "new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(1))))"

DIST_DIR = Path("web/dist")
"""The built bundle ``stokes.server`` serves; gitignored, so git cannot tell a reader it is old."""
SRC_DIR = Path("web/src")
"""The front end's sources: the only thing whose mtime says what the bundle was built from."""
SRC_FILES = (
    Path("web/index.html"),
    Path("web/package.json"),
    Path("web/vite.config.ts"),
    Path("web/tsconfig.json"),
)
"""Front-end inputs that live outside ``web/src`` but still decide the bundle's bytes.

All four are inputs to the Vite build: ``index.html`` is its entry, ``vite.config.ts`` and
``tsconfig.json`` select plugins, aliases and compiler options, and ``package.json`` pins the
toolchain and the dependency versions that get bundled in. A source edit alone can therefore miss a
rebuild, and the point of this check is to catch exactly that. They are considered only when
``web/src`` exists, and a missing one is skipped rather than treated as an error: a packaged
checkout has the config files and no sources, and dating them there would turn "cannot tell" into a
false ``stale``.
"""
REMEDY = "cd web && npm run build"
"""The one command that makes a stale or missing ``web/dist`` fresh again."""

FreshnessState = Literal["fresh", "stale", "missing", "unknown"]


@dataclass(frozen=True)
class DistFreshness:
    """Whether the built front end still reflects the sources it was built from.

    ``dist_mtime``/``src_mtime`` are the newest mtimes found, as ``st_mtime_ns`` (integer
    nanoseconds: two files written in the same tick compare exactly, where float seconds can round
    them equal). ``None`` means that side was absent or unreadable, which is what separates
    ``missing`` from ``unknown``.
    """

    state: FreshnessState
    dist_mtime: int | None
    src_mtime: int | None
    detail: str

    @property
    def blocked(self) -> bool:
        """Whether the state is proof enough to refuse a measurement before it starts."""
        return self.state in ("stale", "missing")


def newest_mtime(paths: list[Path]) -> int | None:
    """The newest ``st_mtime_ns`` among the files these paths name, or ``None`` if none is read.

    A directory is walked; a file is taken as it is; a path that does not exist, or that cannot be
    read, contributes nothing. ``None`` therefore means "nothing here could be dated", never "old".
    """
    newest: int | None = None
    for path in paths:
        try:
            found = [path] if path.is_file() else list(path.rglob("*")) if path.is_dir() else []
        except OSError:
            continue
        for entry in found:
            try:
                if not entry.is_file():
                    continue
                stamp = entry.stat().st_mtime_ns
            except OSError:
                # A path that vanished or is unreadable mid-walk is a missing input, not a failure:
                # this check exists to report doubt, and refusing on doubt is the bug it prevents.
                continue
            if newest is None or stamp > newest:
                newest = stamp
    return newest


def describe(mtime: int | None) -> str:
    """A timestamp a human can read, or the reason there is none; also the ``--json`` value."""
    if mtime is None:
        return "absent"
    return datetime.fromtimestamp(mtime / 1e9).isoformat(sep=" ", timespec="seconds")


def dist_freshness(root: Path) -> DistFreshness:
    """Compare the built front end under ``root`` against the sources it was built from.

    Pure and total: no browser, no OCP, no ``web/dist`` required, and every state is a value rather
    than an exception, so this is testable from fabricated trees and callable before anything is
    started. The inputs are ``web/dist`` on one side, and on the other ``web/src`` plus the four
    files named in ``SRC_FILES`` that decide the bundle but live outside ``src`` (see there for
    why each is included, and why they count only when ``web/src`` is present).

    The states, and the reason each is what it is:

    * ``missing`` - ``web/dist/index.html`` is absent, so there is nothing to serve and nothing to
      measure; a server started now would fail or serve a stale copy from elsewhere.
    * ``unknown`` - ``web/dist/index.html`` is there but no source could be dated (``web/src``
      absent or unreadable, as in a packaged checkout). Absence of evidence is not evidence of
      staleness, so the caller warns and continues rather than refusing.
    * ``fresh`` - the newest dist mtime is **greater than or equal to** the newest source mtime. The
      comparison is inclusive on purpose: a build writes its output immediately after reading its
      input, and a filesystem with coarse timestamps can record both in the same tick, so an
      equal pair most likely means "built from this source" and calling it stale would refuse
      correct runs.
    * ``stale`` - the newest dist mtime is provably less than the newest source mtime.
      Any amount older is stale; there is deliberately no tolerance window, because a window is
      indistinguishable from the defect this check exists to catch.

    ``detail`` names the two roots and both timestamps, so a refusal and a ``--json`` report say
    what was compared without the reader having to reconstruct it.
    """
    dist_root = (root / DIST_DIR).resolve()
    src_root = (root / SRC_DIR).resolve()
    index = dist_root / "index.html"
    dist_mtime = newest_mtime([dist_root])
    # The files outside `web/src` only count as sources when `web/src` itself is there. A packaged
    # checkout ships `package.json` and no sources; dating the former alone would turn "I cannot
    # tell" into a confident `stale`, and refusing on that is the false stop this check must not
    # make. No `web/src` means no source root to compare against, full stop.
    if src_root.is_dir():
        src_mtime = newest_mtime([src_root, *(root / name for name in SRC_FILES)])
    else:
        src_mtime = None
    dist_text = describe(dist_mtime)
    src_text = describe(src_mtime)
    where = f"newest web/dist {dist_text} vs newest web/src {src_text} (sources: {src_root})"

    if not index.is_file():
        return DistFreshness(
            "missing",
            dist_mtime,
            src_mtime,
            f"{index} is absent: {where}. Build it with `{REMEDY}`",
        )
    if src_mtime is None:
        return DistFreshness(
            "unknown",
            dist_mtime,
            src_mtime,
            f"no source could be dated under {src_root}: {where}. Cannot prove the build is old, "
            "so this run continues; a packaged checkout is the expected cause",
        )
    if dist_mtime is None:
        # `index.html` exists but resisted `stat`: unreadable rather than old, so not proof.
        return DistFreshness(
            "unknown",
            dist_mtime,
            src_mtime,
            f"{index} is present but its mtime could not be read: {where}. Cannot prove the build "
            "is old, so this run continues",
        )
    if dist_mtime < src_mtime:
        return DistFreshness(
            "stale",
            dist_mtime,
            src_mtime,
            f"{index} predates the front end's sources: {where}. These figures would measure code "
            f"that is not in the tree. Rebuild with `{REMEDY}`",
        )
    return DistFreshness(
        "fresh", dist_mtime, src_mtime, f"built from the current front end: {where}"
    )


def _needs_no_sandbox() -> bool:
    """Whether this machine's Chromium needs `--no-sandbox` to start at all.

    Linux only, and tested rather than assumed: **unprivileged user namespaces are disabled on some
    distributions**, so the browser refuses to launch and says so on stderr. Running as root
    needs it too. Windows and macOS have no such flag and are never given one.
    """
    if sys.platform != "linux":
        return False
    if os.geteuid() == 0:
        return True
    # **The switch that actually governs is AppArmor's, not the user-namespace one.** My first
    # version read `/proc/sys/kernel/unprivileged_userns_clone`, which says `1` on this box - the
    # kernel DOES allow unprivileged user namespaces - while Chromium still refused to start,
    # because `apparmor_restrict_unprivileged_userns` is `1` and AppArmor denies the namespace to
    # an unconfined binary. **Two switches, and the one I checked was not the one in the way**, so
    # the detection is written against the restriction Chromium's own message names.
    for switch, blocked_when in (
        ("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "1"),
        ("/proc/sys/kernel/unprivileged_userns_clone", "0"),
    ):
        try:
            if Path(switch).read_text().strip() == blocked_when:
                return True
        except OSError:
            continue
    return False


def find_browser() -> Path:
    """A Chromium to drive: Chrome or Edge in the usual places, else a Playwright one.

    Edge is the same engine and speaks the same DevTools protocol; a PC without Chrome has it.

    **Linux matters now, not just Windows.** The interaction harness is the UI gate and this box
    is where the front end is built, so a helper that only knew `C:\\Program Files\\...` meant
    the gate could not run where the code is written - it had to be driven from the Windows PC or
    not at all. The Playwright cache is checked because Playwright is already a dependency of the
    drivers here, and a browser it installed is a browser we may use.
    """
    candidates: list[Path] = []
    # **Chromium first on POSIX**: it is what is actually installed, and `google-chrome` on a
    # developer machine is often a wrapper script rather than the binary.
    candidates += [
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/snap/bin/chromium"),
        Path("/opt/google/chrome/chrome"),
    ]
    candidates += [
        Path("/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    ]
    for name in BROWSER_ENV:
        root = os.environ.get(name)
        if root:
            candidates += [
                Path(root) / "Google/Chrome/Application/chrome.exe",
                Path(root) / "chromium",
                Path(root) / "chrome",
            ]
    candidates += [
        Path("/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("/usr/bin/microsoft-edge"),
    ]
    for name in BROWSER_ENV:
        root = os.environ.get(name)
        if root:
            candidates.append(Path(root) / "Microsoft/Edge/Application/msedge.exe")
    candidates += _playwright_browsers()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "no Chrome, Chromium or Edge found: pass --browser <path to the browser binary>"
    )


def _playwright_browsers() -> list[Path]:
    """Chromiums under the Playwright cache, newest build first.

    The folder name carries a build number (`chromium-1248`), so sorting **by that number** rather
    than by name is what picks the newest rather than the alphabetically last.
    """
    root = Path(
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (Path.home() / ".cache" / "ms-playwright")
    )
    if not root.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for folder in root.glob("chromium-*"):
        try:
            number = int(folder.name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            continue
        names = (
            "chrome-linux64/chrome",
            "chrome-linux/chrome",
            "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        )
        for name in names:
            binary = folder / name
            if binary.is_file():
                found.append((number, binary))
    return [binary for _, binary in sorted(found, reverse=True)]


def percentiles(values: list[float]) -> dict[str, float]:
    """Count, median, p95 and worst of a sample; the shape every row of the report uses."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 2),
        "p50": round(statistics.median(ordered), 2),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
        "max": round(ordered[-1], 2),
    }


def as_floats(value: object) -> list[float]:
    """The numbers a probe field should hold; a page that returned something else is a failure."""
    if not isinstance(value, list):
        raise SystemExit(f"the page returned {value!r} where a list of numbers was expected")
    return [float(item) for item in value]


def as_dict(value: object) -> dict[str, Any]:
    """The object a probe field should hold (``Any``: the page's JSON, read by field name here)."""
    if not isinstance(value, dict):
        raise SystemExit(f"the page returned {value!r} where an object was expected")
    return value


def as_float(value: object) -> float:
    """One number a probe field should hold, with a missing one read as zero."""
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SystemExit(f"the page returned {value!r} where a number was expected")
    return float(value)


def as_int(value: object) -> int:
    """One integer a probe field should hold, with a missing one read as zero."""
    return int(as_float(value))


def build_project(project: Path, pins: str) -> dict[str, int]:
    """Write a project of the pin grid with its transport meshes cached, as an import leaves it.

    Returns the counts it actually got, which the caller prints rather than assuming: the size of
    this benchmark is itself a measurement, and the brief for it named a grid that does not give
    4,500 solids.
    """
    nx, ny = (int(part) for part in pins.lower().split("x"))
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    from stokes.api import Session
    from stokes.geometry import step
    from stokes.geometry.cache import write_cache
    from stokes.geometry.mesh import PartMesh
    from stokes.geometry.transport import pack_part
    from stokes.geometry.worker_main import _batches
    from tests import geometry_fixtures as fx

    solids: list[step.StepSolid] = []
    explorer = TopExp_Explorer(fx.pin_grid(nx, ny), TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        body = explorer.Current()
        solids.append(step.StepSolid(f"pin_{len(solids)}", None, body, step.face_count(body)))
        explorer.Next()
    meshes = []
    for batch in _batches(list(enumerate(solids))):
        bodies = [
            step.Body(item.shape, *step.deflections(item.shape, step.FINE), item.name)
            for _, item in batch
        ]
        meshes.extend(step.tessellate_bodies(bodies, quality=step.FINE.name))

    if project.exists():
        shutil.rmtree(project)
    project.parent.mkdir(parents=True, exist_ok=True)
    session = Session()
    session.execute("project.new", name=project.stem, path=str(project))
    opened = session.require_project()
    nodes = [opened.add("/geometry", "part", mesh.name) for mesh in meshes]
    session.execute("project.save")
    with ThreadPoolExecutor(max_workers=8) as pool:
        written = pool.map(
            lambda pair: write_cache(project, pair[0].id, pack_part(PartMesh((pair[1],)))),
            zip(nodes, meshes, strict=True),
        )
        assert len(list(written)) == len(nodes)
    return {
        "pins": nx * ny,
        "parts": len(meshes),
        "faces": sum(mesh.faces for mesh in meshes),
        # `TriMesh.triangle_count` is `indices.shape[0]` with indices shaped (triangles, 3).
        "triangles": sum(mesh.triangle_count for mesh in meshes),
    }


class DevToolsError(RuntimeError):
    """An error reply from the DevTools protocol (not a page-side throw)."""


class DevTools:
    """One page's DevTools protocol session over a WebSocket.

    ``websockets`` is a declared dependency of the server extra (``pyproject.toml``), not a new
    one; a hand-written stdlib client would be the only alternative.
    """

    def __init__(self, url: str) -> None:
        from websockets.sync.client import connect

        self._stack = contextlib.ExitStack()
        self._socket = self._stack.enter_context(
            connect(url, open_timeout=30.0, max_size=None, ping_interval=None)
        )
        self._next = 0

    def call(
        self, method: str, params: dict[str, object] | None = None, *, timeout: float = 120.0
    ) -> dict[str, Any]:
        """One request, waiting for its own reply and ignoring the events in between.

        ``Any``: the reply is the protocol's JSON; every caller reads the fields it needs.
        """
        from websockets.exceptions import ConnectionClosed

        self._next += 1
        wanted = self._next
        self._socket.send(json.dumps({"id": wanted, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise SystemExit(f"{method} did not answer within {timeout:.0f} s")
            try:
                raw = self._socket.recv(timeout=left)
            except TimeoutError:
                raise SystemExit(f"{method} did not answer within {timeout:.0f} s") from None
            except ConnectionClosed as exc:
                raise SystemExit(f"the page's DevTools socket closed: {exc}") from exc
            message: dict[str, Any] = json.loads(raw)
            if message.get("id") != wanted:
                continue
            if "error" in message:
                raise DevToolsError(f"{method} failed: {json.dumps(message['error'])}")
            result: dict[str, Any] = message.get("result") or {}
            return result

    def evaluate(self, expression: str, *, timeout: float = 120.0) -> Any:
        """Evaluate in the page and bring the value back; a page-side throw is a failure here.

        A navigation tears the old execution context down under a running evaluation, so a context
        error is waited out rather than reported: the first call lands while the page loads.
        """
        for attempt in range(40):
            try:
                result = self.call(
                    "Runtime.evaluate",
                    {"expression": expression, "awaitPromise": True, "returnByValue": True},
                    timeout=timeout + 30.0,
                )
                break
            except DevToolsError as exc:
                if "context" not in str(exc).lower() or attempt == 39:
                    raise SystemExit(str(exc)) from exc
                time.sleep(0.25)
        if result.get("exceptionDetails") is not None:
            raise SystemExit(f"the page threw: {json.dumps(result['exceptionDetails'])[:600]}")
        remote: dict[str, Any] = result.get("result") or {}
        return remote.get("value")

    def mouse(
        self,
        kind: str,
        x: float,
        y: float,
        *,
        buttons: int,
        button: str = "left",
        modifiers: int = 0,
    ) -> None:
        """One trusted input event; the browser maps it to the pointer events the scene sees.

        ``button`` names the button the event is about and ``buttons`` is the bitmask of the buttons
        held down. The rotate gesture needs the middle button, so the two are no longer derivable
        from each other and each is passed explicitly.
        """
        self.call(
            "Input.dispatchMouseEvent",
            {
                "type": kind,
                "x": x,
                "y": y,
                "button": button if buttons or kind != "mouseMoved" else "none",
                "buttons": buttons,
                "clickCount": 1 if kind in ("mousePressed", "mouseReleased") else 0,
                "modifiers": modifiers,
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._stack.close()


class Chrome:
    """A headless Chrome with a DevTools endpoint, killed with its profile on the way out."""

    def __init__(self, browser: Path, url: str, *, swiftshader: bool) -> None:
        self.port = free_port()
        self.profile = Path(tempfile.mkdtemp(prefix="stokes-bench-chrome-"))
        args = [
            str(browser),
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--no-proxy-server",
            "--mute-audio",
            "--hide-scrollbars",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--remote-allow-origins=*",
            "--window-size=1280,720",
            f"--user-data-dir={self.profile}",
            f"--remote-debugging-port={self.port}",
        ]
        if swiftshader:
            args += ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        if _needs_no_sandbox():
            # **`--no-sandbox`, because the browser cannot start without it here.** Chromium's own
            # message on this box is "No usable sandbox! ... has disabled unprivileged user
            # namespaces with AppArmor", and it exits before opening a DevTools port. The harness
            # then reported only "Chrome exited before its DevTools endpoint came up", because the
            # browser's stderr went to DEVNULL - **a discarded error made a definite cause look like
            # a mystery.** The flag is added only where it is needed, so a machine with a working
            # sandbox keeps it.
            args.append("--no-sandbox")
        args.append(url)
        # **The browser's own stderr is KEPT.** Discarding it is what turned a one-line cause into a
        # hunt; it is captured to a file in the profile so a failure can be read, not guessed.
        self.stderr = self.profile / "browser.stderr"
        self._stderr_handle = self.stderr.open("wb")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=self._stderr_handle)
        self._attached = False
        self.page_url = self._page_endpoint()

    @classmethod
    def attach(cls, port: int, timeout: float = 60.0) -> Chrome:
        """Attach to an ALREADY-RUNNING browser's DevTools endpoint instead of launching one.

        For driving the desktop app: its window is QtWebEngine, which is Chromium and serves the
        same protocol, but it is a Qt process rather than a chrome binary and no chrome is installed
        on Linux. Started with ``QTWEBENGINE_REMOTE_DEBUGGING=<port>`` it answers ``/json/list``
        exactly as Chrome does, so every method below this line — and every check built on them —
        works unchanged, including the **trusted** input events.

        The instance owns no process and no profile, so :meth:`stop` must not try to kill one; the
        ``proc`` of an attachment is None, which is what the methods test.
        """
        self = cls.__new__(cls)
        self.port = port
        self.profile = None
        self.proc = None
        self._attached = True
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.page_url = self._page_endpoint()
                return self
            except (SystemExit, urllib.error.URLError, OSError) as exc:
                # The window may still be starting; a missing endpoint is not yet a failure.
                last = exc
                time.sleep(0.5)
        raise SystemExit(
            f"no page endpoint on the DevTools port {port} within {timeout:.0f} s: {last}"
        )

    def _targets(self) -> list[dict[str, Any]]:
        """``Any``: the DevTools target list is JSON of which two fields are read."""
        url = f"http://127.0.0.1:{self.port}/json/list"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                targets: list[dict[str, Any]] = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError):
            return []
        return targets

    def _page_endpoint(self) -> str:
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise SystemExit("Chrome exited before its DevTools endpoint came up")
            for target in self._targets():
                endpoint = target.get("webSocketDebuggerUrl")
                if target.get("type") == "page" and isinstance(endpoint, str):
                    return endpoint
            time.sleep(0.25)
        raise SystemExit("Chrome printed no page endpoint within 60 s")

    def stop(self) -> None:
        # An attached browser is somebody else's process: closing the socket is all this owns.
        # Killing
        # it would take the user's window down with the harness, which is a surprising thing for a
        # test
        # to do.
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=15)
        # **Closed before the profile is removed**, or the file handle keeps it alive on Windows.
        handle = getattr(self, "_stderr_handle", None)
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()
        if self.profile is not None:
            shutil.rmtree(self.profile, ignore_errors=True)


INJECT = """
(() => {
  const scene = () => window.__stokesScene;
  const canvas = () => document.querySelector("canvas");
  const box = () => canvas().getBoundingClientRect();
  const bench = { press: [], release: [], hits: { down: 0, up: 0 } };
  let downAt = null;
  let upAt = null;
  // The page's own handlers run between these two: capture on window, then bubble back to window.
  addEventListener("pointerdown", () => { downAt = performance.now(); }, true);
  addEventListener("pointerdown", () => {
    if (downAt !== null) { bench.press.push(performance.now() - downAt); downAt = null; }
  }, false);
  addEventListener("pointerup", () => { upAt = performance.now(); }, true);
  addEventListener("pointerup", () => {
    if (upAt !== null) { bench.release.push(performance.now() - upAt); upAt = null; }
  }, false);
  // Proof that a gesture reached the element the scene listens on: a DOM panel over the canvas
  // swallows the event, and a release that cost nothing is then not a selection time at all.
  canvas().addEventListener("pointerdown", () => { bench.hits.down++; }, true);
  canvas().addEventListener("pointerup", () => { bench.hits.up++; }, true);
  bench.reached = () => ({ down: bench.hits.down, up: bench.hits.up });
  bench.reset = () => {
    bench.press.length = 0;
    bench.release.length = 0;
    bench.hits.down = 0;
    bench.hits.up = 0;
    return true;
  };
  bench.env = () => {
    const gl = canvas().getContext("webgl2") || canvas().getContext("webgl");
    let renderer = "unknown";
    let vendor = "unknown";
    let timer = false;
    if (gl) {
      const info = gl.getExtension("WEBGL_debug_renderer_info");
      if (info) {
        renderer = String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL));
        vendor = String(gl.getParameter(info.UNMASKED_VENDOR_WEBGL));
      }
      timer = !!gl.getExtension("EXT_disjoint_timer_query_webgl2");
    }
    const r = box();
    return { renderer, vendor, timer, ua: navigator.userAgent, dpr: devicePixelRatio,
             innerWidth, innerHeight,
             canvas: { left: r.left, top: r.top, width: r.width, height: r.height } };
  };
  bench.perf = () => scene().perf();
  bench.pickCount = () => {
    const p = scene().pick();
    let faces = 0;
    for (const list of Object.values(p.faces)) faces += list.length;
    return { bodies: p.bodies.length, faces };
  };
  bench.pickMs = () => scene().pickMs;
  bench.idReadMs = () => scene().idReadMs;
  // The release cost split: the id read-back, and the pick-overlay rebuild that follows it.
  bench.selectionMs = () => scene().selectionMs;
  bench.selectionPicks = () => scene().selectionPicks;
  bench.selectionBuilds = () => scene().selectionBuilds;
  bench.selectionShape = () => scene().selectionShape();
  bench.idPickEnabled = () => scene().idPickEnabled;
  bench.setIdPickEnabled = (on) => {
    scene().setIdPickEnabled(!!on);
    return scene().idPickEnabled;
  };
  bench.idPick = (x, y) => scene().idPick(x, y);
  bench.measureRender = (n) => scene().measureRender(n);
  bench.measurePick = (n) => scene().measurePick(n);
  bench.startFrames = () => { scene().startFrames(); return true; };
  bench.stopFrames = () => scene().stopFrames();
  bench.emptyPoint = () => {
    // A box may only start where no patch is AND the canvas is what the pointer meets: the tree,
    // the ribbon and the islands are DOM panels over the canvas, and an event that lands on one of
    // them never reaches the scene (the first run of this tool measured 0.0 ms releases that way).
    // A projected part box is conservative, so a point outside every one of them is empty. 4,500
    // pins leave thin margins and gaps, so the grid is fine, and the id buffer is asked one pixel
    // at a time only if even that finds nothing.
    const r = box();
    const rects = Object.values(scene().partBounds());
    const inside = (x, y) => rects.some((b) => x >= b.x0 && x <= b.x1 && y >= b.y0 && y <= b.y1);
    const free = (x, y) =>
      !inside(x, y) && document.elementFromPoint(x + r.left, y + r.top) === canvas();
    const candidates = [];
    for (let gy = 0; gy < 24; gy++) {
      for (let gx = 0; gx < 24; gx++) {
        candidates.push([r.width * (0.02 + 0.96 * ((gx + 0.5) / 24)),
                         r.height * (0.03 + 0.94 * ((gy + 0.5) / 24))]);
      }
    }
    for (let gy = 0; gy < 9; gy++) {
      for (let gx = 0; gx < 48; gx++) {
        candidates.push([r.width * (0.004 + 0.992 * (gx / 47)),
                         r.height * (0.006 + 0.988 * (gy / 8))]);
      }
    }
    for (const [x, y] of candidates) {
      if (free(x, y)) return { x: x + r.left, y: y + r.top };
    }
    for (let i = 0; i < 40; i++) {
      const t = i / 39;
      const xs = [r.width * (0.002 + 0.02 * t), r.width * (0.998 - 0.02 * t),
                  r.width * t, r.width * t];
      const ys = [r.height * t, r.height * t,
                  r.height * (0.002 + 0.02 * t), r.height * (0.998 - 0.02 * t)];
      for (let k = 0; k < 4; k++) {
        const x = xs[k] + r.left;
        const y = ys[k] + r.top;
        if (document.elementFromPoint(x, y) !== canvas()) continue;
        if (scene().idPick(x, y) === null) return { x, y };
      }
    }
    return null;
  };
  bench.partPoint = (skip) => {
    const r = box();
    const cx = r.width / 2;
    const cy = r.height / 2;
    const points = Object.values(scene().partBounds())
      .filter((b) => b.x1 - b.x0 >= 3 && b.y1 - b.y0 >= 3)
      .map((b) => ({ x: (b.x0 + b.x1) / 2, y: (b.y0 + b.y1) / 2 }))
      .filter((p) => p.x > 8 && p.y > 8 && p.x < r.width - 8 && p.y < r.height - 8)
      .filter((p) => document.elementFromPoint(p.x + r.left, p.y + r.top) === canvas())
      .sort((a, b) => (a.x - cx) ** 2 + (a.y - cy) ** 2 - ((b.x - cx) ** 2 + (b.y - cy) ** 2));
    if (points.length === 0) return null;
    const point = points[Math.min(skip, points.length - 1)];
    return { x: point.x + r.left, y: point.y + r.top };
  };
  bench.setPickMode = (mode) => {
    const button = document.querySelector('[aria-label^="Pick mode"]');
    if (!button) return Promise.resolve(false);
    button.click();
    return new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const wanted = mode === "face" ? "Face" : "Body";
        const item = Array.from(document.querySelectorAll("[role^='menuitem']"))
          .find((el) => (el.textContent || "").trim() === wanted);
        if (!item) { resolve(false); return; }
        item.click();
        requestAnimationFrame(() => resolve(true));
      }));
    });
  };
  window.__bench = bench;
  return true;
})()
"""

WAIT = """
(async () => {
  const want = __WANT__;
  const began = performance.now();
  const deadline = began + __TIMEOUT_MS__;
  let probeMs = null;
  while (performance.now() < deadline) {
    const probe = window.__stokesScene;
    if (probe) {
      if (probeMs === null) probeMs = performance.now() - began;
      const stats = probe.perf();
      if (stats.parts >= want) {
        return { parts: stats.parts, allPartsMs: performance.now() - began, probeMs,
                 batches: stats.batches, batchInstances: stats.batchInstances,
                 drawCalls: stats.drawCalls, triangles: stats.triangles };
      }
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  const probe = window.__stokesScene;
  return { parts: probe ? probe.perf().parts : 0, allPartsMs: null, probeMs, error: "timed out" };
})()
"""


def orbit_button(dev: DevTools) -> tuple[str, int]:
    """The button that actually ROTATES under the scheme the page is running, and its mask.

    The first version of this tool dragged with the LEFT button on a patch. That is not an orbit:
    ADR-0021 3.4 gives the left button to select and the marquee, and only `paraview` binds it to
    rotation. In the default `cad` scheme three's `mouseButtons.LEFT` is `null`, so OrbitControls
    never rotated, the camera never moved, the render-on-demand loop had nothing to draw, and
    `startFrames`/`stopFrames` recorded nothing. The tool then printed `frame_ms {'n': 0}` — an
    empty record read as a measurement. The bench asks the scene which button rotates instead of
    assuming one, so the gesture is a real orbit in every scheme.

    Returns the CDP button name and the `buttons` bitmask for it.
    """
    names = as_dict(dev.evaluate("window.__stokesScene.navButtons()"))
    for button, mask in (("middle", 4), ("right", 2), ("left", 1)):
        if str(names.get(button.upper(), "")).upper() == "ROTATE":
            return button, mask
    return "middle", 4


def orbit(dev: DevTools, point: dict[str, float], moves: int) -> dict[str, object]:
    """A rotate drag that travels across the canvas, one move per two animation frames.

    Two animation frames between moves means each move's frame is drawn and timed on its own,
    instead of a burst of moves collapsing into the few frames the renderer happened to catch.
    The pointer TRAVELS rather than stepping back and forth: an alternating drag that returns to
    where it started leaves the view where it began, which is not an orbit and draws no frames.

    When the recording comes back empty the run does not abort: the field is reported as
    unavailable with the reason, so the still, hover, click and box figures are still measured.
    """
    button, mask = orbit_button(dev)
    dev.evaluate("window.__bench.reset(); window.__bench.startFrames()")
    x = point["x"]
    y = point["y"]
    dev.mouse("mousePressed", x, y, buttons=mask, button=button)
    began = time.perf_counter()
    for step in range(moves):
        # A steady travel in one direction, so the camera rotates the whole way and every move's
        # frame is a frame of a moving view.
        x = point["x"] + 3 * (step + 1)
        y = point["y"] + 2 * (step + 1)
        dev.mouse("mouseMoved", x, y, buttons=mask, button=button)
        dev.evaluate(TWO_FRAMES, timeout=60.0)
    elapsed = time.perf_counter() - began
    dev.mouse("mouseReleased", x, y, buttons=0, button=button)
    timing = as_dict(dev.evaluate("window.__bench.stopFrames()", timeout=120.0))
    frames = percentiles(as_floats(timing.get("ms", [])))
    row: dict[str, object] = {
        "moves": moves,
        "button": button,
        "wall_s": round(elapsed, 3),
        "interval_ms": round(elapsed * 1000 / moves, 2),
        "method": timing.get("method"),
        "frame_ms": frames,
        "cpu_ms": percentiles(as_floats(timing.get("cpuMs", []))),
    }
    if frames.get("n", 0) == 0:
        # Degrade, never abort: an empty record is a broken gesture or a broken hook, not a fast
        # frame, and the rest of the run still has to report.
        row["frame_ms"] = None
        row["unavailable"] = (
            "the scene drew no frame during the orbit, so no frame time is available "
            f"(method {timing.get('method')!r}, button {button!r}); the camera may not have moved"
        )
    return row


def hover(dev: DevTools, points: list[dict[str, float]]) -> dict[str, object]:
    """Move onto a patch, let the scene's 30 ms hover interval pass, read its own pick time.

    `hover_pick_ms` is the scene's `lastPickMs` after a real pointer move: the raycast the hover
    path runs, at the point a part's projected box is centred on. `centre_pick_ms` is the probe's
    own `measurePick(1)` ten times over: the same raycast on the exact canvas centre, which is the
    densest line of sight through the pile, so the two are deliberately reported apart.
    """
    picks: list[float] = []
    for point in points:
        dev.mouse("mouseMoved", point["x"], point["y"], buttons=0)
        time.sleep(SETTLE_MS / 1000)
        picks.append(float(dev.evaluate("window.__bench.pickMs()")))
    centre = [float(dev.evaluate("window.__bench.measurePick(1)")) for _ in range(10)]
    return {"hover_pick_ms": percentiles(picks), "centre_pick_ms": percentiles(centre)}


def clicks(dev: DevTools, points: list[dict[str, float]], repeats: int) -> dict[str, object]:
    """One click per point: the press and the release handler each timed, with what they picked."""
    dev.evaluate("window.__bench.reset()")
    events: list[dict[str, object]] = []
    for index in range(repeats):
        point = points[index % len(points)]
        dev.mouse("mousePressed", point["x"], point["y"], buttons=1)
        dev.mouse("mouseReleased", point["x"], point["y"], buttons=0)
        events.append(
            {
                "picked": dev.evaluate("window.__bench.pickCount()"),
                "idReadMs": round(float(dev.evaluate("window.__bench.idReadMs()")), 2),
            }
        )
        time.sleep(CLICK_GAP_MS / 1000)
    return {
        "press_ms": percentiles(as_floats(dev.evaluate("window.__bench.press"))),
        "release_ms": percentiles(as_floats(dev.evaluate("window.__bench.release"))),
        "events": events,
    }


def release_gauges(dev: DevTools) -> dict[str, object]:
    """Where the last release's time went: the id read-back, then the pick-overlay rebuild.

    The remainder of the release is the store update and the React render that follow the pick, so
    the three figures together attribute T-032's 50 ms budget instead of leaving it a single number.
    """
    return {
        "id_read_ms": round(as_float(dev.evaluate("window.__bench.idReadMs()")), 2),
        "selection_ms": round(as_float(dev.evaluate("window.__bench.selectionMs()")), 2),
        "selection_picks": as_int(dev.evaluate("window.__bench.selectionPicks()")),
        "selection_shape": dev.evaluate("window.__bench.selectionShape()"),
        # The overlay is usually rebuilt in the frame AFTER the release, so a figure read straight
        # after the release may belong to an earlier gesture. The bench stamps the counter before
        # and after, and only trusts the overlay time when the counter moved.
        "selection_builds": as_int(dev.evaluate("window.__bench.selectionBuilds()")),
    }


def release_summary(runs: list[dict[str, object]], what: str) -> dict[str, object]:
    """The release times of several runs, with the p50 of each phase in the split."""
    times = [value for run in runs for value in as_floats(run["release_ms"])]
    reads = [as_float(run["id_read_ms"]) for run in runs]
    # Only the runs whose overlay rebuild we actually watched: a stale figure is worse than none.
    fresh = [run for run in runs if run.get("selection_fresh")]
    overlays = [as_float(run["selection_ms"]) for run in fresh]
    return {
        "release_ms": [round(value, 2) for value in times],
        "release_p50": round(statistics.median(times), 2) if times else None,
        "id_read_p50": round(statistics.median(reads), 2) if reads else None,
        "selection_p50": round(statistics.median(overlays), 2) if overlays else None,
        "selection_fresh": f"{len(fresh)}/{len(runs)}",
        "selection_picks": [as_int(run["selection_picks"]) for run in runs],
        "selection_shape": [run["selection_shape"] for run in runs],
        "picked": [run["picked"] for run in runs],
        "what": what,
    }


def gesture(
    dev: DevTools, start: dict[str, float], end: dict[str, float], *, modifiers: int
) -> dict[str, object]:
    """One marquee drag: press, six moves along the way, release timed by the page's own handler."""
    dev.evaluate("window.__bench.reset()")
    builds_before = int(dev.evaluate("window.__bench.selectionBuilds()") or 0)
    dev.mouse("mousePressed", start["x"], start["y"], buttons=1, modifiers=modifiers)
    for step in range(1, 7):
        x = start["x"] + (end["x"] - start["x"]) * step / 6
        y = start["y"] + (end["y"] - start["y"]) * step / 6
        dev.mouse("mouseMoved", x, y, buttons=1, modifiers=modifiers)
    dev.mouse("mouseReleased", end["x"], end["y"], buttons=0, modifiers=modifiers)
    time.sleep(0.05)
    done = {
        "release_ms": [
            round(value, 2) for value in as_floats(dev.evaluate("window.__bench.release"))
        ],
        "reached": dev.evaluate("window.__bench.reached()"),
        "picked": dev.evaluate("window.__bench.pickCount()"),
    }
    gauges = release_gauges(dev)
    # The overlay belongs to THIS release only if its build counter moved while we watched.
    gauges["selection_fresh"] = as_int(gauges["selection_builds"]) > builds_before
    done.update(gauges)
    return done


def require_fired(run: dict[str, object], what: str) -> None:
    """A gesture whose events never reached the canvas is not a selection time at all.

    The first run of this tool reported 0.0 ms releases because the "empty" point it dragged from
    sat under a DOM panel: the drag never reached the scene and the number looked like a very fast
    selection. The canvas's own hit counters are what tells the two apart.
    """
    reached = as_dict(run.get("reached") or {})
    if int(reached.get("down", 0)) < 1 or int(reached.get("up", 0)) < 1:
        raise SystemExit(
            f"{what}: the drag never reached the canvas ({reached or 'no hits'}), so the release "
            "figure is not a selection time"
        )


def marquees(
    dev: DevTools, count: int, *, start: dict[str, float], end: dict[str, float], modifiers: int
) -> dict[str, object]:
    """`count` identical marquees; the first pays the id pass the last camera move dirtied."""
    runs = [gesture(dev, start, end, modifiers=modifiers) for _ in range(count)]
    for run in runs:
        require_fired(run, "box")
    return release_summary(runs, "box")


def lasso_path(
    start: dict[str, float], radius_x: float, radius_y: float, points: int
) -> list[dict[str, float]]:
    """A closed freehand loop around `start`, as the client positions the pointer visits."""
    return [
        {
            "x": start["x"] + radius_x * math.cos(step / points * 2 * math.pi),
            "y": start["y"] + radius_y * math.sin(step / points * 2 * math.pi),
        }
        for step in range(points)
    ]


def lassoes(
    dev: DevTools, path: list[dict[str, float]], count: int, *, modifiers: int
) -> dict[str, object]:
    """`count` Alt-drags of the same freehand path, timed at release by the page's own handler."""
    runs: list[dict[str, object]] = []
    for _ in range(count):
        dev.evaluate("window.__bench.reset()")
        builds_before = int(dev.evaluate("window.__bench.selectionBuilds()") or 0)
        first = path[0]
        dev.mouse("mousePressed", first["x"], first["y"], buttons=1, modifiers=modifiers)
        for point in path:
            dev.mouse("mouseMoved", point["x"], point["y"], buttons=1, modifiers=modifiers)
        dev.mouse("mouseReleased", first["x"], first["y"], buttons=0, modifiers=modifiers)
        time.sleep(0.05)
        done = {
            "release_ms": [
                round(value, 2) for value in as_floats(dev.evaluate("window.__bench.release"))
            ],
            "reached": dev.evaluate("window.__bench.reached()"),
            "picked": dev.evaluate("window.__bench.pickCount()"),
        }
        gauges = release_gauges(dev)
        gauges["selection_fresh"] = as_int(gauges["selection_builds"]) > builds_before
        done.update(gauges)
        runs.append(done)
    for run in runs:
        require_fired(run, "lasso")
    return release_summary(runs, "lasso")


def freshness_row(freshness: DistFreshness) -> dict[str, object]:
    """The ``dist`` record the report and ``--json`` carry, so a figure is never quoted bare.

    The timestamps are written as readable local strings as well as raw nanoseconds: the report is
    read by eye as often as by another tool, and the two states that matter most are the two a
    reader compares.
    """
    return {
        "state": freshness.state,
        "dist_mtime": freshness.dist_mtime,
        "src_mtime": freshness.src_mtime,
        "dist_time": describe(freshness.dist_mtime),
        "src_time": describe(freshness.src_mtime),
        "detail": freshness.detail,
    }


def check_dist(args: argparse.Namespace) -> DistFreshness:
    """The t=0 freshness gate: refuse only on proof, and never after measuring has begun.

    Called once from ``main`` before a server or a browser exists, so a refusal costs nothing and
    Chrome is never launched. The exit code is non-zero rather than an exception so that a wrapper
    script sees a clean refusal. ``--allow-stale-dist`` turns the refusal into the same warning the
    unknown case prints; that warning is the whole point, because the numbers that follow are real
    measurements of code that may not be in the tree.
    """
    freshness = dist_freshness(ROOT)
    if freshness.state == "fresh":
        return freshness
    headline = (
        f"DIST {freshness.state.upper()}"
        if freshness.blocked
        else "note: web/dist cannot be checked against the sources"
    )
    print(f"{headline}: {freshness.detail}", file=sys.stderr)
    if not freshness.blocked:
        return freshness
    if args.allow_stale_dist:
        print(
            f"  --allow-stale-dist: measuring anyway. Every figure below describes the bundle in "
            f"{ROOT / DIST_DIR}, which may not be this tree's front end.",
            file=sys.stderr,
        )
        return freshness
    print(
        f"  refusing to measure: rebuild with `{REMEDY}`, or pass --allow-stale-dist to measure "
        "the bundle that is there anyway.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def measure(args: argparse.Namespace) -> dict[str, object]:
    """Everything one browser session measures, as a JSON-shaped report."""
    # t=0 and before any server or browser exists: a refusal here costs nothing and cannot lose a
    # figure, which is why this is the only place the check may ever abort. It is a statement of
    # what the numbers below describe, not a precondition on a run that has already collected them.
    freshness = check_dist(args)
    report: dict[str, object] = {"dist": freshness_row(freshness)}
    server = Server(args.project)
    chrome: Chrome | None = None
    try:
        page = f"http://127.0.0.1:{server.port}/?token={server.token}&debug=1"
        chrome = Chrome(args.browser, page, swiftshader=args.renderer == "swiftshader")
        dev = DevTools(chrome.page_url)
        try:
            wait = WAIT.replace("__WANT__", str(args.expect)).replace(
                "__TIMEOUT_MS__", str(int(args.timeout * 1000))
            )
            loaded = as_dict(dev.evaluate(wait, timeout=args.timeout))
            report["load"] = loaded
            if int(loaded.get("parts", 0)) == 0:
                raise SystemExit(f"the page never showed a part: {json.dumps(loaded)}")
            if int(loaded.get("parts", 0)) < args.expect:
                report["warning"] = f"only {loaded['parts']} of {args.expect} parts loaded"
            # Injected after the load: the navigation that starts the app tears down any earlier
            # context, and with it anything a previous injection left on `window`.
            dev.evaluate(INJECT)
            if dev.evaluate("!!window.__bench") is not True:
                raise SystemExit("the page did not accept the timing hooks")

            env = as_dict(dev.evaluate("window.__bench.env()"))
            report["env"] = env
            report["perf"] = dev.evaluate("window.__bench.perf()")
            report["still"] = dev.evaluate(
                f"window.__bench.measureRender({args.still})", timeout=600.0
            )

            centre = dev.evaluate("window.__bench.partPoint(0)")
            if centre is None:
                raise SystemExit("no part is on screen to press on")
            centre = as_dict(centre)
            report["orbit"] = orbit(dev, centre, args.moves)
            report["perf_after_orbit"] = dev.evaluate("window.__bench.perf()")

            found = [
                dev.evaluate(f"window.__bench.partPoint({index})") for index in (0, 7, 13, 21, 34)
            ]
            report["hover"] = hover(dev, [as_dict(point) for point in found if point is not None])

            report["click_raycaster"] = clicks(dev, [centre], args.clicks)
            dev.evaluate("window.__bench.setIdPickEnabled(true)")
            report["click_id_buffer"] = clicks(dev, [centre], args.clicks)
            report["id_pick"] = dev.evaluate(f"window.__bench.idPick({centre['x']}, {centre['y']})")
            dev.evaluate("window.__bench.setIdPickEnabled(false)")

            empty = dev.evaluate("window.__bench.emptyPoint()")
            if empty is None:
                raise SystemExit("no empty space on screen to start a box from")
            report["empty_point"] = empty
            empty = as_dict(empty)
            canvas = as_dict(env["canvas"])
            width = float(canvas["width"])
            height = float(canvas["height"])
            left = float(canvas["left"])
            top = float(canvas["top"])
            # How far across the canvas the body box reaches. The default 0.94 covers nearly all of
            # the 4,500-part grid, which is what a "select everything" drag does; a series of these
            # separates the read-back's cost from the cost of the pick it produces.
            wide = {"x": left + width * args.wide, "y": top + height * args.wide}
            small = {"x": left + width * 0.55, "y": top + height * 0.55}

            report["box_body"] = marquees(dev, args.boxes, start=empty, end=wide, modifiers=0)
            if not dev.evaluate('window.__bench.setPickMode("face")'):
                raise SystemExit("the Face pick mode could not be chosen")
            report["box_face"] = marquees(dev, args.boxes, start=empty, end=small, modifiers=0)
            dev.evaluate('window.__bench.setPickMode("body")')

            # The lasso runs on Alt, which is what the scene takes as "draw a lasso whatever the
            # toggle says", so its start may be on a patch: the path loops the pile's middle.
            path = lasso_path(centre, width * 0.18, height * 0.22, 12)
            report["lasso_body"] = lassoes(dev, path, args.lassoes, modifiers=1)
            if not dev.evaluate('window.__bench.setPickMode("face")'):
                raise SystemExit("the Face pick mode could not be chosen")
            report["lasso_face"] = lassoes(dev, path, args.lassoes, modifiers=1)
            dev.evaluate('window.__bench.setPickMode("body")')
        finally:
            dev.close()
    finally:
        if chrome is not None:
            chrome.stop()
        server.stop()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    verbs = parser.add_subparsers(dest="verb", required=True)
    builder = verbs.add_parser("build", help="write the pin-grid project with its meshes cached")
    builder.add_argument("--project", type=Path, required=True)
    builder.add_argument(
        "--pins", default="50x45", help="nx x ny pins; 50x45 is the 4,500-solid grid"
    )
    runner = verbs.add_parser("measure", help="drive a headless browser and print the figures")
    runner.add_argument("--project", type=Path, required=True)
    runner.add_argument("--browser", type=Path, default=None)
    runner.add_argument(
        "--renderer",
        choices=("auto", "swiftshader"),
        default="auto",
        help="swiftshader forces software rendering, so a number is never mistaken for the GPU's",
    )
    runner.add_argument("--expect", type=int, default=PARTS)
    runner.add_argument("--moves", type=int, default=MOVE_FRAMES)
    runner.add_argument("--still", type=int, default=STILL_FRAMES)
    runner.add_argument("--boxes", type=int, default=3)
    runner.add_argument(
        "--wide",
        type=float,
        default=0.94,
        help="how far across the canvas the body box reaches (0.94 sweeps nearly all of the grid)",
    )
    runner.add_argument("--lassoes", type=int, default=3)
    runner.add_argument("--clicks", type=int, default=5)
    runner.add_argument("--timeout", type=float, default=600.0)
    runner.add_argument(
        "--allow-stale-dist",
        action="store_true",
        help="measure web/dist even when it predates web/src (the report still records the state)",
    )
    runner.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    if args.verb == "build":
        counts = build_project(args.project, args.pins)
        print(f"pins     : {counts['pins']} ({args.pins})")
        print(f"parts    : {counts['parts']} solids")
        print(f"faces    : {counts['faces']}")
        print(f"triangles: {counts['triangles']}")
        print(f"project  : {args.project}")
        return 0

    args.browser = args.browser or find_browser()
    report = measure(args)
    env = as_dict(report["env"])
    renderer = str(env.get("renderer", "unknown"))
    software = any(word in renderer.lower() for word in ("swiftshader", "llvmpipe", "software"))
    report["software"] = software
    canvas = as_dict(env["canvas"])
    dist = as_dict(report["dist"])
    print(f"dist     : {dist['state']} (dist {dist['dist_time']}, src {dist['src_time']})")
    if dist["state"] != "fresh":
        print(f"           {dist['detail']}")
    print(f"browser  : {env.get('ua')}")
    print(f"renderer : {renderer}")
    print(f"vendor   : {env.get('vendor')}")
    print(
        "gpu timer: "
        + (
            "EXT_disjoint_timer_query_webgl2"
            if env.get("timer")
            else "ABSENT (frame ms are CPU submission times, not the GPU's)"
        )
    )
    print(
        f"canvas   : {canvas['width']:.0f} x {canvas['height']:.0f} CSS px at dpr {env.get('dpr')}"
    )
    loaded = as_dict(report["load"])
    print(
        f"load     : {loaded.get('parts')} parts, all present after "
        f"{loaded.get('allPartsMs')} ms in-page"
    )
    print(f"perf     : {json.dumps(report['perf'])}")
    orbit_row = as_dict(report["orbit"])
    print(
        f"orbit    : {orbit_row['moves']} moves on the {orbit_row['button']} button, "
        f"{orbit_row['interval_ms']} ms apart, {orbit_row['method']} method, "
        f"frames {orbit_row['frame_ms']}, cpu {orbit_row['cpu_ms']}"
    )
    if orbit_row.get("unavailable"):
        print(f"           UNAVAILABLE: {orbit_row['unavailable']}")
    still = as_dict(report["still"])
    print(f"still    : {still.get('method')} {percentiles(as_floats(still.get('ms', [])))}")
    print(f"hover    : {json.dumps(report['hover'])}")
    for name in ("click_raycaster", "click_id_buffer"):
        row = as_dict(report[name])
        print(f"{name:16}: press {row['press_ms']}, release {row['release_ms']}")
    print(f"id_pick  : {json.dumps(report['id_pick'])}")
    for name in ("box_body", "box_face", "lasso_body", "lasso_face"):
        row = as_dict(report[name])
        print(
            f"{name:10}: release p50 {row['release_p50']} ms "
            f"(read {row['id_read_p50']} + overlay {row['selection_p50']}, "
            f"{row['selection_picks']} picks) picked {json.dumps(row['picked'])[:120]}"
        )
    if software:
        print("\nSOFTWARE RENDERING: this is not the owner's GPU. These numbers are a regression")
        print("baseline only and are never the budget verdict. The real comparison is the recipe")
        print("below, run on the machine with the discrete GPU and idPickEnabled flipped on.")
    print("\nowner recipe (the renderer line must not say SwiftShader):")
    print(
        f"  python tools/bench_scene.py measure --project {args.project} "
        "--renderer auto --json gpu.json"
    )
    print(
        "  the printed renderer line is the verdict's precondition; setIdPickEnabled(true) in the"
    )
    print("  page console then repeats click_raycaster and click_id_buffer on the id buffer.")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
