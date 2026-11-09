"""The whole GUI path on real presses: load CAD, define boundaries, mesh, run, plot (T-219).

    python tools/test_gui_flow.py
    python tools/test_interactions.py --only gui

The owner's words: "load a CAD file, define boundaries, mesh, run and plot the results using
the GUI", and "I don't see all the settings in the tree for each stage". These steps walk that
path in the order a person takes it, in one session, on a NEW project made from the project menu:

* A. a CAD file from the browser: the empty project's hint, the file input and the drop target,
  the upload card with its progress and its Cancel, the refusals (413, 415, a dropped set of
  files, a name that is not a geometry format), and the real ``geometry.import`` that follows
  an upload;
* B. the tree for every stage: a placeholder row for each setting that does not exist yet, the
  rows that are not nodes (the runs, the results), and the subtitles of the boundaries;
* C. boundaries from a freshly imported body: Next step, the Set up flow card, Assign Inlet and
  Outlet on picked faces by presses, the card's walls, the tree's subtitles and dots, the status
  turning ready;
* D. the results: the residual chart and the monitors table in the Results stage, Copy table,
  Export CSV, the pressure drop beside the legend.

The SERVER half of an upload (``POST /api/upload``) is built separately, so it is played from
``tools/fake_upload.js``; the solver is played from ``tools/fake_run.js`` and the results from
``tools/fake_results.js`` (OpenFOAM is not installed here). The commands that do not need a
solver (the project, the import, the set-up, the conditions) are the REAL ones, against a real
STEP box written by ``tests/geometry_fixtures``. Dark and light captures of the states a reviewer
wants to see go to ``.mailbox/shots/`` as ``gui-<name>-<theme>.png``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_interactions import App, Checks, Failure

HERE = Path(__file__).resolve().parent
FAKE_UPLOAD_SCRIPT = HERE / "fake_upload.js"
FAKE_RUN_SCRIPT = HERE / "fake_run.js"
FAKE_RESULTS_SCRIPT = HERE / "fake_results.js"
SHOTS = HERE.parent / ".mailbox" / "shots"

STORE = "window.__stokes.getState()"


class Ui:
    """The page, as a person sees it: what is there, what it says, and a real press on it."""

    def __init__(self, app: App, run: Checks) -> None:
        self.app = app
        self.run = run

    # ---------------------------------------------------------------- looking
    def js(self, expression: str) -> Any:
        return self.app.evaluate(expression)

    def present(self, selector: str) -> bool:
        return bool(self.js(f"Boolean(document.querySelector({json.dumps(selector)}))"))

    def count(self, selector: str) -> int:
        return int(self.js(f"document.querySelectorAll({json.dumps(selector)}).length"))

    def text(self, selector: str) -> str:
        found = self.js(
            "(() => { const el = document.querySelector("
            + json.dumps(selector)
            + "); return el ? (el.textContent || '').trim() : null; })()"
        )
        return str(found) if found is not None else ""

    def attr(self, selector: str, name: str) -> str:
        found = self.js(
            f"document.querySelector({json.dumps(selector)})?.getAttribute({json.dumps(name)})"
        )
        return str(found) if found is not None else ""

    def disabled(self, selector: str) -> bool:
        return self.attr(selector, "aria-disabled") == "true"

    def toasts(self) -> list[str]:
        found = self.js("[...document.querySelectorAll('.toast-text')].map((e) => e.textContent)")
        return [str(t) for t in found] if isinstance(found, list) else []

    def row(self, kind: str) -> dict[str, Any]:
        """A tree row of the stage model: its subtitle, its dot, and whether it is a placeholder."""
        return self._describe(f".tree-row[data-row='{kind}']")

    def node_row(self, path: str) -> dict[str, Any]:
        """The tree row of a REAL node, found by its path: it carries no ``data-row``.

        Since the server creates ``/mesh/settings`` and ``/solver/settings`` with the project, those
        rows are nodes and not stage-model placeholders, so ``row()`` cannot find them.
        """
        node_id = self.js(
            "(() => { const find = (n) => n.path === "
            + json.dumps(path)
            + " ? n : (n.children || []).map(find).find(Boolean);"
            f" const n = find({STORE}.tree); return n ? n.id : null; }})()"
        )
        return self._describe(f"#tree-row-{node_id}") if node_id else {}

    def _describe(self, selector: str) -> dict[str, Any]:
        found = self.js(
            "(() => { const r = document.querySelector("
            + json.dumps(selector)
            + "); if (!r) return null;"
            " const dot = r.querySelector('.dot');"
            " return {name: (r.children[3] || r).textContent.trim(),"
            " text: r.textContent.trim(),"
            " value: (r.querySelector('.tree-value') || {}).textContent || '',"
            " placeholder: r.getAttribute('data-placeholder') === 'true',"
            " dot: dot ? dot.className : '',"
            " opacity: parseFloat(getComputedStyle(r.querySelector('.tree-icon')).opacity)}; })()"
        )
        return dict(found) if isinstance(found, dict) else {}

    def named_row(self, name: str) -> dict[str, Any]:
        """A tree row by the name it shows (a real node: a region, a boundary)."""
        found = self.js(
            "(() => { const r = [...document.querySelectorAll('.tree-row')].find((e) => {"
            " const n = [...e.querySelectorAll('span')].find((s) =>"
            " !s.className.includes('tree') && s.textContent.trim() === "
            + json.dumps(name)
            + "); return Boolean(n); });"
            " if (!r) return null; const dot = r.querySelector('.dot');"
            " return {value: (r.querySelector('.tree-value') || {}).textContent || '',"
            " dot: dot ? dot.className : ''}; })()"
        )
        return dict(found) if isinstance(found, dict) else {}

    # ---------------------------------------------------------------- doing
    def double_press(self, selector: str) -> None:
        """A trusted double click: the second press carries ``clickCount`` 2, which makes the page's
        ``dblclick`` fire (two single presses in a row would not)."""
        self.js(
            "(() => { const el = document.querySelector("
            + json.dumps(selector)
            + "); if (el) el.scrollIntoView({block: 'center'}); })()"
        )
        time.sleep(0.2)
        x, y = self.app.centre(selector)
        self.app.dev.mouse("mouseMoved", x, y, buttons=0)
        for count in (1, 2):
            self.app.dev.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "buttons": 1,
                    "clickCount": count,
                },
            )
            self.app.dev.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": x,
                    "y": y,
                    "button": "left",
                    "buttons": 0,
                    "clickCount": count,
                },
            )
        time.sleep(0.35)

    def press(self, selector: str) -> None:
        self.js(
            "(() => { const el = document.querySelector("
            + json.dumps(selector)
            + "); if (el) el.scrollIntoView({block: 'center'}); })()"
        )
        time.sleep(0.2)
        self.app.click(*self.app.centre(selector))

    def press_text(self, selector: str, label: str) -> None:
        """Click the first element matching `selector` whose text starts with `label`."""
        self.js(
            "document.querySelector('[data-harness-press]')?.removeAttribute('data-harness-press');"
            "[...document.querySelectorAll("
            + json.dumps(selector)
            + ")].find((e) => (e.textContent || '').trim().startsWith("
            + json.dumps(label)
            + "))?.setAttribute('data-harness-press', '1')"
        )
        if not self.present("[data-harness-press='1']"):
            raise Failure(f"nothing matching {selector!r} starts with {label!r}")
        self.press("[data-harness-press='1']")

    def type_text(self, text: str) -> None:
        """Trusted typing into the focused field."""
        self.app.dev.call("Input.insertText", {"text": text})
        time.sleep(0.2)

    def enter(self) -> None:
        """A trusted Enter that types its character, so a form submits (`App.press` sends none)."""
        for kind in ("keyDown", "keyUp"):
            params: dict[str, Any] = {
                "type": kind,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
                "nativeVirtualKeyCode": 13,
            }
            if kind == "keyDown":
                params["text"] = "\r"
            self.app.dev.call("Input.dispatchKeyEvent", params)
        time.sleep(0.3)

    def wait_for(self, condition: Any, what: str, seconds: float = 25.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.2)
        # A capture of what was on screen, so a failure can be looked at (the temp folder:
        # it is not a deliverable).
        look = Path(tempfile.gettempdir()) / "stokes-gui-failure.png"
        try:
            self.app.capture(look)
        except Exception:  # a screenshot must never be what fails a check
            look = Path("(no screenshot)")
        raise Failure(f"timed out waiting for {what}; toasts: {self.toasts()}; screen: {look}")

    def stage(self, name: str) -> None:
        self.press(f".stage-chips [aria-label='{name}']")
        self.wait_for(
            lambda: self.attr(f".stage-chips [aria-label='{name}']", "aria-current") == "step",
            f"the {name} stage",
        )
        time.sleep(0.4)

    # ---------------------------------------------------------------- capturing
    def appearance(self, theme: str) -> None:
        """The appearance, by a real press on the top bar's sun/moon button."""
        if self.js("document.documentElement.dataset.theme") == theme:
            return
        self.app.click(*self.app.centre("[data-ws-item='appearance']"))
        self.wait_for(
            lambda: self.js("document.documentElement.dataset.theme") == theme, f"the {theme} theme"
        )
        time.sleep(0.8)

    def shots(self, name: str) -> None:
        """A dark and a light capture of the state on screen, named for the review."""
        for theme in ("dark", "light"):
            self.appearance(theme)
            self.app.capture(SHOTS / f"gui-{name}-{theme}.png")
        self.appearance("dark")


