"""Drive the real interface and check it, so a UI claim can be tested rather than believed.

The gap this closes: the repository has no way to check that a control EXISTS, that a menu OPENS,
that a tooltip appears on hover, or that clicking an option changes what the app does. An agent
cannot move a mouse, so a UI change was verified by types and by a screenshot of whatever state the
page happened to open in — which is how the mouse-scheme menu shipped with its button map printed
as a chip inside the row, and how a regression that took the orbit away survived a review.

This tool opens the app in headless Chrome, drives it over the DevTools protocol with **trusted**
input events (a script-built event does not move React or `setPointerCapture`), and asserts what a
person would see. It exits non-zero on the first broken expectation, so it can gate a change.

    python tools/test_interactions.py --project <folder.stokes>
    python tools/test_interactions.py --project <folder.stokes> --shots out/ --json report.json

``--project`` is optional: without one the built-in demo project is used, which is enough for the
chrome (islands, menus, tooltips). Pass a project with imported parts to exercise the viewport
gestures. ``--shots DIR`` writes a PNG per step, so a failure or a review can be looked at.

Import rule: the DevTools plumbing is ``tools/bench_scene.py``'s (``Chrome``, ``DevTools``), which
already drives this app; this module imports it rather than keeping a second copy. Both need the
``server`` extra (``websockets``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_scene import Chrome, DevTools, find_browser


class Failure(AssertionError):
    """One expectation a person would call a bug."""


class App:
    """The running app: one server, one headless page, and the tools to look at it."""

    def __init__(self, project: Path | None, shots: Path | None, attach: int | None = None) -> None:
        from stokes.api import Session
        from stokes.app.demo import build_demo
        from stokes.server.app import create_app, new_token
        from stokes.server.runner import ServerThread

        self.shots = shots
        if shots is not None:
            shots.mkdir(parents=True, exist_ok=True)
        # Before either branch: the attach path returns early, and a counter set only on the launch
        # path left `shot()` raising `AttributeError` for every step of an attached run.
        self._shot_number = 0

        if attach is not None:
            # Drive the DESKTOP app instead of launching a browser. No chrome is installed on Linux,
            # and none is needed: the window is QtWebEngine, which is Chromium and serves the same
            # DevTools protocol. The app must already be running with
            # `QTWEBENGINE_REMOTE_DEBUGGING=<port>`; this attaches to it and owns nothing but the
            # socket, so the checks below run against the real window rather than a lookalike.
            #
            # The session it holds is read-only for this harness (`--project` is ignored): the
            # checks look at and click the interface, and anything they need to know they read out
            # of the page's own stores.
            self.chrome = Chrome.attach(attach)
            self.dev = DevTools(self.chrome.page_url)
            self.session = None
            self.server = None
            self.api = None
            self.token = None
            self._wait_for_app()
            return

        session = Session()
        if project is not None:
            session.execute("project.open", path=str(project))
        else:
            build_demo(session)
        self.session = session
        self.token = new_token()
        self.server = ServerThread(create_app(session, token=self.token))
        self.api = self.server.start()
        # `drive=1` exposes the stores, `debug=1` the scene probe: the harness needs to read state.
        page = f"{self.api}/?token={self.token}&api={self.api}&drive=1&debug=1"
        self.chrome = Chrome(find_browser(), page, swiftshader=False)
        self.dev = DevTools(self.chrome.page_url)
        self._shot_number = 0
        self._wait_for_app()

    def _wait_for_app(self, timeout: float = 90.0) -> None:
        """Wait until the page has the app mounted and the store reachable."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = self.dev.evaluate(
                "Boolean(window.__stokes && document.querySelector('[data-ws-island]'))"
            )
            if ready:
                return
            time.sleep(0.5)
        raise Failure("the app did not come up: no __stokes or no island in the page")

    def close(self) -> None:
        try:
            self.dev.close()
        finally:
            self.chrome.stop()  # a no-op for an attached browser: it is not ours to stop
            if self.server is not None:
                self.server.stop()

    # ------------------------------------------------------------------ looking at the page

    def evaluate(self, expression: str) -> Any:
        return self.dev.evaluate(expression)

    def shot(self, name: str) -> str | None:
        """A screenshot of the current state, numbered so the order is obvious."""
        if self.shots is None:
            return None
        self._shot_number += 1
        path = self.shots / f"{self._shot_number:02d}-{name}.png"
        raw = self.dev.call("Page.captureScreenshot", {"format": "png"}).get("data")
        if not isinstance(raw, str):
            raise Failure("Page.captureScreenshot returned no image")
        import base64

        path.write_bytes(base64.b64decode(raw))
        return str(path)

    def capture(self, path: Path) -> None:
        """A screenshot written to ``path``, whether or not ``--shots`` is on (the captures)."""
        import base64

        raw = self.dev.call("Page.captureScreenshot", {"format": "png"}).get("data")
        if not isinstance(raw, str):
            raise Failure("Page.captureScreenshot returned no image")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(raw))

    def box(self, selector: str) -> dict[str, float]:
        """The client rectangle of an element, or a Failure naming it."""
        found = self.evaluate(
            "(() => { const el = document.querySelector(" + json.dumps(selector) + ");"
            " if (!el) return null; const r = el.getBoundingClientRect();"
            " return {x: r.x, y: r.y, width: r.width, height: r.height}; })()"
        )
        if found is None:
            raise Failure(f"nothing matches {selector!r}")
        return {k: float(v) for k, v in found.items()}

    def centre(self, selector: str) -> tuple[float, float]:
        rect = self.box(selector)
        return rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2

    # ------------------------------------------------------------------ driving the page

    def move(self, x: float, y: float) -> None:
        self.dev.mouse("mouseMoved", x, y, buttons=0)

    def click(self, x: float, y: float) -> None:
        self.dev.mouse("mouseMoved", x, y, buttons=0)
        self.dev.mouse("mousePressed", x, y, buttons=1)
        self.dev.mouse("mouseReleased", x, y, buttons=0)
        time.sleep(0.35)

    def click_with(self, x: float, y: float, modifiers: int) -> None:
        """A click with held modifiers - **Ctrl is 2, Shift is 8** in the DevTools convention.

        A multi-selection gesture is a modified CLICK, not a key press, so `press` cannot make one:
        the app reads `event.ctrlKey` on the pointer event. `modifiers` is the same field
        `bench_scene` already uses for its lasso runs.
        """
        self.dev.mouse("mouseMoved", x, y, buttons=0, modifiers=modifiers)
        self.dev.mouse("mousePressed", x, y, buttons=1, modifiers=modifiers)
        self.dev.mouse("mouseReleased", x, y, buttons=0, modifiers=modifiers)
        time.sleep(0.35)

    def reload_page(self, settle: float = 26.0) -> None:
        """Reload the page and wait for the app to come back.

        **A real page load**, not a store read: a preference that survives a reload has come back
        from the workspace file, which is the only thing that proves it was saved. The wait is the
        app's own load time under SwiftShader, the same order the capture harness allows at start.
        """
        self.dev.call("Page.reload", {"ignoreCache": True})
        time.sleep(settle)

    def hover(self, x: float, y: float, settle: float = 1.2) -> None:
        """Move onto a point and wait: tooltips appear on a delay, so this is not instant."""
        self.move(x - 40, y - 40)
        self.move(x, y)
        time.sleep(settle)

    def drag(self, start: tuple[float, float], end: tuple[float, float], steps: int = 6) -> None:
        self.dev.mouse("mouseMoved", *start, buttons=0)
        self.dev.mouse("mousePressed", *start, buttons=1)
        for i in range(1, steps + 1):
            x = start[0] + (end[0] - start[0]) * i / steps
            y = start[1] + (end[1] - start[1]) * i / steps
            self.dev.mouse("mouseMoved", x, y, buttons=1)
        self.dev.mouse("mouseReleased", *end, buttons=0)
        time.sleep(0.5)

    def wheel(self, x: float, y: float, delta: float) -> None:
        """One trusted wheel event at a position: the camera zooms towards the point it is over."""
        self.dev.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "button": "none",
                "buttons": 0,
                "deltaX": 0,
                "deltaY": delta,
                "clickCount": 0,
            },
            timeout=30.0,
        )
        time.sleep(0.15)

    def press(self, key: str, code: str, virtual: int, modifiers: int = 0) -> None:
        """One trusted key press, down and up, as `mouse` does for the pointer.

        A key event built in the page does not reach the keymap: the app listens on `window` and
        checks `event.target`, and a synthesised event is not trusted. This goes through DevTools,
        so the app sees the same keydown a person's keyboard sends.
        """
        for kind in ("keyDown", "keyUp"):
            self.dev.call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": key,
                    "code": code,
                    "windowsVirtualKeyCode": virtual,
                    "nativeVirtualKeyCode": virtual,
                    "modifiers": modifiers,
                },
                timeout=30.0,
            )
        time.sleep(0.3)


class Checks:
    """Runs the steps, recording each one and stopping at the first real failure."""

    def __init__(self, app: App) -> None:
        self.app = app
        self.steps: list[dict[str, Any]] = []

    def step(self, name: str, fn: Any, *, fatal: bool = True) -> None:
        """Run one step. A broken expectation stops the run, unless the step is a ``fatal=False``
        finding: it is recorded as failed (the run's result and exit code are red) and printed, and
        the checks after it still run, so one finding does not hide the rest of the harness."""
        started = time.monotonic()
        try:
            detail = fn()
        except Failure as exc:
            self.shots(name)
            self.steps.append({"step": name, "ok": False, "error": str(exc)})
            if not fatal:
                print(f"  FAIL {name} — {exc}")
                return
            raise Failure(f"{name}: {exc}") from exc
        self.shots(name)
        self.steps.append(
            {
                "step": name,
                "ok": True,
                "ms": round((time.monotonic() - started) * 1000),
                "detail": detail,
            }
        )
        print(f"  ok   {name}" + (f" — {detail}" if detail else ""))

    def shots(self, name: str) -> None:
        try:
            self.app.shot(name)
        except Exception as exc:  # a screenshot must never be what fails a check
            print(f"       (no screenshot for {name}: {exc})", file=sys.stderr)

    def expect(self, condition: bool, message: str) -> None:
        if not condition:
            raise Failure(message)


def check_mouse_menu(run: Checks, app: App) -> None:
    """The mouse-scheme menu: it opens, it lists the schemes, and its hints are TOOLTIPS.

    The bug this exists for: the button map for each scheme was passed as the menu's `hint`, which
    the menu renders as a right-hand chip, so a sentence per row covered the labels.
    """

    def opens_and_is_opt_in() -> str:
        selector = "[aria-label^='Mouse scheme']"
        label = app.evaluate(
            f"document.querySelector({json.dumps(selector)})?.textContent?.trim() ?? null"
        )
        run.expect(label is not None, "the Modes island has no Mouse scheme button")
        run.expect("CAD" in str(label), f"the default scheme is not CAD: {label!r}")
        x, y = app.centre(selector)
        app.click(x, y)
        menu = app.evaluate(
            "Array.from(document.querySelectorAll('[role=menuitemradio], [role=menuitemcheckbox]'))"
            ".map((n) => n.textContent?.trim() ?? '')"
        )
        run.expect(isinstance(menu, list) and len(menu) > 0, "the Mouse menu did not open")
        for want in ("CAD", "ParaView", "Blender", "Select / Orbit"):
            run.expect(
                any(want in str(entry) for entry in menu), f"the menu has no {want} entry: {menu}"
            )
        return f"{len(menu)} entries"

    def hints_are_not_inline_chips() -> str:
        # A chip is `<span class="kbd">` INSIDE the menu row. The button map must not be one.
        inline = app.evaluate(
            "Array.from(document.querySelectorAll('.menu-item'))"
            ".filter((n) => n.querySelector('.kbd'))"
            ".map((n) => n.textContent?.trim() ?? '')"
        )
        run.expect(
            not inline,
            "a menu row still carries its description as an inline chip: " + repr(inline),
        )
        # And the rows must be narrow enough that the label is not squeezed by long text.
        widths = app.evaluate(
            "Array.from(document.querySelectorAll('.menu-item'))"
            ".map((n) => Math.round(n.getBoundingClientRect().width))"
        )
        run.expect(isinstance(widths, list) and len(widths) > 0, "no menu rows to measure")
        return f"widest row {max(int(w) for w in widths)} px"

    def tooltip_appears_on_hover() -> str:
        # Hover the row whose label names the scheme, and wait for the bubble.
        rect = app.evaluate(
            "(() => { const rows = Array.from(document.querySelectorAll('.menu-item'));"
            " const row = rows.find((n) => (n.textContent || '').includes('ParaView'));"
            " if (!row) return null; const r = row.getBoundingClientRect();"
            " return {x: r.x, y: r.y, width: r.width, height: r.height}; })()"
        )
        run.expect(rect is not None, "the ParaView row is not in the open menu")
        x = float(rect["x"]) + float(rect["width"]) / 2
        y = float(rect["y"]) + float(rect["height"]) / 2
        app.hover(x, y)
        text = app.evaluate(
            "(() => { const tips = Array.from(document.querySelectorAll("
            "'[role=tooltip], .tooltip, .bubble'));"
            " return tips.map((n) => (n.textContent || '').trim()).filter(Boolean); })()"
        )
        joined = " | ".join(str(t) for t in text) if isinstance(text, list) else ""
        run.expect(
            "rotat" in joined.lower(),
            f"hovering the ParaView row showed no button-map tooltip; bubbles were: {joined!r}",
        )
        return joined[:90]

    def selecting_a_scheme_changes_the_app() -> str:
        rect = app.evaluate(
            "(() => { const rows = Array.from(document.querySelectorAll('.menu-item'));"
            " const row = rows.find((n) => (n.textContent || '').includes('ParaView'));"
            " if (!row) return null; const r = row.getBoundingClientRect();"
            " return {x: r.x, y: r.y, width: r.width, height: r.height}; })()"
        )
        run.expect(rect is not None, "the ParaView row is not in the open menu")
        app.click(
            float(rect["x"]) + float(rect["width"]) / 2,
            float(rect["y"]) + float(rect["height"]) / 2,
        )
        mode = app.evaluate("window.__stokes.getState().navMode")
        run.expect(mode == "paraview", f"clicking ParaView left navMode = {mode!r}")
        # The rig must have taken the binding that comes with it.
        left = app.evaluate("window.__stokesScene?.navButtons?.().LEFT ?? null")
        run.expect(
            left == "ROTATE",
            f"the ParaView scheme did not give the left button to rotate: LEFT={left!r}",
        )
        # Put it back so the next check starts from the default.
        app.evaluate("window.__stokes.getState().setNavMode('cad')")
        return "navMode and the rig's LEFT binding both followed the click"

    run.step(
        "the Modes island has a Mouse scheme button, and the menu lists every scheme",
        opens_and_is_opt_in,
    )
    run.step(
        "a scheme's button map is a tooltip, not a chip in the row", hints_are_not_inline_chips
    )
    run.step("hovering a scheme shows its button map", tooltip_appears_on_hover)
    run.step(
        "choosing a scheme changes the app and the camera bindings",
        selecting_a_scheme_changes_the_app,
    )


TOP_CENTRE_PROBE = (
    "(function(){"
    "var bar=document.querySelector('[data-ws=scale-bar]');"
    "var prog=document.querySelector('.import-progress');"
    "var label=document.querySelector('.import-progress-label');"
    "if(!bar||!prog||!label)return null;"
    # The label is EMPTY when nothing is importing, so its box has no height and every overlap test
    # passes. Give it the widest text it carries, measure that, then put both back.
    "var wasText=label.textContent;var wasAttr=prog.getAttribute('data-active');"
    # The scale slides aside for the import line, so the measurement has to be taken with that
    # motion out of the way: mid-transition the box is still where it started and the check would
    # report the collision it is meant to catch.
    "var wasTransition=bar.style.transition;"
    "bar.style.transition='none';"
    "label.textContent='Reading baseline_wall.stl...';"
    "prog.setAttribute('data-active','true');"
    "void bar.offsetHeight;"  # force the layout, so the box read below is the settled one
    "var b=bar.getBoundingClientRect();var l=label.getBoundingClientRect();"
    "bar.style.transition=wasTransition;"
    "label.textContent=wasText;"
    "prog.setAttribute('data-active',wasAttr===null?'false':wasAttr);"
    "return {barTop:Math.round(b.top),barBottom:Math.round(b.bottom),"
    "barLeft:Math.round(b.left),barRight:Math.round(b.right),"
    "labelTop:Math.round(l.top),labelBottom:Math.round(l.bottom),"
    "labelLeft:Math.round(l.left),labelRight:Math.round(l.right)};"
    "})()"
)
"""Measures the scale and the import label with the line forced active and text in the label.

The bug this exists for: both live at the top centre, and an import printed "Reading thing.stl..."
with the scale's own figure struck through it. The probe forces the line on, gives the label the
widest text it carries, measures, then puts both back — so it measures the real collision whether or
not a file happens to be loading.
"""


def check_top_centre_is_not_shared(run: Checks, app: App) -> None:
    """Nothing may print over the scale: the scale steps aside for the import line.

    A screenshot would show this, but only if a file happened to be importing at the time, which is
    why the state is forced rather than waited for.
    """

    def scale_and_import_line_do_not_collide() -> str:
        box = app.evaluate(TOP_CENTRE_PROBE)
        run.expect(box is not None, "the scale or the import line is not in the page")
        assert box is not None  # the expect above raises; this narrows the type
        # A zero-height label means nothing was really measured, so the check could never fail.
        run.expect(
            box["labelBottom"] > box["labelTop"],
            f"the import label measured as empty, so nothing was tested: {box}",
        )
        sideways = box["barLeft"] < box["labelRight"] and box["barRight"] > box["labelLeft"]
        run.expect(
            not sideways
            or box["barBottom"] <= box["labelTop"]
            or box["barTop"] >= box["labelBottom"],
            "the scale and the import line are printed over each other: "
            f"scale {box['barTop']}-{box['barBottom']}, "
            f"label {box['labelTop']}-{box['labelBottom']}",
        )
        # How far the scale sits below the import label, which is the arrangement the fix provides.
        clear = box["barTop"] - box["labelBottom"]
        return (
            f"import label {box['labelTop']}-{box['labelBottom']}, "
            f"scale {box['barTop']}-{box['barBottom']}, {clear} px clear"
        )

    run.step(
        "the top centre is not shared by the scale and the import line",
        scale_and_import_line_do_not_collide,
    )


def check_box_select(run: Checks, app: App) -> None:
    """A body box-select works end to end: it starts on empty space and it selects the model.

    This is the gesture check, not the budget one. The 50 ms release budget (T-032, met by T-095) is
    measured precisely by `tools/bench_scene.py`, whose page script owns the pointerup timing; this
    tool exists for the things a screenshot cannot answer, and "does a drag select anything at all"
    is one of them. A box that silently selects nothing has happened before.
    """
    # **State this check needs, stated here rather than inherited.** The harness runs against one
    # live app, so whatever the check before left behind is what this one starts with — and two of
    # those leftovers break it in ways that look like a broken box-select:
    #
    #  * the pick mode. A box selects BODIES; left in `face` mode the drag selects faces, the body
    #    count stays 0, and this reports "selected nothing" for a gesture that works.
    #  * the camera. The scale-bar check zooms hard to read a bigger figure and never zooms back, so
    #    the model is ~90 px wide in a corner; a drag from an "empty" corner point to 55% of the
    #    canvas then crosses no part at all. `requestFit` frames it, as the measure check already
    #    does for the same reason.
    app.evaluate("window.__stokes.getState().setPickMode('body')")
    app.evaluate("window.__stokes.getState().requestFit()")
    time.sleep(0.8)

    def selects_and_grows_with_the_box() -> str:
        canvas = app.evaluate(
            "(() => { const c = document.querySelector('canvas');"
            " const r = c.getBoundingClientRect();"
            " return {x: r.x, y: r.y, width: r.width, height: r.height}; })()"
        )
        run.expect(canvas is not None, "there is no canvas to drag on")
        left = float(canvas["x"])
        top = float(canvas["y"])
        width = float(canvas["width"])
        height = float(canvas["height"])
        # Start where no patch projects, or the drag would orbit instead of drawing a box. The
        # scene probe lists every part's screen rectangle, so a point outside all of them is empty.
        #
        # **`partBounds` is CANVAS-LOCAL, and the drag is in PAGE coordinates.** The probe
        # projects with the canvas's own width and height (`scene.projectRect`), while
        # `Input.dispatchMouseEvent` takes viewport pixels; the canvas starts below the top bar, so
        # the two differ by its offset and comparing them directly is wrong by that much. This
        # scanned from the canvas's top edge and found (4, 52) "empty" — actually inside the top
        # strip — then dragged a box that reached the model nowhere, and the check failed with
        # "selected nothing" for a gesture that works. `canvasCssPoint` is the app's own conversion
        # for exactly this, so the offset is applied here.
        start = app.evaluate(
            "(() => { const b = window.__stokesScene.partBounds();"
            " const c = document.querySelector('canvas');"
            " const r = c.getBoundingClientRect();"
            " const inside = (x, y) => Object.values(b).some("
            "(q) => x >= q.x0 + r.left && x <= q.x1 + r.left"
            "     && y >= q.y0 + r.top && y <= q.y1 + r.top);"
            # The point must also be ON THE CANVAS. `elementFromPoint` is the test that matters,
            # because a `pointerdown` goes to the element under it: the top-left of the canvas is
            # covered by the 8x8 `ctx-toolbar` overlay (z-index 14), so a scan that only asked "is
            # no part here" started its drag on that overlay, no `pointerdown` reached the canvas,
            # no box began, and this check reported "selected nothing" for a gesture that works.
            # The pad below keeps the point off the canvas's own edge as well.
            " const free = (x, y) => { const el = document.elementFromPoint(x, y);"
            "   return (el === c || c.contains(el)) && !inside(x, y); };"
            " for (let y = r.top + 12; y < r.bottom - 12; y += 8)"
            "   for (let x = r.left + 12; x < r.right - 12; x += 8)"
            "     if (free(x, y)) return {x, y};"
            " return null; })()"
        )
        run.expect(start is not None, "every pixel is over a patch; no empty space to drag from")
        counts: list[int] = []
        # Two boxes that both reach the model, so "the wider one picks more" is a real comparison: a
        # small box from a corner may legitimately cover no pin at all.
        for fraction in (0.55, 0.94):
            end = (left + width * fraction, top + height * fraction)
            app.drag((float(start["x"]), float(start["y"])), end, steps=6)
            time.sleep(0.2)
            picked = app.evaluate("window.__stokes.getState().picked.bodies.length")
            counts.append(int(picked or 0))
        run.expect(counts[0] > 0, f"a box over the model selected nothing: {counts}")
        run.expect(counts[1] >= counts[0], f"the wider box selected fewer bodies: {counts}")
        return f"a 0.55 box picked {counts[0]}, a 0.94 box picked {counts[1]}"

    run.step(
        "a drag on empty space draws a box and it selects bodies", selects_and_grows_with_the_box
    )


def check_scale_bar(run: Checks, app: App) -> None:
    """The viewport has a scale, it reads as a length, and zooming changes it.

    The app had no scale at all, so a user could not tell a 20 mm part from a 20 m one without
    opening the tree and reading the bounds. A bar that is present but never changes is just as
    useless, so this checks the figure moves with the camera.
    """

    def present_readable_and_adaptive() -> str:
        bar = app.evaluate(
            "(() => { const el = document.querySelector(\"[data-ws='scale-bar']\");"
            " if (!el) return null;"
            " const arms = Array.from(el.querySelectorAll('.scale-bar-arm'));"
            " return {label: el.querySelector('.scale-bar-label')?.textContent ?? null,"
            "   width: arms.reduce((w, a) => w + a.getBoundingClientRect().width, 0),"
            "   arms: arms.length,"
            "   state: window.__stokes.getState().scaleBar}; })()"
        )
        run.expect(bar is not None, "the viewport has no scale bar")
        label = (bar or {}).get("label")
        run.expect(
            isinstance(label, str) and label.strip() != "", f"the scale bar is unlabelled: {bar}"
        )
        # A length with a unit: "10 mm", "2 m", "1.5 km".
        run.expect(
            any(unit in str(label) for unit in ("mm", "m", "µm", "km")),
            f"the scale label is not a length: {label!r}",
        )
        run.expect(int((bar or {}).get("arms") or 0) == 2, f"the crosshair has no two arms: {bar}")
        first = float((bar or {}).get("width") or 0)
        run.expect(first > 4, f"the scale bar has no width: {first}")
        # Zoom out hard: the same corner must now stand for a longer length.
        app.evaluate(
            "(() => { const s = window.__stokesScene;"
            " const c = window.__stokes.getState();"
            " c.requestFit(); return true; })()"
        )
        time.sleep(0.4)
        before = app.evaluate("window.__stokes.getState().scaleBar")
        app.evaluate(
            "(() => { const el = document.querySelector('canvas');"
            " const wheel = {deltaY: 240, bubbles: true, cancelable: true};"
            " for (let i = 0; i < 12; i++) el.dispatchEvent(new WheelEvent('wheel', wheel));"
            " return true; })()"
        )
        time.sleep(0.5)
        after = app.evaluate("window.__stokes.getState().scaleBar")
        run.expect(after is not None, "the scale bar disappeared after zooming")
        # The orbit is on the same target, so zooming out must lengthen what the bar represents.
        run.expect(
            float((after or {}).get("metres") or 0) >= float((before or {}).get("metres") or 0),
            f"zooming out did not lengthen the scale: {before} -> {after}",
        )
        return f"{label}, {round(first)} px (zoomed: {before} -> {after})"

    run.step(
        "the viewport has a readable scale that follows the camera", present_readable_and_adaptive
    )


MEASURE_POINTS = (
    "(function(){"
    "var scene=window.__stokesScene;"
    "var b=scene.partBounds();"
    "var canvas=document.querySelector('canvas').getBoundingClientRect();"
    # The biggest parts first, so the two points are on different parts and far apart: a pair inside
    # one small part would make the distance depend on that part's size.
    "var rects=Object.keys(b).map(function(k){return b[k];})"
    ".sort(function(p,q){return (q.x1-q.x0)*(q.y1-q.y0)-(p.x1-p.x0)*(p.y1-p.y0);});"
    "var fractions=[[0.5,0.5],[0.5,0.2],[0.5,0.8],[0.2,0.5],[0.8,0.5],[0.3,0.3],[0.7,0.7]];"
    "var hits=[];"
    "for(var i=0;i<rects.length&&i<6;i++){"
    "  var q=rects[i];"
    "  for(var j=0;j<fractions.length;j++){"
    "    var x=q.x0+(q.x1-q.x0)*fractions[j][0];var y=q.y0+(q.y1-q.y0)*fractions[j][1];"
    "    if(x<canvas.left+4||x>canvas.right-4||y<canvas.top+4||y>canvas.bottom-4)continue;"
    # `idPick` is what a click sees, so a point it answers for is a point the measurement will get.
    "    if(scene.idPick(x,y))hits.push({x:x,y:y});"
    "  }"
    "}"
    "var best=null;"
    "for(var p=0;p<hits.length;p++)"
    "  for(var r=p+1;r<hits.length;r++){"
    "    var d=Math.hypot(hits[p].x-hits[r].x,hits[p].y-hits[r].y);"
    "    if(best===null||d>best.gap)best={gap:d,a:hits[p],b:hits[r]};"
    "  }"
    "return {points:best!==null&&best.gap>=40?[best.a,best.b]:null,"
    "gap:best===null?0:Math.round(best.gap),hits:hits.length,rects:rects.length};"
    "})()"
)
"""Two client positions on the model a click really meets, as far apart as the parts allow.

The measure tool records the surface point under the pointer, so the check needs points the pointer
path resolves to a face. The scene's own `idPick` is that path's answer (T-032), which is why it
decides which candidates count rather than a bounding box that may sit over a hole; a few places
inside each of the biggest parts are tried, because a rect's centre can land in one. The pair is the
farthest apart of the ones that answered, so the measurement is not a few pixels of nothing. The
counts come back with it, so a failure says what the scan saw instead of only that it found nothing.
"""

MEASURE_READOUT = (
    "(function(){"
    "var el=document.querySelector(\"[data-ws='measure-readout']\");"
    "var state=window.__stokes.getState().measureReadout;"
    "var scene=window.__stokesScene.measure();"
    "if(!el)return {text:null,state:state,scene:scene};"
    "var r=el.getBoundingClientRect();"
    "return {text:(el.textContent||'').trim(),state:state,scene:scene,"
    "box:{x:r.x,y:r.y,width:r.width,height:r.height}};"
    "})()"
)
"""The measurement's label, the store's readout and what the scene says it measured."""


def overlaps(a: dict[str, float], b: dict[str, float]) -> bool:
    """True when two client rectangles share a pixel."""
    return (
        a["x"] < b["x"] + b["width"]
        and a["x"] + a["width"] > b["x"]
        and a["y"] < b["y"] + b["height"]
        and a["y"] + a["height"] > b["y"]
    )


