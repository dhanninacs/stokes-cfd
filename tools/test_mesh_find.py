"""Finding a mesh that has been made, on real presses.

    python tools/test_mesh_find.py
    python tools/test_interactions.py --only meshfind

The report behind it: after meshing through the browser, `mesh/results` showed a count of 1 and a
click on it did nothing, the `mesh_patch` rows never appeared, and the mesh sat behind the geometry
with no control that said "show me". The tree did expand (the chevron and a double click both opened
`results`), but a click only selects, and `results` and the result under it arrived folded; and the
mesh is drawn only in the Mesh stage and only for visible patches, so a finished mesh could be on no
screen at all.

The mesh is the synthetic one of `tools/mesh_demo_project.py` (OpenFOAM is not installed here),
written into a running session through the service's own `_finish_with`, the one place a real run's
result also arrives, so the tree gets its `mesh_patch` rows from `node.added` events as it does for
a real mesh. The page, the store, the tree, the panel and every press are real.

It runs its own App (a second server and browser) on a project it makes, so it does not depend on
what the interaction run's project holds and leaves that one alone.
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from test_interactions import App, Checks, Failure

STORE = "window.__stokes.getState()"


def make_duct(folder: Path) -> Path:
    """The duct project without a mesh, saved; the path of the `.stokes` folder."""
    from stokes.api import Session
    from tools.e2e_support import duct_project

    session = Session()
    duct_project(session, folder)
    session.execute("project.save")
    assert session.project_path is not None
    return Path(session.project_path)


def check_mesh_find(run: Checks, app: App) -> None:
    """A finished mesh opens the tree to its patches, and the Mesh panel can show it."""
    if app.session is None:
        print("  skip the mesh-find checks: an attached window has no session to script")
        return
    folder = Path(tempfile.mkdtemp(prefix="stokes-meshfind-")) / "duct"
    project = make_duct(folder)
    me = App(project, None)
    inner = Checks(me)

    def js(expression: str) -> Any:
        return me.evaluate(expression)

    def present(selector: str) -> bool:
        return bool(js(f"Boolean(document.querySelector({json.dumps(selector)}))"))

    def wait_for(condition: Any, what: str, seconds: float = 30.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.25)
        raise Failure(f"timed out waiting for {what}")

    def rows() -> list[dict[str, Any]]:
        found = js(
            "Array.from(document.querySelectorAll('[role=treeitem]')).map((r) => ({"
            " name: r.querySelector('span.truncate')?.textContent ?? '',"
            " level: Number(r.getAttribute('aria-level')),"
            " expanded: r.getAttribute('aria-expanded')}))"
        )
        return list(found) if isinstance(found, list) else []

    def row(name: str) -> dict[str, Any] | None:
        return next((r for r in rows() if r["name"] == name), None)

    def patches() -> int:
        return int(js(f"{STORE}.geometry.filter((g) => g.kind === 'mesh').length") or 0)

    def meshes_drawn() -> int:
        info = js("window.__stokesScene.meshInfo()")
        return int(info.get("patchesShown", 0)) if isinstance(info, dict) else 0

    def press(selector: str) -> None:
        js(
            "(() => { const el = document.querySelector("
            + json.dumps(selector)
            + "); if (el) el.scrollIntoView({block: 'center'}); })()"
        )
        time.sleep(0.2)
        me.click(*me.centre(selector))

    def result_node_ids() -> list[str]:
        found = js(
            "(() => { const out = [];"
            " const walk = (n) => { if (n.type === 'mesh_patch') out.push(n.id);"
            " (n.children ?? []).forEach(walk); };"
            " walk(" + STORE + ".tree); return out; })()"
        )
        return [str(i) for i in found] if isinstance(found, list) else []

    def arrives_and_opens() -> str:
        wait_for(lambda: rows() != [], "the tree")
        js(f"{STORE}.setStage('Mesh')")
        wait_for(lambda: row("settings") is not None, "the Mesh stage's rows")
        run.expect(row("results") is None, "the tree has a results group before any mesh")
        from stokes.mesh.model import SETTINGS_PATH
        from stokes.mesh.snapshot import build_snapshot
        from tools.mesh_demo_project import write_result

        assert me.session is not None
        snap = build_snapshot(me.session, me.session.require_project().find(SETTINGS_PATH))
        made = write_result(me.session, snap)
        wait_for(lambda: patches() >= 3, "the mesh patches to reach the page")
        wait_for(lambda: row("results") is not None, "the results row")
        results, result = row("results"), row("blockmesh")
        run.expect(results is not None and results["expanded"] == "true", f"results is {results}")
        run.expect(result is not None, "the result row is not under results")
        names = [r["name"] for r in rows()]
        for patch in made["patches"]:
            run.expect(patch in names, f"the tree has no row for the patch {patch}: {names}")
        return f"results and the result are open; rows for {', '.join(made['patches'])}"

    def a_press_still_folds() -> str:
        press("[role=treeitem][aria-expanded=true] button[aria-label='Collapse results']")
        wait_for(lambda: (row("results") or {}).get("expanded") == "false", "results to fold")
        time.sleep(0.6)
        folded = (row("results") or {}).get("expanded")
        run.expect(folded == "false", f"the tree reopened a group the person folded: {folded}")
        press("[aria-label='Expand results']")
        wait_for(lambda: (row("results") or {}).get("expanded") == "true", "results to open")
        return "the chevron folds and opens it, and the fold is not undone"

    def banner_in_another_stage() -> str:
        js(f"{STORE}.setStage('Physics')")
        time.sleep(0.5)
        run.expect(meshes_drawn() == 0, "the mesh is drawn in the Physics stage")
        js("document.querySelector('[data-ws-panel-tab=\"mesh\"]')?.click()")
        wait_for(lambda: present("[data-ws=mesh-offscreen]"), "the 'not shown' banner")
        run.expect(present("[data-ws=mesh-show-surface]"), "the banner has no Show mesh button")
        run.expect(present("[data-ws=mesh-show-cells]"), "the banner has no With cells button")
        return "the Mesh panel says the mesh is not shown and offers Show mesh"

    def show_mesh_draws_it() -> str:
        press("[data-ws=mesh-show-surface]")
        wait_for(lambda: js(f"{STORE}.stage") == "Mesh", "the Mesh stage")
        wait_for(lambda: meshes_drawn() >= 3, "the mesh to be drawn")
        wait_for(lambda: not present("[data-ws=mesh-offscreen]"), "the banner to go")
        run.expect(js(f"{STORE}.meshDisplay") == "surface", "the display is not the mesh surface")
        return f"Show mesh went to the Mesh stage and drew {meshes_drawn()} patches"

    def hidden_patches_come_back() -> str:
        assert me.session is not None
        for node_id in result_node_ids():
            me.session.execute("parameter.set", node=node_id, name="visible", value=False)
        wait_for(lambda: meshes_drawn() == 0, "every patch to hide")
        wait_for(lambda: present("[data-ws=mesh-offscreen]"), "the banner for hidden patches")
        press("[data-ws=mesh-show-cells]")
        wait_for(lambda: meshes_drawn() >= 3, "the patches to show again")
        run.expect(
            js(f"{STORE}.meshDisplay") == "cells", "With cells did not choose the cell edges"
        )
        run.expect(not present("[data-ws=mesh-offscreen]"), "the banner stayed")
        js(f"{STORE}.setMeshDisplay('surface')")
        return "Show mesh switches the hidden patches back on; With cells draws the cell edges"

    try:
        inner.step(
            "a mesh that arrives opens results and the result to its patches", arrives_and_opens
        )
        inner.step("the chevron still folds it, and nothing reopens it", a_press_still_folds)
        inner.step(
            "another stage: the Mesh panel says the mesh is not shown", banner_in_another_stage
        )
        inner.step("Show mesh goes to the Mesh stage and draws it", show_mesh_draws_it)
        inner.step(
            "hidden patches: Show mesh turns them on, With cells adds the edges",
            hidden_patches_come_back,
        )
    finally:
        run.steps.extend(inner.steps)
        me.close()


if __name__ == "__main__":
    from test_interactions import main

    sys.exit(main(["--only", "meshfind"]))