def step_faces(session: Any, part: str) -> dict[str, list[list[int]]]:
    """The inlet cap, the outlet cap and the walls of the box, by position, not by face number."""
    from tools.e2e_support import _faces_by_side

    return _faces_by_side(session, part)


def flat(ranges: list[list[int]]) -> list[int]:
    return [i for low, high in ranges for i in range(low, high + 1)]


def check_gui_flow(run: Checks, app: App) -> None:
    """Load a CAD file, define boundaries, mesh, run and plot, on real presses (T-219 A to D)."""
    session = app.session
    if session is None:
        print("  skip the GUI flow checks: an attached window has no session to script")
        return
    from stokes.geometry.store import GeometryStore, parts_under
    from tests import geometry_fixtures as fx

    ui = Ui(app, run)
    # A clean page: the checks before these leave scripted servers in the one they ran in (a solver
    # node in every tree, a fake run), and these steps start from a project with nothing set.
    app.dev.call("Page.reload", {"ignoreCache": True})
    time.sleep(1.0)
    app._wait_for_app()
    ui.wait_for(lambda: bool(ui.js(f"{STORE}.connected")), "the page to reconnect")
    work = Path(tempfile.mkdtemp(prefix="stokes_gui_"))
    step_file = fx.write_named([("duct", fx.box(500, 20, 20), None)], work / "duct.step")
    fake = "window.__fakeUpload"
    app.evaluate(FAKE_UPLOAD_SCRIPT.read_text(encoding="utf-8"))
    app.evaluate(f"{fake}.install({json.dumps({'path': str(step_file)})})")
    # The output drawer is shut for the captures: the viewport and the right slot get its height.
    app.evaluate(f"{STORE}.toggleDrawer(false)")

    # ======================================================== A: a CAD file from the browser
    def a_new_project_shows_the_import_hint() -> str:
        ui.press(".project-btn")
        ui.wait_for(lambda: ui.present("[role=menu]"), "the project menu")
        ui.press_text("[role=menuitem]", "New project")
        ui.wait_for(lambda: ui.present("[role=dialog] input"), "the name prompt")
        ui.type_text("gui_flow")
        ui.enter()
        ui.wait_for(
            lambda: session.project is not None and session.project.name == "gui_flow",
            "the new project",
        )
        ui.wait_for(lambda: ui.present("[data-ws=import-hint]"), "the empty-project hint")
        hint = ui.text("[data-ws=import-hint]")
        run.expect("Drop a CAD file here" in hint, f"the hint reads {hint!r}")
        run.expect("Import geometry" in hint, f"the hint names no way in: {hint!r}")
        ui.shots("import-hint")
        return "an empty project says 'Drop a CAD file here' and names the menu entry"

    run.step(
        "a new project shows the hint for dropping a CAD file", a_new_project_shows_the_import_hint
    )

    # ======================================================== B: the tree for every stage
    def b_every_stage_shows_its_settings() -> str:
        said: list[str] = []
        ui.stage("Mesh")
        # The server creates the mesh and solver settings with the project
        # (`ensure_stage_settings`), so those two rows are real nodes with their defaults; the mesh
        # result has no node until a mesh exists, and stays a dimmed placeholder.
        settings = ui.node_row("/mesh/settings")
        result = ui.row("mesh-result")
        run.expect(settings != {}, "the Mesh stage shows no Mesh settings row")
        run.expect(
            settings.get("placeholder") is not True,
            f"Mesh settings is a placeholder, though the project has the node: {settings}",
        )
        run.expect(result.get("placeholder") is True, f"Mesh result is no placeholder: {result}")
        run.expect(
            result.get("value") == "not meshed", f"Mesh result reads {result.get('value')!r}"
        )
        run.expect(
            float(result.get("opacity", 1)) < 1.0,
            "a placeholder row is not dimmed: its icon is opaque",
        )
        run.expect(float(settings.get("opacity", 1)) == 1.0, "the real Mesh settings row is dimmed")
        said.append("Mesh: settings, not meshed")
        ui.shots("tree-mesh")

        ui.stage("Physics")
        flow = ui.row("flow-model")
        run.expect(flow != {}, "the Physics stage shows no Flow model row")
        run.expect(
            flow.get("placeholder") is not True,
            f"Flow model is a placeholder, though the solver settings exist: {flow}",
        )
        run.expect("laminar" in str(flow.get("value")), f"Flow model reads {flow.get('value')!r}")
        run.expect(
            ui.named_row("parameters") != {}, "the Parameters group is not in the Physics stage"
        )
        run.expect(
            ui.named_row("materials") != {}, "the Materials group is not in the Physics stage"
        )
        said.append("Physics: flow model, parameters, materials")
        ui.shots("tree-physics")

        ui.stage("Solve")
        solver = ui.node_row("/solver/settings")
        run.expect(solver != {}, "the Solve stage shows no Solver settings row")
        run.expect(
            solver.get("placeholder") is not True,
            f"Solver settings is a placeholder, though the project has the node: {solver}",
        )
        run.expect(
            " it" in str(solver.get("value")), f"Solver settings reads {solver.get('value')!r}"
        )
        run.expect(ui.named_row("monitors") != {}, "the Monitors group is not in the Solve stage")
        said.append("Solve: solver settings, monitors")
        ui.shots("tree-solve")

        ui.stage("Results")
        for kind in ("fields", "slice", "residuals", "monitors"):
            row = ui.row(kind)
            run.expect(row != {}, f"the Results stage has no {kind} row")
            run.expect(row.get("value") == "no run yet", f"{kind} reads {row.get('value')!r}")
        said.append("Results: fields, slice, residuals, monitors")
        ui.shots("tree-results")
        return "; ".join(said)

    run.step(
        "the tree shows every stage's settings in a new project", b_every_stage_shows_its_settings
    )

    def b_a_settings_row_opens_its_panel() -> str:
        # A real node: a click selects it (Properties) and a double click or Enter opens its panel.
        # The panel has the settings already, so it offers no "Create settings".
        ui.stage("Mesh")
        mesh_row = ui.js(
            "(() => { const find = (n) => n.path === '/mesh/settings' ? n"
            " : (n.children || []).map(find).find(Boolean);"
            f" const n = find({STORE}.tree); return n ? '#tree-row-' + n.id : null; }})()"
        )
        run.expect(isinstance(mesh_row, str), "the tree has no /mesh/settings node")
        ui.double_press(str(mesh_row))
        ui.wait_for(lambda: ui.present("[data-ws=mesh-panel]"), "the Mesh panel")
        run.expect(
            ui.attr("[data-ws-panel-tab='mesh']", "aria-selected") == "true",
            "opening Mesh settings did not bring the Mesh tab forward",
        )
        ui.wait_for(lambda: ui.present("[data-ws=mesh-panel] .mesh-fields"), "the mesh settings")
        run.expect(
            not ui.present("[data-ws=mesh-create]"),
            "the Mesh panel offers Create settings, though the project has them",
        )
        marked = ui.attr(str(mesh_row), "aria-selected")
        run.expect(marked == "true", f"the chosen Mesh settings row is not selected ({marked!r})")
        ui.stage("Solve")
        solver_row = ui.js(
            "(() => { const find = (n) => n.path === '/solver/settings' ? n"
            " : (n.children || []).map(find).find(Boolean);"
            f" const n = find({STORE}.tree); return n ? '#tree-row-' + n.id : null; }})()"
        )
        run.expect(isinstance(solver_row, str), "the tree has no /solver/settings node")
        ui.double_press(str(solver_row))
        ui.wait_for(
            lambda: ui.present("[data-ws=run-settings] .mesh-fields"), "the Solve panel's settings"
        )
        run.expect(
            not ui.present("[data-ws=run-create]"),
            "the Solve panel offers Create settings, though the project has them",
        )
        run.expect(
            ui.attr("[data-ws-panel-tab='solve']", "aria-selected") == "true",
            "Solver settings did not bring the Solve tab forward",
        )
        # Enter on a chosen row opens its panel again, whichever tab is in front.
        ui.press("[data-ws-panel-tab='properties']")
        ui.wait_for(
            lambda: ui.attr("[data-ws-panel-tab='properties']", "aria-selected") == "true",
            "the Properties tab",
        )
        app.evaluate("document.querySelector('[role=tree]').focus()")
        ui.enter()
        ui.wait_for(
            lambda: ui.attr("[data-ws-panel-tab='solve']", "aria-selected") == "true",
            "Enter to bring the Solve tab forward",
        )
        return (
            "Mesh settings opens the Mesh panel and Solver settings the Solve panel, each on its "
            "settings and neither offering Create settings; Enter on a chosen row opens its panel"
        )

    run.step("opening a settings row brings its panel forward", b_a_settings_row_opens_its_panel)

    def a_import_menu_offers_a_file_input() -> str:
        ui.press(".project-btn")
        ui.wait_for(lambda: ui.present("[role=menu]"), "the project menu")
        items = ui.js(
            "[...document.querySelectorAll('[role=menuitem]')].map((e) => e.textContent.trim())"
        )
        run.expect(
            any("Import geometry" in str(i) for i in items)
            and any("Server path" in str(i) for i in items),
            f"the project menu lists {items!r}: no Import geometry and Server path",
        )
        ui.press_text("[role=menuitem]", "Import geometry")
        time.sleep(0.5)
        run.expect(
            not ui.present("[role=dialog]"),
            "Import geometry opened a typed-path dialog: a browser has no path to give",
        )
        accept = ui.attr("[data-ws=import-file-input]", "accept")
        run.expect(
            all(s in accept for s in (".step", ".stp", ".stl", ".3mf")),
            f"the file input accepts {accept!r}",
        )
        return f"Import geometry goes to the file input (accept {accept}); Server path is beside it"

    run.step(
        "Import geometry takes a file, with the typed path one entry away",
        a_import_menu_offers_a_file_input,
    )

    def a_refusals_say_why() -> str:
        said = []
        # A name no format claims is refused before anything goes up.
        before = int(app.evaluate(f"{fake}.requests.length"))
        app.evaluate(f"{fake}.fileInput('notes.txt', 2000)")
        ui.wait_for(
            lambda: any("not a geometry file" in t for t in ui.toasts()), "the refusal of notes.txt"
        )
        run.expect(
            int(app.evaluate(f"{fake}.requests.length")) == before,
            "a name that is no format was uploaded",
        )
        said.append("notes.txt refused on the page")
        for status, words in ((413, "too large"), (415, "not supported")):
            app.evaluate(f"{fake}.status = {status}")
            app.evaluate(f"{fake}.fileInput('duct.step', 3000000)")
            ui.wait_for(
                lambda words=words: any(words in t for t in ui.toasts()),
                f"the {status} toast",
            )
            said.append(f"{status} reads '{words}'")
            time.sleep(0.4)
        app.evaluate(f"{fake}.status = 200; {fake}.failWith = 'network'")
        app.evaluate(f"{fake}.fileInput('duct.step', 3000000)")
        ui.wait_for(
            lambda: any("connection" in t for t in ui.toasts()), "the network failure toast"
        )
        said.append("a dropped connection says so")
        app.evaluate(f"{fake}.failWith = null")
        run.expect(
            not ui.present("[data-ws=upload-status]"), "the upload card stays after a failure"
        )
        imports = int(app.evaluate(f"{fake}.imports.length"))
        run.expect(imports == 0, f"{imports} imports started from uploads that failed")
        return "; ".join(said)

    run.step("an upload that is refused or lost says why and imports nothing", a_refusals_say_why)

    def a_progress_and_cancel() -> str:
        app.evaluate(f"{fake}.hold = true")
        app.evaluate(f"{fake}.fileInput('duct.step', 12000000)")
        ui.wait_for(lambda: ui.present("[data-ws=upload-status]"), "the upload card")
        ui.wait_for(
            lambda: "MB /" in ui.text("[data-ws=upload-status-text]"), "the progress to be reported"
        )
        said = ui.text("[data-ws=upload-status-text]")
        run.expect(
            said.startswith("Uploading duct.step") and said.endswith("/ 12 MB"),
            f"the card reads {said!r}",
        )
        now = ui.attr("[data-ws=upload-status] [role=progressbar]", "aria-valuenow")
        run.expect(now.isdigit() and 0 < int(now) < 100, f"the bar reads {now!r} per cent")
        ui.shots("upload")
        ui.press("[data-ws=upload-cancel]")
        ui.wait_for(lambda: not ui.present("[data-ws=upload-status]"), "the card to go")
        ui.wait_for(lambda: any("Cancelled" in t for t in ui.toasts()), "the cancel toast")
        run.expect(int(app.evaluate(f"{fake}.cancelled")) == 1, "the request was not aborted")
        run.expect(
            int(app.evaluate(f"{fake}.imports.length")) == 0, "a cancelled upload was imported"
        )
        app.evaluate(f"{fake}.hold = false")
        return f"{said}; Cancel aborts the request and nothing is imported"

    run.step("an upload shows its progress and Cancel stops it", a_progress_and_cancel)

    def a_units_cancel_says_so() -> str:
        # A mesh format does not say its units, so the import asks; backing out of the question
        # ends the import and must say so. The reply names a .stl path only to reach that question.
        real_path = str(app.evaluate(f"{fake}.path"))
        app.evaluate(f"{fake}.path = 'C:/parts/duct.stl'")
        before = int(app.evaluate(f"{fake}.imports.length"))
        try:
            app.evaluate(f"{fake}.fileInput('duct.stl', 3000000)")
            ui.wait_for(lambda: ui.present("[role=dialog]"), "the units question")
            app.press("Escape", "Escape", 27)
            ui.wait_for(lambda: not ui.present("[role=dialog]"), "the units question to close")
            ui.wait_for(
                lambda: any("Import cancelled" in t for t in ui.toasts()),
                "the Import cancelled toast",
            )
            run.expect(
                int(app.evaluate(f"{fake}.imports.length")) == before,
                "an import started after the units question was cancelled",
            )
        finally:
            app.evaluate(f"{fake}.path = {json.dumps(real_path)}")
        return "Escape on the units question says 'Import cancelled' and starts nothing"

    run.step("backing out of the units question says Import cancelled", a_units_cancel_says_so)

    def a_picked_file_is_uploaded_and_imported() -> str:
        before = int(app.evaluate(f"{fake}.requests.length"))
        app.evaluate(f"{fake}.fileInput('duct.step', 12000000)")
        ui.wait_for(
            lambda: int(app.evaluate(f"{fake}.requests.length")) > before, "the upload request"
        )
        request = app.evaluate(f"{fake}.requests[{fake}.requests.length - 1]")
        run.expect(request["method"] == "POST", f"the upload is a {request['method']}")
        run.expect(
            request["url"].endswith("/api/upload?name=duct.step"),
            f"the upload went to {request['url']}",
        )
        token = str(app.evaluate(f"{STORE}.client.token"))
        run.expect(token != "", "the page has no token to compare with")
        run.expect(
            token not in request["url"] and "token" not in request["url"],
            "the token is in the upload URL",
        )
        run.expect(
            request["token"] == token,
            f"the X-Stokes-Token header was {request['token']!r}, not the session's token",
        )
        run.expect(request["size"] == 12000000, f"the body is {request['size']} bytes")
        ui.wait_for(
            lambda: int(app.evaluate(f"{fake}.imports.length")) == 1,
            "geometry.import after the upload",
        )
        sent = app.evaluate(f"{fake}.imports[0]")
        run.expect(sent["path"] == str(step_file), f"geometry.import was given {sent!r}")

        def has_part() -> bool:
            project = session.project
            return project is not None and any(True for _ in parts_under(project.root))

        ui.wait_for(has_part, "the imported part", 60.0)
        part = _part_id(app)
        GeometryStore.of(session).wait(part, timeout=120)
        ui.wait_for(lambda: not ui.present("[data-ws=import-hint]"), "the hint to go")
        return (
            "the file goes up with the token in the header (not the URL), the server's path "
            "goes to geometry.import, the part appears and the hint goes"
        )

    run.step("a picked file is uploaded and imported", a_picked_file_is_uploaded_and_imported)

    def a_drop_outlines_and_uploads() -> str:
        app.evaluate(f"{fake}.skipImport = true")
        before = int(app.evaluate(f"{fake}.requests.length"))
        app.evaluate(f"{fake}.drag(['bracket.step'], 3000000)")
        ui.wait_for(lambda: ui.present("[data-ws=import-drop]"), "the drop outline")
        outline = ui.js(
            "getComputedStyle(document.querySelector('[data-ws=import-drop]')).borderTopStyle"
        )
        run.expect(outline == "dashed", f"the drop outline is {outline!r}")
        ui.shots("drop")
        app.evaluate(f"{fake}.drop(['bracket.step'], 3000000)")
        ui.wait_for(
            lambda: not ui.present("[data-ws=import-drop]"), "the outline to go after the drop"
        )
        ui.wait_for(
            lambda: int(app.evaluate(f"{fake}.requests.length")) == before + 1, "the dropped upload"
        )
        run.expect(
            app.evaluate(f"{fake}.requests[{fake}.requests.length - 1].name") == "bracket.step",
            "the dropped file was not the one uploaded",
        )
        ui.wait_for(
            lambda: int(app.evaluate(f"{fake}.imports.length")) == 2, "the import after the drop"
        )
        # Several files at once are refused whole.
        app.evaluate(f"{fake}.drop(['a.step', 'b.step'], 1000)")
        ui.wait_for(
            lambda: any("one file at a time" in t for t in ui.toasts()), "the several-files toast"
        )
        run.expect(
            int(app.evaluate(f"{fake}.requests.length")) == before + 1,
            "two dropped files were uploaded",
        )
        app.evaluate(f"{fake}.skipImport = false")
        return (
            "a drag outlines the viewport, a drop uploads and imports the one file, "
            "two files are refused"
        )

    run.step(
        "a file dropped on the viewport is uploaded, several are refused",
        a_drop_outlines_and_uploads,
    )

    # ======================================================== C: boundaries from a new body
    def c_next_step_leads_to_the_boundaries() -> str:
        label = ui.text("[data-ws=next-step-label]")
        run.expect(
            label == "Define boundaries", f"Next step reads {label!r} with a body and no set-up"
        )
        ui.stage("Geometry")
        ui.press("[data-ws=next-step]")
        ui.wait_for(
            lambda: ui.attr(".stage-chips [aria-label='Physics']", "aria-current") == "step",
            "the Physics stage",
        )
        ui.wait_for(lambda: ui.present("[data-ws=setup-flow]"), "the Set up flow card")
        run.expect(
            ui.attr("[data-ws-panel-tab='conditions']", "aria-selected") == "true",
            "Next step did not bring the Conditions tab forward",
        )
        run.expect(
            ui.disabled("[data-ws=setup-flow-create]"),
            "the region button is enabled before the inlet and the outlet are assigned",
        )
        why = ui.text("[data-ws=setup-flow-why]")
        run.expect("inlet and the outlet" in why, f"the card says {why!r}")
        ui.shots("set-up-flow")
        return (
            "Next step goes to Physics and the Conditions panel; "
            "the card holds the region back and says why"
        )

    run.step(
        "Next step goes to Define boundaries and the Set up flow card",
        c_next_step_leads_to_the_boundaries,
    )

    def assign(kind: str, faces: list[int], value: str | None) -> None:
        part = _part_id(app)
        # Face mode, then the faces picked as a click on them would (the store's one bulk path).
        app.evaluate(f"{STORE}.setPickMode('face')")
        app.evaluate(
            f"{STORE}.pickMany("
            f"[{{part: {json.dumps(part)}, faces: {json.dumps(faces)}}}], 'replace')"
        )
        ui.wait_for(lambda: ui.present("[aria-label='Assign a condition']"), "the Assign button")
        ui.press("[aria-label='Assign a condition']")
        ui.wait_for(lambda: ui.present("[role=menu]"), "the Assign menu")
        ui.press_text("[role=menuitem]", kind)
        if value is not None:
            field = "[data-ws=condition-panel] [data-prop-field='quantity']"
            ui.wait_for(lambda: ui.present(field), "the value field")
            ui.press(field)
            app.evaluate(
                "(() => { const el = document.querySelector("
                + json.dumps(field)
                + "); el.focus(); el.select(); })()"
            )
            ui.type_text(value)
            # The headline flow of the value panel: type the value, Enter. It commits the field
            # and the card.
            ui.enter()
            ui.wait_for(
                lambda: not ui.present("[data-ws=condition-panel]"), "the value panel to close"
            )
        else:
            time.sleep(0.8)

    def c_assign_the_inlet_and_the_outlet() -> str:
        part = _part_id(app)
        sides = step_faces(session, part)
        inlet, outlet = flat(sides["inlet"]), flat(sides["outlet"])
        assign("Velocity inlet", inlet, "0.1")
        ui.wait_for(lambda: ui.named_row("inlet") != {}, "the inlet row in the tree")
        said = ui.named_row("inlet").get("value")
        run.expect(said == "inlet · 0.1 m/s", f"the inlet row reads {said!r}")
        run.expect(
            ui.attr("[data-ws=setup-flow-create]", "aria-disabled") == "true",
            "the region button is enabled with only the inlet assigned",
        )
        run.expect(
            "outlet" in ui.text("[data-ws=setup-flow-why]"), "the card does not ask for the outlet"
        )
        assign("Pressure outlet", outlet, None)
        ui.wait_for(lambda: ui.named_row("outlet") != {}, "the outlet row in the tree")
        outlet_text = str(ui.named_row("outlet").get("value"))
        run.expect(outlet_text.startswith("outlet · "), f"the outlet row reads {outlet_text!r}")
        ui.wait_for(
            lambda: not ui.disabled("[data-ws=setup-flow-create]"), "the region button to enable"
        )
        return f"inlet reads '{said}', outlet reads '{outlet_text}'; the region can be made"

    run.step(
        "assign Inlet and Outlet to picked faces by presses; the tree says kind and value",
        c_assign_the_inlet_and_the_outlet,
    )

    def c_set_up_flow_makes_the_region_and_the_walls() -> str:
        ui.press("[data-ws=setup-flow-create]")
        ui.wait_for(
            lambda: not ui.present("[data-ws=setup-flow]"), "the card to go once the region is made"
        )
        ui.wait_for(
            lambda: (ui.named_row("walls") or {}).get("value", "").startswith("wall"),
            "the walls row",
        )
        fluid = ui.named_row("fluid")
        run.expect(
            str(fluid.get("value")) == "water · 3 boundaries",
            f"the region row reads {fluid.get('value')!r}",
        )
        walls = ui.named_row("walls")
        run.expect(
            walls.get("value") == "wall · no_slip", f"the walls row reads {walls.get('value')!r}"
        )
        for name in ("fluid", "inlet", "outlet", "walls"):
            dot = str(ui.named_row(name).get("dot"))
            run.expect("ok" in dot, f"the {name} row's status dot is {dot!r}, not ok")
        ui.wait_for(lambda: ui.js(f"{STORE}.setup?.state") == "ready", "setup.check to say ready")
        check = ui.js(f"{STORE}.setup")
        run.expect(
            not any(f["code"] == "setup.overlap" for f in check["findings"]),
            "the walls overlap the inlet and the outlet: the region was made too early",
        )
        ui.shots("boundaries")
        return (
            "water, 3 boundaries; inlet, outlet and walls are ok; "
            "setup.check is ready with no overlap"
        )

    run.step(
        "Set up flow makes the region and walls; the tree turns ready",
        c_set_up_flow_makes_the_region_and_the_walls,
    )

    def c_next_step_moves_on() -> str:
        ui.wait_for(
            lambda: ui.text("[data-ws=next-step-label]") == "Generate mesh",
            "Next step to say Generate mesh",
        )
        generate = (
            ui.disabled("[data-ws='mesh-generate']")
            if ui.present("[data-ws='mesh-generate']")
            else None
        )
        blocked = ui.disabled("[data-ws=next-step]")
        tip = ui.attr("[data-ws=next-step]", "aria-label")
        run.expect("Generate mesh" in tip, f"Next step is named {tip!r}")
        if generate is not None:
            run.expect(
                blocked == generate,
                f"Next step is blocked={blocked} but Generate is blocked={generate}",
            )
        if blocked:
            app.hover(*app.centre("[data-ws=next-step]"))
            shown = ""
            for _ in range(15):
                shown = str(
                    app.evaluate(
                        "(document.querySelector('[role=tooltip]')?.textContent || '').trim()"
                    )
                )
                if shown:
                    break
                time.sleep(0.2)
            run.expect(
                "Generate mesh" in shown and len(shown) > len("Generate mesh. "),
                f"the blocked step's tooltip does not say why: {shown!r}",
            )
            return f"Next step reads Generate mesh and is blocked; its tooltip says: {shown}"
        return "Next step reads Generate mesh and is enabled"

    run.step(
        "Next step follows setup.check to the mesh and says why when it is blocked",
        c_next_step_moves_on,
    )

    # ======================================================== D: the results
    def d_run_and_plot() -> str:
        app.evaluate(FAKE_RUN_SCRIPT.read_text(encoding="utf-8"))
        app.evaluate("window.__fakeRun.install()")
        app.evaluate(
            "window.__fakeRun.monitors = {inlet: {flow_rate: -3.2e-5, p_mean: 0.0851},"
            " outlet: {flow_rate: 3.2e-5, p_mean: 0}, walls: {flow_rate: null, p_mean: null}}"
        )
        app.evaluate("window.__fakeRun.setReady(true)")
        app.evaluate(FAKE_RESULTS_SCRIPT.read_text(encoding="utf-8"))
        reply = {
            "run": "r_0001",
            "time": 52,
            "slice": True,
            "surfaces": "ok",
            "fields": [
                {"name": "U", "units": "m/s", "min": 0.0, "max": 0.16},
                {"name": "U_mag", "units": "m/s", "min": 0.0, "max": 0.158},
                {"name": "p", "units": "m2/s2", "min": -0.01, "max": 0.0851},
            ],
        }
        app.evaluate(
            "window.__fakeResults.install(" + json.dumps({"routes": {}, "reply": reply}) + ")"
        )
        ui.stage("Solve")
        ui.wait_for(lambda: not ui.disabled("[data-ws=run-top]"), "Run to be enabled")
        ui.press("[data-ws=run-top]")
        ui.wait_for(
            lambda: str(app.evaluate(f"{STORE}.run.runs[0]?.state")) == "done",
            "the run to finish",
            60.0,
        )
        row = ui.js(
            "[...document.querySelectorAll('.tree-row[data-row=run]')]"
            ".map((r) => r.textContent.trim())"
        )
        run.expect(bool(row) and "converged" in str(row[0]), f"the Runs group lists {row!r}")
        return f"a run of the scripted solver finishes; the tree lists it as {row[0]!r}"

    run.step("Run on the scripted solver finishes and the tree lists the run", d_run_and_plot)

    def d_results_stage_plots_and_tabulates() -> str:
        ui.stage("Results")
        ui.wait_for(lambda: ui.present("[data-ws=results-panel]"), "the Results panel")
        ui.wait_for(lambda: ui.present("[data-ws=monitors-table]"), "the monitors table")
        ui.wait_for(
            lambda: ui.present("[data-ws=results-residuals] [data-ws=run-chart]"),
            "the residual chart in the Results stage",
        )
        legend = str(
            ui.js(
                "[...document.querySelectorAll('[data-ws=results-residuals] .run-legend li')]"
                ".map((e) => e.textContent.trim()).join(',')"
            )
        )
        run.expect("p" in legend and "Ux" in legend, f"the chart's legend reads {legend!r}")
        final = ui.text("[data-ws=results-final]")
        run.expect("Iterations52" in final.replace(" ", ""), f"the final values read {final!r}")
        rows = ui.js(
            "[...document.querySelectorAll('[data-ws=monitors-table] tbody tr')]"
            ".map((r) => [...r.children].map((c) => c.textContent.trim()))"
        )
        run.expect(
            rows
            == [["inlet", "-3.2e-5", "0.0851"], ["outlet", "3.2e-5", "0"], ["walls", "-", "-"]],
            f"the monitors table reads {rows!r}",
        )
        drop = ui.text("[data-ws=monitors-drop]")
        run.expect("0.0851 m2/s2" in drop, f"the pressure drop reads {drop!r}")
        # The drop sits beside the legend too, where the colours are read.
        beside = ui.text("[data-ws=field-drop]")
        run.expect("0.0851 m2/s2" in beside, f"the viewport's pressure drop reads {beside!r}")
        for section, name in (
            ("results-residuals", "results-residuals"),
            ("results-monitors", "results"),
        ):
            ui.js(
                "document.querySelector("
                + json.dumps(f"[data-ws={section}]")
                + ").scrollIntoView({block: 'start'})"
            )
            time.sleep(0.4)
            ui.shots(name)
        # ASCII only: the console of a Windows run prints through cp1252, which has no Delta.
        said = beside.replace("\u0394", "d").replace("\u03c1", "rho")
        return f"chart ({legend}), table {rows}, drop '{said}'"

    run.step(
        "the Results stage plots the residuals and tabulates the monitors",
        d_results_stage_plots_and_tabulates,
    )

    def d_copy_and_export() -> str:
        app.evaluate(
            "window.__copied = []; window.__blobs = [];"
            "const sel = HTMLTextAreaElement.prototype.select;"
            "HTMLTextAreaElement.prototype.select = function () {"
            " window.__copied.push(this.value); return sel.call(this); };"
            "const make = URL.createObjectURL.bind(URL);"
            "URL.createObjectURL = (b) => { window.__blobs.push(b); return make(b); };"
        )
        ui.press("[data-ws=monitors-copy]")
        ui.wait_for(
            lambda: any("Copied the monitors table" in t for t in ui.toasts()), "the copy toast"
        )
        copied = str(app.evaluate("window.__copied[window.__copied.length - 1] || ''"))
        run.expect(
            copied.startswith(
                "patch\tflow rate (m3/s)\tmean p/rho (m2/s2)\ninlet\t-3.2e-5\t0.0851\n"
            ),
            f"the table was copied as {copied!r}",
        )
        ui.press("[data-ws=monitors-csv]")
        ui.wait_for(lambda: any("Exported" in t for t in ui.toasts()), "the export toast")
        app.evaluate(
            "window.__blobs[window.__blobs.length - 1].text().then((t) => { window.__csv = t; })"
        )
        ui.wait_for(lambda: bool(app.evaluate("window.__csv")), "the CSV text")
        csv = str(app.evaluate("window.__csv"))
        run.expect(
            csv == "patch,flow_rate_m3/s,p_mean_m2/s2\n"
            "inlet,-0.000032,0.0851\noutlet,0.000032,0\nwalls,,\n",
            f"the CSV reads {csv!r}",
        )
        return (
            "Copy table puts a tab-separated table on the clipboard, "
            "Export CSV writes the full numbers"
        )

    run.step("Copy table and Export CSV work from the data in the store", d_copy_and_export)

    def d_section_view_and_picking_still_work() -> str:
        # The stage panels must not break Section view or picking: the section switches on
        # and a pick still lands.
        app.evaluate(f"{STORE}.setSection(true)")
        time.sleep(0.6)
        on = app.evaluate(f"{STORE}.section !== null")
        app.evaluate(f"{STORE}.setSection(false)")
        part = _part_id(app)
        app.evaluate(f"{STORE}.pickMany([{{part: {json.dumps(part)}, faces: [0]}}], 'replace')")
        time.sleep(0.4)
        picked = app.evaluate(f"Object.values({STORE}.picked.faces).flat().length")
        run.expect(bool(on), "Section view did not switch on in the Results stage")
        run.expect(int(picked) == 1, f"a pick in the Results stage holds {picked} faces")
        return "Section view switches on and a face pick lands with the results stage's panels open"

    run.step(
        "Section view and picking still work beside the Results panels",
        d_section_view_and_picking_still_work,
    )


def _part_id(app: App) -> str:
    from stokes.geometry.store import parts_under

    session = app.session
    project = session.project if session is not None else None
    if project is not None:
        for node in parts_under(project.root):
            return str(node.id)
    raise Failure("the project has no part")


def main() -> int:
    app = App(None, None, None)
    run = Checks(app)
    try:
        print("checks:")
        check_gui_flow(run, app)
    finally:
        app.close()
        passed = sum(1 for s in run.steps if s["ok"])
        verdict = "PASS" if all(s["ok"] for s in run.steps) else "FAIL"
        print(f"\n{verdict}: {passed}/{len(run.steps)} checks")
    return 0 if all(s["ok"] for s in run.steps) else 1


if __name__ == "__main__":
    sys.exit(main())