def check_measure(run: Checks, app: App) -> None:
    """Measure works end to end: two clicks give a distance, and Escape takes it away.

    The gap this closes: `view.measure` was a `comingSoon` stub, so every part of the tool — the
    mode's active state, two clicks reaching the scene through the real pointer path, a figure with
    a unit, and Esc closing the measurement before it clears the pick — was a claim nobody could
    test. The line itself is drawn in the canvas in world space and is reviewed in the screenshots;
    what is asserted here is the figure a person reads, that the camera leaves it alone, and where
    the label is allowed to sit.
    """

    def two_clicks_measure_and_escape_clears() -> str:
        # The mode this check needs, stated here: a box-select check before it leaves `body`, but
        # the checks must not depend on one another's leftovers. Face mode is what makes a two-click
        # distance meaningful on this model — two clicks land on two faces of two bodies.
        app.evaluate("window.__stokes.getState().setPickMode('face')")
        time.sleep(0.2)
        selector = "[data-ws-island='modes'] [aria-label='Measure']"
        run.expect(
            bool(app.evaluate(f"Boolean(document.querySelector({json.dumps(selector)}))")),
            "the Modes island has no Measure button",
        )
        x, y = app.centre(selector)
        app.click(x, y)
        run.expect(
            app.evaluate("window.__stokes.getState().measureMode") is True,
            "clicking Measure did not turn the mode on",
        )
        run.expect(
            app.evaluate(
                f"document.querySelector({json.dumps(selector)}).getAttribute('aria-pressed')"
            )
            == "true",
            "the Measure button shows no active state while the mode is on",
        )

        # Frame the whole model first: an earlier check zooms out hard, and a model a few pixels
        # across has no two points worth measuring between.
        app.evaluate("window.__stokes.getState().requestFit()")
        time.sleep(0.6)

        found: dict[str, Any] = app.evaluate(MEASURE_POINTS) or {}
        places = found.get("points")
        run.expect(
            isinstance(places, list) and len(places) == 2,
            f"found no two points on the model to measure: {found!r}",
        )
        assert isinstance(places, list)  # the expect above raises; this narrows the type
        points = [(float(place["x"]), float(place["y"])) for place in places]
        for px, py in points:
            app.click(px, py)
        scene = app.evaluate("window.__stokesScene.measure()")
        run.expect(
            isinstance(scene, dict) and scene.get("b") is not None,
            f"two clicks on the model recorded no second point: {scene!r}",
        )

        readout = app.evaluate(MEASURE_READOUT)
        text = str((readout or {}).get("text") or "")
        run.expect(text != "", f"the measurement published no label: {readout!r}")
        # A length with a unit, as the scale bar's own check reads one: "30 mm", "2 m", "1.5 km".
        run.expect(
            any(unit in text for unit in ("mm", "m", "µm", "km"))
            and any(c.isdigit() for c in text),
            f"the readout is not a length with a unit: {text!r}",
        )
        metres = float((readout or {}).get("scene", {}).get("metres") or 0)
        run.expect(metres > 0, f"the measurement has no length: {metres!r}")

        box = app.box("[data-ws='measure-readout']")
        canvas = app.box("canvas")
        run.expect(
            box["x"] >= canvas["x"]
            and box["x"] + box["width"] <= canvas["x"] + canvas["width"]
            and box["y"] >= canvas["y"]
            and box["y"] + box["height"] <= canvas["y"] + canvas["height"],
            f"the readout hangs outside the viewport: {box} in {canvas}",
        )
        # The top centre belongs to the scale bar and the import line; the label floats at a 3D
        # point and must be clamped clear of them rather than printed through them (2026-10-06).
        bar = app.box("[data-ws='scale-bar']")
        run.expect(
            not overlaps(box, bar),
            f"the readout prints over the scale bar: readout {box}, scale {bar}",
        )
        # The line and its markers are canvas pixels, which no assertion here can see: this shot is
        # where the drawing itself is reviewed, and it is taken while the measurement is on screen.
        run.shots("measure-line")

        # An orbit is the camera's, not a click: the measurement is world space, so it must stay
        # exactly as it was, and the label must still be on screen and still clear of the scale.
        before = app.evaluate("window.__stokes.getState().measureReadout")
        app.drag(
            (points[0][0], points[0][1]),
            (points[0][0] + 60, points[0][1] + 20),
            steps=6,
        )
        after = app.evaluate(MEASURE_READOUT)
        run.expect(
            isinstance(after, dict) and after.get("text") == text,
            f"orbiting lost or changed the measurement: {before!r} -> {after!r}",
        )
        after_metres = float((after or {}).get("scene", {}).get("metres") or 0)
        run.expect(
            abs(after_metres - metres) < 1e-9,
            f"the orbit changed the measured distance: {metres} -> {after_metres}",
        )
        run.expect(
            not overlaps(after.get("box") or {}, bar),
            f"the label printed over the scale bar after orbiting: {after!r}",
        )

        # Zoom in towards a point below the pair, so the line climbs the view: the figure must not
        # change (the points did not move) and the label must stay inside the viewport, clear of the
        # top strip, however far the midpoint goes off the top of the screen.
        below = max(points[0][1], points[1][1]) + 100
        for _ in range(6):
            app.wheel(points[0][0], below, -240.0)
        zoomed = app.evaluate(MEASURE_READOUT)
        run.expect(
            isinstance(zoomed, dict) and zoomed.get("text") == text,
            f"zooming changed or lost the measurement: {after!r} -> {zoomed!r}",
        )
        zbox = app.box("[data-ws='measure-readout']")
        run.expect(
            zbox["x"] >= canvas["x"]
            and zbox["x"] + zbox["width"] <= canvas["x"] + canvas["width"]
            and zbox["y"] >= canvas["y"]
            and zbox["y"] + zbox["height"] <= canvas["y"] + canvas["height"],
            f"the readout left the viewport when the camera zoomed in: {zbox} in {canvas}",
        )
        run.expect(
            zbox["y"] - canvas["y"] >= 32,
            f"the label printed against the top edge: {zbox} in {canvas}",
        )
        run.expect(
            not overlaps(zbox, bar),
            f"the label printed over the scale bar after zooming: {zbox} scale {bar}",
        )

        # Escape closes what is open, one thing at a time: the measurement first (T-098).
        app.press("Escape", "Escape", 27)
        run.expect(
            app.evaluate("window.__stokes.getState().measureMode") is False,
            "Escape did not leave measure mode",
        )
        run.expect(
            app.evaluate("Boolean(document.querySelector(\"[data-ws='measure-readout']\"))")
            is False,
            "Escape left the measurement's readout in the page",
        )
        run.expect(
            app.evaluate("window.__stokesScene.measure().b") is None,
            "Escape left the measurement in the scene",
        )
        return (
            f"{text} over {round(metres * 1000)} mm between "
            f"({round(points[0][0])}, {round(points[0][1])}) and "
            f"({round(points[1][0])}, {round(points[1][1])}); the scan found "
            f"{found.get('hits')} points on {found.get('rects')} parts, top gap "
            f"{round(found.get('gap') or 0)} px, label at y {round(box['y'] - canvas['y'])} "
            f"-> {round(zbox['y'] - canvas['y'])} after zoom"
        )

    run.step(
        "measuring two points shows a distance, the camera leaves it alone, Esc clears it",
        two_clicks_measure_and_escape_clears,
    )


# What the app says when a control does nothing. A sweep of every control treats these as failures:
# "not built yet" is exactly the stub class this check exists to find, and it is how Section view
# was
# found to be the last one standing.
DEAD_END_TOASTS = ("not built yet", "no engine attached", "unimplemented", "todo", "coming soon")
"""What the app says when a control does nothing at all — a silent stub, which is a defect."""

DOCUMENTED_STUBS = (
    # The Run button names its milestone, which is the honest form: "this arrives in M3" is a
    # statement the project can keep, and it is why `actions.ts` distinguishes a stub that names a
    # milestone from one that invents a promise. Reported, not failed.
    "no engine attached yet (milestone m3)",
)

# The stage controls, in BOTH presentations the workspace ships: `chips` is the default
# (`workspace/defaults.ts`), and `ribbon` replaces the chips with a row of tasks. A sweep that named
# only the ribbon found no stage buttons at all on the default workspace and reported "0 tasks
# answered" — the shape of a test that passes while checking nothing, which is worth as much as no
# test and more dangerous.
STAGE_CONTROLS = "[data-ws-item='stages'] button, .stage-chips button"

# The window's own controls, which a sweep of "every button" must NEVER click. They are in the DOM
# (the top bar renders Minimise/Restore/Close), and clicking them does what it says: the first run
# of the sweep below clicked Close, the DevTools socket died, and the run ended with
# "the page's DevTools socket closed" instead of a result. `host.close()` closes the user's window —
# a test that does that to somebody's session is worse than one that misses a button.
WINDOW_CONTROLS = (
    "[data-ws-item='window-controls'] button, .traffic-light, .win-close, "
    "[aria-label='Close'], [aria-label='Minimise'], [aria-label='Restore']"
)
STAGE_TASKS = ".ribbon button, [data-ws=ribbon] button, [data-ws-item='stages'] ~ * button"


def check_picks_respect_the_cut(run: Checks, app: App) -> None:
    """A pick must not reach through the cut (Claude's defect 7).

    Three things, each of which was broken in a different way and each invisible in a screenshot:

    * **A rake across the kept side finds only the bodies still drawn.** The scene's pick is asked
      at a row of points; with the cut on, the part the cut removed must not appear.
    * **The id buffer obeys the cut.** `IdPass` draws with its own `ShaderMaterial`, which receives
      `renderer.clippingPlanes` only through three's clipping chunks — and the pass is CACHED, so it
      must also be told the view changed. Both were wrong: the shader had no clipping code at all,
      and `setSection` never set `idDirty`, so even a correct shader drew a stale image.
    * **A pick follows a section change at once**, with no stale plane left from the previous one.

    It rides the scene's probe (`window.__stokesScene`), a read-only measurement hook: it can report
    what a pick returns but cannot set the section. The section is store state, so it is set the way
    the UI sets it.
    """

    def picks_follow_the_cut() -> str:
        app.evaluate("window.__stokes.getState().requestFit()")
        time.sleep(0.8)
        app.evaluate("window.__stokes.setState({ section: null })")
        time.sleep(0.8)

        def rake() -> dict[str, object]:
            """Ask the scene's pick across the viewport, and collect what it names."""
            return json.loads(
                app.evaluate(
                    """
                    (() => {
                      const c = document.querySelector('canvas');
                      const r = c.getBoundingClientRect();
                      // **Count distinct (patch, face) PAIRS, not hits.** A hit tally cannot
                      // see the cut: removing near faces and exposing interior ones changes which
                      // pixels answer without changing how many do, so a tally reports "unchanged"
                      // for a cut that is
                      // honoured. Measured on the cold plate: the tally was identical across
                      // off/on/off while the drawn frame was visibly cut. The pair set changes
                      // the face ids behind the cut are different faces.
                      const faces = new Set();
                      let hits = 0;
                      const seen = {};
                      for (let x = r.left + 40; x < r.right - 40; x += 20) {
                        for (let y = r.top + 40; y < r.bottom - 40; y += 20) {
                          const id = window.__stokesScene.idPick(x, y);
                          if (!id) continue;
                          hits += 1;
                          seen[id.patch] = (seen[id.patch] || 0) + 1;
                          faces.add(id.patch + '#' + id.face);
                        }
                      }
                      return JSON.stringify({ hits, faces: faces.size, patches: seen,
                        plane: window.__stokesScene.planeInfo() });
                    })()
                    """
                )
            )

        uncut = rake()
        run.expect(
            int(uncut["hits"]) > 0,  # type: ignore[arg-type]
            "the rake found nothing even with no section: the probe is not measuring the model",
        )

        # **Every offset comes from the model's own bounds.** The first version hardcoded
        # `offset: 0.04`, the cold plate's half-width: on the demo pipe that fell outside the model,
        # so
        # the cut removed nothing and the check failed while the feature worked. A check that names
        # one
        # model is a check of one model.
        bounds = json.loads(app.evaluate("JSON.stringify(window.__stokesScene.modelBounds())"))
        run.expect(bounds is not None, "the probe reports no model bounds to cut through")
        size = [bounds["max"][i] - bounds["min"][i] for i in range(3)]  # type: ignore[index]
        # **The thinnest axis faces the camera**, which is the one a cut has to be made along for
        # the
        # removed half to be the half the user sees. The middle cut is kept as a smoke test that a
        # real
        # mid-model section applies at all; the assertions below use planes OUTSIDE the model,
        # because a
        # mid-model cut cannot be told apart from no cut by counting (see below).
        index = size.index(min(size))
        axis = "xyz"[index]
        middle = (bounds["min"][index] + bounds["max"][index]) / 2  # type: ignore[index]
        app.evaluate(
            "window.__stokes.setState({ section: "
            f"{{ axis: {axis!r}, offset: {middle!r}, invert: false }} }})"
        )
        time.sleep(1.2)
        cut = rake()

        plane = cut["plane"]
        run.expect(
            isinstance(plane, list) and len(plane) == 1,  # type: ignore[arg-type]
            f"the renderer does not hold exactly one clip plane: {plane}",
        )
        # **A plane BEYOND the model, keeping the empty side, is the test that cannot be
        # confounded.**
        # Comparing a mid-model cut with no cut does NOT work: the cut removes the near half and
        # exposes
        # interior back faces, which answer the same number of pixels with the same face ids.
        # Measured:
        # middle {hits:678, faces:10} against off {hits:678, faces:10} — identical, while the frame
        # was
        # visibly cut. Pushing the plane past the model's far side leaves NOTHING on the kept side,
        # so a
        # pass that honours the clip must go empty and one that ignores it cannot.
        app.evaluate(
            "window.__stokes.setState({ section: "
            f"{{ axis: {axis!r}, offset: {bounds['max'][index] + max(size)!r}, invert: false }} }})"
        )
        time.sleep(1.2)
        empty = rake()
        run.expect(
            empty["hits"] == 0,
            "the id buffer reports geometry with the whole model clipped away: ignores "
            f"the clip planes ({empty['hits']} hits, {empty['faces']} distinct faces)",
        )

        # And the opposite side: nothing removed, so the uncut reading must come back exactly.
        app.evaluate(
            "window.__stokes.setState({ section: "
            f"{{ axis: {axis!r}, offset: {bounds['min'][index] - max(size)!r}, invert: false }} }})"
        )
        time.sleep(1.2)
        intact = rake()
        run.expect(
            intact["faces"] == uncut["faces"],
            "clipping the empty side removed geometry that was there: "
            f"{uncut['faces']} distinct faces -> {intact['faces']}",
        )

        # **Removing the section must restore the picks even when the cut REMOVED something.** The
        # previous version removed the section straight after cutting BEYOND the model — a cut that
        # removes nothing — so the cached id-buffer image happened to equal the correct one and the
        # check passed while removing the section left box/lasso reading a stale picture (Claude's
        # defect 8: the OFF branch never set `idDirty`).
        #
        # **The offset has to be chosen, not assumed.** Cutting the thinnest axis at its exact
        # middle
        # removes the near half and exposes the interior, and on the cold plate the distinct-FACE
        # count
        # comes out identical (measured: `10 faces either way`), so a stale buffer is
        # indistinguishable there — and the check SAID so rather than passing. Sweeping a few
        # offsets
        # for one that changes the count makes the measurement real; the assertion below fails
        # loudly
        # if none does, rather than testing nothing.
        removing = None
        for fraction in (0.3, 0.4, 0.6, 0.7, 0.25, 0.75):
            offset = bounds["min"][index] + size[index] * fraction  # type: ignore[index]
            app.evaluate(
                "window.__stokes.setState({ section: "
                f"{{ axis: {axis!r}, offset: {offset!r}, invert: false }} }})"
            )
            time.sleep(0.9)
            candidate = rake()
            if candidate["faces"] != uncut["faces"]:
                removing = candidate
                break
        run.expect(
            removing is not None,
            "no cut tried removed any geometry, so this check cannot tell a stale id buffer from a "
            f"fresh one ({uncut['faces']} faces at every offset)",
        )

        # Now remove it. `idDirty` must be set, or the box below reads the cut image: it would
        # report
        # the faces the cut left visible and omit every face the removed half was covering.
        app.evaluate("window.__stokes.setState({ section: null })")
        time.sleep(1.2)
        restored = rake()
        run.expect(
            restored["faces"] == uncut["faces"],
            "removing the section did not restore the picks: the id buffer kept the cut "
            f"image ({uncut['faces']} faces uncut, {restored['faces']} after removal)",
        )

    run.step("picks follow the section cut", picks_follow_the_cut)


# A second wheel step at the same spot, so the standing choice is not the nearest. `%d` and not
# an f-string: the braces below are JavaScript object literals, which an f-string would read as its
# own placeholders.
WHEEL_AGAIN_JS = """
(() => {
  const c = document.querySelector('canvas');
  const x = %d, y = %d;
  c.dispatchEvent(new WheelEvent('wheel', { clientX: x, clientY: y, deltaY: 120,
    altKey: true, bubbles: true, cancelable: true }));
  return JSON.stringify(window.__stokesScene.cycleStanding());
})()
"""

# A real pointer move swept across the model until the store reports a hover, then that hover text.
OVER_EDGE_JS = """
(async () => {
  const c = document.querySelector('canvas');
  const r = c.getBoundingClientRect();
  const s = window.__stokes;
  for (let y = r.top + 40; y < r.bottom - 40; y += 12) {
    for (let x = r.left + 40; x < r.right - 40; x += 12) {
      c.dispatchEvent(new PointerEvent('pointermove', { clientX: x, clientY: y, bubbles: true,
        pointerId: 1, isPrimary: true }));
      await new Promise((res) => setTimeout(res, 25));
      const h = s.getState().hoverInfo;
      if (h) return JSON.stringify(h);
    }
  }
  return null;
})()
"""


# `times` presses at one spot, close enough together that the scene counts a click RUN. Each pair is
# a
# full down/up; `POINTER_SLOP` in the scene allows them at identical coordinates.
PRESS_AT_JS = """
(async () => {
  const c = document.querySelector('canvas');
  const x = %d, y = %d, times = %d;
  const send = (type, buttons) => c.dispatchEvent(new PointerEvent(type, {
    clientX: x, clientY: y, button: 0, buttons: buttons, bubbles: true,
    pointerId: 1, isPrimary: true }));
  for (let i = 0; i < times; i++) {
    send('pointerdown', 1);
    send('pointerup', 0);
    await new Promise((r) => setTimeout(r, 40));
  }
})()
"""


# A real press and release, which is what a click is.
PRESS_AND_RELEASE_JS = """
(() => {
  const c = document.querySelector('canvas');
  const x = %d, y = %d;
  const opts = { clientX: x, clientY: y, button: 0, bubbles: true, pointerId: 1,
                 isPrimary: true };
  c.dispatchEvent(new PointerEvent('pointerdown', { ...opts, buttons: 1 }));
  c.dispatchEvent(new PointerEvent('pointerup', { ...opts, buttons: 0 }));
})()
"""


def check_topology_click_selects(run: Checks, app: App) -> None:
    """A click in Edge mode selects an edge, and the `Alt` + wheel choice is what it selects.

    **The defect this exists for.** `pointerdown` armed the marquee in every pick mode, and
    `pointerup`'s marquee branch returns — so in Edge or Point mode the click never reached the
    topology pick and selected NOTHING. Measured before the fix: the wheel stood on edge 15, a press
    and release at that same spot, and the pick came back empty while the wheel's choice was still
    standing. The box is a face-picking gesture (T-032), so it is not armed in the topology modes.

    It also covers T-058/T-060's `Alt` + wheel cycle, which had no caller before: the wheel walks
    the edges under the pointer and the click takes the one it stands on.
    """

    def edge_click_takes_the_wheel_choice() -> str:
        app.evaluate("window.__stokes.getState().requestFit()")
        time.sleep(1.0)
        app.evaluate(
            "window.__stokes.setState({ pickMode: 'edge', picked: "
            "{ bodies: [], faces: {}, edges: {}, vertices: {} } })"
        )
        time.sleep(1.2)

        # Find a spot with MORE THAN ONE edge candidate: a cycle over one cannot be observed, and a
        # click there would prove nothing about the wheel.
        found = json.loads(
            app.evaluate(
                """
                (() => {
                  const c = document.querySelector('canvas');
                  const r = c.getBoundingClientRect();
                  const sc = window.__stokesScene;
                  const move = (x, y) => c.dispatchEvent(new PointerEvent('pointermove',
                    { clientX: x, clientY: y, bubbles: true, pointerId: 1, isPrimary: true }));
                  const wheel = (x, y) => c.dispatchEvent(new WheelEvent('wheel', {
                    clientX: x, clientY: y, deltaY: 120, altKey: true, bubbles: true,
                    cancelable: true }));
                  for (let y = r.top + 40; y < r.bottom - 40; y += 8) {
                    for (let x = r.left + 40; x < r.right - 40; x += 8) {
                      move(x, y);
                      wheel(x, y);
                      const st = sc.cycleStanding();
                      if (st && st.candidates > 1) return JSON.stringify({ x, y, st });
                    }
                  }
                  return null;
                })()
                """
            )
        )
        run.expect(
            found is not None, "no spot on the model has more than one edge under the pointer"
        )
        run.expect(
            found["st"]["kind"] == "edge",
            f"the edge-mode cycle chose a {found['st']['kind']}, not an edge",
        )

        # Wheel once more, so the standing choice is NOT the nearest — otherwise a click that
        # ignored
        # the wheel entirely would still pass this check.
        # `%d` and not an f-string: the JS below is full of `{` braces, which an f-string reads as
        # its
        # own placeholders. (`UP031` wants format specifiers, but this is a JS template, not a
        # Python
        # format string — the braces are the reason, so the lint is answered here rather than
        # obeyed.)
        chosen = json.loads(app.evaluate(WHEEL_AGAIN_JS % (found["x"], found["y"])))
        run.expect(chosen is not None, "the wheel cleared its choice instead of moving it")

        # A REAL press and release at that spot.
        app.evaluate(PRESS_AND_RELEASE_JS % (found["x"], found["y"]))
        time.sleep(1.0)
        held = app.evaluate("JSON.stringify(window.__stokes.getState().picked.edges)")
        picked = json.loads(held)
        flat = [i for ids in picked.values() for i in ids]
        run.expect(
            len(flat) > 0,
            "a click in Edge mode selected nothing: the topology click never ran "
            f"(the wheel stood on {chosen})",
        )
        run.expect(
            chosen["id"] in flat,
            f"the click took edge {flat} but the wheel stood on {chosen['id']}",
        )
        return (
            f"the wheel chose edge {chosen['id']} of {chosen['candidates']} and the click took it"
        )

    run.step(
        "a click in Edge mode takes the edge the Alt+wheel chose",
        edge_click_takes_the_wheel_choice,
    )


def check_topology_escalation(run: Checks, app: App) -> None:
    """A double-click selects a wire and a triple-click the whole body (ADR-0022 §15.4, T-058).

    **A real press, not a probe assertion.** The escalation is a SELECTION, so counting clicks and
    reading the store is the only honest test: reading `topologyEscalation` would only prove the
    pure walk, which its own unit tests cover. What this adds is that the count reaches the store
    through the scene's click path at all — the same gap that hid the Edge-mode click bug.

    It asserts the clicks DIFFER in size, because on the cold plate most edges are a ONE-edge wire:
    a double click there is correct and uninteresting, so a check that took the first edge it found
    would pass whether or not the escalation worked.
    """

    def the_clicks_escalate() -> str:
        app.evaluate("window.__stokes.getState().requestFit()")
        time.sleep(1.0)
        app.evaluate(
            "window.__stokes.setState({ pickMode: 'edge', picked: "
            "{ bodies: [], faces: {}, edges: {}, vertices: {} } })"
        )
        time.sleep(1.2)

        # **Find an edge whose double-click selects MORE than itself.** Sweeping the probe first,
        # then
        # pressing for real, is the difference between choosing a meaningful case and taking
        # whatever is
        # under the first pixel.
        found = json.loads(
            app.evaluate(
                """
                (() => {
                  const c = document.querySelector('canvas');
                  const r = c.getBoundingClientRect();
                  const sc = window.__stokesScene;
                  const move = (x, y) => c.dispatchEvent(new PointerEvent('pointermove',
                    { clientX: x, clientY: y, bubbles: true, pointerId: 1, isPrimary: true }));
                  const wheel = (x, y) => c.dispatchEvent(new WheelEvent('wheel',
                    { clientX: x, clientY: y, deltaY: 120, altKey: true, bubbles: true,
                      cancelable: true }));
                  for (let y = r.top + 40; y < r.bottom - 40; y += 8) {
                    for (let x = r.left + 40; x < r.right - 40; x += 8) {
                      move(x, y);
                      wheel(x, y);
                      const st = sc.cycleStanding();
                      if (!st || st.kind !== 'edge') continue;
                      // The escalation returns `{kind, ids}`: the kind travels with the ids,
                      // because a vertex's double-click escalates to EDGES.
                      const wire = sc.topologyEscalation(st.part, st.kind, st.id, 2).ids;
                      const whole = sc.topologyEscalation(st.part, st.kind, st.id, 3).ids;
                      if (wire.length > 1 && whole.length > wire.length) {
                        return JSON.stringify({ x, y, edge: st.id, wire: wire.length,
                                                whole: whole.length });
                      }
                    }
                  }
                  return null;
                })()
                """
            )
        )
        run.expect(
            found is not None,
            "no edge on this model has a multi-edge wire, so the escalation cannot be measured",
        )

        def press(times: int) -> list[int]:
            """`times` real clicks at the spot, reading the store's edge pick after them."""
            # One `pointerdown`/`pointerup` pair, repeated inside the double-click interval: the
            # scene
            # counts
            # a run of clicks within `CLICK_INTERVAL`, exactly as a user's hand does.
            app.evaluate(PRESS_AT_JS % (found["x"], found["y"], times))
            time.sleep(0.6)
            picked = json.loads(
                app.evaluate("JSON.stringify(window.__stokes.getState().picked.edges)")
            )
            return [i for ids in picked.values() for i in ids]

        once = press(1)
        run.expect(len(once) == 1, f"a single click selected {len(once)} edges, not one: {once}")

        twice = press(2)
        run.expect(
            len(twice) == found["wire"],
            f"a double click selected {len(twice)} edges but the wire is {found['wire']}: {twice}",
        )

        thrice = press(3)
        run.expect(
            len(thrice) == found["whole"],
            f"a triple click selected {len(thrice)} edges but the body has {found['whole']}",
        )
        return (
            f"1 click took 1 edge, 2 took the {found['wire']}-edge wire, "
            f"3 took all {found['whole']}"
        )

    run.step("a double click takes the wire and a triple the body", the_clicks_escalate)


def check_point_mode_and_hover(run: Checks, app: App) -> None:
    """Point mode's click, its double-click and the hover, all with REAL pointer events.

    Claude asked for this specifically after the marquee fix: "check the Point mode click and the
    hover with a real press, not an assertion on a callback". Two things are checked that no unit
    test can:

    * **a press in Point mode selects a vertex**, which goes through a different branch from Edge
      mode;
    * **a double press selects the incident EDGES**, not vertices — the escalation changes kind,
      and getting that wrong put edge ids into the vertex map: measured, a double click on vertex 11
      left `edges={}` while its incident edges are `[13, 14, 15]`.
    * **the hover reports on a real pointer move and clears off the model**, which is store state
      fed by a scene callback — the callback existing is not the store being updated.
    """

    def point_mode_and_hover() -> str:
        app.evaluate("window.__stokes.getState().requestFit()")
        time.sleep(1.0)
        app.evaluate(
            "window.__stokes.setState({ pickMode: 'point', picked: "
            "{ bodies: [], faces: {}, edges: {}, vertices: {} } })"
        )
        time.sleep(1.2)

        # A vertex under the pointer, found by a real move plus the probe's own cycle: the wheel is
        # the
        # only way the probe can name what is under a pixel.
        found = json.loads(
            app.evaluate(
                """
                (() => {
                  const c = document.querySelector('canvas');
                  const r = c.getBoundingClientRect();
                  const sc = window.__stokesScene;
                  for (let y = r.top + 40; y < r.bottom - 40; y += 8) {
                    for (let x = r.left + 40; x < r.right - 40; x += 8) {
                      c.dispatchEvent(new PointerEvent('pointermove',
                        { clientX: x, clientY: y, bubbles: true, pointerId: 1, isPrimary: true }));
                      c.dispatchEvent(new WheelEvent('wheel', { clientX: x, clientY: y, deltaY: 120,
                        altKey: true, bubbles: true, cancelable: true }));
                      const st = sc.cycleStanding();
                      if (st && st.kind === 'vertex') {
                        const esc = sc.topologyEscalation(st.part, 'vertex', st.id, 2);
                        if (esc.ids.length > 1) {
                          return JSON.stringify({ x, y, id: st.id, part: st.part,
                                                  edges: esc.ids.length, kind: esc.kind });
                        }
                      }
                    }
                  }
                  return null;
                })()
                """
            )
        )
        run.expect(
            found is not None,
            "no vertex on this model has more than one incident edge, so this cannot be measured",
        )
        run.expect(
            found["kind"] == "edge",
            f"a vertex's double-click should escalate to EDGES, got kind={found['kind']}",
        )

        # One real press: the vertex itself.
        app.evaluate(PRESS_AT_JS % (found["x"], found["y"], 1))
        time.sleep(0.5)
        vertices = json.loads(
            app.evaluate("JSON.stringify(window.__stokes.getState().picked.vertices)")
        )
        held = [i for ids in vertices.values() for i in ids]
        run.expect(
            found["id"] in held,
            f"a press in Point mode selected {held}, not vertex {found['id']}",
        )

        # A double press: the incident edges, in the EDGE map.
        app.evaluate(PRESS_AT_JS % (found["x"], found["y"], 2))
        time.sleep(0.5)
        edges = json.loads(app.evaluate("JSON.stringify(window.__stokes.getState().picked.edges)"))
        got = [i for ids in edges.values() for i in ids]
        run.expect(
            len(got) == found["edges"],
            f"a double press selected {len(got)} edges, not the {found['edges']} incident on "
            f"vertex {found['id']}: {got}",
        )

        # The hover, on a real move over the model and then off it.
        app.evaluate("window.__stokes.setState({ pickMode: 'edge' })")
        time.sleep(0.8)
        hover = app.evaluate(OVER_EDGE_JS)
        run.expect(
            hover not in (None, "null", ""),
            "hovering an edge with a real pointer move reported nothing to the store",
        )
        app.evaluate(
            """
            (() => {
              const c = document.querySelector('canvas');
              const r = c.getBoundingClientRect();
              c.dispatchEvent(new PointerEvent('pointermove', { clientX: r.left + 4,
                clientY: r.top + 4, bubbles: true, pointerId: 1, isPrimary: true }));
            })()
            """
        )
        time.sleep(0.5)
        cleared = app.evaluate("JSON.stringify(window.__stokes.getState().hoverInfo)")
        run.expect(
            cleared in ("null", ""),
            f"the hover did not clear when the pointer left the model: {cleared}",
        )
        return (
            f"a press took vertex {found['id']}, a double press its "
            f"{found['edges']} incident edges, and the hover reported then cleared"
        )

    run.step("Point mode selects, escalates and hovers on real presses", point_mode_and_hover)


def check_section_handle_drags(run: Checks, app: App) -> None:
    """The section's handle on the model: it is drawn, and dragging it moves the cut.

    Direct manipulation is the thing a screenshot cannot confirm — a handle can render perfectly
    and still not be grabbable, which is exactly what happened: the handle was on a layer of its own
    to keep it out of the other passes, and `THREE.Raycaster` has a layer mask of its own that
    defaults to layer 0, so the raycast found nothing while the cone looked solid on screen. This
    check grabs it and asserts the cut moved.
    """

    def handle_moves_the_cut() -> str:
        # A section, and the pick mode is irrelevant here: the handle belongs to the section, not to
        # the pick. The camera is framed so the model is where the handle's projection expects it.
        app.evaluate("window.__stokes.getState().requestFit()")
        time.sleep(0.6)
        app.evaluate(
            "window.__stokes.setState({ section: { axis: 'z', offset: 0, invert: false } })"
        )
        time.sleep(1.0)

        # **The handle is located by its PROJECTED POSITION, not by its colour.** The colour scan
        # below found it when the theme's accent was orange; the accent is now teal, so the scan
        # reported "the handle is not drawn anywhere in the viewport" for a handle that is drawn,
        # visible and on-canvas. Measured: `{x:746, y:469, visible:true, inFront:true}`, on canvas,
        # painted teal — while the scan looked for `red > 180, 50 < green < 140, blue < 90`.
        # A check that hardcodes a theme colour is a check of that theme.
        #
        # The colour scan is kept BELOW as a corroboration, because "the projection says it is
        # there" and "something is painted there" are different claims: the first is what the scene
        # asserts about itself, the second is what the compositor shows.
        import base64
        import io

        raw = app.dev.call("Page.captureScreenshot", {"format": "png"}).get("data")
        run.expect(isinstance(raw, str), "the screenshot for the handle came back empty")
        from PIL import Image

        image = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
        scale = image.width / float(app.box("canvas")["width"])
        canvas = app.box("canvas")
        left = int(canvas["x"] * scale)
        top = int(canvas["y"] * scale)
        right = int((canvas["x"] + canvas["width"]) * scale)
        bottom = int((canvas["y"] + canvas["height"]) * scale)
        # **The densest cluster, not the centroid of every orange pixel.** The accent colour is used
        # by the UI as well (the active Section button, the tree's add button), and those pixels sit
        # in
        # the same rectangle as the viewport; averaging them all put the "handle" at (931, 470) when
        # it
        # was at (738, 423), and the drag then missed it. A grid of buckets with the fullest one
        # taken
        # finds the solid: the only large orange AREA on the canvas.
        #
        # **And the canvas rectangle is not enough**, which the corroboration check above proved:
        # the
        # Properties panel is drawn OVER the canvas's right-hand side, so a scan bounded only by the
        # canvas rect found the panel's own text at (1186, 199) — 48 px of it — while the handle was
        # projected at (718, 456). The projected position is the authority and the scan corroborates
        # it,
        # so the scan now asks `elementFromPoint` whether the pixel really belongs to the canvas,
        # the
        # same test `check_box_select` uses to find empty space.
        free = set(
            json.loads(
                app.evaluate(
                    """
                    (() => {
                      const c = document.querySelector('canvas');
                      const r = c.getBoundingClientRect();
                      const out = [];
                      for (let y = Math.ceil(r.top); y < r.bottom; y += 3) {
                        for (let x = Math.ceil(r.left); x < r.right; x += 3) {
                          const el = document.elementFromPoint(x, y);
                          if (el === c || c.contains(el)) out.push(x + ',' + y);
                        }
                      }
                      return JSON.stringify(out);
                    })()
                    """
                )
            )
        )
        buckets: dict[tuple[int, int], list[int]] = {}
        cell = 24
        for y in range(top, min(bottom, image.height), 3):
            for x in range(left, min(right, image.width), 3):
                if f"{int(x / scale)},{int(y / scale)}" not in free:
                    continue
                red, green, blue = image.getpixel((x, y))
                if red > 180 and 50 < green < 140 and blue < 90:
                    key = ((x - left) // cell, (y - top) // cell)
                    found_cell = buckets.setdefault(key, [0, 0, 0])
                    found_cell[0] += x
                    found_cell[1] += y
                    found_cell[2] += 1
        if not buckets:
            found = None
        else:
            best = max(buckets.values(), key=lambda c: c[2])
            # A solid the size of the handle leaves far more pixels in its bucket than a UI icon
            # does; the floor keeps the check from grabbing a toolbar button instead.
            found = (
                None
                if best[2] < 30
                else {
                    "x": best[0] / best[2] / scale,
                    "y": best[1] / best[2] / scale,
                    "pixels": best[2],
                }
            )
        # The projection is the authority on WHERE; the colour scan only corroborates that pixels
        # are painted there. `found` is the scanned cluster, `where` the projected position.
        where = json.loads(app.evaluate("JSON.stringify(window.__stokesScene.handleScreen())"))
        run.expect(
            where is not None and where["visible"],
            f"the scene reports no visible section handle with a section on: {where}",
        )
        run.expect(
            where["inFront"],
            f"the section handle is outside the camera's near/far range: {where}",
        )
        px, py = where["x"] * scale, where["y"] * scale  # type: ignore[index]
        inside = left <= px <= right and top <= py <= bottom
        run.expect(inside, f"the section handle projects off the canvas: {where}")
        # The painted cluster, when the scan finds one, must be near the projected position: that is
        # the claim the projection alone cannot make. Skipped when the theme's accent is not the
        # scanned hue, which is not a failure.
        if found is not None:
            far = abs(found["x"] - where["x"]) > 40 or abs(found["y"] - where["y"]) > 40
            run.expect(not far, f"the handle is projected at {where} but painted at {found}")
        centre = {"x": where["x"], "y": where["y"], "pixels": found["pixels"] if found else 0}

        before = app.evaluate("JSON.stringify(window.__stokes.getState().section)")
        # Drag along the pipe's screen direction (+Z runs to the lower right in the default view).
        app.drag((centre["x"], centre["y"]), (centre["x"] + 150, centre["y"] + 80), steps=12)
        time.sleep(0.5)
        after = app.evaluate("JSON.stringify(window.__stokes.getState().section)")
        was = json.loads(before)
        now = json.loads(after)
        run.expect(
            abs(float(now["offset"]) - float(was["offset"])) > 0.005,
            f"dragging the handle did not move the cut: {was['offset']} -> {now['offset']}",
        )
        return (
            f"the cut moved from {was['offset']:.3f} to "
            f"{now['offset']:.3f} m by dragging the handle"
        )

    run.step("the section handle is drawn and dragging it moves the cut", handle_moves_the_cut)


def check_menu_primitive(run: Checks, app: App) -> None:
    """The menu primitive: a right-click menu opens AT the pointer and goes away on its own.

    The tree used to fake a point with a zero-size span; `Popover` now takes `at`. What a person
    sees is checked here, because the node has no DOM:

    * the menu's left edge is where the pointer was; Escape closes it and refocuses the tree;
    * a wheel anywhere outside it closes it (a wheel zoom over the canvas moves the model under a
      point menu and fires no scroll event);
    * the arrow keys land on a disabled row too, so its reason can be read (the menu pattern).
    """
    menu = "[role='menu'][aria-label='Node actions']"

    def menu_open() -> bool:
        return bool(app.evaluate(f"Boolean(document.querySelector({json.dumps(menu)}))"))

    def rows() -> list[dict[str, float]]:
        found = app.evaluate(
            """
            JSON.stringify(Array.from(document.querySelectorAll("[role='tree'] [role='treeitem']"))
              .map((el) => { const r = el.getBoundingClientRect();
                return {x: r.x, y: r.y, width: r.width, height: r.height}; })
              .filter((r) => r.width > 40 && r.height > 8
                && r.y >= 0 && r.y + r.height <= window.innerHeight))
            """
        )
        return json.loads(found) if found else []

    def right_click(x: float, y: float) -> None:
        app.dev.mouse("mouseMoved", x, y, buttons=0)
        app.dev.mouse("mousePressed", x, y, buttons=2, button="right")
        app.dev.mouse("mouseReleased", x, y, buttons=0, button="right")
        time.sleep(0.4)

    def close_with_escape() -> None:
        if menu_open():
            app.press("Escape", "Escape", 27)

    def opens_at_the_pointer_and_escape_returns_to_the_tree() -> str:
        close_with_escape()
        found = rows()
        run.expect(len(found) > 0, "no tree row is on screen to right-click")
        row = found[0]
        x, y = row["x"] + min(60.0, row["width"] / 2), row["y"] + row["height"] / 2
        right_click(x, y)
        run.expect(menu_open(), "right-clicking a tree row did not open the Node actions menu")
        left = app.box(menu)["x"]
        run.expect(
            abs(left - x) <= 12,
            f"the menu's left edge is at {left:.0f} px, not within 12 px of the click at {x:.0f}",
        )
        app.press("Escape", "Escape", 27)
        run.expect(not menu_open(), "Escape did not close the menu")
        focused = app.evaluate(
            "document.activeElement ? document.activeElement.getAttribute('role') : null"
        )
        run.expect(
            focused == "tree",
            f"after Escape the focus is on {focused!r}, not on the tree",
        )
        return (
            f"opened {abs(left - x):.0f} px from the click; Escape closed it and refocused the tree"
        )

    def a_wheel_closes_it() -> str:
        close_with_escape()
        found = rows()
        run.expect(len(found) > 0, "no tree row is on screen to right-click")
        row = found[0]
        x, y = row["x"] + min(60.0, row["width"] / 2), row["y"] + row["height"] / 2
        right_click(x, y)
        run.expect(menu_open(), "right-clicking a tree row did not open the Node actions menu")
        app.wheel(x, y, 120)
        run.expect(not menu_open(), "a wheel over the tree left the point menu open")
        return "a wheel over the tree closed the point menu"

    def arrows_reach_a_disabled_row() -> str:
        # A fixed group of the project has Rename and Delete disabled; Delete is the last row.
        for row in rows():
            close_with_escape()
            # Selecting the row focuses the tree; Shift+F10 opens its menu with the keyboard, so the
            # menu takes the focus the way a keyboard user gets it.
            app.click(row["x"] + min(60.0, row["width"] / 2), row["y"] + row["height"] / 2)
            app.press("F10", "F10", 121, modifiers=8)
            if not menu_open():
                continue
            disabled = app.evaluate(
                f"document.querySelectorAll({json.dumps(menu + ' [aria-disabled=true]')}).length"
            )
            if not disabled:
                continue
            app.press("End", "End", 35)
            landed = app.evaluate(
                "(() => { const a = document.activeElement; if (!a) return null;"
                " return {label: a.textContent, disabled: a.getAttribute('aria-disabled')}; })()"
            )
            run.expect(
                isinstance(landed, dict) and landed.get("disabled") == "true",
                f"End landed on {landed!r}, not on the last row, which is disabled",
            )
            app.press("Enter", "Enter", 13)
            run.expect(menu_open(), "Enter on a disabled row closed the menu or ran it")
            close_with_escape()
            return f"End reached the disabled row {landed['label']!r}; Enter on it did nothing"
        raise Failure("no tree row offered a menu with a disabled row to test")

    run.step(
        "a right-click menu opens at the pointer; Escape closes it and refocuses the tree",
        opens_at_the_pointer_and_escape_returns_to_the_tree,
    )
    run.step("a wheel outside closes a point menu", a_wheel_closes_it)
    run.step(
        "the arrow keys reach a disabled row and Enter on it does nothing",
        arrows_reach_a_disabled_row,
    )


#: The accessible name of the context menu while its first list shows (`ContextMenu.tsx` passes
#: `label="Viewport"` for every target). A nested list takes its parent row's label ("Display"),
#: so a check that must tell "a viewport menu" from the tree's "Node actions" names the top level.
VIEWPORT_MENU = "[role='menu'][aria-label='Viewport']"

#: WCAG contrast of one menu row against what the eye sees behind its text, as a page function.
#:
#: * the text colour has its alpha multiplied by the `opacity` of every ancestor: a disabled row is
#:   `opacity: .45`, which computed `color` does not show;
#: * the surface is `.popover`'s `--menu-bg` (it may be translucent) over the viewport's
#:   backdrop, the gradient `--vp-top` to `--vp-bottom` (the menu opens over the canvas, not over
#:   `body`), taken at the worse end of the gradient; a row with a fill of its own (hover, focus)
#:   adds it on top.
#:
#: **Not modelled:** the popover's `backdrop-filter: blur()` over a bright model. That is a limit of
#: this measurement, reported in the step's detail, and not something to approximate here.
CONTRAST_JS = """
(selector) => {
  const probe = document.createElement('canvas');
  probe.width = probe.height = 1;
  const g = probe.getContext('2d', {willReadFrequently: true});
  const rgba = (css) => {
    const m = css.match(/^rgba?\\(([^)]+)\\)$/);
    if (m) {
      const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number);
      return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
    }
    g.clearRect(0, 0, 1, 1);
    g.fillStyle = '#000';
    g.fillStyle = css;
    g.fillRect(0, 0, 1, 1);
    const d = g.getImageData(0, 0, 1, 1).data;
    return {r: d[0], g: d[1], b: d[2], a: d[3] / 255};
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const lin = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const ratio = (a, b) => {
    const x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const token = (name) => {
    const el = document.createElement('div');
    el.style.color = 'var(' + name + ')';
    document.body.appendChild(el);
    const c = getComputedStyle(el).color;
    el.remove();
    return rgba(c);
  };
  const row = document.querySelector(selector);
  if (!row) return null;
  let opacity = 1;
  for (let el = row; el; el = el.parentElement) opacity *= parseFloat(getComputedStyle(el).opacity);
  const fg = rgba(getComputedStyle(row).color);
  fg.a *= opacity;
  const surface = rgba(getComputedStyle(row.closest('.popover')).backgroundColor);
  const fill = rgba(getComputedStyle(row).backgroundColor);
  let worst = Infinity;
  for (const name of ['--vp-top', '--vp-bottom']) {
    let behind = over(surface, token(name));
    if (fill.a > 0) behind = over(fill, behind);
    worst = Math.min(worst, ratio(over(fg, behind), behind));
  }
  const label = (row.querySelector('.menu-text') || row).textContent.trim();
  return {ratio: Math.round(worst * 100) / 100, opacity, label};
}
"""


def check_context_menu(run: Checks, app: App) -> None:
    """The viewport's context menu under real presses, keys and all four theme families (T-173).

    T-172 put a menu on a right click in the viewport. Three of its rules cannot be seen in a
    screenshot, so they are driven here with **trusted** DevTools input, on the built-in demo
    project (no ``--project``):

    * a right *drag* pans and opens nothing (the right button pans in every mouse scheme);
    * the keyboard opens it, and the focus is in it;
    * it can be read in every theme.

    Steps 1 to 12 follow the task brief. Where the tree differs from the brief the step says so:
    ``Shift+F10`` has no default chord for the viewport (it is the tree's ``tree.menu``), so the
    keyboard route is the palette action ``context.menu``; the menu's accessible name is
    ``Viewport`` for every target; "Show all" is "Show every hidden body again" in the table.
    """
    if app.session is None:
        print("  skip the context menu checks: an attached window has no session to script")
        return
    state = "window.__stokes.getState()"
    scene = "window.__stokesScene"
    canvas = ".viewport-bg canvas"
    any_menu = "[role='menu']"
    row_selector = "[role^='menuitem']"
    # What the page held before, restored at the end: the checks after these start from it.
    before = app.evaluate(
        "(() => { const s = window.__stokes.getState();"
        " return {theme: window.__workspace.getState().document.theme, viewMode: s.viewMode,"
        " navMode: s.navMode, pickMode: s.pickMode, section: s.section}; })()"
    )

    def js(expression: str) -> Any:
        return app.evaluate(expression)

    def present(selector: str) -> bool:
        return bool(js(f"Boolean(document.querySelector({json.dumps(selector)}))"))

    def wait_for(condition: Any, what: str, seconds: float = 12.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.15)
        raise Failure(f"timed out waiting for {what}")

    def picked() -> dict[str, Any]:
        found = js(f"JSON.stringify({state}.picked)")
        return dict(json.loads(str(found)))

    def right_click(x: float, y: float, *, move: bool = True) -> None:
        """A trusted right press and release; ``move=False`` presses where the pointer is."""
        if move:
            app.dev.mouse("mouseMoved", x, y, buttons=0)
        app.dev.mouse("mousePressed", x, y, buttons=2, button="right")
        app.dev.mouse("mouseReleased", x, y, buttons=0, button="right")
        time.sleep(0.5)

    def close_menus() -> None:
        for _ in range(3):
            if not present(any_menu):
                return
            app.press("Escape", "Escape", 27)

    def body_point(only: str | None = None) -> dict[str, Any]:
        """A canvas point (client pixels) on the first shown part that the id buffer names.

        ``partBounds()`` is canvas-local and a rectangle: the centre of a pipe's rectangle may be
        empty, so the points of the rectangle are tried from the centre outwards until the id
        buffer says the part is there and nothing covers the canvas. ``only`` names the part.
        """
        found = js(
            "(() => { const b = " + scene + ".partBounds();"
            f" const c = document.querySelector({json.dumps(canvas)});"
            f" const only = {json.dumps(only)};"
            " const r = c.getBoundingClientRect();"
            " for (const [name, q] of Object.entries(b)) {"
            "   if (only !== null && name !== only) continue;"
            "   const cx = (q.x0 + q.x1) / 2, cy = (q.y0 + q.y1) / 2; const pts = [];"
            "   for (let y = q.y0; y <= q.y1; y += 6) for (let x = q.x0; x <= q.x1; x += 6)"
            "     pts.push([x, y]);"
            "   const d = (p) => Math.hypot(p[0] - cx, p[1] - cy);"
            "   pts.sort((p, q2) => d(p) - d(q2));"
            "   for (const [x, y] of pts) { const px = x + r.left, py = y + r.top;"
            "     const hit = " + scene + ".idPick(px, py);"
            "     if (hit && hit.patch === name && document.elementFromPoint(px, py) === c)"
            "       return {x: px, y: py, name, part: hit.part}; } }"
            " return null; })()"
        )
        if not isinstance(found, dict):
            raise Failure("no point of any shown part is hit by the id buffer: nothing to click on")
        return dict(found)

    #: What would keep a right-click from opening the menu, for a failure message.
    why_not = (
        "(() => { const s = window.__stokes.getState();"
        " const e = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);"
        " return {modal: Boolean(document.querySelector(\"[aria-modal='true']\")),"
        " seedPick: Boolean(s.seedPick), paintMode: s.paintMode, measureMode: s.measureMode,"
        " pickMode: s.pickMode, menus: document.querySelectorAll(\"[role='menu']\").length,"
        " centre: e ? e.tagName + '.' + e.className : null}; })()"
    )

    def pick_a_body() -> dict[str, Any]:
        """Left-click the first part a click can pick, and return its point.

        Which part that is depends on what the checks before left on the screen: the first shown
        part may be one a click does not take (a region drawn behind the others).
        """
        names = js(f"Object.keys({scene}.partBounds())")
        tried: list[str] = []
        for name in names if isinstance(names, list) else []:
            try:
                point = body_point(str(name))
            except Failure:
                continue
            app.click(float(point["x"]), float(point["y"]))
            if picked().get("bodies"):
                return point
            tried.append(str(name))
        raise Failure(f"a left click picked nothing on any shown part ({tried})")

    def empty_point() -> tuple[float, float]:
        """A canvas point 40 px or more outside every part rectangle that nothing covers."""
        found = js(
            "(() => { const b = Object.values(" + scene + ".partBounds());"
            f" const c = document.querySelector({json.dumps(canvas)});"
            " const r = c.getBoundingClientRect(); const gap = 40;"
            " const near = (x, y) => b.some((q) =>"
            "   x >= q.x0 + r.left - gap && x <= q.x1 + r.left + gap"
            "   && y >= q.y0 + r.top - gap && y <= q.y1 + r.top + gap);"
            " for (let y = r.bottom - 60; y > r.top + 60; y -= 12)"
            "   for (let x = r.left + 80; x < r.right - 80; x += 12)"
            "     if (!near(x, y) && document.elementFromPoint(x, y) === c"
            "       && " + scene + ".idPick(x, y) === null) return {x, y};"
            " return null; })()"
        )
        if not isinstance(found, dict):
            raise Failure("no point of the canvas is 40 px clear of every part")
        return float(found["x"]), float(found["y"])

    def box_of(name: str) -> dict[str, float]:
        found = js(f"{scene}.partBounds()[{json.dumps(name)}]")
        if not isinstance(found, dict):
            raise Failure(f"{name!r} has no screen rectangle")
        return {k: float(v) for k, v in found.items()}

    def row_label(selector: str) -> str:
        return str(
            js(
                f"(document.querySelector({json.dumps(selector)})?.querySelector('.menu-text')"
                "?.textContent || '').trim()"
            )
        )

    def labels() -> list[str]:
        found = js(
            f"[...document.querySelectorAll({json.dumps(any_menu + ' ' + row_selector)})]"
            ".map((e) => (e.querySelector('.menu-text')?.textContent || '').trim())"
        )
        return [str(x) for x in found] if isinstance(found, list) else []

    def press_row(label: str) -> None:
        """A real click on the first menu row whose text starts with ``label``."""
        js(
            "document.querySelector('[data-harness-press]')?.removeAttribute('data-harness-press');"
            f"[...document.querySelectorAll({json.dumps(any_menu + ' ' + row_selector)})]"
            f".find((e) => (e.textContent || '').trim().startsWith({json.dumps(label)}))"
            "?.setAttribute('data-harness-press', '1')"
        )
        if not present("[data-harness-press='1']"):
            raise Failure(f"no menu row starts with {label!r}: the rows are {labels()}")
        app.click(*app.centre("[data-harness-press='1']"))

    def focus_state() -> dict[str, Any]:
        found = js(
            f"(() => {{ const m = document.querySelector({json.dumps(any_menu)});"
            " if (!m) return null;"
            " const first = m.querySelector(\"[role^='menuitem'][aria-checked='true'],"
            " [role^='menuitem']:not([aria-disabled='true'])\");"
            " const rows = [...m.querySelectorAll(\"[role^='menuitem']\")];"
            " return {inside: m.contains(document.activeElement),"
            " onFirst: document.activeElement === first,"
            " at: rows.indexOf(document.activeElement), first: rows.indexOf(first),"
            " active: (document.activeElement?.querySelector?.('.menu-text')?.textContent"
            " || '').trim(),"
            " rows: rows.length}; })()"
        )
        if not isinstance(found, dict):
            raise Failure("no menu is open")
        return dict(found)

    def canvas_has_the_focus() -> bool:
        return bool(js(f"document.activeElement === document.querySelector({json.dumps(canvas)})"))

    tree_scroller = "[aria-label='Simulation tree']"

    def scroll_tree(to: str) -> None:
        """The tree's list to its top or bottom: where the picked part's row is, or is not."""
        js(
            f"(() => {{ const t = document.querySelector({json.dumps(tree_scroller)});"
            f" if (t) t.scrollTop = {'0' if to == 'top' else 't.scrollHeight'}; }})()"
        )
        time.sleep(0.4)

    def settle_view() -> None:
        # The state these steps need, stated here: the section check before this one leaves a cut
        # on, which clips the model away from under the pointer. The tree is at its top, where the
        # parts' rows are: picking a part reveals its row, and a tree that has to scroll to do it
        # closes the menu (the scroll closes any point menu; see the last step).
        js(
            "window.__stokes.setState({section: null, hiddenPatches: [], isolatedPatch: null});"
            f" {state}.setPickMode('body'); {state}.clearPick(); {state}.requestFit()"
        )
        scroll_tree("top")
        time.sleep(1.2)

    # The demo's STEP import finishes some seconds after the window is up, longer on a first run.
    def parts_on_screen() -> bool:
        js(f"{state}.requestFit()")
        return bool(js(f"Object.keys({scene}.partBounds()).length"))

    wait_for(parts_on_screen, "the demo's parts to be on screen", 90.0)

    # ---- 1
    def right_click_on_a_body_opens_the_menu_and_selects_it() -> str:
        settle_view()
        point = body_point()
        x, y = float(point["x"]), float(point["y"])
        # A zoom towards the point leaves it on the part and dirties the id buffer, so the first
        # right-click below does a full id pass. The press is made without moving the pointer: a
        # move would run the hover probe, which would do that pass before the click.
        app.wheel(x, y, -40)
        time.sleep(0.6)
        right_click(x, y, move=False)
        first_ms = float(js(f"{scene}.idReadMs"))
        run.expect(
            present(VIEWPORT_MENU),
            f"right-clicking a body did not open the Viewport menu (the page holds: {js(why_not)})",
        )
        focus = focus_state()
        run.expect(
            bool(focus["onFirst"]),
            f"the first focusable item does not have the focus: {focus}",
        )
        held = picked()
        run.expect(
            point["part"] in held.get("bodies", []),
            f"the right-click did not select {point['part']!r}: the pick is {held}",
        )
        count = len(labels())
        app.shot("context-menu-body")
        close_menus()
        right_click(x, y)
        second_ms = float(js(f"{scene}.idReadMs"))
        run.expect(
            present(VIEWPORT_MENU), "a second right-click on the same body did not reopen it"
        )
        run.expect(
            picked() == held, "a right-click on a body that is already picked changed the pick"
        )
        close_menus()
        return (
            f"demo project; right-click on {point['name']!r} opened 'Viewport' with {count} rows, "
            f"focus on {focus['active']!r}, picked {point['part']!r}; idReadMs first "
            f"{first_ms:.2f} ms (id buffer dirty after a zoom), second {second_ms:.2f} ms"
        )

    # ---- 2
    def escape_closes_it_and_the_canvas_has_the_focus() -> str:
        point = body_point()
        right_click(float(point["x"]), float(point["y"]))
        run.expect(present(VIEWPORT_MENU), "the menu did not open to be closed")
        held = picked()
        app.press("Escape", "Escape", 27)
        run.expect(not present(any_menu), "Escape did not close the menu")
        run.expect(
            canvas_has_the_focus(),
            "after Escape the focus is on "
            f"{js('document.activeElement?.tagName + "." + document.activeElement?.className')!r}"
            ", not on the canvas",
        )
        run.expect(picked() == held, "Escape also cleared the selection: it was not consumed")
        return "Escape closed the menu, the canvas has the focus and the pick stayed"

    # ---- 3
    def a_right_drag_pans_and_opens_nothing() -> str:
        js(f"{state}.setNavMode('cad')")
        time.sleep(0.2)
        buttons = js(f"{scene}.navButtons()")
        run.expect(
            isinstance(buttons, dict) and buttons.get("RIGHT") == "PAN",
            f"under the CAD scheme the right button is {buttons!r}, not PAN",
        )
        point = body_point()
        x, y = float(point["x"]), float(point["y"])
        name = str(point["name"])
        # The control that gives "opens nothing" its meaning: a right *click* on this very spot
        # does open the menu. Without it the step passes on a tree that has no menu at all.
        right_click(x, y)
        run.expect(present(VIEWPORT_MENU), "a right click on the part (the control) opened no menu")
        close_menus()
        held = picked()
        start = box_of(name)
        width = float(js("window.innerWidth"))
        dx = 80.0 if x + 80.0 < width - 20.0 else -80.0
        app.dev.mouse("mouseMoved", x, y, buttons=0)
        app.dev.mouse("mousePressed", x, y, buttons=2, button="right")
        for i in range(1, 7):
            app.dev.mouse("mouseMoved", x + dx * i / 6, y, buttons=2, button="right")
        app.dev.mouse("mouseReleased", x + dx, y, buttons=0, button="right")
        time.sleep(0.6)
        run.expect(not present(any_menu), "a right drag opened a menu")
        end = box_of(name)
        moved = abs((end["x0"] + end["x1"]) / 2 - (start["x0"] + start["x1"]) / 2)
        run.expect(
            moved > 20.0, f"the right drag moved {name!r} by {moved:.1f} px, not more than 20"
        )
        run.expect(picked() == held, "a right drag changed the pick")
        js(f"{state}.requestFit()")
        time.sleep(1.0)
        return (
            f"a right click there opens the menu, but an {abs(dx):.0f} px right drag moved "
            f"{name!r} by {moved:.0f} px and opened none"
        )

    # ---- 4
    def empty_space_opens_the_view_menu_and_keeps_the_pick() -> str:
        settle_view()
        pick_a_body()
        held = picked()
        ex, ey = empty_point()
        right_click(ex, ey)
        run.expect(present(VIEWPORT_MENU), "right-clicking empty space opened no menu")
        rows = labels()
        run.expect(
            "Orthographic" in rows and "Display" in rows,
            f"the menu is not the empty-space (view) menu: {rows}",
        )
        run.expect(picked() == held, "right-clicking empty space changed the pick")
        app.shot("context-menu-empty")
        close_menus()
        return (
            f"empty space at ({ex:.0f}, {ey:.0f}) opened the view menu ({len(rows)} rows); "
            "the pick stayed"
        )

    # ---- 5
    def display_wireframe_changes_the_view_mode() -> str:
        ex, ey = empty_point()
        right_click(ex, ey)
        run.expect(present(VIEWPORT_MENU), "the view menu did not open")
        try:
            press_row("Display")
            wait_for(lambda: "Wireframe" in labels(), "the Display list to show Wireframe")
            press_row("Wireframe")
            wait_for(lambda: not present(any_menu), "the menu to close after the choice")
            mode = js(f"{state}.viewMode")
            run.expect(mode == "wireframe", f"Display > Wireframe left the view mode at {mode!r}")
        finally:
            js(f"{state}.setViewMode('shaded')")
            close_menus()
        return "Display > Wireframe set the view mode to wireframe; put back to shaded"

    # ---- 6
    def the_keyboard_opens_it_and_arrows_walk_it() -> str:
        pick_a_body()
        run.expect(canvas_has_the_focus(), "a click on the canvas did not give it the focus")
        # `Shift+F10` is the tree's `tree.menu`; `context.menu` has no default chord in this tree.
        # The route a keyboard user has is the palette (Ctrl+K), which runs the same action.
        app.press("k", "KeyK", 75, modifiers=2)
        wait_for(lambda: present("[role='dialog'][aria-label='Command palette']"), "the palette")
        app.dev.call("Input.insertText", {"text": "Open the context menu"})
        time.sleep(0.4)
        app.press("Enter", "Enter", 13)
        wait_for(lambda: present(VIEWPORT_MENU), "the context menu to open from the palette")
        time.sleep(0.4)
        start = focus_state()
        run.expect(bool(start["inside"]), f"the menu opened without taking the focus: {start}")
        app.press("ArrowDown", "ArrowDown", 40)
        app.press("ArrowDown", "ArrowDown", 40)
        end = focus_state()
        run.expect(
            end["at"] == start["at"] + 2,
            f"two ArrowDown moved the focus from row {start['at']} to row {end['at']}, "
            "not two rows on (disabled rows take the focus too)",
        )
        landed = end["active"]
        app.press("Escape", "Escape", 27)
        run.expect(not present(any_menu), "Escape did not close the keyboard's menu")
        return (
            f"Ctrl+K, 'Open the context menu' opened it with {start['rows']} rows, focus on row "
            f"{start['at']} ({start['active']!r}); two ArrowDown reached row {end['at']} "
            f"({landed!r}); Escape closed it. No default chord: Shift+F10 is the tree's"
        )

    # ---- 7
    def the_tree_row_menu_is_unchanged() -> str:
        close_menus()
        rows = js(
            "(() => Array.from(document.querySelectorAll(\"[role='tree'] [role='treeitem']\"))"
            ".map((el) => { const r = el.getBoundingClientRect();"
            " return {x: r.x, y: r.y, width: r.width, height: r.height}; })"
            ".filter((r) => r.width > 40 && r.height > 8 && r.y >= 0"
            " && r.y + r.height <= window.innerHeight))()"
        )
        run.expect(isinstance(rows, list) and len(rows) > 0, "no tree row is on screen")
        row = rows[0]
        right_click(row["x"] + min(60.0, row["width"] / 2), row["y"] + row["height"] / 2)
        run.expect(
            present("[role='menu'][aria-label='Node actions']"),
            "right-clicking a tree row did not open 'Node actions'",
        )
        run.expect(not present(VIEWPORT_MENU), "a tree row opened the viewport menu as well")
        close_menus()
        return "a tree row right-click opens 'Node actions' and no Viewport menu"

    # ---- 8
    def a_disabled_entry_explains_itself() -> str:
        js("window.__stokes.setState({hiddenPatches: [], isolatedPatch: null})")
        ex, ey = empty_point()
        right_click(ex, ey)
        run.expect(present(VIEWPORT_MENU), "the view menu did not open")
        # In the action table `view.showAll` is "Show every hidden body again".
        js(
            "document.querySelector('[data-harness-press]')?.removeAttribute('data-harness-press');"
            f"[...document.querySelectorAll({json.dumps(any_menu + ' ' + row_selector)})]"
            ".find((e) => (e.textContent || '').trim().startsWith('Show every hidden'))"
            "?.setAttribute('data-harness-press', '1')"
        )
        run.expect(present("[data-harness-press='1']"), f"no 'Show all' row in {labels()}")
        run.expect(
            js("document.querySelector(\"[data-harness-press='1']\").getAttribute('aria-disabled')")
            == "true",
            "'Show every hidden body again' is not aria-disabled with nothing hidden",
        )
        app.hover(*app.centre("[data-harness-press='1']"))
        tip = str(js("(document.querySelector('[role=tooltip]')?.textContent || '').trim()"))
        run.expect(
            tip == "Nothing is hidden", f"the tooltip reads {tip!r}, not 'Nothing is hidden'"
        )
        close_menus()
        return "hovering the disabled 'Show every hidden body again' says 'Nothing is hidden'"

    # ---- 9
    #: Per family, the contrast ratio of an enabled, a checked and a disabled row.
    contrast: dict[str, dict[str, Any]] = {}

    def every_family_is_readable() -> str:
        families = ("dark", "light", "cupertino", "ubuntu")
        report: list[str] = []
        failures: list[str] = []
        point = body_point()
        ex, ey = empty_point()
        # The pointer rests away from the menu: a hovered row has a fill of its own.
        away = (ex, ey)
        try:
            for name in families:
                js(
                    "window.__workspace.getState().setValue(['theme'], "
                    f"{{name: {json.dumps(name)}}})"
                )
                wait_for(
                    lambda name=name: str(js("document.documentElement.dataset.theme")).startswith(
                        name
                    ),
                    f"the {name} theme",
                )
                time.sleep(0.8)
                applied = str(js("document.documentElement.dataset.theme"))
                right_click(float(point["x"]), float(point["y"]))
                run.expect(present(VIEWPORT_MENU), f"the body menu did not open in {name}")
                app.move(*away)
                time.sleep(0.5)
                app.shot(f"context-menu-{name}")
                measure = "(" + CONTRAST_JS + ")"
                enabled = js(
                    f"{measure}(\"{any_menu} [role='menuitem']"
                    ":not([aria-disabled='true']):not(:focus)\")"
                )
                disabled = js(f"{measure}(\"{any_menu} [role='menuitem'][aria-disabled='true']\")")
                close_menus()
                right_click(ex, ey)
                run.expect(present(VIEWPORT_MENU), f"the view menu did not open in {name}")
                app.press("ArrowDown", "ArrowDown", 40)
                app.move(*away)
                time.sleep(0.5)
                checked = js(
                    f"{measure}(\"{any_menu} [role^='menuitem'][aria-checked='true']:not(:focus)\")"
                )
                close_menus()
                contrast[name] = {"theme": applied, "disabled": disabled}
                for kind, found in (("enabled", enabled), ("checked", checked)):
                    if not isinstance(found, dict):
                        failures.append(f"{name}: no {kind} row to measure")
                    elif float(found["ratio"]) < 4.5:
                        failures.append(
                            f"{name}/{applied} {kind} {found['label']!r}: {found['ratio']} < 4.5"
                        )
                if isinstance(enabled, dict) and isinstance(checked, dict):
                    report.append(f"{name}: {enabled['ratio']} / {checked['ratio']}")
        finally:
            js(f"window.__workspace.getState().setValue(['theme'], {json.dumps(before['theme'])})")
            time.sleep(0.8)
            close_menus()
        run.expect(
            not failures,
            "contrast below 4.5: " + "; ".join(failures) + f". Measured: {'; '.join(report)}",
        )
        return (
            "contrast of an enabled / a checked row (floor 4.5), "
            + "; ".join(report)
            + "; backdrop blur over a bright model is not modelled"
        )

    def disabled_rows_are_readable() -> str:
        # A finding against the themes, not a broken harness: `.menu-item[aria-disabled]` is
        # `opacity: .45`, and the floor of 3.0 is the brief's, not to be relaxed to pass.
        run.expect(len(contrast) == 4, "the four families were not measured")
        short: list[str] = []
        measured: list[str] = []
        for name, found in contrast.items():
            row = found["disabled"]
            if not isinstance(row, dict):
                short.append(f"{name}: no disabled row to measure")
                continue
            measured.append(f"{name} {row['ratio']}")
            if float(row["ratio"]) < 3.0:
                short.append(f"{name} ({row['label']!r}, opacity {row['opacity']}): {row['ratio']}")
        run.expect(
            not short,
            "a disabled row is below 3.0 in " + "; ".join(short) + f". All: {'; '.join(measured)}",
        )
        return "contrast of a disabled row (floor 3.0), " + "; ".join(measured)

    # ---- 11
    def an_explore_plot_opens_no_viewport_menu() -> str:
        js(
            f"{state}.setStage('Explore');"
            f" (() => {{ const s = {state}; const find = (n) => n.type === 'study' ? n"
            " : (n.children || []).map(find).find(Boolean); const study = find(s.tree);"
            " if (study) s.select(study.id); })()"
        )
        time.sleep(1.0)
        tab = "[data-ws-panel-tab='plots']"
        if not present(tab):
            raise Failure("the Plots tab is not in the page")
        # The right slot's tab strip is wider than the window: bring the tab into reach first.
        js(
            f"document.querySelector({json.dumps(tab)}).scrollIntoView("
            "{block: 'nearest', inline: 'center'})"
        )
        time.sleep(0.3)
        app.click(*app.centre(tab))
        plot = ".u-wrap canvas"
        try:
            wait_for(lambda: present(plot), "the study's chart canvas", 8.0)
        except Failure:
            seen = js(
                "[...document.querySelectorAll('canvas')].map((c) => c.parentElement?.className)"
            )
            raise Failure(
                f"the demo's study draws no chart canvas in the Plots tab (canvases: {seen})"
            ) from None
        x, y = app.centre(plot)
        right_click(x, y)
        shown = present(any_menu)
        close_menus()
        run.expect(not shown, "a right-click on an Explore plot opened a menu")
        return "a right-click on the Plots tab's chart canvas opened no menu"

    # ---- 12
    def a_right_click_in_edge_mode_keeps_the_pick() -> str:
        settle_view()
        point = pick_a_body()
        before_pick = picked()
        button = "[data-ws-island='modes'] [aria-label^='Pick mode']"
        run.expect(present(button), "the Modes island has no Pick mode button")
        app.click(*app.centre(button))
        press_row("Edge")
        mode = js(f"{state}.pickMode")
        if mode != "edge":
            raise Failure(f"choosing Edge in the Modes island left the pick mode at {mode!r}")
        # The step means something only if there is a pick to keep: the mode switch may clear it.
        held = picked()
        if not held.get("bodies"):
            js(f"window.__stokes.setState({{picked: {json.dumps(before_pick)}}})")
            held = picked()
        run.expect(len(held.get("bodies", [])) > 0, "there is no body pick to keep in Edge mode")
        right_click(float(point["x"]), float(point["y"]))
        opened = present(VIEWPORT_MENU)
        after = picked()
        mode = js(f"{state}.pickMode")
        close_menus()
        run.expect(opened, "right-clicking in Edge mode opened no menu")
        run.expect(after == held, f"a right-click in Edge mode changed the pick: {held} -> {after}")
        run.expect(mode == "edge", f"the pick mode is {mode!r} after the right-click, not 'edge'")
        return (
            f"in Edge mode ({len(held['bodies'])} body picked) the right-click opened the menu "
            "and left picked and pickMode alone"
        )

    # ---- 13 (not in the brief)
    def a_menu_survives_the_tree_revealing_the_pick() -> str:
        """A right-click selects the part, and the tree scrolls to show its row. A scroll anywhere
        closes a point menu (T-170), so when the row is out of view the menu that has just opened
        closes again at once: a user sees it flash and go."""
        settle_view()
        point = body_point()
        scroll_tree("bottom")
        right_click(float(point["x"]), float(point["y"]))
        stayed = present(VIEWPORT_MENU)
        held = picked()
        close_menus()
        scroll_tree("top")
        run.expect(
            len(held.get("bodies", [])) > 0, "the right-click did not select the part either"
        )
        run.expect(
            stayed,
            "the menu closed after a right-click on a body whose row was scrolled out of the "
            "tree: the tree's reveal scroll closed it",
        )
        return "the menu stayed open although the tree had to scroll to the picked part's row"

    try:
        run.step(
            "a right-click on a body opens the menu and selects it",
            right_click_on_a_body_opens_the_menu_and_selects_it,
        )
        run.step(
            "Escape closes the menu and the canvas has the focus",
            escape_closes_it_and_the_canvas_has_the_focus,
        )
        run.step("a right drag pans and opens no menu", a_right_drag_pans_and_opens_nothing)
        run.step(
            "a right-click on empty space opens the view menu and keeps the pick",
            empty_space_opens_the_view_menu_and_keeps_the_pick,
        )
        run.step(
            "Display then Wireframe changes the view mode", display_wireframe_changes_the_view_mode
        )
        run.step(
            "the keyboard opens the menu and the arrows walk it",
            the_keyboard_opens_it_and_arrows_walk_it,
        )
        run.step("a tree row's menu is still the tree's", the_tree_row_menu_is_unchanged)
        run.step("a disabled entry explains itself on hover", a_disabled_entry_explains_itself)
        run.step("the menu is readable in all four theme families", every_family_is_readable)
        run.step(
            "a disabled row is readable in all four theme families",
            disabled_rows_are_readable,
            fatal=False,
        )
        run.step(
            "a right-click on an Explore plot opens no viewport menu",
            an_explore_plot_opens_no_viewport_menu,
        )
        js(f"{state}.setStage('Geometry'); {state}.clearPick()")
        time.sleep(0.4)
        run.step(
            "a right-click in Edge mode keeps the pick", a_right_click_in_edge_mode_keeps_the_pick
        )
        # A finding about the app (a menu that closes itself), not a broken harness: later checks
        # still run.
        run.step(
            "a right-click on a body whose row is out of the tree's view keeps the menu open",
            a_menu_survives_the_tree_revealing_the_pick,
            fatal=False,
        )
    finally:
        # Put the page back the way the next check expects it, whatever happened above.
        close_menus()
        js(
            f"{state}.setViewMode({json.dumps(before['viewMode'])});"
            f" {state}.setNavMode({json.dumps(before['navMode'])});"
            f" {state}.setPickMode({json.dumps(before['pickMode'])});"
            f" window.__stokes.setState({{section: {json.dumps(before['section'])}}});"
            f" {state}.setStage('Geometry'); {state}.clearPick(); {state}.requestFit()"
        )
        time.sleep(0.8)


def check_every_control_answers(run: Checks, app: App) -> None:
    """Click every ribbon task, open every dock menu, and check nothing is a dead end.

    The owner's instruction was to stress the interface "by manually clicking all menu items and
    interactive items". That is a sweep, not a list of known controls, so this enumerates what is on
    screen and clicks it rather than naming what it expects to find:

    * **every ribbon task** in every stage, one stage at a time;
    * **every dock button**, including the ones that open a menu, and for a menu, every row in it.

    What it asserts is the thing a user feels: **no control is a dead end**. A tool that says "not
    built yet", throws, or leaves the window in a broken state is a defect; a tool that opens a
    card, toggles a mode or pushes an explanatory toast is working. A "not built yet" toast is
    therefore FAILED here — that is exactly the class of stub this sweep exists to catch, and it is
    how Section view was found to be the last one.

    Deliberately tolerant about the *result*: this does not decide whether an operation should be
    disabled for the current pick (the Prepare menu's rules are its own tests). It cares that the
    click reached something and that the app answered.
    """

    def toasts() -> list[str]:
        found = app.evaluate(
            """
            JSON.stringify((window.__stokes.getState().toasts || [])
              .map((t) => String(t.text || t.message || t)))
            """
        )
        return json.loads(found) if found else []

    def click_and_read(x: float, y: float) -> str:
        before = len(toasts())
        app.click(x, y)
        # A control may answer synchronously or a frame later; one short settle covers both and
        # keeps
        # the sweep quick enough to run on every change.
        time.sleep(0.25)
        after = toasts()
        return after[-1] if len(after) > before else ""

    def every_ribbon_task() -> str:
        # Bound once so the JavaScript below stays readable: the selectors are long, and inlining
        # them into three f-strings made each line over the project's limit.
        stages_js = json.dumps(STAGE_CONTROLS)
        tasks_js = json.dumps(STAGE_TASKS)
        stages = json.loads(
            app.evaluate(
                f"""
                JSON.stringify([...document.querySelectorAll({stages_js})]
                  .map((b) => (b.textContent || "").trim()).filter(Boolean))
                """
            )
            or "[]"
        )
        run.expect(stages, "no stage buttons were found in the top bar")
        checked = 0
        dead: list[str] = []
        documented: list[str] = []
        for stage in stages:
            app.evaluate(
                f"""
                (() => {{ const b = [...document.querySelectorAll({stages_js})].find(
                  (x) => (x.textContent || "").trim() === {json.dumps(stage)});
                  if (b) b.click(); }})()
                """
            )
            time.sleep(0.4)
            # Every enabled ribbon task, by its accessible name.
            names = json.loads(
                app.evaluate(
                    f"""
                    JSON.stringify([...document.querySelectorAll({tasks_js})].filter(
                      (b) => !b.disabled && b.getAttribute("aria-disabled") !== "true"
                        && !b.matches({json.dumps(WINDOW_CONTROLS)})
                        && !b.closest({json.dumps(WINDOW_CONTROLS)}))
                      .map((b) => (b.textContent || b.getAttribute("aria-label") || "").trim())
                      .filter(Boolean))
                    """
                )
                or "[]"
            )
            for name in names:
                box = app.evaluate(
                    f"""
                    (() => {{ const b = [...document.querySelectorAll({tasks_js})].find(
                      (x) => !x.disabled
                        && ((x.textContent || x.getAttribute("aria-label") || "").trim()
                             === {json.dumps(name)}));
                      if (!b) return null; const r = b.getBoundingClientRect();
                      return {{x: r.x + r.width / 2, y: r.y + r.height / 2}}; }})()
                    """
                )
                if box is None:
                    continue
                # A ribbon task may open a menu rather than act; Escape closes it before the next
                # one.
                answer = click_and_read(float(box["x"]), float(box["y"]))
                checked += 1
                lowered = answer.lower()
                if any(mark in lowered for mark in DOCUMENTED_STUBS):
                    documented.append(f"{name}: {answer}")
                elif any(mark in lowered for mark in DEAD_END_TOASTS):
                    dead.append(f"{name}: {answer}")
                app.press("Escape", "Escape", 27)
                time.sleep(0.1)
        run.expect(
            not dead,
            "stage task(s) report a stub rather than doing something: " + "; ".join(dead[:4]),
        )
        # Leave the app in Geometry: the checks after this one assume the CAD context, and a sweep
        # that finished on Explore would silently change what they test.
        app.evaluate("window.__stokes.getState().setStage('Geometry')")
        time.sleep(0.3)
        note = (
            f" ({len(documented)} documented milestone stub(s): {documented[0].split(':')[0]})"
            if documented
            else ""
        )
        return f"{checked} stage tasks across {len(stages)} stages answered{note}"

    def every_dock_button_and_menu_row() -> str:
        islands = json.loads(
            app.evaluate(
                """
                JSON.stringify([...document.querySelectorAll("[data-ws-island]")]
                  .map((el) => el.getAttribute("data-ws-island")))
                """
            )
            or "[]"
        )
        run.expect(islands, "no dock islands were found")
        checked = 0
        dead: list[str] = []
        for island in islands:
            count = int(
                app.evaluate(
                    f"""
                    document.querySelectorAll("[data-ws-island='{island}'] button").length
                    """
                )
                or 0
            )
            for index in range(count):
                label = app.evaluate(
                    f"""
                    (() => {{ const b = document.querySelectorAll(
                      "[data-ws-island='{island}'] button")[{index}];
                      return (b.getAttribute("aria-label") || b.textContent || "").trim(); }})()
                    """
                )
                box = app.evaluate(
                    f"""
                    (() => {{ const b = document.querySelectorAll(
                      "[data-ws-island='{island}'] button")[{index}];
                      if (!b || b.disabled) return null;
                      // Never a window control: Close ends the session (see WINDOW_CONTROLS).
                      if (b.matches({json.dumps(WINDOW_CONTROLS)})
                          || b.closest({json.dumps(WINDOW_CONTROLS)})) return null;
                      const r = b.getBoundingClientRect();
                      return {{x: r.x + r.width / 2, y: r.y + r.height / 2}}; }})()
                    """
                )
                if box is None:
                    continue
                answer = click_and_read(float(box["x"]), float(box["y"]))
                checked += 1
                if any(mark in answer.lower() for mark in DEAD_END_TOASTS):
                    dead.append(f"{island}/{label}: {answer}")
                # Click every row of a menu this button opened, then close it.
                rows = json.loads(
                    app.evaluate(
                        """
                        JSON.stringify([...document.querySelectorAll(
                          ".popover [role^=menuitem]")].map((r) => {
                            const b = r.getBoundingClientRect();
                            return {x: b.x + b.width / 2, y: b.y + b.height / 2}; }))
                        """
                    )
                    or "[]"
                )
                for row in rows:
                    row_answer = click_and_read(float(row["x"]), float(row["y"]))
                    checked += 1
                    if any(mark in row_answer.lower() for mark in DEAD_END_TOASTS):
                        dead.append(f"{island}/{label} row: {row_answer}")
                    # A row may close the menu; reopen it for the next one.
                    still_open = json.loads(
                        app.evaluate(
                            """
                            JSON.stringify(document.querySelectorAll(
                              ".popover [role^=menuitem]").length)
                            """
                        )
                        or "0"
                    )
                    if not still_open:
                        app.click(float(box["x"]), float(box["y"]))
                        time.sleep(0.2)
                app.press("Escape", "Escape", 27)
                time.sleep(0.15)
        run.expect(
            not dead,
            "dock control(s) report a stub rather than doing something: " + "; ".join(dead[:4]),
        )
        return f"{checked} dock controls and menu rows answered"

    run.step("every ribbon task in every stage answers", every_ribbon_task)
    run.step("every dock button and menu row answers", every_dock_button_and_menu_row)


# ---------------------------------------------------------------------- the Mesh panel (T-211b)

#: What the fake run reports: a checkMesh report that passes, with the numbers the panel shows.
FAKE_MESH_REPORT: dict[str, Any] = {
    "cells": 8000,
    "points": 9261,
    "faces": 24400,
    "max_non_ortho": 0.0,
    "max_skewness": 0.25,
    "max_aspect_ratio": 1.0,
    "failed_checks": 0,
    "mesh_ok": True,
}


def check_mesh_look(run: Checks, app: App) -> None:
    """The mesh in the viewport (T-211): the Mesh stage draws it, the rest stays intact.

    Needs a project with a finished mesh, which `tools/mesh_demo_project.py` makes without OpenFOAM
    (`--project`); without one the steps are skipped and say so. Steps: the Mesh stage draws the
    patches and sets the geometry aside, and leaving it brings the geometry back exactly as it was;
    the Display menu offers the mesh entries and "Mesh with cells" draws the cell edges of every
    patch; the edges count out to the cells the result reports; the Section still cuts, and a mesh
    patch gets no cap; a click on a patch selects its tree row and picks no body.
    """
    if app.session is None:
        print("  skip the mesh look checks: an attached window has no session to read")
        return
    state = "window.__stokes.getState()"
    patches_js = f"{state}.geometry.filter((g) => g.kind === 'mesh')"
    deadline = time.monotonic() + 45.0
    count = 0
    while time.monotonic() < deadline and count < 1:
        count = int(app.evaluate(f"{patches_js}.length") or 0)
        if count < 1:
            time.sleep(0.5)
    if count < 1:
        print("  skip the mesh look checks: no mesh patches (see tools/mesh_demo_project.py)")
        return

    def info() -> dict[str, Any]:
        found = app.evaluate("window.__stokesScene.meshInfo()")
        run.expect(isinstance(found, dict), "the scene probe has no meshInfo")
        return dict(found)

    def wait_for(condition: Any, what: str, seconds: float = 20.0) -> dict[str, Any]:
        limit = time.monotonic() + seconds
        last: dict[str, Any] = {}
        while time.monotonic() < limit:
            last = info()
            if condition(last):
                return last
            time.sleep(0.25)
        raise Failure(f"timed out waiting for {what}; the scene says {last}")

    def part_names() -> list[str]:
        found = app.evaluate("Object.keys(window.__stokesScene.partBounds())")
        return [str(n) for n in found] if isinstance(found, list) else []

    app.evaluate(f"{state}.setStage('Geometry'); {state}.setMeshDisplay('surface')")
    time.sleep(0.5)
    before = sorted(part_names())
    run.expect(
        len(before) > 0 and not any(n.endswith("(mesh)") for n in before),
        f"the Geometry stage shows {before}",
    )

    def mesh_stage_swaps_the_layers() -> str:
        app.evaluate(f"{state}.setStage('Mesh'); {state}.requestFit()")
        found = wait_for(
            lambda i: i["patchesShown"] == count and not i["geometryLayer"],
            "the mesh patches to show",
        )
        shown = sorted(part_names())
        run.expect(
            shown and all(n.endswith("(mesh)") for n in shown),
            f"the Mesh stage still shows geometry: {shown}",
        )
        run.expect(found["cellEdgesDrawn"] == 0, f"cell edges drawn on 'Mesh surface': {found}")
        # Leaving the stage restores the geometry exactly: same parts, nothing hidden or isolated.
        app.evaluate(f"{state}.setStage('Geometry')")
        back = wait_for(
            lambda i: i["patchesShown"] == 0 and i["geometryLayer"], "the geometry to return"
        )
        again = sorted(part_names())
        run.expect(
            again == before, f"the geometry did not come back as it was: {before} -> {again}"
        )
        hidden = app.evaluate(f"[{state}.hiddenPatches.length, {state}.isolatedPatch]")
        run.expect(hidden == [0, None], f"the view filter touched Hide or Isolate: {hidden}")
        invisible = app.evaluate(f"{state}.partNodes.filter((p) => !p.visible).length")
        run.expect(invisible == 0, f"{invisible} part(s) had their own 'visible' changed")
        app.evaluate(f"{state}.setStage('Mesh')")
        wait_for(lambda i: i["patchesShown"] == count, "the mesh patches to show again")
        return (
            f"{count} patches shown with the geometry layer off; back in Geometry "
            f"{back['patchesShown']} shown and {len(again)} parts as before"
        )

    run.step(
        "the Mesh stage draws the patches and leaving it restores the geometry",
        mesh_stage_swaps_the_layers,
    )

    def display_menu_draws_the_cells() -> str:
        x, y = app.centre("[aria-label^='Display mode']")
        app.click(x, y)
        entries = app.evaluate(
            "Array.from(document.querySelectorAll('[role=menuitemradio]'))"
            ".map((n) => [n.textContent?.trim() ?? '', n.getAttribute('aria-checked')])"
        )
        names = [str(e[0]) for e in entries] if isinstance(entries, list) else []
        run.expect(
            "Mesh surface" in names and "Mesh with cells" in names,
            f"the Display menu has no mesh entries: {names}",
        )
        checked = [str(e[0]) for e in entries if e[1] == "true"]
        run.expect(
            "Mesh surface" in checked, f"'Mesh surface' is not the default choice: {checked}"
        )
        rect = app.evaluate(
            "(() => { const n = Array.from(document.querySelectorAll('[role=menuitemradio]'))"
            ".find((e) => e.textContent.trim() === 'Mesh with cells');"
            " const r = n.getBoundingClientRect();"
            " return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()"
        )
        app.click(float(rect["x"]), float(rect["y"]))
        found = wait_for(lambda i: i["cellEdgesDrawn"] == count, "the cell edges to draw")
        app.evaluate(f"{state}.setMeshDisplay('surface')")
        hidden = wait_for(lambda i: i["cellEdgesDrawn"] == 0, "the cell edges to hide")
        run.expect(
            hidden["cellEdges"] == count, f"the edges were thrown away on 'Mesh surface': {hidden}"
        )
        app.evaluate(f"{state}.setMeshDisplay('cells')")
        wait_for(lambda i: i["cellEdgesDrawn"] == count, "the cell edges to come back")
        return f"{found['cellEdgesDrawn']} of {count} patches with cell edges"

    run.step(
        "the Display menu's 'Mesh with cells' draws the cell edges", display_menu_draws_the_cells
    )

    def edges_count_out_to_the_cells() -> str:
        import numpy as np

        from stokes.mesh.service import MeshService
        from stokes.mesh.surfaces import unpack_edges

        project = app.session.project
        results = project.find("/mesh/results") if project is not None else None
        result = results.children[0] if results is not None and results.children else None
        run.expect(result is not None, "the project has no mesh result")
        cells = int(result.params["cells"])
        # The patch with the most edges touches every axis (a duct's walls): its distinct values
        # per axis are the grid lines of the block.
        service = MeshService.of(app.session)
        best: tuple[str, Any] | None = None
        for patch in result.children:
            payload = service.patch_edges(str(patch.id))
            run.expect(payload is not None, f"the server has no edges for patch {patch.name}")
            segments = unpack_edges(payload)
            if best is None or len(segments) > len(best[1]):
                best = (str(patch.name), segments)
        run.expect(best is not None, "the result has no patches")
        name, segments = best
        points = np.round(segments.reshape(-1, 3).astype("float64"), 7)
        along = [len(np.unique(points[:, axis])) - 1 for axis in range(3)]
        run.expect(
            along[0] * along[1] * along[2] == cells,
            f"{name}'s edges show {along} cells per axis ({int(np.prod(along))}); "
            f"the result says {cells}",
        )
        shape = f"{along[0]} x {along[1]} x {along[2]}"
        return f"{name}: {shape} = {cells} cells, {len(segments)} segments"

    run.step(
        "the cell edges count out to the cells the result reports", edges_count_out_to_the_cells
    )

    def section_cuts_without_caps() -> str:
        app.evaluate(f"{state}.requestFit()")
        time.sleep(0.8)
        bounds = app.evaluate("window.__stokesScene.modelBounds()")
        run.expect(isinstance(bounds, dict), "the scene has no bounds with the mesh shown")
        middle = (float(bounds["min"][2]) + float(bounds["max"][2])) / 2
        app.evaluate(f"{state}.setSection(true); {state}.setSectionPlane('z', {middle})")
        time.sleep(1.0)
        clipping = int(app.evaluate("window.__stokesScene.clippingCount()") or 0)
        caps = app.evaluate("window.__stokesScene.capsInfo()")
        run.expect(clipping == 1, f"the section plane is not on the renderer: {clipping}")
        run.expect(
            isinstance(caps, dict) and caps["groups"] == 0 and caps["marks"] == 0,
            f"a mesh patch was capped as if it were a closed body: {caps}",
        )
        shown = info()["patchesShown"]
        run.expect(shown == count, f"the section hid mesh patches: {shown} of {count}")
        app.evaluate(f"{state}.setSection(false)")
        time.sleep(0.5)
        return f"clipped, no caps, {shown} patches shown"

    run.step("the Section still cuts the mesh and caps nothing", section_cuts_without_caps)

    def a_click_selects_the_row() -> str:
        app.evaluate(f"{state}.setPickMode('body'); {state}.clearPick(); {state}.requestFit()")
        time.sleep(1.0)
        found = app.evaluate(
            "(() => { const b = window.__stokesScene.partBounds();"
            " const key = Object.keys(b).find((k) => k.startsWith('walls')) ?? Object.keys(b)[0];"
            " const c = document.querySelector('canvas').getBoundingClientRect(); const q = b[key];"
            " return {key, x: c.left + (q.x0 + q.x1) / 2, y: c.top + (q.y0 + q.y1) / 2}; })()"
        )
        run.expect(
            isinstance(found, dict) and found.get("key") is not None, "no mesh patch to click"
        )
        app.click(float(found["x"]), float(found["y"]))
        time.sleep(0.4)
        chosen = app.evaluate(
            f"(() => {{ const s = {state}; const walk = (n) => n.id === s.selectedId ? n"
            " : (n.children || []).map(walk).find(Boolean);"
            " const hit = s.tree && s.selectedId ? walk(s.tree) : null;"
            " return {type: hit ? hit.type : null, bodies: s.picked.bodies.length}; })()"
        )
        run.expect(
            isinstance(chosen, dict) and chosen["type"] == "mesh_patch",
            f"a click on {found['key']} selected {chosen}, not a mesh_patch row",
        )
        run.expect(chosen["bodies"] == 0, f"a click on a mesh patch picked a body: {chosen}")
        return f"{found['key']} selected the mesh_patch row and picked no body"

    run.step("a click on a mesh patch selects its row and picks no body", a_click_selects_the_row)
    app.evaluate(f"{state}.setStage('Geometry'); {state}.setMeshDisplay('surface')")
    time.sleep(0.5)


def install_fake_mesh(app: App, tick: float = 0.25) -> Any:
    """Replace the session's mesh service with one whose JOB is played from a script.

    OpenFOAM is not installed on the machine this runs on (the real run happens on the Linux host),
    so the job is the only part faked: the settings, the snapshot, the result node, the events, the
    staleness check and every command are the real ones. The subclass overrides `generate`, `cancel`
    and `log` and plays two steps (blockMesh, checkMesh) of eight log lines each, `tick` seconds
    apart, through the service's own `_replace`, `_emit` and `_finish_with`.
    """
    import threading

    from stokes.geometry.store import GeometryStore
    from stokes.mesh.basis import mesh_basis
    from stokes.mesh.drivers.blockmesh import DRIVER_VERSION
    from stokes.mesh.errors import MeshInvalid
    from stokes.mesh.model import SETTINGS_PATH
    from stokes.mesh.service import EXTENSION, MeshService
    from stokes.mesh.snapshot import build_snapshot

    assert app.session is not None

    class FakeMeshService(MeshService):
        def __init__(self, session: Any) -> None:
            super().__init__(session)
            self._stop = threading.Event()
            self._player: threading.Thread | None = None
            self._lines: dict[str, list[str]] = {}

        def generate(
            self, project: Any, host: str | None, force: bool, result_id: str | None
        ) -> dict[str, Any]:
            settings = project.find(SETTINGS_PATH)
            if settings is None:
                raise MeshInvalid("there is no mesh settings node; run mesh.configure first")
            snap = build_snapshot(self._session, settings)
            self.cancel()
            if self._player is not None:
                self._player.join(10.0)
            self._stop.clear()
            basis = mesh_basis(snap, DRIVER_VERSION, "org-14")
            result = self._replace(project, snap, "local", basis, "org-14", result_id)
            self._reset(result, snap, "org-14", basis)
            tags = project.find("/geometry/tags")
            by_name = {str(t.name): str(t.id) for t in (tags.children if tags else [])}
            result.meta["patch_tags"] = {
                side: by_name[name]
                for side, name in (("xmin", "inlet"), ("xmax", "outlet"), ("ymin", "walls"))
                if name in by_name
            }
            result.meta["warnings"] = ["tag fins lies inside the domain; blockMesh cannot mesh it"]
            with self._lock:
                self._job = "j_000000000001"
                self._total = 2
                self._lines = {}
            project.set_param(result.id, "job", "j_000000000001")
            self._player = threading.Thread(
                target=self._play, args=(str(result.id),), name="fake-mesh", daemon=True
            )
            self._player.start()
            return self._info()

        def cancel(self) -> bool:
            with self._lock:
                running = self._job is not None
            if running:
                self._stop.set()
            return running

        def log(self, lines: int) -> dict[str, Any]:
            with self._lock:
                step = self._step or ""
                tail = list(self._lines.get(step, []))[-lines:]
            return {"step": step, "lines": tail, "truncated": False}

        def _progress(
            self, result_id: str, path: str, step: str, stage: str, fraction: float
        ) -> None:
            self._emit(
                "mesh.progress",
                node_id=result_id,
                path=path,
                step=step,
                stage=stage,
                fraction=fraction,
                message="",
            )

        def _play(self, result_id: str) -> None:
            project = self._session.project
            node = project.find(result_id) if project is not None else None
            if project is None or node is None:
                return
            path = str(node.path)
            for index, step in enumerate(("blockMesh", "checkMesh")):
                with self._lock:
                    self._step, self._done_steps = step, index
                self._progress(result_id, path, step, "running", index / 2)
                for number in range(8):
                    line = f"{step}: working, line {number}"
                    with self._lock:
                        self._lines.setdefault(step, []).append(line)
                    self._emit("mesh.log", node_id=result_id, step=step, lines=[line])
                    if self._stop.wait(tick):
                        self._progress(result_id, path, step, "cancelling", index / 2)
                        self._end(project, result_id, "cancelled", {}, 0.0)
                        return
                self._progress(result_id, path, step, "done", 1.0)
            self._end(project, result_id, "done", FAKE_MESH_REPORT, 3.2)

        def _end(
            self, project: Any, result_id: str, state: str, report: dict[str, Any], seconds: float
        ) -> None:
            def finish() -> None:
                found = project.find(result_id)
                if found is not None:
                    self._finish_with(project, found, state, report=report, seconds=seconds)

            GeometryStore.of(self._session).on_session_thread(finish, threading.Event())

    fake = FakeMeshService(app.session)
    app.session.extensions[EXTENSION] = fake
    return fake


def check_mesh_panel(run: Checks, app: App) -> None:
    """The Mesh panel on real presses: open the stage, Generate, watch the state change, Cancel.

    The job is played from a script (`install_fake_mesh`); the panel, the commands, the events and
    the tree are real. Steps: the Mesh stage brings the tab to the front; Generate is disabled on a
    machine with no host, with its reason; with a host it is enabled; Generate changes the state
    text and shows Cancel; Cancel returns to an idle, enabled Generate; a second run finishes with
    the result card (cells, quality against limits, warnings, mapping, log); changing a size makes
    the mesh stale and Regenerate runs it again.
    """
    if app.session is None:
        print("  skip the Mesh panel checks: an attached window has no session to script")
        return
    install_fake_mesh(app)
    # The demo's tags arrive when its import finishes, some seconds after the window is up; the
    # boundary mapping the fake reports names them.
    deadline = time.monotonic() + 90.0
    have_tags = False
    while time.monotonic() < deadline and not have_tags:
        tags = app.session.project.find("/geometry/tags") if app.session.project else None
        have_tags = tags is not None and len(tags.children) >= 3
        time.sleep(0.5)

    state_selector = "[data-ws='mesh-state-text']"
    generate_selector = "[data-ws='mesh-generate']"
    cancel_selector = "[data-ws='mesh-cancel']"
    chip_selector = ".stage-chips [aria-label='Mesh']"

    def text(selector: str) -> str:
        found = app.evaluate(
            "(() => { const el = document.querySelector("
            + json.dumps(selector)
            + "); return el ? (el.textContent || '').trim() : null; })()"
        )
        return str(found) if found is not None else ""

    def present(selector: str) -> bool:
        return bool(app.evaluate(f"Boolean(document.querySelector({json.dumps(selector)}))"))

    def press(selector: str) -> None:
        app.evaluate(
            "(() => { const el = document.querySelector("
            + json.dumps(selector)
            + "); if (el) el.scrollIntoView({block: 'center'}); })()"
        )
        time.sleep(0.2)
        app.click(*app.centre(selector))

    def wait_for(condition: Any, what: str, seconds: float = 25.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.25)
        raise Failure(f"timed out waiting for {what}; the state reads {text(state_selector)!r}")

    def disabled(selector: str) -> bool:
        return bool(
            app.evaluate(
                f"document.querySelector({json.dumps(selector)})"
                "?.getAttribute('aria-disabled') === 'true'"
            )
        )

    def the_stage_brings_the_tab_forward() -> str:
        press(chip_selector)
        wait_for(lambda: present("[data-ws='mesh-panel']"), "the Mesh panel")
        selected = app.evaluate(
            "document.querySelector('[data-ws-panel-tab=\"mesh\"]')?.getAttribute('aria-selected')"
        )
        run.expect(selected == "true", "the Mesh stage did not bring the Mesh tab to the front")
        run.expect(text(state_selector) == "No mesh yet", f"idle reads {text(state_selector)!r}")
        return "the Mesh tab is in front and says 'No mesh yet'"

    def no_host_disables_generate_and_says_why() -> str:
        wait_for(lambda: disabled(generate_selector), "Generate to be disabled")
        why = text("[data-ws='mesh-why']")
        run.expect("No host can mesh" in why, f"the reason reads {why!r}")
        run.expect(
            bool(
                app.evaluate(
                    f"document.querySelector({json.dumps(generate_selector)}).title.length > 0"
                )
            ),
            "the disabled Generate has no tooltip",
        )
        return why[:80]

    def a_host_enables_it() -> str:
        from stokes.run.hosts import HostRegistry

        assert app.session is not None
        registry = HostRegistry.of(app.session)
        host = {"name": "local", "runner": "fake", "available": True, "why": "", "record": None}
        registry.hosts = lambda: [host]  # type: ignore[method-assign]
        app.dev.call("Page.reload", {}, timeout=60.0)
        app._wait_for_app()
        press(chip_selector)
        wait_for(lambda: present(generate_selector), "the panel after the reload")
        wait_for(lambda: not disabled(generate_selector), "Generate to be enabled")
        return "Generate is enabled once a host is available"

    def generate_changes_the_state() -> str:
        # The server creates `/mesh/settings` with the project (`ensure_stage_settings`), so the
        # panel opens on the form and offers no "Create settings" button.
        wait_for(lambda: present(".mesh-fields"), "the settings form")
        run.expect(not present("[data-ws='mesh-create']"), "the panel still offers Create settings")
        app.evaluate(
            "window.__stokes.getState().execute('mesh.configure', {values: {domain: 'box',"
            " box: [0, 0, 0, 0.2, 0.2, 0.2], base_size: '25 [mm]'}}, {silent: true})"
        )
        wait_for(lambda: not disabled(generate_selector), "Generate after the box was set")
        press(generate_selector)
        wait_for(lambda: text(state_selector).startswith("Meshing"), "the state to read Meshing")
        wait_for(lambda: present(cancel_selector), "Cancel to appear")
        bar = app.evaluate("document.querySelector('.mesh-bar')?.getAttribute('aria-label')")
        run.expect(bar == "Mesh progress", f"the progress bar is labelled {bar!r}")
        return text(state_selector)

    def cancel_returns_to_idle() -> str:
        press(cancel_selector)
        wait_for(lambda: text(state_selector) == "Cancelled", "the state to read Cancelled")
        run.expect(not present(cancel_selector), "Cancel is still offered after the run ended")
        run.expect(not disabled(generate_selector), "Generate is not enabled again after Cancel")
        return "Cancelled; Generate is enabled again"

    def a_second_run_finishes() -> str:
        press(generate_selector)
        wait_for(lambda: text(state_selector).startswith("Meshing"), "the second run to start")
        wait_for(lambda: text(state_selector).startswith("Finished"), "the run to finish", 40.0)
        cells = text("[data-ws='mesh-cells']")
        run.expect(cells == "8,000", f"the result card shows {cells!r} cells")
        rows = int(app.evaluate("document.querySelectorAll('[data-ws=mesh-quality] tr').length"))
        run.expect(rows == 4, f"the quality table has {rows} rows, not 4")
        verdicts = app.evaluate(
            "[...document.querySelectorAll('[data-ws=mesh-quality] tr')]"
            ".map((r) => r.getAttribute('data-verdict')).join(',')"
        )
        run.expect(verdicts == "pass,pass,pass,pass", f"the verdicts read {verdicts!r}")
        run.expect(present("[data-ws='mesh-warnings']"), "the warnings list is missing")
        mapping = text("[data-ws='mesh-mapping']")
        # The demo's tags come from an import that can be slow or absent on a machine without OCP;
        # the mapping is only asserted when they exist.
        run.expect(not have_tags or "inlet" in mapping, f"the boundary mapping reads {mapping!r}")
        press("[data-ws='mesh-log-toggle']")
        wait_for(lambda: present("[data-ws='mesh-log']"), "the log to open")
        log = text("[data-ws='mesh-log']")
        run.expect("checkMesh: working" in log, f"the log reads {log[:80]!r}")
        return text(state_selector)

    def a_changed_size_makes_it_stale() -> str:
        app.evaluate(
            "window.__stokes.getState().execute('mesh.configure',"
            " {values: {base_size: '20 [mm]'}}, {silent: true})"
        )
        wait_for(lambda: present("[data-ws='mesh-stale']"), "the out-of-date banner")
        press(generate_selector)
        wait_for(lambda: text(state_selector).startswith("Meshing"), "Regenerate to start a run")
        press(cancel_selector)
        wait_for(lambda: text(state_selector) == "Cancelled", "the regenerated run to stop")
        return "the banner appeared, Regenerate started a run"

    run.step("the Mesh stage brings the Mesh tab forward", the_stage_brings_the_tab_forward)
    run.step(
        "with no host, Generate is disabled and says why", no_host_disables_generate_and_says_why
    )
    run.step("with a host, Generate is enabled", a_host_enables_it)
    run.step("Generate changes the state text and offers Cancel", generate_changes_the_state)
    run.step("Cancel stops the run and Generate is idle again", cancel_returns_to_idle)
    run.step("a second run finishes with the result card", a_second_run_finishes)
    run.step(
        "a changed size makes the mesh stale and Regenerate runs it", a_changed_size_makes_it_stale
    )
    app.evaluate("window.__stokes.getState().setStage('Geometry')")
    time.sleep(0.3)


# ---------------------------------------------------------------------- the Run panel (T-217)

FAKE_RUN_SCRIPT = Path(__file__).resolve().parent / "fake_run.js"


def check_run_panel(run: Checks, app: App) -> None:
    """The Solve panel, the top bar's Run and Stop, the residuals and the job log, on real presses.

    The SERVER half of a run is played from `tools/fake_run.js` (OpenFOAM is not installed here and
    the `solve.*`/`run.*` commands are on the Linux host's tree): it answers the commands with the
    contract's shapes and pushes the events a run would. The store, the reducer, the panels, the
    top bar, the drawer, the chart, the toasts and every click are real. Steps: the Solve stage
    brings the tab forward and Run says why it cannot start; a ready case enables Run; pressing
    the top bar's Run starts a run (Stop appears, the chart draws, the readout counts); the
    Residuals tab draws the same run with its target line; the Log tab's Job view shows the
    solver's lines and the Events view is still there; the run ends with its toast and readout;
    Run again and Stop; a run that stops short is "not converged" and not an error; an
    interrupted run is not a spinner; a settings edit is one `solve.configure` and a refusal is
    the command's own message under the field.
    """
    if app.session is None:
        print("  skip the Run panel checks: an attached window has no session to script")
        return
    app.evaluate(FAKE_RUN_SCRIPT.read_text(encoding="utf-8"))
    app.evaluate("window.__fakeRun.install()")

    def text(selector: str) -> str:
        found = app.evaluate(
            "(() => { const el = document.querySelector("
            + json.dumps(selector)
            + "); return el ? (el.textContent || '').trim() : null; })()"
        )
        return str(found) if found is not None else ""

    def present(selector: str) -> bool:
        return bool(app.evaluate(f"Boolean(document.querySelector({json.dumps(selector)}))"))

    def count(selector: str) -> int:
        return int(app.evaluate(f"document.querySelectorAll({json.dumps(selector)}).length"))

    def attr(selector: str, name: str) -> str:
        found = app.evaluate(
            f"document.querySelector({json.dumps(selector)})?.getAttribute({json.dumps(name)})"
        )
        return str(found) if found is not None else ""

    def disabled(selector: str) -> bool:
        return attr(selector, "aria-disabled") == "true"

    def press(selector: str) -> None:
        app.evaluate(
            "(() => { const el = document.querySelector("
            + json.dumps(selector)
            + "); if (el) el.scrollIntoView({block: 'center'}); })()"
        )
        time.sleep(0.2)
        app.click(*app.centre(selector))

    def press_text(selector: str, label: str) -> None:
        """Click the first element matching `selector` whose text starts with `label`."""
        app.evaluate(
            "document.querySelector('[data-harness-press]')?.removeAttribute('data-harness-press');"
            "[...document.querySelectorAll("
            + json.dumps(selector)
            + ")].find((e) => (e.textContent || '').trim().startsWith("
            + json.dumps(label)
            + "))?.setAttribute('data-harness-press', '1')"
        )
        press("[data-harness-press='1']")

    def wait_for(condition: Any, what: str, seconds: float = 25.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.2)
        raise Failure(
            f"timed out waiting for {what}; the state reads {text('[data-ws=run-state-text]')!r}"
        )

    state_text = "[data-ws='run-state-text']"
    top_run = "[data-ws='run-top']"
    panel_run = "[data-ws='run-button']"
    readout = "[data-ws='run-readout']"

    def the_stage_brings_the_tab_forward() -> str:
        app.evaluate("window.__stokes.getState().setStage('Solve')")
        wait_for(lambda: present("[data-ws='run-panel']"), "the Solve panel")
        selected = attr("[data-ws-panel-tab='solve']", "aria-selected")
        run.expect(selected == "true", "the Solve stage did not bring the Solve tab to the front")
        run.expect(text(state_text) == "No run yet", f"idle reads {text(state_text)!r}")
        return "the Solve tab is in front and says 'No run yet'"

    def not_ready_says_why() -> str:
        wait_for(lambda: disabled(top_run), "the top bar's Run to be disabled")
        wait_for(lambda: "Generate the mesh" in text("[data-ws='run-why']"), "the reason")
        run.expect(disabled(panel_run), "the panel's Run is not disabled")
        why = text("[data-ws='run-why']")
        run.expect("Generate the mesh" in why, f"the reason reads {why!r}")
        findings = text("[data-ws='run-findings']")
        run.expect("material in the Physics stage" in findings, f"findings: {findings!r}")
        run.expect("Generate the mesh" not in findings, f"the reason is said twice: {findings!r}")
        label = attr(top_run, "aria-label")
        run.expect(label == "Run the simulation", f"the top bar's Run is named {label!r}")
        wait_for(lambda: "Generate the mesh" in text(readout), "the Run readout", 10.0)
        return f"disabled; {why}"

    def tooltip_carries_the_reason() -> str:
        app.hover(*app.centre(top_run))
        tip = ""
        for _ in range(20):
            tip = str(
                app.evaluate("(document.querySelector('[role=tooltip]')?.textContent || '').trim()")
            )
            if tip:
                break
            time.sleep(0.2)
        run.expect(
            tip.startswith("Run ·") and "Mesh stage" in tip,
            f"the disabled Run's tooltip reads {tip!r}",
        )
        app.shot("run-top-bar-disabled")
        return tip

    def ready_enables_run() -> str:
        app.evaluate("window.__fakeRun.setReady(true)")
        wait_for(lambda: not disabled(top_run), "the top bar's Run to be enabled")
        run.expect(not disabled(panel_run), "the panel's Run is not enabled")
        run.expect(not present("[data-ws='run-why']"), "the reason is still shown")
        return "Run is enabled once solve.check says ready"

    def run_starts_from_the_top_bar() -> str:
        app.evaluate(
            "window.__fakeRun.scenario = {iterations: 52, tick: 400, perTick: 2, final: 'done'}"
        )
        press(top_run)
        wait_for(lambda: text(state_text).startswith("Running"), "the state to read Running")
        label = attr(top_run, "aria-label")
        run.expect(label == "Stop the run", f"the top bar reads {label!r} while running")
        run.expect(text(panel_run).endswith("Stop"), "the panel's button does not say Stop")
        wait_for(lambda: count("[data-ws='run-chart'] path.res-line") >= 2, "the chart's lines")
        wait_for(lambda: text(readout).startswith("Run · it "), "the readout to count iterations")
        role = attr(".mesh-bar[aria-label='Run progress']", "role")
        run.expect(role == "progressbar", "the progress bar is missing")
        names = app.evaluate(
            "[...document.querySelectorAll('[data-ws=run-chart] .run-legend button')]"
            ".map((b) => b.textContent.trim()).join(',')"
        )
        run.expect(names == "Ux,Uy,Uz,p", f"the legend reads {names!r}")
        run.expect(
            present("[data-ws='run-chart'] line.res-target"), "the convergence target is not drawn"
        )
        app.shot("run-panel-running")
        return f"{text(state_text)}; {text(readout)}"

    def residuals_tab_draws_the_run() -> str:
        press_text("[role=tab]", "Residuals")
        wait_for(lambda: present("[data-ws='residuals-live']"), "the live Residuals tab")
        run.expect(
            count("[data-ws='residuals-live'] path.res-line") >= 2,
            "the Residuals tab draws fewer than two lines",
        )
        note = str(app.evaluate("document.querySelector('.residuals-note')?.textContent || ''"))
        run.expect(note == "", "the demo-traces note is still shown")
        labels = app.evaluate(
            "[...document.querySelectorAll('[data-ws=residuals-live] .res-tick')]"
            ".map((e) => e.textContent).join(' ')"
        )
        run.expect("1e-" in str(labels), f"the log axis has no decade labels: {labels!r}")
        app.shot("run-residuals-tab")
        return "the tab draws the run's lines on a log axis with the target"

    def job_log_shows_the_solver() -> str:
        press_text("[role=tab]", "Log")
        wait_for(lambda: present(".log-views"), "the Log tab's choice")
        press_text(".log-views [role=radio]", "Job")
        wait_for(lambda: present("[data-ws='job-log']"), "the job log")
        wait_for(lambda: "Time = " in text("[data-ws='job-log']"), "foamRun lines")
        log = text("[data-ws='job-log']")
        run.expect("smoothSolver" in log, f"the job log reads {log[:80]!r}")
        console = int(
            app.evaluate(
                "window.__stokes.getState().consoleLines"
                ".filter((l) => /run\\.(log|residual|progress)/.test(l.text)).length"
            )
        )
        run.expect(console == 0, f"{console} job lines reached the console")
        app.shot("run-job-log")
        press_text(".log-views [role=radio]", "Events")
        wait_for(lambda: not present("[data-ws='job-log']"), "the Events view")
        events = text("[data-ws-panel='output']")
        run.expect("run.started" in events, "the Events view lost the run.started event")
        press_text(".log-views [role=radio]", "Job")
        return "Job shows foamRun lines, none reached the console; Events still lists run.started"

    def the_run_ends_with_a_toast_and_a_readout() -> str:
        wait_for(lambda: text(state_text).startswith("Finished"), "the run to finish", 40.0)
        wait_for(lambda: "converged in 52 iterations" in text(".toast-region"), "the toast")
        toast = text(".toast-region")
        run.expect("Run converged in 52 iterations" in toast, f"the toast reads {toast!r}")
        run.expect(
            text(readout) == "Run · it 52 · converged", f"the readout reads {text(readout)!r}"
        )
        run.expect(not disabled(top_run), "Run is not enabled again")
        run.expect(attr(top_run, "aria-label") == "Run the simulation", "the button is still Stop")
        tone = attr("[data-ws='run-state']", "data-tone")
        run.expect(tone == "ok", f"the finished state's tone is {tone!r}")
        press_text("[role=tab]", "Residuals")
        app.shot("run-finished")
        return f"{toast}; {text(readout)}"

    def a_second_run_stops() -> str:
        app.evaluate(
            "window.__fakeRun.scenario = {iterations: 5000, tick: 150, perTick: 1, final: 'done'}"
        )
        press(top_run)
        wait_for(lambda: text(state_text).startswith("Running"), "the second run to start")
        wait_for(lambda: text(readout).startswith("Run · it "), "the readout")
        run.expect(attr(top_run, "aria-label") == "Stop the run", "the top bar does not offer Stop")
        press(top_run)
        wait_for(lambda: text(readout) == "Run · cancelled", "the readout to read cancelled")
        run.expect(
            attr(top_run, "aria-label") == "Run the simulation", "Run is not back after Stop"
        )
        run.expect(not disabled(top_run), "Run is disabled after Stop")
        run.expect(
            text(state_text).startswith("Stopped at iteration"), f"reads {text(state_text)!r}"
        )
        app.shot("run-stopped")
        return text(readout)

    def not_converged_is_not_an_error() -> str:
        app.evaluate(
            "window.__fakeRun.scenario = {iterations: 12, tick: 60, perTick: 3,"
            " final: 'done_unconverged'}"
        )
        press(top_run)
        wait_for(lambda: text(state_text).startswith("Finished, not converged"), "the end", 30.0)
        tone = attr("[data-ws='run-state']", "data-tone")
        run.expect(tone == "warn", f"an unconverged run's tone is {tone!r}, not warn")
        run.expect(
            text(readout) == "Run · it 12 · not converged", f"the readout reads {text(readout)!r}"
        )
        run.expect(
            "without converging" in text(".toast-region"), f"toast: {text('.toast-region')!r}"
        )
        return text(state_text)

    def interrupted_is_not_a_spinner() -> str:
        app.evaluate(
            "window.__fakeRun.scenario = {iterations: 5000, tick: 200, perTick: 1, final: 'done'}"
        )
        press(top_run)
        wait_for(lambda: text(state_text).startswith("Running"), "the run to start")
        app.evaluate("window.__fakeRun.endWith('interrupted')")
        wait_for(lambda: text(state_text).startswith("Interrupted"), "the interrupted state")
        spin = count("[data-ws='run-state'] .mesh-spin")
        run.expect(spin == 0, "an interrupted run still shows a spinner")
        run.expect(text(readout) == "Run · interrupted", f"the readout reads {text(readout)!r}")
        return text(state_text)

    def the_engine_choice_lists_what_is_coming() -> str:
        wait_for(lambda: present("[data-ws='run-engines']"), "the engine choice")
        built = app.evaluate(
            "[...document.querySelectorAll('[data-ws=run-engines] [role=radio]')]"
            ".map((r) => r.textContent.trim() + ':' + r.getAttribute('aria-checked') + ':'"
            " + (r.getAttribute('aria-disabled') === 'true')).join('|')"
        )
        run.expect(
            "incompressibleFluid:true:false" in str(built) and "kOmegaSST" in str(built),
            f"the engine choice reads {built!r}",
        )
        run.expect("later task" in str(built), "the unbuilt engine does not say why")
        return str(built)[:120]

    def a_settings_edit_is_one_configure() -> str:
        field = "[data-ws='run-settings'] input[aria-label*='terations' i]"
        run.expect(present(field), "the Solve settings card has no Iterations field")
        app.evaluate("window.__fakeRun.calls.length = 0")

        def type_into(value: str) -> None:
            press(field)
            app.evaluate(f"document.querySelector({json.dumps(field)}).select()")
            app.dev.call("Input.insertText", {"text": value})
            app.press("Enter", "Enter", 13)

        type_into("0")
        wait_for(lambda: "at least 1" in text("[data-ws='run-settings']"), "the refusal's words")
        type_into("800")
        wait_for(
            lambda: bool(
                app.evaluate(
                    "window.__stokes.getState().tree.children.find((c) => c.path === '/solver')"
                    "?.children?.find((c) => c.type === 'solver_settings')?.params.iterations"
                    " === 800"
                )
            ),
            "the node to show 800",
        )
        calls = int(
            app.evaluate("window.__fakeRun.calls.filter((c) => c === 'solve.configure').length")
        )
        run.expect(calls == 2, f"{calls} solve.configure calls for two edits")
        app.shot("run-settings")
        return "a refusal reads as the command's words; a good edit reaches the node"

    run.step("the Solve stage brings the Solve tab forward", the_stage_brings_the_tab_forward)
    run.step("with no mesh, Run is disabled and says why", not_ready_says_why)
    run.step("the disabled Run's tooltip carries the reason", tooltip_carries_the_reason)
    run.step("a ready case enables Run", ready_enables_run)
    run.step("Run starts a run: Stop, the chart, the readout", run_starts_from_the_top_bar)
    run.step("the Residuals tab draws the run with its target", residuals_tab_draws_the_run)
    run.step("the Job log shows the solver and not the console", job_log_shows_the_solver)
    run.step("the end is a toast and a readout", the_run_ends_with_a_toast_and_a_readout)
    run.step("Run again, then Stop", a_second_run_stops)
    run.step("a run that stops short is not an error", not_converged_is_not_an_error)
    run.step("an interrupted run is not a spinner", interrupted_is_not_a_spinner)
    run.step(
        "the engine choice lists the engines, built or not", the_engine_choice_lists_what_is_coming
    )
    run.step("a settings edit is one solve.configure", a_settings_edit_is_one_configure)
    app.evaluate("window.__stokes.getState().setStage('Geometry')")
    time.sleep(0.3)


FAKE_RESULTS_SCRIPT = Path(__file__).resolve().parent / "fake_results.js"
RESULTS_SHOTS = Path(__file__).resolve().parent.parent / ".mailbox" / "shots"


def check_results(run: Checks, app: App) -> None:
    """The Results stage on real presses: field colours, the legend, the slice (T-218).

    The SERVER half of the results (`run.fields` and the `/api/results/*` routes) is played
    from `tools/fake_results.js` with bodies `tools/fake_results.py` builds from the demo
    project's own mesh (needs `--project` made by `tools/mesh_demo_project.py`; without one
    the steps are skipped and say so). The store, the controller, the scene, the panel, the
    legend and every click are real.

    Steps: a run's end reads `run.fields` and fetches nothing yet; the Results stage colours
    every patch with |U| and shows the legend; p replaces it (a real key press on the field
    select) and reads in the kinematic unit; the range can be locked and typed, and Auto
    restores it; the mid-plane slice is one more mesh source; Section view and a click on a
    patch still work with the tint on; a patch the server has no field for stays neutral; a
    new run clears the tint and its end brings it back.
    """
    if app.session is None:
        print("  skip the Results checks: an attached window has no session to script")
        return
    state = "window.__stokes.getState()"
    deadline = time.monotonic() + 45.0
    count = 0
    while time.monotonic() < deadline and count < 1:
        count = int(app.evaluate(f"{state}.geometry.filter((g) => g.kind === 'mesh').length") or 0)
        if count < 1:
            time.sleep(0.5)
    if count < 1:
        print("  skip the Results checks: no mesh patches (see tools/mesh_demo_project.py)")
        return
    from fake_results import build

    built = build(app.session)
    app.evaluate(FAKE_RESULTS_SCRIPT.read_text(encoding="utf-8"))
    app.evaluate(
        "window.__fakeResults.install("
        + json.dumps({"routes": built["routes"], "reply": built["reply"]})
        + ")"
    )
    fake = "window.__fakeResults"
    slice_triangles = 2 * 100 * 10

    def js(expression: str) -> Any:
        return app.evaluate(expression)

    def wait_for(condition: Any, what: str, seconds: float = 20.0) -> None:
        limit = time.monotonic() + seconds
        while time.monotonic() < limit:
            if condition():
                return
            time.sleep(0.1)
        raise Failure(
            f"timed out waiting for {what}; results are {js(f'{state}.results.status')!r}, "
            f"asked {js(f'{fake}.asked')!r}"
        )

    baseline = [0]

    def tinted() -> int:
        """Triangles with a tint beyond what the tags of the geometry already paint."""
        return int(js("window.__stokesScene.perf().tagTriangles") or 0) - baseline[0]

    def mesh_triangles(name: str | None = None) -> int:
        match = "" if name is None else f" && g.name === {json.dumps(name)}"
        return int(
            js(
                f"{state}.geometry.filter((g) => g.kind === 'mesh' && g.part !== 'mesh:slice'"
                f"{match}).reduce((n, g) => n + g.triangles, 0)"
            )
            or 0
        )

    def info() -> dict[str, Any]:
        return dict(js("window.__stokesScene.meshInfo()"))

    def legend() -> dict[str, Any]:
        found = js(
            "(() => { const el = document.querySelector('[data-ws=field-legend]');"
            " if (!el) return null;"
            " return {title: el.querySelector('.field-legend-title').textContent,"
            " ticks: [...el.querySelectorAll('li')].map((li) => li.textContent),"
            " note: (el.querySelector('.field-legend-note') || {}).textContent || ''}; })()"
        )
        return dict(found) if isinstance(found, dict) else {}

    def switch_at(label: str) -> tuple[float, float]:
        found = js(
            "(() => { const sw = [...document.querySelectorAll('[role=switch]')].find((el) => {"
            " const id = el.getAttribute('aria-labelledby');"
            " const l = id && document.getElementById(id);"
            f" return l && l.textContent.trim() === {json.dumps(label)}; }});"
            " if (!sw) return null; sw.scrollIntoView({block: 'center'});"
            " const r = sw.getBoundingClientRect();"
            " return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()"
        )
        run.expect(isinstance(found, dict), f"no switch named {label!r} in the page")
        return float(found["x"]), float(found["y"])

    def switch_on(label: str) -> bool:
        return bool(
            js(
                "[...document.querySelectorAll('[role=switch]')].some((el) => {"
                " const id = el.getAttribute('aria-labelledby');"
                " const l = id && document.getElementById(id);"
                f" return l && l.textContent.trim() === {json.dumps(label)}"
                " && el.getAttribute('aria-checked') === 'true'; })"
            )
        )

    def appearance(theme: str) -> None:
        """Switch the appearance with a real press on the top bar's sun/moon button."""
        if js("document.documentElement.dataset.theme") == theme:
            return
        app.click(*app.centre("[data-ws-item='appearance']"))
        wait_for(
            lambda: js("document.documentElement.dataset.theme") == theme, f"the {theme} theme"
        )
        time.sleep(0.8)

    def themed_shots(name: str) -> None:
        for theme in ("dark", "light"):
            appearance(theme)
            app.capture(RESULTS_SHOTS / f"results-{name}-{theme}.png")
        appearance("dark")

    # The Results tab is a built-in panel the server's workspace defaults do not place yet (the
    # Python mirror is a separate change): the harness places it in the right slot as a user would.
    js(
        "(() => { const w = window.__workspace.getState();"
        " const next = JSON.parse(JSON.stringify(w.document.layout));"
        " if (next.right.panels.some((p) => p.id === 'results')) return;"
        " next.right.panels.push({id: 'results', order: next.right.panels.length});"
        " w.setValue(['layout'], next); })()"
    )
    wait_for(
        lambda: bool(
            js(
                "window.__workspace.getState().document.layout.right.panels"
                ".some((p) => p.id === 'results')"
            )
        ),
        "the Results tab to be placed",
    )
    # A larger window than the default, so the captures show the duct and the whole panel.
    app.dev.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1500, "height": 900, "deviceScaleFactor": 1, "mobile": False},
    )
    js("window.__stokes.setState({drawerOpen: false})")
    appearance("dark")
    js(f"{state}.setStage('Solve'); {state}.setMeshDisplay('surface'); {state}.setSection(false)")
    time.sleep(0.5)
    # The viewport may be mounting again after the layout change: its probe is back when the
    # scene is.
    wait_for(lambda: bool(js("!!window.__stokesScene")), "the viewport's scene probe")
    baseline[0] = tinted()
    total = mesh_triangles()

    def a_run_end_reads_run_fields() -> str:
        js(f"{fake}.endRun('r_e2e', 'done')")
        wait_for(lambda: js(f"{state}.results.status") == "ready", "run.fields to be read")
        calls = int(js(f"{fake}.calls.length"))
        run.expect(calls == 1, f"{calls} run.fields calls for one run end")
        fetched = int(js(f"{fake}.asked.filter((p) => p.includes('/field/')).length"))
        run.expect(fetched == 0, f"{fetched} field requests outside the Results stage")
        run.expect(tinted() == 0 and not legend(), "the Solve stage shows colours or a legend")
        names = js(f"{state}.results.data.fields.map((f) => f.name)")
        return f"run.fields read once ({names}); no field fetched, no tint, no legend in Solve"

    def the_results_stage_colours_with_speed() -> str:
        js(f"{state}.setStage('Results'); {state}.requestFit()")
        wait_for(lambda: tinted() == total and total > 0, "every patch to be tinted")
        shown = info()
        run.expect(
            shown["meshLayer"] and not shown["geometryLayer"],
            f"the Results stage did not swap the layers: {shown}",
        )
        fetched = int(
            js(f"{fake}.asked.filter((p) => p.includes('/field/') && p.endsWith('U_mag')).length")
        )
        run.expect(fetched == count, f"{fetched} |U| requests for {count} patches")
        shown_legend = legend()
        run.expect(shown_legend.get("title") == "|U| (m/s)", f"legend: {shown_legend}")
        run.expect(len(shown_legend.get("ticks", [])) == 5, f"legend ticks: {shown_legend}")
        run.expect(
            shown_legend["ticks"][-1] == "0", f"the lowest tick is {shown_legend['ticks'][-1]!r}"
        )
        run.expect(
            bool(js("document.querySelector('[data-ws=results-panel]') !== null")),
            "the Results panel is not in front in the Results stage",
        )
        time.sleep(1.2)
        themed_shots("u-duct")
        return f"{total} triangles tinted; legend {shown_legend['title']} {shown_legend['ticks']}"

    def p_replaces_it_with_a_key_press() -> str:
        js("document.querySelector('[data-ws=results-select]').focus()")
        asked_before = int(js(f"{fake}.asked.length"))
        started = time.perf_counter()
        app.press("ArrowDown", "ArrowDown", 40)
        wait_for(
            lambda: (
                js(f"{state}.results.view.field") == "p"
                and int(js(f"{fake}.asked.filter((p) => p.endsWith('field=p')).length")) >= count
                and tinted() == total
                and legend().get("title", "").startswith("p/rho")
            ),
            "p to colour the patches",
        )
        ms = round((time.perf_counter() - started) * 1000)
        shown_legend = legend()
        run.expect(shown_legend["title"] == "p/rho (m2/s2)", f"legend: {shown_legend}")
        top = shown_legend["ticks"][0]
        expected = float(f"{built['dp']:.3g}")
        run.expect(abs(float(top) - expected) < 1e-4, f"the top tick {top} is not {expected}")
        again = int(js(f"{fake}.asked.length")) - asked_before
        run.expect(again == count, f"{again} requests to switch to p ({count} patches)")
        time.sleep(1.0)
        themed_shots("p-duct")
        return (
            f"legend {shown_legend['title']} {top} .. 0 (expected dp/rho {built['dp']:.4g}); "
            f"{ms} ms from key press to coloured"
        )

    def the_range_can_be_locked_and_typed() -> str:
        fetched = int(js(f"{fake}.asked.length"))
        app.click(*switch_at("Lock range"))
        run.expect(switch_on("Lock range"), "the Lock range switch did not turn on")
        field = "[data-ws='results-range-max']"
        app.click(*app.centre(field))
        js(f"document.querySelector({json.dumps(field)}).select()")
        app.dev.call("Input.insertText", {"text": "0.01"})
        app.press("Enter", "Enter", 13)
        wait_for(
            lambda: legend().get("ticks", [""])[0] == "0.01", "the legend to show the typed maximum"
        )
        shown_legend = legend()
        run.expect(shown_legend.get("note") == "fixed range", f"legend: {shown_legend}")
        run.expect(int(js(f"{fake}.asked.length")) == fetched, "a range change fetched something")
        app.shot("results-range-fixed")
        app.click(*switch_at("Lock range"))
        wait_for(lambda: legend().get("ticks", [""])[0] == "0.0267", "Auto to restore the range")
        run.expect(legend().get("note") == "", "the fixed-range note stayed after Auto")
        return "typed 0..0.01 fixed the legend and fetched nothing; Auto restored 0.0267"

    def the_slice_is_one_more_mesh_source() -> str:
        import tempfile

        from PIL import Image

        scratch = Path(tempfile.mkdtemp(prefix="stokes-slice-"))
        slice_name = "Mid-plane slice (mesh)"
        run.expect(not switch_on("Mid-plane slice"), "the slice starts on")
        run.expect(js(f"{state}.section") is None, "a section is on before the slice is")

        def settle_view() -> None:
            js(f"{state}.requestFit()")
            time.sleep(1.2)

        def picture(path: Path, theme: str) -> Any:
            appearance(theme)
            app.capture(path)
            return Image.open(path).convert("RGB")

        def changed_share(before: Any, after: Any, box: dict[str, Any]) -> float:
            """Of the duct's coloured pixels inside the slice's screen box, the share that changed.

            The duct is a thin bar on the screen, so the box is mostly empty: only pixels that were
            coloured by the field before (a high chroma, which the grid and the backdrop lack)
            count.
            """
            scale = before.width / float(js("window.innerWidth"))
            x0, x1 = float(box["x0"]), float(box["x1"])
            y0, y1 = float(box["y0"]), float(box["y1"])
            run.expect(x1 - x0 > 40 and y1 - y0 > 10, f"the slice is tiny on screen: {box}")
            moved = candidates = 0
            for y in range(int(y0 * scale), int(y1 * scale), 2):
                for x in range(int(x0 * scale), int(x1 * scale), 2):
                    a, b = before.getpixel((x, y)), after.getpixel((x, y))
                    if max(a) - min(a) < 50:
                        continue
                    candidates += 1
                    if max(abs(a[i] - b[i]) for i in range(3)) > 30:
                        moved += 1
            run.expect(
                candidates > 200,
                f"only {candidates} field-coloured pixels in the slice's box {box}",
            )
            return moved / candidates

        def cut_face(label: str) -> str:
            """Slice off (the closed body), then on: the cut face must differ from the body
            there."""
            settle_view()
            before = {t: picture(scratch / f"{label}-{t}-before.png", t) for t in ("dark", "light")}
            appearance("dark")
            app.click(*switch_at("Mid-plane slice"))
            wait_for(lambda: info()["patches"] == count + 1, "the slice to join the scene")
            wait_for(lambda: switch_on("Mid-plane slice"), "the slice switch to read on")
            wait_for(
                lambda: int(js("window.__stokesScene.clippingCount()") or 0) == 1,
                "the patches to be clipped at the slice",
            )
            wait_for(lambda: tinted() >= total + slice_triangles, "the slice to be tinted")
            time.sleep(1.5)
            cut = js(f"{state}.section")
            run.expect(
                isinstance(cut, dict) and cut["axis"] == "z", f"the slice set no z section: {cut}"
            )
            box = js(f"window.__stokesScene.partBounds()[{json.dumps(slice_name)}]")
            run.expect(isinstance(box, dict), f"the slice has no screen box: {box}")
            shares = []
            for theme in ("dark", "light"):
                after = picture(RESULTS_SHOTS / f"{label}-cut-{theme}.png", theme)
                share = changed_share(before[theme], after, box)
                run.expect(
                    share > 0.3,
                    f"{label} {theme}: only {share:.0%} of the slice differs from the closed body",
                )
                shares.append(f"{theme} {share:.0%}")
            appearance("dark")
            return ", ".join(shares)

        # p is the field on the surfaces now: its cut face first.
        run.expect(js(f"{state}.results.view.field") == "p", "p is not the field")
        p_shares = cut_face("slice-p")
        section_during = js(f"{state}.section")
        # Off: the section is what it was (none), the walls close the slice in again.
        app.click(*switch_at("Mid-plane slice"))
        wait_for(lambda: info()["patches"] == count, "the slice to leave the scene")
        wait_for(lambda: js(f"{state}.section") is None, "the section to be restored")
        run.expect(
            int(js("window.__stokesScene.clippingCount()") or 0) == 0,
            "the clip stayed after the slice went",
        )
        # |U| on the slice.
        js(
            "window.__stokes.setState((s) => ({results: {...s.results,"
            " view: {...s.results.view, field: 'U_mag'}}}))"
        )
        wait_for(lambda: legend().get("title") == "|U| (m/s)", "|U| on the patches")
        u_shares = cut_face("slice-u")
        wait_for(lambda: tinted() == total + slice_triangles, "the slice to be tinted with |U|")
        return (
            f"mesh:slice drawn ({info()['patches']} mesh sources), "
            f"{slice_triangles} triangles tinted; "
            f"the cut ({section_during}) exposes it: p {p_shares}; |U| {u_shares}; "
            "off restores the section"
        )

    def section_and_picking_keep_working() -> str:
        js(f"{state}.setSection(false); {state}.requestFit()")
        time.sleep(1.0)
        before = tinted()
        js(f"{state}.setPickMode('body'); {state}.clearPick()")
        found = js(
            "(() => { const b = window.__stokesScene.partBounds();"
            " const key = Object.keys(b).find((k) => k.startsWith('walls')) ?? Object.keys(b)[0];"
            " const c = document.querySelector('canvas').getBoundingClientRect(); const q = b[key];"
            " return {key, x: c.left + (q.x0 + q.x1) / 2, y: c.top + (q.y0 + q.y1) / 2}; })()"
        )
        app.click(float(found["x"]), float(found["y"]))
        chosen = js(
            f"(() => {{ const s = {state}; const walk = (n) => n.id === s.selectedId ? n"
            " : (n.children || []).map(walk).find(Boolean);"
            " const hit = s.tree && s.selectedId ? walk(s.tree) : null;"
            " return {type: hit ? hit.type : null, bodies: s.picked.bodies.length}; })()"
        )
        run.expect(
            isinstance(chosen, dict) and chosen["type"] == "mesh_patch" and chosen["bodies"] == 0,
            f"a click on {found['key']} with the tint on selected {chosen}",
        )
        run.expect(js(f"{state}.stage") == "Results", "picking a patch left the Results stage")
        run.expect(tinted() == before, f"the pick changed the tint: {before} -> {tinted()}")
        bounds = js("window.__stokesScene.modelBounds()")
        middle = (float(bounds["min"][2]) + float(bounds["max"][2])) / 2
        js(f"{state}.setSection(true); {state}.setSectionPlane('z', {middle})")
        time.sleep(1.0)
        clipping = int(js("window.__stokesScene.clippingCount()") or 0)
        run.expect(clipping == 1, f"the section plane is not on the renderer: {clipping}")
        run.expect(tinted() == before, f"the section changed the tint: {before} -> {tinted()}")
        run.expect(info()["patchesShown"] == count + 1, f"the section hid patches: {info()}")
        js(f"{state}.setSection(false)")
        return (
            f"a click on {found['key']} selected its mesh_patch row and picked no body; "
            f"the cut left {before} triangles tinted"
        )

    def a_patch_without_a_field_stays_neutral() -> str:
        outlet = built["nodes"]["outlet"]
        key = f"/api/results/field/r_e2e/{outlet}?field=U_mag"
        js(f"{fake}.status.set({json.dumps(key)}, 404)")
        js(f"{fake}.endRun('r_e2e', 'done')")
        outlet_triangles = mesh_triangles("outlet (mesh)")
        run.expect(outlet_triangles > 0, "no outlet patch in the scene")
        wait_for(
            lambda: tinted() == total + slice_triangles - outlet_triangles,
            "the outlet to stay neutral while the others are tinted",
        )
        app.shot("results-neutral-patch")
        js(f"{fake}.status.delete({json.dumps(key)})")
        return (
            f"outlet answered 404: its {outlet_triangles} triangles keep the mesh colour, "
            "never zero"
        )

    def a_new_run_clears_the_tint_and_its_end_brings_it_back() -> str:
        js(f"{fake}.startRun('r_new')")
        wait_for(lambda: tinted() == 0 and not legend(), "the tint to clear when a new run starts")
        run.expect(js(f"{state}.results.data") is None, "the held results survived a new run")
        info_now = info()
        run.expect(info_now["geometryLayer"], f"the geometry did not come back: {info_now}")
        js(f"{fake}.endRun('r_new', 'done')")
        wait_for(
            lambda: tinted() > 0 and bool(legend()), "the tint to return when the new run ends"
        )
        return "a new run cleared the tint and the legend; its end brought them back"

    run.step("a run's end reads run.fields and fetches nothing yet", a_run_end_reads_run_fields)
    run.step(
        "the Results stage colours the patches with |U| and shows the legend",
        the_results_stage_colours_with_speed,
    )
    run.step("a key press on the field select switches to p", p_replaces_it_with_a_key_press)
    run.step(
        "the range can be locked and typed; Auto restores it", the_range_can_be_locked_and_typed
    )
    run.step("the mid-plane slice is one more mesh source", the_slice_is_one_more_mesh_source)
    run.step(
        "Section view and picking keep working with the tint on", section_and_picking_keep_working
    )
    run.step(
        "a patch the server has no field for stays neutral", a_patch_without_a_field_stays_neutral
    )
    run.step(
        "a new run clears the tint and its end brings it back",
        a_new_run_clears_the_tint_and_its_end_brings_it_back,
    )
    js(f"{state}.setStage('Geometry')")
    app.dev.call("Emulation.clearDeviceMetricsOverride", {})
    time.sleep(0.3)


def check_settings_dialog(run: Checks, app: App) -> None:
    """The Settings dialog under real presses and keys (T-178).

    Opened from the Project menu, the palette and ``Ctrl+Shift+,`` (``Ctrl+,`` stays on Display
    settings); five sections in order; the mouse scheme chosen in it applies at once and is saved
    as the user's preference; the default import unit is the units question's starting answer;
    Escape closes the top dialog only, and the focus goes back to the Project menu button.

    The preferences are written to the user workspace file, which is why the harness must run with
    ``STOKES_HOME`` pointing at a temporary folder; the check resets ``preferences`` when it ends.
    """
    state = "window.__stokes.getState()"
    workspace = "window.__workspace.getState()"
    project_button = "[aria-label^='Project menu']"
    dialog_js = (
        "((title) => Array.from(document.querySelectorAll(\"[role='dialog']\")).find((d) =>"
        " document.getElementById(d.getAttribute('aria-labelledby') || '')?.textContent === title)"
        " ?? null)"
    )
    any_dialog = "[role='dialog'][aria-labelledby]"
    tabs = "[aria-label='Settings sections']"

    def js(expression: str) -> Any:
        return app.evaluate(expression)

    def dialog_open(title: str) -> bool:
        return bool(js(f"Boolean({dialog_js}({json.dumps(title)}))"))

    def wait_for(condition: Any, what: str, seconds: float = 8.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.1)
        raise Failure(f"timed out waiting for {what}")

    def press_text(selector: str, text: str, scope: str = "") -> None:
        """A real click on the first ``selector`` element whose text starts with ``text``."""
        js(
            "document.querySelector('[data-harness-press]')?.removeAttribute('data-harness-press');"
            f"[...document.querySelectorAll({json.dumps((scope + ' ' + selector).strip())})]"
            f".find((e) => (e.textContent || '').trim().startsWith({json.dumps(text)}))"
            "?.setAttribute('data-harness-press', '1')"
        )
        if not js("Boolean(document.querySelector('[data-harness-press=\"1\"]'))"):
            raise Failure(
                f"nothing matching {(scope + ' ' + selector).strip()!r} starts with {text!r}"
            )
        # A tall popover scrolls inside a short window: bring the target into view first.
        js("document.querySelector('[data-harness-press=\"1\"]').scrollIntoView({block: 'center'})")
        time.sleep(0.2)
        app.click(*app.centre("[data-harness-press='1']"))

    def press_selector(selector: str) -> None:
        app.click(*app.centre(selector))

    def page_text() -> str:
        return str(js("document.querySelector('#settings-page')?.innerText ?? ''"))

    def tab(name: str) -> None:
        press_text("[role='tab']", name, tabs)
        wait_for(
            lambda: bool(
                js(
                    "document.querySelector(\"[aria-label='Settings sections']"
                    ' [role=tab][aria-selected=true]")'
                    "?.textContent?.trim()"
                    f" === {json.dumps(name)}"
                )
            ),
            f"the {name} tab",
        )

    def preference(path: str) -> Any:
        return js(
            f"(() => {{ let v = {workspace}.document;"
            f" for (const k of {json.dumps(path)}.split('.')) v = v?.[k]; return v ?? null; }})()"
        )

    def choose(selector: str, value: str) -> None:
        """Set a drawn ``<select>`` the way a change event does, which React's onChange reads."""
        js(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            " Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, "
            f"{json.dumps(value)}); el.dispatchEvent(new Event('change', {{bubbles: true}})); }})()"
        )

    def shown(selector: str) -> bool:
        return bool(js(f"Boolean(document.querySelector({json.dumps(selector)}))"))

    def open_from_the_project_menu() -> None:
        press_selector(project_button)
        wait_for(lambda: shown("[role='menu']"), "the Project menu")
        press_text("[role='menuitem']", "Settings")
        wait_for(lambda: dialog_open("Settings"), "the Settings dialog")

    def escape_until_closed() -> None:
        for _ in range(4):
            if not shown(any_dialog):
                return
            app.press("Escape", "Escape", 27)

    def opens_from_the_project_menu_with_five_sections() -> str:
        escape_until_closed()
        open_from_the_project_menu()
        names = js(
            "Array.from(document.querySelectorAll(\"[aria-label='Settings sections'] [role=tab]\"))"
            ".map((t) => (t.textContent || '').trim())"
        )
        run.expect(
            names == ["Appearance", "Units", "Mouse", "Workspace", "About"],
            f"the sections are not the five in order: {names}",
        )
        selected = js(
            "document.querySelectorAll(\"[aria-label='Settings sections']"
            ' [aria-selected=true]").length'
        )
        run.expect(selected == 1, f"{selected} tabs are selected")
        orientation = js(
            "document.querySelector(\"[aria-label='Settings sections']\")"
            "?.getAttribute('aria-orientation')"
        )
        run.expect(orientation == "vertical", f"the tab list is {orientation!r}, not vertical")
        run.expect(
            js(
                "document.querySelector('#settings-page')?.getAttribute('aria-labelledby')?.startsWith('settings-tab-')"
            )
            is True,
            "the tab panel is not labelled by its tab",
        )
        return "Settings… is in the Project menu; five tabs, vertical"

    def appearance_tab_has_its_controls_and_the_grid_writes_the_workspace() -> str:
        tab("Appearance")
        text = page_text()
        for want in ("Style", "Appearance", "Reduced motion", "Ground grid", "Theme editor"):
            run.expect(
                want.lower() in text.lower(), f"the Appearance tab has no {want!r}: {text!r}"
            )
        before = preference("viewport.grid")
        grid = "[data-setting='appearance.grid'] [role=switch]"
        press_selector(grid)
        wait_for(
            lambda: preference("viewport.grid") == (not before), "the grid to follow the switch"
        )
        press_selector(grid)
        wait_for(lambda: preference("viewport.grid") == before, "the grid back")
        return "the Ground grid switch writes viewport.grid"

    def mouse_tab_applies_a_scheme_at_once_and_saves_it() -> str:
        tab("Mouse")
        text = page_text()
        run.expect(
            "The island's Mouse menu changes it for this session; this setting is remembered."
            in text,
            f"the Mouse help line is missing: {text!r}",
        )
        run.expect(
            "Reset workspace also resets" in text or "Reset workspace resets this too" in text,
            "the help does not say Reset resets it",
        )
        run.expect(
            not shown("[data-setting='mouse.orbitLeft']"),
            "the orbit switch shows for a scheme that has none",
        )
        press_text("[role=radio]", "Blender", "[data-setting='mouse.scheme']")
        mode = js(f"{state}.navMode")
        run.expect(mode == "blender", f"clicking Blender left navMode = {mode!r}")
        time.sleep(0.5)
        saved = preference("preferences.mouse.scheme")
        run.expect(saved == "blender", f"the workspace holds {saved!r}, not blender")
        source = js(f"{workspace}.sources['preferences.mouse.scheme'] ?? null")
        run.expect(
            source == "user", f"the scheme was stored in the {source!r} layer, not the user's"
        )
        return "navMode and the workspace preference (user layer) followed the click"

    def orbit_switch_is_for_select_orbit_only() -> str:
        press_text("[role=radio]", "Select / Orbit", "[data-setting='mouse.scheme']")
        wait_for(
            lambda: shown("[data-setting='mouse.orbitLeft'] [role=switch]"), "the orbit switch"
        )
        press_selector("[data-setting='mouse.orbitLeft'] [role=switch]")
        wait_for(lambda: js(f"{state}.orbitMode") is True, "orbitMode to follow the switch")
        wait_for(lambda: preference("preferences.mouse.orbitLeft") is True, "orbitLeft to be saved")
        press_text("[role=radio]", "CAD", "[data-setting='mouse.scheme']")
        wait_for(lambda: not shown("[data-setting='mouse.orbitLeft']"), "the switch to go")
        orbit = js(f"{state}.orbitMode")
        run.expect(orbit is False, "choosing CAD left the left-drag orbit switch on")
        return "the switch exists for Select / Orbit only, and writes orbitLeft"

    def a_console_change_shows_in_the_dialog() -> str:
        js(f"{workspace}.setValue(['preferences','mouse','scheme'], 'paraview', {{scope: 'user'}})")
        wait_for(lambda: js(f"{state}.navMode") == "paraview", "navMode to follow workspace.set")
        checked = js(
            "document.querySelector(\"[data-setting='mouse.scheme']"
            ' [role=radio][aria-checked=true]")'
            "?.textContent ?? ''"
        )
        run.expect("ParaView" in str(checked), f"the dialog still shows {checked!r}")
        return "workspace.set changes the scheme live and the dialog reads it back"

    def units_tab_changes_the_project_and_saves_the_default_import_unit() -> str:
        tab("Units")
        label = js(
            "document.querySelector(\"[data-setting='units.system'] .section-title\")"
            "?.textContent ?? ''"
        )
        run.expect(label == "Display units (this project)", f"the label is {label!r}")
        run.expect(
            js("document.querySelector(\"[data-setting='units.system'] select\")?.disabled")
            is False,
            "the display units are disabled with a project open",
        )
        was = js(f"{state}.project?.display_units")
        want = "mm" if was != "mm" else "si"
        select = "[data-setting='units.system'] select"
        choose(select, want)
        wait_for(lambda: js(f"{state}.project?.display_units") == want, "project.set_units to land")
        badge = js("document.querySelector('.tb-units')?.textContent?.trim() ?? ''")
        run.expect(badge == ("mm" if want == "mm" else "SI"), f"the units badge says {badge!r}")
        choose(select, str(was))
        wait_for(lambda: js(f"{state}.project?.display_units") == was, "the units to go back")
        choose("[data-setting='units.importUnit'] select", "ft")
        wait_for(
            lambda: preference("preferences.import.units") == "ft", "the import unit to be saved"
        )
        return f"project.set_units {was} to {want} and back; the import unit is saved as ft"

    def the_units_question_starts_from_the_default_import_unit() -> str:
        escape_until_closed()
        press_selector(project_button)
        wait_for(lambda: shown("[role='menu']"), "the Project menu")
        typed_path = js(
            "Array.from(document.querySelectorAll(\"[role='menuitem']\"))"
            ".some((e) => (e.textContent || '').startsWith('Server path'))"
        )
        if not typed_path:
            app.press("Escape", "Escape", 27)
            return "skipped: the host has a native picker, so there is no typed-path entry"
        press_text("[role='menuitem']", "Server path")
        wait_for(lambda: dialog_open("Import geometry"), "the path prompt")
        js(
            "(() => { const el = document.querySelector(\"[role='dialog'] input\");"
            " Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call("
            "el, 'C:\\\\parts\\\\bracket.stl');"
            " el.dispatchEvent(new Event('input', {bubbles: true})); })()"
        )
        press_text("button", "Import", "[role='dialog']")
        wait_for(lambda: dialog_open("Units of the file"), "the units question")
        value = js("document.querySelector(\"[role='dialog'] input\")?.value ?? null")
        run.expect(
            value == "ft",
            f"the units question starts from {value!r}, not the default import unit ft",
        )
        app.press("Escape", "Escape", 27)
        wait_for(lambda: not dialog_open("Units of the file"), "the question to close")
        return "the question starts from ft"

    def workspace_tab_disables_this_project_and_escape_closes_the_top_dialog_only() -> str:
        open_from_the_project_menu()
        tab("Workspace")
        reason = js(
            "document.querySelector(\"[data-setting='workspace.scope']"
            ' [role=radio][aria-disabled=true]")'
            "?.getAttribute('title') ?? null"
        )
        run.expect(
            reason == "Save the project to a folder first",
            f"'This project' is not disabled with its reason: {reason!r}",
        )
        press_text("[role=radio]", "This project", "[data-setting='workspace.scope']")
        run.expect(js(f"{workspace}.scope") == "user", "a disabled scope choice changed the scope")
        press_text("button", "Export", "#settings-page")
        wait_for(lambda: dialog_open("Export the workspace"), "the Export dialog")
        app.press("Escape", "Escape", 27)
        wait_for(lambda: not dialog_open("Export the workspace"), "Export to close")
        run.expect(dialog_open("Settings"), "Escape in Export also closed Settings")
        return "Escape closed Export only; 'This project' is disabled without a folder"

    def reset_dialog_names_the_preferences() -> str:
        press_text("button", "Reset workspace", "#settings-page")
        wait_for(lambda: dialog_open("Reset the workspace?"), "the Reset dialog")
        text = str(
            js("Array.from(document.querySelectorAll(\"[role='dialog']\")).pop()?.innerText ?? ''")
        )
        run.expect(
            "mouse scheme" in text, f"the Reset dialog does not mention the mouse scheme: {text!r}"
        )
        app.press("Escape", "Escape", 27)
        wait_for(lambda: not dialog_open("Reset the workspace?"), "Reset to close")
        run.expect(dialog_open("Settings"), "Escape in Reset also closed Settings")
        return "the Reset dialog names the mouse scheme and the import unit"

    def about_tab_reads_the_server() -> str:
        tab("About")
        wait_for(lambda: "…" not in page_text().split("Licence")[0], "the About rows to load")
        rows = js(
            "Object.fromEntries(Array.from(document.querySelectorAll('.settings-facts > div'))"
            ".map((r) => [r.children[0].textContent, r.children[1].textContent]))"
        )
        run.expect(
            isinstance(rows, dict) and bool(rows.get("Version")), f"About has no Version: {rows}"
        )
        for key in (
            "Python",
            "Platform",
            "Stokes home",
            "Workspace file (user)",
            "Licence",
            "Engine",
        ):
            run.expect(bool(rows.get(key)), f"About has no {key}: {rows}")
        run.expect(
            "not available" not in str(rows["Version"]),
            f"app.info is missing on this server: {rows}",
        )
        return f"version {rows['Version']}; home {rows['Stokes home']}"

    def notices_open_over_settings_and_escape_closes_them_only() -> str:
        press_text("button", "Third-party notices", "#settings-page")
        wait_for(lambda: dialog_open("Third-party notices"), "the notices dialog")
        wait_for(
            lambda: (
                int(
                    js(
                        "document.querySelector(\"textarea[aria-label='Third-party notices']\")"
                        "?.value.length ?? 0"
                    )
                )
                > 100
            ),
            "the notices text",
        )
        source = js(
            "Array.from(document.querySelectorAll(\"[role='dialog'] p.muted\"))"
            ".pop()?.textContent ?? ''"
        )
        app.press("Escape", "Escape", 27)
        wait_for(lambda: not dialog_open("Third-party notices"), "the notices to close")
        run.expect(dialog_open("Settings"), "Escape in the notices also closed Settings")
        return f"app.notices text shown ({str(source).strip()}); Escape closes it only"

    def escape_closes_settings_and_the_focus_returns() -> str:
        app.press("Escape", "Escape", 27)
        wait_for(lambda: not dialog_open("Settings"), "Settings to close")
        label = js("document.activeElement?.getAttribute('aria-label') ?? ''")
        run.expect(
            str(label).startswith("Project menu"),
            f"the focus is on {label!r}, not the Project menu button",
        )
        return "focus is back on the Project menu button"

    def the_chord_and_the_palette_open_it_and_ctrl_comma_is_still_display_settings() -> str:
        # Ctrl+Shift+, is modifiers 2 + 8. `key` is what a US keyboard sends with Shift held.
        app.press("<", "Comma", 188, modifiers=10)
        wait_for(lambda: dialog_open("Settings"), "Ctrl+Shift+, to open Settings")
        app.press("Escape", "Escape", 27)
        wait_for(lambda: not dialog_open("Settings"), "Settings to close")
        app.press("k", "KeyK", 75, modifiers=2)
        wait_for(lambda: shown("[role='dialog'][aria-label='Command palette']"), "the palette")
        app.dev.call("Input.insertText", {"text": "Open Settings"})
        time.sleep(0.4)
        app.press("Enter", "Enter", 13)
        wait_for(lambda: dialog_open("Settings"), "the palette's Open Settings")
        app.press("Escape", "Escape", 27)
        wait_for(lambda: not dialog_open("Settings"), "Settings to close")
        app.press(",", "Comma", 188, modifiers=2)
        wait_for(
            lambda: shown("[role='dialog'][aria-label='Display settings']"),
            "Ctrl+, to open Display settings",
        )
        press_text("button", "All settings")
        wait_for(lambda: dialog_open("Settings"), "All settings… to open Settings")
        run.expect(
            not shown("[role='dialog'][aria-label='Display settings']"),
            "the Display settings popover stayed open under Settings",
        )
        return (
            "Ctrl+Shift+, and the palette open Settings; Ctrl+, opens Display settings, "
            "whose All settings… opens it"
        )

    def the_dialog_fits_and_reads_in_every_theme() -> str:
        escape_until_closed()
        before = js(f"JSON.stringify({workspace}.document.theme)")
        seen = []
        try:
            for name in ("dark", "light", "cupertino", "ubuntu"):
                js(f"{workspace}.setValue(['theme'], {{name: {json.dumps(name)}}})")
                time.sleep(0.5)
                open_from_the_project_menu()
                tab("Units")
                box = js(
                    "(() => { const r = document.querySelector(\"[role='dialog'] .settings-body\")"
                    ".closest('.dialog').getBoundingClientRect();"
                    " return {top: r.top, bottom: r.bottom, left: r.left, right: r.right,"
                    " w: innerWidth, h: innerHeight}; })()"
                )
                run.expect(
                    box["top"] >= 0
                    and box["left"] >= 0
                    and box["bottom"] <= box["h"]
                    and box["right"] <= box["w"],
                    f"the dialog does not fit the window in {name}: {box}",
                )
                # The selected tab and the page text must be readable against their fill.
                ink = js(
                    "(() => { const t = document.querySelector(\"[aria-label='Settings sections']"
                    " [aria-selected=true]\"); const p = document.querySelector('#settings-page');"
                    " return {tab: getComputedStyle(t).color, tabFill: getComputedStyle(t)"
                    ".backgroundColor, page: getComputedStyle(p).color}; })()"
                )
                run.expect(
                    ink["tab"] != ink["tabFill"] and ink["page"] != "rgba(0, 0, 0, 0)",
                    f"unreadable in {name}: {ink}",
                )
                app.shot(f"settings-units-{name}")
                seen.append(name)
                escape_until_closed()
        finally:
            js(f"{workspace}.setValue(['theme'], JSON.parse({json.dumps(before)}))")
            time.sleep(0.4)
        return "fits and reads in " + ", ".join(seen)

    def a_theme_change_leaves_the_session_scheme_alone() -> str:
        escape_until_closed()
        js(f"{state}.setNavMode('blender')")  # the Modes island: this session only
        theme = js(f"JSON.stringify({workspace}.document.theme)")
        js(f"{workspace}.setValue(['theme'], {{name: 'light'}})")
        time.sleep(0.6)
        js(f"{workspace}.setValue(['theme'], JSON.parse({json.dumps(theme)}))")
        time.sleep(0.6)
        mode = js(f"{state}.navMode")
        run.expect(
            mode == "blender", f"an unrelated workspace change reset the session scheme to {mode!r}"
        )
        return "a workspace change that is not the mouse section leaves the island's choice alone"

    try:
        run.step(
            "Project menu, Settings… opens a dialog with the five sections",
            opens_from_the_project_menu_with_five_sections,
        )
        run.step(
            "the Appearance tab has its controls and the grid switch writes the workspace",
            appearance_tab_has_its_controls_and_the_grid_writes_the_workspace,
        )
        run.step(
            "the Mouse tab applies a scheme at once and saves it as the user's preference",
            mouse_tab_applies_a_scheme_at_once_and_saves_it,
        )
        run.step(
            "the orbit switch is for Select / Orbit only", orbit_switch_is_for_select_orbit_only
        )
        run.step(
            "a workspace.set from the console changes the scheme and the dialog",
            a_console_change_shows_in_the_dialog,
        )
        run.step(
            "the Units tab changes the project's units and saves the default import unit",
            units_tab_changes_the_project_and_saves_the_default_import_unit,
        )
        run.step(
            "the units question starts from the default import unit",
            the_units_question_starts_from_the_default_import_unit,
        )
        run.step(
            "the Workspace tab disables 'This project' and Escape closes the top dialog only",
            workspace_tab_disables_this_project_and_escape_closes_the_top_dialog_only,
        )
        run.step("the Reset dialog names the preferences", reset_dialog_names_the_preferences)
        run.step("the About tab reads app.info", about_tab_reads_the_server)
        run.step(
            "the third-party notices open over Settings",
            notices_open_over_settings_and_escape_closes_them_only,
        )
        run.step(
            "Escape closes Settings and the focus returns to the Project menu button",
            escape_closes_settings_and_the_focus_returns,
        )
        run.step(
            "Ctrl+Shift+, and the palette open Settings; Ctrl+, is still Display settings",
            the_chord_and_the_palette_open_it_and_ctrl_comma_is_still_display_settings,
        )
        run.step(
            "the dialog fits and reads in every theme", the_dialog_fits_and_reads_in_every_theme
        )
        run.step(
            "a workspace change that is not the mouse section leaves a session scheme alone",
            a_theme_change_leaves_the_session_scheme_alone,
        )
    finally:
        escape_until_closed()
        # Back to the defaults: the preferences were the only user-layer values this check wrote.
        js(f"{workspace}.resetValue(['preferences'], 'user')")
        time.sleep(0.6)
        js(f"{state}.setNavMode('cad')")


SLICES_SHOTS = Path(__file__).resolve().parent.parent / "out" / "slices"


def check_slices(run: Checks, app: App) -> None:
    """Slices at any plane in the Results stage, on real presses (the slices feature).

    The server half is played as in `check_results` (`tools/fake_results.js`), plus the cut
    routes of `.mailbox/plan/slices/CONTRACT.md`: a request for a plane the page has not been
    answered for waits in `__fakeResults.pending` and is answered here from
    `tools/fake_results.py` (`plane_response`), so the duct is really cut at the plane the slider
    asked for. Needs `--project` made by `tools/mesh_demo_project.py`. The store, the controller,
    the scene, the panel, the legend, the slider and every click are real.

    Steps: Add slice cuts the duct in the middle and draws it (with a spinner while it is cut); a
    slider drag is debounced to the plane it rests at, and the old cut stays until the new one
    comes; slices are shown, hidden and removed; a slice takes its own field and the legend gains
    its scale; a plane that misses the model says so; the section plane makes a slice; the ninth
    slice is refused; the walls fade so the slices can be seen; contour lines (counted against
    an independent implementation), their colour and levels; banded fills.
    """
    if app.session is None:
        print("  skip the Slices checks: an attached window has no session to script")
        return
    state = "window.__stokes.getState()"
    deadline = time.monotonic() + 45.0
    count = 0
    while time.monotonic() < deadline and count < 1:
        count = int(app.evaluate(f"{state}.geometry.filter((g) => g.kind === 'mesh').length") or 0)
        if count < 1:
            time.sleep(0.5)
    if count < 1:
        print("  skip the Slices checks: no mesh patches (see tools/mesh_demo_project.py)")
        return
    import base64

    from fake_results import build, plane_response

    built = build(app.session)
    app.evaluate(FAKE_RESULTS_SCRIPT.read_text(encoding="utf-8"))
    app.evaluate(
        "window.__fakeResults.install("
        + json.dumps({"routes": built["routes"], "reply": built["reply"], "planes": True})
        + ")"
    )
    fake = "window.__fakeResults"
    held = [False]

    def js(expression: str) -> Any:
        return app.evaluate(expression)

    def serve() -> int:
        """Answer the cut routes the page is waiting on, unless the harness is holding them."""
        if held[0]:
            return 0
        keys = js(f"[...{fake}.pending.keys()]") or []
        for key in keys:
            status, body, text = plane_response(key)
            answer = {
                "status": status,
                "b64": base64.b64encode(body).decode("ascii") if body else "",
                "text": text,
            }
            js(f"{fake}.provide({json.dumps(key)}, {json.dumps(answer)})")
        return len(keys)

    def wait_for(condition: Any, what: str, seconds: float = 20.0) -> None:
        limit = time.monotonic() + seconds
        while time.monotonic() < limit:
            serve()
            if condition():
                return
            time.sleep(0.1)
        raise Failure(
            f"timed out waiting for {what}; slices are {js(f'{state}.slices.status')!r}, "
            f"asked {js(f'{fake}.asked.slice(-6)')!r}"
        )

    def asked(part: str) -> list[str]:
        return list(js(f"{fake}.asked.filter((p) => p.includes({json.dumps(part)}))") or [])

    def cut_requests() -> list[str]:
        """The surface requests of the cuts, one per distinct plane asked of the server."""
        paths = js(f"{fake}.asked.filter((p) => p.startsWith('/api/results/slice/'))") or []
        return sorted({str(p) for p in paths})

    def rows() -> list[dict[str, Any]]:
        found = js(
            "[...document.querySelectorAll('[data-ws=slice-row]')].map((r) => ({"
            " id: r.dataset.slice, status: r.dataset.status, visible: r.dataset.visible,"
            " name: r.querySelector('[data-ws=slice-name]').textContent,"
            " note: r.querySelector('[data-ws=slice-status]').textContent,"
            " spinner: !!r.querySelector('[data-ws=slice-spinner]')}))"
        )
        return [dict(r) for r in found] if isinstance(found, list) else []

    def row_selector(slice_id: str, inner: str) -> str:
        return f"[data-ws=slice-row][data-slice='{slice_id}'] {inner}"

    def click_in(selector: str) -> None:
        js(f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block: 'center'}})")
        time.sleep(0.15)
        app.click(*app.centre(selector))

    def legend_titles() -> list[str]:
        found = js(
            "[...document.querySelectorAll('[data-ws=field-legend] .field-legend-title')]"
            ".map((el) => el.textContent)"
        )
        return [str(t) for t in found] if isinstance(found, list) else []

    def appearance(theme: str) -> None:
        if js("document.documentElement.dataset.theme") == theme:
            return
        app.click(*app.centre("[data-ws-item='appearance']"))
        wait_for(
            lambda: js("document.documentElement.dataset.theme") == theme, f"the {theme} theme"
        )
        time.sleep(0.8)

    def info() -> dict[str, Any]:
        return dict(js("window.__stokesScene.meshInfo()"))

    def shot(name: str) -> None:
        for theme in ("dark", "light"):
            appearance(theme)
            app.capture(SLICES_SHOTS / f"{name}-{theme}.png")
        appearance("dark")

    # The Results tab and the window, as `check_results` sets them.
    js(
        "(() => { const w = window.__workspace.getState();"
        " const next = JSON.parse(JSON.stringify(w.document.layout));"
        " if (next.right.panels.some((p) => p.id === 'results')) return;"
        " next.right.panels.push({id: 'results', order: next.right.panels.length});"
        " w.setValue(['layout'], next); })()"
    )
    wait_for(
        lambda: bool(
            js(
                "window.__workspace.getState().document.layout.right.panels"
                ".some((p) => p.id === 'results')"
            )
        ),
        "the Results tab to be placed",
    )
    app.dev.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": 1500, "height": 900, "deviceScaleFactor": 1, "mobile": False},
    )
    js("window.__stokes.setState({drawerOpen: false})")
    appearance("dark")
    js(f"{state}.setStage('Solve'); {state}.setMeshDisplay('surface'); {state}.setSection(false)")
    time.sleep(0.5)
    wait_for(lambda: bool(js("!!window.__stokesScene")), "the viewport's scene probe")
    js(f"{fake}.endRun('r_e2e', 'done')")
    wait_for(lambda: js(f"{state}.results.status") == "ready", "run.fields to be read")
    js(f"{state}.setStage('Results'); {state}.requestFit()")
    baseline = [0]
    wait_for(
        lambda: int(js("window.__stokesScene.perf().tagTriangles") or 0) > 0,
        "the walls to be tinted",
    )
    baseline[0] = 0
    walls = count

    def tinted() -> int:
        return int(js("window.__stokesScene.perf().tagTriangles") or 0)

    wall_triangles = tinted()

    def patches() -> int:
        return int(info()["patches"])

    def add_slice(axis: str = "x") -> None:
        click_in(
            "[role=radiogroup][aria-label='Axis of the new slice'] "
            f"[role=radio]:nth-child({'xyz'.index(axis) + 1})"
        )
        click_in("[data-ws=slice-add]")

    def a_slice_is_added_and_cut() -> str:
        run.expect(not rows(), "slices exist before any was added")
        run.expect(
            js("document.querySelector('[data-ws=results-slices]') !== null"),
            "the Results panel has no Slices section",
        )
        held[0] = True
        add_slice("x")
        wait_for(lambda: len(rows()) == 1, "the slice row")
        wait_for(lambda: bool(asked("/api/results/slice/r_e2e?plane=")), "the cut to be asked for")
        wait_for(lambda: rows()[0]["spinner"], "the spinner while it is cut")
        run.expect(
            rows()[0]["status"] == "loading" and "Cutting" in rows()[0]["note"],
            f"the row does not say it is cutting: {rows()[0]}",
        )
        run.expect(patches() == walls, "a slice is in the scene before its cut arrived")
        run.expect(rows()[0]["name"] == "X = 250 mm", f"slice name {rows()[0]['name']!r}")
        held[0] = False
        wait_for(lambda: rows()[0]["status"] == "ready", "the cut to arrive")
        wait_for(lambda: patches() == walls + 1, "the slice to join the scene")
        plane = "0.25,0,0,1,0,0"
        route = f"/api/results/slice/r_e2e?plane={plane}"
        field_route = f"/api/results/field/r_e2e/slice?plane={plane}&field=U_mag"
        run.expect(route in asked("/slice/"), f"no cut at {plane}: {cut_requests()}")
        wait_for(lambda: field_route in asked("/field/"), "the field of the cut")
        wait_for(lambda: tinted() > wall_triangles, "the slice to be tinted")
        added = tinted() - wall_triangles
        run.expect(added == 512, f"{added} triangles tinted on the x = 0.25 cut (512 expected)")
        run.expect(not rows()[0]["spinner"], "the spinner stayed after the cut arrived")
        run.expect(legend_titles() == ["|U| (m/s)"], f"legend: {legend_titles()}")
        time.sleep(1.0)
        shot("slice-x")
        return (
            f"cut at {plane}, {added} triangles tinted with the walls' |U| scale; "
            "spinner while cutting"
        )

    def the_slider_is_debounced() -> str:
        before = set(cut_requests())
        js(
            "(() => { const el = document.querySelector('[data-ws=slice-position]');"
            " window.__sliderEvents = 0;"
            " el.addEventListener('input', () => { window.__sliderEvents += 1; }); })()"
        )
        selector = "[data-ws=slice-position]"
        box = app.box(selector)
        lo = float(js(f"document.querySelector({json.dumps(selector)}).min"))
        hi = float(js(f"document.querySelector({json.dumps(selector)}).max"))
        value = float(js(f"document.querySelector({json.dumps(selector)}).value"))
        y = box["y"] + box["height"] / 2
        start_x = box["x"] + 6 + (box["width"] - 12) * (value - lo) / (hi - lo)
        end_x = box["x"] + 6 + (box["width"] - 12) * 0.82
        held[0] = True
        app.drag((start_x, y), (end_x, y), steps=12)
        events = int(js("window.__sliderEvents"))
        landed = float(js(f"document.querySelector({json.dumps(selector)}).value"))
        run.expect(events >= 4, f"the drag made only {events} slider events")
        run.expect(abs(landed - value) > 50, f"the slider did not move: {value} -> {landed}")
        wait_for(lambda: len(set(cut_requests()) - before) >= 1, "the plane to be asked for")
        time.sleep(0.6)
        fresh = sorted(set(cut_requests()) - before)
        run.expect(
            len(fresh) == 1,
            f"{events} slider events asked for {len(fresh)} planes (one expected): {fresh}",
        )
        ox = float(fresh[0].split("plane=")[1].split(",")[0]) * 1000
        run.expect(abs(ox - landed) <= 0.5, f"asked for x = {ox} mm, the slider rests at {landed}")
        # The old cut is still on screen while the new one is cut.
        run.expect(patches() == walls + 1, "the old cut left before the new one arrived")
        run.expect(
            rows()[0]["status"] == "loading" and rows()[0]["spinner"],
            f"the row is not cutting: {rows()[0]}",
        )
        held[0] = False
        wait_for(lambda: rows()[0]["status"] == "ready", "the moved cut")
        wait_for(lambda: patches() == walls + 1, "one slice in the scene")
        run.expect(rows()[0]["name"] == f"X = {round(landed * 10) / 10:g} mm", rows()[0]["name"])
        time.sleep(0.8)
        return (
            f"{events} slider events asked for one plane (x = {ox:.1f} mm); "
            "the old cut stayed until it came"
        )

    def keys_move_the_slider_too() -> str:
        before = set(cut_requests())
        js("document.querySelector('[data-ws=slice-position]').focus()")
        # Three presses with no pause between them (`App.press` rests 0.3 s after each, which is the
        # debounce itself): a held arrow key.
        for _ in range(3):
            for kind in ("keyDown", "keyUp"):
                app.dev.call(
                    "Input.dispatchKeyEvent",
                    {
                        "type": kind,
                        "key": "ArrowRight",
                        "code": "ArrowRight",
                        "windowsVirtualKeyCode": 39,
                        "nativeVirtualKeyCode": 39,
                    },
                    timeout=30.0,
                )
        wait_for(lambda: len(set(cut_requests()) - before) >= 1, "the plane after key presses")
        wait_for(lambda: rows()[0]["status"] == "ready", "the cut after key presses")
        fresh = sorted(set(cut_requests()) - before)
        run.expect(len(fresh) == 1, f"three key presses asked for {len(fresh)} planes: {fresh}")
        return f"three quick arrow presses made one cut ({fresh[0].split('plane=')[1]})"

    def slices_are_hidden_shown_and_removed() -> str:
        add_slice("z")
        wait_for(lambda: len(rows()) == 2 and rows()[1]["status"] == "ready", "the second slice")
        wait_for(lambda: patches() == walls + 2, "two slices in the scene")
        names = [r["name"] for r in rows()]
        run.expect(names[1] == "Z = 10 mm", f"names {names}")
        before = len(cut_requests())
        click_in(row_selector("s1", "[data-ws=slice-eye]"))
        wait_for(lambda: patches() == walls + 1, "the hidden slice to leave the scene")
        run.expect(rows()[0]["visible"] == "false", "the row does not read hidden")
        run.expect(rows()[0]["note"] == "Hidden", f"note {rows()[0]['note']!r}")
        click_in(row_selector("s1", "[data-ws=slice-eye]"))
        wait_for(lambda: patches() == walls + 2, "the shown slice to return")
        run.expect(len(cut_requests()) == before, "showing a slice again asked for its cut again")
        click_in(row_selector("s2", "[data-ws=slice-remove]"))
        wait_for(lambda: len(rows()) == 1 and patches() == walls + 1, "the removed slice to go")
        return (
            "hide and show take the cut out of and back into the scene without asking "
            "again; remove deletes it"
        )

    def a_slice_takes_its_own_field() -> str:
        run.expect(js(f"{state}.results.view.field") == "U_mag", "the walls are not on |U|")
        selector = row_selector("s1", "[data-ws=slice-field]")
        js(f"document.querySelector({json.dumps(selector)}).focus()")
        # The options are: the surfaces' field, |U|, p, none. Two presses reach p.
        app.press("ArrowDown", "ArrowDown", 40)
        app.press("ArrowDown", "ArrowDown", 40)
        wait_for(lambda: js(f"{state}.slices.list[0].field") == "p", "the slice to take p")
        chosen = js(f"{state}.slices.list[0].field")
        wait_for(lambda: len(legend_titles()) == 2, "the legend to gain the slice's scale")
        titles = legend_titles()
        run.expect(titles[0] == "|U| (m/s)", f"legend: {titles}")
        run.expect(titles[1].startswith("p/rho"), f"legend: {titles}")
        wait_for(lambda: any("&field=p" in p for p in asked("/field/")), "the field of the p cut")
        return f"the slice paints with {chosen!r}; the legend lists {titles}"

    def a_plane_that_misses_the_model_says_so() -> str:
        selector = row_selector("s1", "[data-ws=slice-position-text]")
        click_in(selector)
        js(f"document.querySelector({json.dumps(selector)}).select()")
        app.dev.call("Input.insertText", {"text": "600"})
        app.press("Enter", "Enter", 13)
        wait_for(lambda: rows()[0]["status"] == "empty", "the empty cut")
        run.expect(rows()[0]["note"] == "The plane misses the model", f"note {rows()[0]['note']!r}")
        wait_for(lambda: patches() == walls, "the cut to leave the scene")
        # And it is not asked again by a later sync.
        js(f"{state}.setSection(false)")
        time.sleep(0.4)
        asks = [p for p in cut_requests() if "plane=0.6," in p]
        run.expect(len(asks) == 1, f"the empty plane was asked {len(asks)} times")
        # Back inside the model.
        click_in(selector)
        js(f"document.querySelector({json.dumps(selector)}).select()")
        app.dev.call("Input.insertText", {"text": "120"})
        app.press("Enter", "Enter", 13)
        wait_for(lambda: rows()[0]["status"] == "ready", "the cut back inside the model")
        wait_for(lambda: patches() == walls + 1, "the cut to return")
        return (
            "x = 600 mm: 404 results.slice_empty reads 'The plane misses the model' and the "
            "cut goes; 120 mm brings it back"
        )

    def the_section_plane_makes_a_slice() -> str:
        js(f"{state}.setSection(true); {state}.setSectionPlane('y', 0.01)")
        click_in("[data-ws=slice-from-section]")
        wait_for(lambda: len(rows()) == 2, "the section slice")
        wait_for(lambda: rows()[1]["status"] == "ready", "its cut")
        run.expect(rows()[1]["name"] == "Section Y = 10 mm", f"name {rows()[1]['name']!r}")
        run.expect(
            any("plane=0,0.01,0,0,1,0" in p for p in cut_requests()),
            f"no cut at the section plane: {cut_requests()}",
        )
        js(f"{state}.setSection(false)")
        return "the Section view's plane (y = 10 mm) became a slice"

    def the_ninth_slice_is_refused() -> str:
        while len(rows()) < 8:
            add_slice("z")
            wait_for(lambda: True, "a beat")
            time.sleep(0.2)
        run.expect(len(rows()) == 8, f"{len(rows())} slices")
        click_in("[data-ws=slice-add]")
        time.sleep(0.4)
        run.expect(len(rows()) == 8, "a ninth slice was added")
        disabled = js("document.querySelector('[data-ws=slice-add]').getAttribute('aria-disabled')")
        title = js("document.querySelector('[data-ws=slice-add]').title")
        run.expect(
            disabled == "true" and "8" in str(title), f"the button reads {disabled!r} {title!r}"
        )
        wait_for(lambda: all(r["status"] == "ready" for r in rows()), "all eight cuts")
        shot("slices-eight")
        for _ in range(6):
            click_in("[data-ws=slice-row] [data-ws=slice-remove]")
            time.sleep(0.2)
        wait_for(lambda: len(rows()) == 2, "six slices to be removed")
        return "eight slices stand, the ninth press adds nothing and the button says why"

    def fading_the_walls_shows_the_slices() -> str:
        import tempfile

        from PIL import Image

        scratch = Path(tempfile.mkdtemp(prefix="stokes-slices-"))
        for _ in range(2):
            click_in("[data-ws=slice-row] [data-ws=slice-remove]")
            time.sleep(0.2)
        wait_for(lambda: len(rows()) == 0, "no slices")
        js(f"{state}.requestFit()")
        time.sleep(1.2)
        before = scratch / "before.png"
        app.capture(before)
        add_slice("z")
        wait_for(lambda: len(rows()) == 1 and rows()[0]["status"] == "ready", "the z slice")
        wait_for(lambda: patches() == walls + 1, "the slice in the scene")
        time.sleep(1.5)
        box = js(
            "(() => { const b = window.__stokesScene.partBounds();"
            " const k = Object.keys(b).find((n) => n.startsWith('Slice')); return b[k]; })()"
        )
        run.expect(isinstance(box, dict), f"the slice has no screen box: {box}")
        faded = scratch / "faded.png"
        app.capture(faded)
        click_in("[data-ws=slice-fade] [role=switch]")
        time.sleep(1.2)
        solid = scratch / "solid.png"
        app.capture(solid)
        a, b, c = (Image.open(path).convert("RGB") for path in (before, faded, solid))
        scale = a.width / float(js("window.innerWidth"))

        def changed(one: Any, two: Any) -> float:
            moved = candidates = 0
            for y in range(int(float(box["y0"]) * scale), int(float(box["y1"]) * scale), 2):
                for x in range(int(float(box["x0"]) * scale), int(float(box["x1"]) * scale), 2):
                    p, q = one.getpixel((x, y)), two.getpixel((x, y))
                    if max(p) - min(p) < 50 and max(q) - min(q) < 50:
                        continue
                    candidates += 1
                    if max(abs(p[i] - q[i]) for i in range(3)) > 30:
                        moved += 1
            run.expect(candidates > 200, f"only {candidates} coloured pixels in the box {box}")
            return moved / candidates

        with_fade = changed(a, b)
        without = changed(a, c)
        run.expect(
            with_fade > 0.3, f"only {with_fade:.0%} of the slice's box changed with the walls faded"
        )
        run.expect(
            without < with_fade / 2,
            f"with solid walls {without:.0%} changed, with faded walls {with_fade:.0%}",
        )
        click_in("[data-ws=slice-fade] [role=switch]")
        time.sleep(1.0)
        shot("slice-z-faded")
        return (
            f"faded walls show the slice ({with_fade:.0%} of its box changed); "
            f"solid walls hide it ({without:.0%})"
        )

    # ------------------------------------------------------------------------------ contours
    def clear_slices() -> None:
        while rows():
            click_in("[data-ws=slice-row] [data-ws=slice-remove]")
            time.sleep(0.2)
        wait_for(lambda: patches() == walls, "the slices to leave the scene")

    def zoom_shot(name: str) -> None:
        """The slice's screen box alone, enlarged, in both themes (for a look at the lines)."""
        import base64 as b64

        found = js(
            "(() => { const b = window.__stokesScene.partBounds();"
            " const k = Object.keys(b).find((n) => n.startsWith('Slice')); return b[k]; })()"
        )
        if not isinstance(found, dict):
            return
        pad = 6.0
        canvas = js(
            "(() => { const r = document.querySelector('canvas').getBoundingClientRect();"
            " return {x: r.left, y: r.top}; })()"
        )
        clip = {
            "x": float(found["x0"]) + float(canvas["x"]) - pad,
            "y": float(found["y0"]) + float(canvas["y"]) - pad,
            "width": float(found["x1"]) - float(found["x0"]) + 2 * pad,
            "height": float(found["y1"]) - float(found["y0"]) + 2 * pad,
            "scale": 5,
        }
        for theme in ("dark", "light"):
            appearance(theme)
            raw = app.dev.call("Page.captureScreenshot", {"format": "png", "clip": clip}).get(
                "data"
            )
            if isinstance(raw, str):
                target = SLICES_SHOTS / f"{name}-zoom-{theme}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b64.b64decode(raw))
        appearance("dark")

    def slice_box() -> dict[str, Any]:
        box = js(
            "(() => { const b = window.__stokesScene.partBounds();"
            " const k = Object.keys(b).find((n) => n.startsWith('Slice')); return b[k]; })()"
        )
        run.expect(isinstance(box, dict), f"the slice has no screen box: {box}")
        return dict(box)

    def pixels(box: dict[str, Any]) -> list[tuple[int, int, int]]:
        """The colours of the pixels inside the slice's screen box, from a fresh capture."""
        import tempfile

        from PIL import Image

        path = Path(tempfile.mkdtemp(prefix="stokes-contours-")) / "shot.png"
        app.capture(path)
        image = Image.open(path).convert("RGB")
        scale = image.width / float(js("window.innerWidth"))
        out: list[tuple[int, int, int]] = []
        for y in range(int(float(box["y0"]) * scale), int(float(box["y1"]) * scale)):
            for x in range(int(float(box["x0"]) * scale), int(float(box["x1"]) * scale)):
                out.append(image.getpixel((x, y)))
        return out

    def expected_segments(levels: int) -> int:
        """The contour segments of |U| on the x = 0.25 cut, counted here, apart from the page."""
        import numpy as np
        from fake_results import cut_duct, parse_plane, speed

        plane = parse_plane("0.25,0,0,1,0,0")
        assert plane is not None
        cut = cut_duct(*plane)
        assert cut is not None
        points, triangles = cut
        values = speed(points).astype("float64")
        low, high = built["ranges"]["U_mag"]
        total = 0
        for k in range(1, levels):
            level = low + ((high - low) * k) / levels
            for tri in triangles:
                v = values[tri]
                above = v >= level
                if above.all() or not above.any():
                    continue
                ends = []
                for e in range(3):
                    f, g = e, (e + 1) % 3
                    if above[f] == above[g]:
                        continue
                    t = (level - v[f]) / (v[g] - v[f])
                    p, q = points[tri[f]].astype("float64"), points[tri[g]].astype("float64")
                    ends.append(p + (q - p) * t)
                if len(ends) == 2 and float(np.linalg.norm(ends[0] - ends[1])) >= 1e-12:
                    total += 1
        return total

    def switch_in(selector: str) -> None:
        click_in(f"{selector} [role=switch]")

    def contour_vertices() -> int:
        return int(info()["contourVertices"])

    def look_along_x(sign: float = 1.0) -> None:
        js(f"window.__stokesScene.fitFrom({sign}, 0, 0)")
        time.sleep(1.6)

    def changed_pixels(
        before: list[tuple[int, int, int]], after: list[tuple[int, int, int]]
    ) -> list[tuple[int, int, int]]:
        """The colours (after) of the pixels that differ clearly between two captures of one box."""
        run.expect(len(before) == len(after), "the slice's box changed size between captures")
        return [
            q
            for p, q in zip(before, after, strict=True)
            if max(abs(p[i] - q[i]) for i in range(3)) > 30
        ]

    def lines_follow_the_values() -> str:
        clear_slices()
        add_slice("x")
        wait_for(lambda: len(rows()) == 1 and rows()[0]["status"] == "ready", "the x slice")
        wait_for(lambda: patches() == walls + 1, "the slice in the scene")
        wait_for(lambda: tinted() > wall_triangles, "the slice to be tinted")
        run.expect(info()["contours"] == 0, "contour lines before Lines was switched on")
        look_along_x(1.0)
        box = slice_box()
        before = pixels(box)
        switch_in("[data-ws=slice-lines]")
        wait_for(lambda: info()["contours"] == 1, "the contour lines")
        # Ten levels: nine lines. Two copies of every segment (one each side), two vertices each.
        expected = expected_segments(10)
        run.expect(expected > 100, f"the independent count found only {expected} segments")
        run.expect(
            contour_vertices() == expected * 4,
            f"{contour_vertices()} line vertices for {expected} segments (4 expected each)",
        )
        time.sleep(0.8)
        drawn = changed_pixels(before, pixels(box))
        run.expect(len(drawn) > 150, f"the lines changed only {len(drawn)} pixels of the slice")
        dark = sum(1 for q in drawn if max(q) < 90)
        run.expect(dark > len(drawn) * 0.8, f"only {dark} of {len(drawn)} line pixels are dark")
        shot("slice-lines-neutral")
        zoom_shot("slice-lines-neutral")
        return (
            f"nine lines, {expected} segments (the page and an independent count agree); "
            f"{len(drawn)} pixels drawn, {dark} of them dark"
        )

    def the_lines_show_from_either_side() -> str:
        look_along_x(-1.0)
        box = slice_box()
        with_lines = pixels(box)
        switch_in("[data-ws=slice-lines]")
        wait_for(lambda: info()["contours"] == 0, "the lines to go")
        time.sleep(0.8)
        without = pixels(box)
        drawn = changed_pixels(without, with_lines)
        run.expect(len(drawn) > 150, f"only {len(drawn)} line pixels seen from the other side")
        switch_in("[data-ws=slice-lines]")
        wait_for(lambda: info()["contours"] == 1, "the lines to return")
        look_along_x(1.0)
        return f"{len(drawn)} line pixels from the far side too (lifted on both sides)"

    def lines_in_the_map_colours() -> str:
        # On a plain sheet (fill off) the neutral lines are dark on grey; coloured by the map each
        # takes the colour of its level. (Over the smooth map they would be the colour under them.)
        switch_in("[data-ws=slice-fill]")
        wait_for(lambda: js(f"{state}.slices.list[0].fill") is False, "fill off")
        time.sleep(0.9)
        box = slice_box()
        neutral = pixels(box)
        chroma_neutral = sum(1 for p in neutral if max(p) - min(p) > 70)
        click_in("[data-ws=slice-line-colour] [role=radio]:nth-child(2)")
        wait_for(lambda: js(f"{state}.slices.list[0].lineColour") == "map", "the line colour")
        time.sleep(0.9)
        run.expect(info()["contours"] == 1, "the lines went when their colour changed")
        mapped = pixels(box)
        chroma_map = sum(1 for p in mapped if max(p) - min(p) > 70)
        run.expect(
            chroma_map > chroma_neutral + 150,
            f"{chroma_map} coloured pixels with map lines, {chroma_neutral} with neutral ones",
        )
        shot("slice-lines-map")
        zoom_shot("slice-lines-map")
        click_in("[data-ws=slice-line-colour] [role=radio]:nth-child(1)")
        wait_for(lambda: js(f"{state}.slices.list[0].lineColour") == "neutral", "neutral lines")
        switch_in("[data-ws=slice-fill]")
        wait_for(lambda: js(f"{state}.slices.list[0].fill") is True, "fill on")
        time.sleep(0.6)
        return (
            f"on a plain sheet {chroma_neutral} coloured pixels with neutral lines, "
            f"{chroma_map} with lines in the map's colours"
        )

    def levels_change_the_lines() -> str:
        before = contour_vertices()
        js("document.querySelector('[data-ws=slice-levels-input]').focus()")
        app.press("End", "End", 35)
        wait_for(lambda: js(f"{state}.slices.list[0].levels") == 20, "20 levels")
        wait_for(lambda: contour_vertices() != before, "the lines to be redrawn")
        expected = expected_segments(20)
        run.expect(
            contour_vertices() == expected * 4,
            f"{contour_vertices()} line vertices for {expected} segments at 20 levels",
        )
        count = js("document.querySelector('[data-ws=slice-levels-count]').textContent")
        run.expect(count == "20", f"the levels read {count!r}")
        app.press("Home", "Home", 36)
        wait_for(lambda: js(f"{state}.slices.list[0].levels") == 5, "5 levels")
        wait_for(lambda: contour_vertices() == expected_segments(5) * 4, "the lines at 5 levels")
        app.press("ArrowRight", "ArrowRight", 39)
        wait_for(lambda: js(f"{state}.slices.list[0].levels") == 6, "6 levels")
        app.press("Home", "Home", 36)
        return (
            "5 levels draw 4 lines, 20 draw 19: the page's segment count equals the "
            "independent one each time"
        )

    def bands_cut_the_fill_into_flat_colours() -> str:
        # Lines off, bands on: the fill is a handful of flat colours instead of a ramp.
        switch_in("[data-ws=slice-lines]")
        wait_for(lambda: info()["contours"] == 0, "the lines to go")
        run.expect(
            js("document.querySelector('[data-ws=slice-levels]') === null"),
            "the Levels slider stays with neither bands nor lines",
        )
        time.sleep(0.8)
        smooth = pixels(slice_box())
        smooth_colours = len(set(smooth))
        switch_in("[data-ws=slice-bands]")
        wait_for(lambda: js(f"{state}.slices.list[0].bands") is True, "bands on")
        time.sleep(1.0)
        banded = pixels(slice_box())
        banded_colours = len(set(banded))
        run.expect(
            smooth_colours > 150,
            f"the smooth fill has only {smooth_colours} colours in the slice's box",
        )
        run.expect(
            banded_colours < smooth_colours / 3 and banded_colours < 80,
            f"{banded_colours} colours with bands against {smooth_colours} smooth",
        )
        shot("slice-bands")
        zoom_shot("slice-bands")
        # Each band is one flat colour: the commonest few cover most of the slice.
        counts: dict[tuple[int, int, int], int] = {}
        for p in banded:
            counts[p] = counts.get(p, 0) + 1
        top = sorted(counts.values(), reverse=True)[:6]
        return (
            f"{smooth_colours} colours smooth, {banded_colours} banded "
            f"(the six commonest hold {sum(top)} px)"
        )

    def bands_and_lines_together() -> str:
        switch_in("[data-ws=slice-lines]")
        wait_for(lambda: info()["contours"] == 1, "the lines over the bands")
        wait_for(lambda: js(f"{state}.slices.list[0].levels") == 5, "5 levels")
        time.sleep(0.9)
        shot("slice-bands-lines")
        zoom_shot("slice-bands-lines")
        switch_in("[data-ws=slice-bands]")
        wait_for(lambda: js(f"{state}.slices.list[0].bands") is False, "bands off")
        wait_for(lambda: tinted() > wall_triangles + 400, "the smooth cut again")
        added = tinted() - wall_triangles
        run.expect(added == 512, f"{added} triangles tinted after bands went (512 expected)")
        return "lines over bands, then bands off restores the cut's 512 triangles"

    # ------------------------------------------------------------------- the section's cap
    def cap_box() -> dict[str, Any]:
        box = js(
            "(() => { const b = window.__stokesScene.partBounds();"
            " const k = Object.keys(b).find((n) => n.startsWith('Section cut')); return b[k]; })()"
        )
        run.expect(isinstance(box, dict), f"the section cut has no screen box: {box}")
        return dict(box)

    def colour_at(box: dict[str, Any], fx: float, fy: float) -> tuple[int, int, int]:
        """The colour of a captured pixel at a fraction of the cap's screen box."""
        import tempfile

        from PIL import Image

        path = Path(tempfile.mkdtemp(prefix="stokes-cap-")) / "shot.png"
        app.capture(path)
        image = Image.open(path).convert("RGB")
        scale = image.width / float(js("window.innerWidth"))
        canvas = js(
            "(() => { const r = document.querySelector('canvas').getBoundingClientRect();"
            " return {x: r.left, y: r.top}; })()"
        )
        x = (
            float(canvas["x"]) + float(box["x0"]) + (float(box["x1"]) - float(box["x0"])) * fx
        ) * scale
        y = (
            float(canvas["y"]) + float(box["y0"]) + (float(box["y1"]) - float(box["y0"])) * fy
        ) * scale
        return image.getpixel((int(x), int(y)))

    def cap_status() -> str | None:
        found = js(f"({state}.slices.status.cap || {{}}).status || null")
        return None if found is None else str(found)

    def the_section_cap_carries_the_field() -> str:
        clear_slices()
        js(f"{state}.setSection(false)")
        time.sleep(0.4)
        held[0] = True
        js(f"{state}.setSection(true); {state}.setSectionPlane('x', 0.35)")
        wait_for(lambda: bool(asked("/slice/r_e2e?plane=0.35,0,0,1,0,0")), "the cut at the section")
        wait_for(lambda: cap_status() == "loading", "the cap to be cutting")
        # The cap of the plane the section had before may still stand (the old cap stays until the
        # new one has arrived), but there is never more than one.
        run.expect(patches() <= walls + 1, "more than one cap in the scene while the cut is made")
        held[0] = False
        wait_for(lambda: cap_status() == "ready", "the cap's cut")
        wait_for(lambda: patches() == walls + 1, "the cap to join the scene")
        wait_for(lambda: tinted() - wall_triangles >= 512, "the cap to be tinted")
        run.expect(
            int(js("window.__stokesScene.clippingCount()") or 0) == 1, "the walls are not clipped"
        )
        js(f"{state}.requestFit()")
        look_along_x(-1.0)
        time.sleep(0.8)
        box = cap_box()
        centre = colour_at(box, 0.5, 0.5)
        corner = colour_at(box, 0.1, 0.1)
        run.expect(
            centre[0] > 120 and centre[1] > 150 and centre[2] < 120,
            f"the middle of the cut is {centre}, not the map's yellow of the fastest flow",
        )
        run.expect(
            corner[0] < 130 and corner[1] < 90 and corner[2] > 60,
            f"the corner of the cut is {corner}, not the map's purple of the walls",
        )
        run.expect(legend_titles() == ["|U| (m/s)"], f"legend: {legend_titles()}")
        shot("section-cap-u")
        zoom_cap("section-cap-u")
        return f"the cut at x = 350 mm is painted: middle {centre}, corner {corner}; one cut asked"

    def zoom_cap(name: str) -> None:
        import base64 as b64

        found = cap_box()
        canvas = js(
            "(() => { const r = document.querySelector('canvas').getBoundingClientRect();"
            " return {x: r.left, y: r.top}; })()"
        )
        pad = 6.0
        clip = {
            "x": float(found["x0"]) + float(canvas["x"]) - pad,
            "y": float(found["y0"]) + float(canvas["y"]) - pad,
            "width": float(found["x1"]) - float(found["x0"]) + 2 * pad,
            "height": float(found["y1"]) - float(found["y0"]) + 2 * pad,
            "scale": 5,
        }
        for theme in ("dark", "light"):
            appearance(theme)
            raw = app.dev.call("Page.captureScreenshot", {"format": "png", "clip": clip}).get(
                "data"
            )
            if isinstance(raw, str):
                target = SLICES_SHOTS / f"{name}-zoom-{theme}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b64.b64decode(raw))
        appearance("dark")

    def a_handle_drag_asks_once_and_the_old_cap_stays() -> str:
        before = set(cut_requests())
        held[0] = True
        for offset in (0.37, 0.39, 0.41, 0.43, 0.45):
            js(f"{state}.setSectionPlane('x', {offset})")
        time.sleep(0.15)
        run.expect(not (set(cut_requests()) - before), "the plane was asked while the handle moved")
        time.sleep(0.5)
        wait_for(
            lambda: len(set(cut_requests()) - before) >= 1, "the plane after the handle rested"
        )
        fresh = sorted(set(cut_requests()) - before)
        run.expect(len(fresh) == 1, f"five moves asked for {len(fresh)} planes: {fresh}")
        run.expect(patches() == walls + 1, "the old cap left before the new one arrived")
        run.expect(cap_status() == "loading", f"the cap reads {cap_status()}")
        held[0] = False
        wait_for(lambda: cap_status() == "ready", "the moved cap")
        run.expect(patches() == walls + 1, "more than one cap in the scene")
        return (
            f"five handle moves asked for one plane ({fresh[0].split('plane=')[1]}); "
            "the old cap stayed"
        )

    def the_cap_follows_the_field_and_the_legend() -> str:
        js("document.querySelector('[data-ws=results-select]').focus()")
        app.press("ArrowDown", "ArrowDown", 40)
        wait_for(lambda: js(f"{state}.results.view.field") == "p", "p on the walls")
        wait_for(
            lambda: any("&field=p" in q for q in asked("/field/") if "plane=0.45" in q),
            "the cap's p",
        )
        wait_for(lambda: legend_titles()[:1] == ["p/rho (m2/s2)"], "the legend")
        time.sleep(0.8)
        box = cap_box()
        near_inlet = colour_at(box, 0.5, 0.5)
        run.expect(
            max(near_inlet) - min(near_inlet) > 40, f"the cap is not coloured in p: {near_inlet}"
        )
        shot("section-cap-p")
        js("document.querySelector('[data-ws=results-select]').focus()")
        app.press("ArrowUp", "ArrowUp", 38)
        wait_for(lambda: js(f"{state}.results.view.field") == "U_mag", "|U| on the walls again")
        return "choosing p repaints the cap with p over the walls' range; the legend follows"

    def turning_the_section_off_restores_everything() -> str:
        js(f"{state}.setSection(false)")
        wait_for(lambda: patches() == walls, "the cap to leave the scene")
        run.expect(cap_status() is None, f"the cap still has a status: {cap_status()}")
        run.expect(
            int(js("window.__stokesScene.clippingCount()") or 0) == 0,
            "the clip stayed after the cut",
        )
        asked_before = len(asked(""))
        js(f"{state}.setSection(true)")
        wait_for(lambda: patches() == walls + 1, "the cap to come back")
        run.expect(
            len(asked("")) == asked_before, "turning the section on again asked the server again"
        )
        js(f"{state}.setSection(false)")
        wait_for(lambda: patches() == walls, "the cap to go again")
        return "section off: the cap, the clip and its status are gone; on again needs no new cut"

    def a_failed_cut_leaves_the_hollow() -> str:
        js(
            f"{fake}.refusals.set('/api/results/slice/r_e2e?plane=0.4,0,0,1,0,0',"
            " {status: 500, text: ''})"
        )
        js(f"{state}.setSection(true); {state}.setSectionPlane('x', 0.4)")
        wait_for(lambda: cap_status() == "error", "the cap to report the failure")
        run.expect(patches() == walls, "a cap was drawn for a cut the server refused")
        js(f"{state}.setSectionPlane('x', 0.6)")
        wait_for(lambda: cap_status() == "empty", "the plane that misses the model")
        run.expect(patches() == walls, "a cap was drawn for a plane outside the model")
        js(f"{state}.setSection(false)")
        return "a 500 and a plane outside the duct leave the cut open (no cap) and say so"

    run.step("Add slice cuts the duct at the middle and draws it", a_slice_is_added_and_cut)
    run.step("a slider drag is debounced to the plane it rests at", the_slider_is_debounced)
    run.step("arrow keys on the slider cut once", keys_move_the_slider_too)
    run.step("slices are hidden, shown and removed", slices_are_hidden_shown_and_removed)
    run.step(
        "a slice takes its own field and the legend gains its scale", a_slice_takes_its_own_field
    )
    run.step("a plane that misses the model says so", a_plane_that_misses_the_model_says_so)
    run.step("the section plane makes a slice", the_section_plane_makes_a_slice)
    run.step("the ninth slice is refused", the_ninth_slice_is_refused)
    run.step("fading the walls shows the slices", fading_the_walls_shows_the_slices)
    run.step("contour lines follow the field's values", lines_follow_the_values)
    run.step("the lines show from either side of the slice", the_lines_show_from_either_side)
    run.step("lines take the colour map or a neutral colour", lines_in_the_map_colours)
    run.step("levels change the number of lines", levels_change_the_lines)
    run.step("bands cut the fill into flat colours", bands_cut_the_fill_into_flat_colours)
    run.step("bands and lines together; bands off restores the cut", bands_and_lines_together)
    run.step("the Section view's cut face carries the field", the_section_cap_carries_the_field)
    run.step(
        "a handle drag asks once and the old cap stays",
        a_handle_drag_asks_once_and_the_old_cap_stays,
    )
    run.step("the cap follows the field and the legend", the_cap_follows_the_field_and_the_legend)
    run.step(
        "turning the section off restores everything", turning_the_section_off_restores_everything
    )
    run.step("a cut that fails leaves the hollow", a_failed_cut_leaves_the_hollow)
    js(f"{state}.setStage('Geometry')")
    app.dev.call("Emulation.clearDeviceMetricsOverride", {})
    time.sleep(0.3)


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911
    parser = argparse.ArgumentParser(prog="test-interactions", description=__doc__)
    parser.add_argument("--project", type=Path, default=None, help="an existing .stokes project")
    parser.add_argument("--shots", type=Path, default=None, help="write one PNG per step here")
    parser.add_argument("--json", type=Path, default=None, help="write the report here")
    parser.add_argument(
        "--attach",
        type=int,
        default=None,
        help="drive an already-running app on this DevTools port (QTWEBENGINE_REMOTE_DEBUGGING)",
    )
    parser.add_argument(
        "--only",
        choices=["run", "results", "slices", "gui", "context", "settings", "meshfind", "outline"],
        default=None,
        help=(
            "run one group of checks and no others (run: the Solve panel, T-217; results: the "
            "Results stage, T-218; slices: slices at any plane and the section's cut; "
            "gui: load CAD, define boundaries, mesh, run and plot, T-219; "
            "context: the viewport context menu, T-173; settings: the Settings dialog, T-178; "
            "meshfind: a finished mesh opens the tree and the panel can show it; "
            "outline: the outline tree folds and selects through its carets, G-306)"
        ),
    )
    args = parser.parse_args(argv)

    app = App(args.project, args.shots, args.attach)
    run = Checks(app)
    report: dict[str, Any] = {
        "renderer": app.evaluate("window.__stokesScene ? 'scene' : 'no-scene'")
    }
    try:
        report["env"] = app.evaluate(
            "(() => { const gl = document.createElement('canvas').getContext('webgl2');"
            " const d = gl.getExtension('WEBGL_debug_renderer_info');"
            " return {renderer: d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'unknown'}; })()"
        )
        print(
            f"renderer: {report['env'].get('renderer') if isinstance(report['env'], dict) else '?'}"
        )
        print("checks:")
        if args.only == "run":
            check_run_panel(run, app)
            return 0 if all(step["ok"] for step in run.steps) else 1
        if args.only == "results":
            check_results(run, app)
            return 0 if all(step["ok"] for step in run.steps) else 1
        if args.only == "slices":
            check_slices(run, app)
            return 0 if all(step["ok"] for step in run.steps) else 1
        if args.only == "context":
            check_context_menu(run, app)
            return 0 if all(step["ok"] for step in run.steps) else 1
        if args.only == "settings":
            check_settings_dialog(run, app)
            return 0 if all(step["ok"] for step in run.steps) else 1
        if args.only == "outline":
            from test_outline import check_outline

            check_outline(run, app)
            return 0 if all(step["ok"] for step in run.steps) else 1
        if args.only in ("gui", "meshfind"):
            from test_gui_flow import check_gui_flow
            from test_mesh_find import check_mesh_find

            (check_gui_flow if args.only == "gui" else check_mesh_find)(run, app)
            return 0 if all(step["ok"] for step in run.steps) else 1
        check_mouse_menu(run, app)
        check_scale_bar(run, app)
        check_top_centre_is_not_shared(run, app)
        # The viewport checks need parts to drag over; the built-in demo has a handful, and
        # `--project` a real model. Run them either way, and say which was used.
        check_box_select(run, app)
        check_measure(run, app)
        check_picks_respect_the_cut(run, app)
        check_topology_click_selects(run, app)
        check_topology_escalation(run, app)
        check_point_mode_and_hover(run, app)
        check_section_handle_drags(run, app)
        check_menu_primitive(run, app)
        check_context_menu(run, app)
        check_mesh_look(run, app)
        check_results(run, app)
        check_slices(run, app)
        check_mesh_panel(run, app)
        check_run_panel(run, app)
        check_every_control_answers(run, app)
        check_settings_dialog(run, app)
        from test_mesh_find import check_mesh_find

        check_mesh_find(run, app)
        # Last: it makes a new project of its own, so nothing above may depend on the demo after it.
        from test_gui_flow import check_gui_flow

        check_gui_flow(run, app)
    finally:
        report["steps"] = run.steps
        app.close()
        if args.json is not None:
            args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        ok = all(step["ok"] for step in run.steps)
        passed = sum(1 for step in run.steps if step["ok"])
        print(f"\n{'PASS' if ok else 'FAIL'}: {passed}/{len(run.steps)} checks")
    return 0 if all(step["ok"] for step in run.steps) else 1


if __name__ == "__main__":
    sys.exit(main())
