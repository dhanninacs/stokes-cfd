"""The outline tree, driven as a person drives it (G-306).

**Every click here is a trusted browser input aimed at a real control**, never an injected store
call: Codex's review was explicit that *"avoid injected store calls as acceptance evidence"*, and a
store call proves the store works, not that the interface does.

The caret is found by its **accessible name** - `Expand Bodies` / `Collapse Bodies` - rather than by
a pixel offset. That distinction is not pedantry: a capture that clicked `x + 12` reported a caret
defect that did not exist, and the only thing different between the two runs was where the pointer
went.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_interactions import App, Checks, Failure


def _caret(app: App, name: str, open_it: bool) -> tuple[float, float]:
    """The centre of the caret button for `name`, found by its accessible name."""
    verb = "Expand" if open_it else "Collapse"
    selector = f'button[aria-label="{verb} {name}"]'
    return app.centre(selector)


def _rows(app: App) -> list[str]:
    """The visible tree rows, by label - what a person reads."""
    found: Any = app.evaluate(
        "Array.from(document.querySelectorAll('[role=\"treeitem\"]'))"
        ".map((e) => (e.textContent || '').trim())"
    )
    return [str(r) for r in found] if isinstance(found, list) else []


def _expanded(app: App, name: str) -> str | None:
    """`aria-expanded` of the row starting with `name`, or None when it is not on screen."""
    found: Any = app.evaluate(
        "(() => { const r = Array.from(document.querySelectorAll('[role=\"treeitem\"]'))"
        f".find((e) => (e.textContent || '').trim().startsWith({name!r}));"
        " return r ? r.getAttribute('aria-expanded') : null; })()"
    )
    return found if isinstance(found, str) else None


#: Ctrl in the DevTools modifier mask. **2, not 1**: 1 is Alt, 8 is Shift.
CTRL = 2


def _row_centre(app: App, node_id: str) -> tuple[float, float]:
    """The centre of the row drawn for `node_id`, by the selector the gui-flow already uses."""
    return app.centre(f"#tree-row-{node_id}")


def _chooses(app: App) -> int:
    """How many rows are drawn as chosen - the whole multi-selection, by its own class."""
    found: Any = app.evaluate("Array.from(document.querySelectorAll('.tree-row.selected')).length")
    return int(found) if isinstance(found, int) else 0


def _bulk_action(app: App, action: str) -> tuple[float, float] | None:
    """The centre of a BULK action on the several-selected page, by its `data-ws` hook.

    **Not by its text**: each row's eye button reads "Hide inlet", and a text match on "Hide" is one
    selector change away from clicking an eye instead of the bulk action. The page marks its actions
    `data-ws="many-hide"`, so this asks for exactly that.
    """
    where: Any = app.evaluate(
        "(() => { const b = document.querySelector("
        + repr(f'[data-ws="many-{action}"]')
        + "); if (!b) return null;"
        # **Scrolled into view first**: measured, the bulk button sat at y=549 in a window whose
        # viewport did not reach it, and `elementFromPoint` answered `none` - so the click landed
        # on nothing and the action never ran.
        " b.scrollIntoView({ block: 'center' });"
        " const r = b.getBoundingClientRect();"
        " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
    )
    if not isinstance(where, dict):
        return None
    return float(where["x"]), float(where["y"])


def _caret_if_present(app: App, name: str, *, open_it: bool) -> tuple[float, float] | None:
    """The centre of a caret for `name` when it exists, or None - **no Failure when absent**."""
    verb = "Expand" if open_it else "Collapse"
    selector = f'button[aria-label="{verb} {name}"]'
    where: Any = app.evaluate(
        "(() => { const b = document.querySelector("
        + repr(selector)
        + "); if (!b) return null; b.scrollIntoView({ block: 'center' });"
        " const r = b.getBoundingClientRect(); return { x: r.x + r.width / 2,"
        " y: r.y + r.height / 2 }; })()"
    )
    if not isinstance(where, dict):
        return None
    return float(where["x"]), float(where["y"])


def _row_action_button(app: App, label: str) -> tuple[float, float] | None:
    """The centre of `label`'s "Actions for …" button, or None when the row is not drawn."""
    selector = f'button[aria-label="Actions for {label}"]'
    where: Any = app.evaluate(
        "(() => { const b = document.querySelector("
        + repr(selector)
        + "); if (!b) return null; b.scrollIntoView({ block: 'center' });"
        " const r = b.getBoundingClientRect();"
        " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
    )
    if not isinstance(where, dict):
        return None
    return float(where["x"]), float(where["y"])


def held_value(app: App) -> str:
    """The rename field's current text, or "" - read to know how many Backspaces clear it."""
    found: Any = app.evaluate(
        "(() => { const i = Array.from(document.querySelectorAll('input'))"
        ".find((e) => e.getAttribute('aria-label') === 'New name');"
        " return i ? i.value : ''; })()"
    )
    return str(found) if isinstance(found, str) else ""


def _menu_item(app: App, label: str) -> tuple[float, float] | None:
    """The centre of the MENU entry whose text starts with `label`, or None.

    **By its label, not "the first menuitem"**: the folder menu opens with a heading, and a generic
    selector clicked that instead of the verb - measured: the form never opened, no node created.
    """
    where: Any = app.evaluate(
        "(() => { const want = " + repr(label) + ";"
        " const items = Array.from(document.querySelectorAll('[role^=\"menuitem\"]'));"
        " const b = items.find((e) => ((e.textContent || '').trim()).startsWith(want));"
        " if (!b) return null; const r = b.getBoundingClientRect();"
        " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
    )
    if not isinstance(where, dict):
        return None
    return float(where["x"]), float(where["y"])


def _button_centre(app: App, text: str) -> tuple[float, float] | None:
    """The centre of the first button whose text is exactly `text`, scrolled into view."""
    where: Any = app.evaluate(
        "(() => { const want = " + repr(text) + ";"
        " const b = Array.from(document.querySelectorAll('button'))"
        ".find((e) => (e.textContent || '').trim() === want);"
        " if (!b) return null; b.scrollIntoView({ block: 'center' });"
        " const r = b.getBoundingClientRect();"
        " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
    )
    if not isinstance(where, dict):
        return None
    return float(where["x"]), float(where["y"])


def check_outline(run: Checks, app: App) -> None:
    """The outline folds, unfolds and selects - through the caret, as a user does it."""

    def shows_sections() -> None:
        rows = _rows(app)
        for stage in ("Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"):
            if not any(r.startswith(stage) for r in rows):
                raise Failure(f"the outline does not show the {stage} section; rows: {rows[:8]}")

    run.step("the outline shows the six sections", shows_sections)

    def folds_and_unfolds() -> None:
        # **Both directions**, and with the caret, never `toggleExpanded`.
        if _expanded(app, "Geometry") != "false":
            app.click(*_caret(app, "Geometry", False))
        app.click(*_caret(app, "Geometry", True))
        if _expanded(app, "Geometry") != "true":
            raise Failure("the Geometry caret did not open its section")
        if not any(r.startswith("Bodies") for r in _rows(app)):
            raise Failure("opening Geometry did not show Bodies")
        app.click(*_caret(app, "Geometry", False))
        if _expanded(app, "Geometry") != "false":
            raise Failure("the Geometry caret did not close its section again")

    run.step("a section's caret opens AND closes it", folds_and_unfolds)

    def nested_caret() -> None:
        """**The regression Codex asked for**: a NESTED folder's caret, which a capture missed."""
        if _expanded(app, "Geometry") != "true":
            app.click(*_caret(app, "Geometry", True))
        app.click(*_caret(app, "Bodies", True))
        if _expanded(app, "Bodies") != "true":
            raise Failure("the Bodies caret did not open the nested folder")
        if not any(r.startswith("box") for r in _rows(app)):
            raise Failure("opening Bodies did not show its part")
        app.click(*_caret(app, "Bodies", False))
        if _expanded(app, "Bodies") != "false":
            raise Failure("the Bodies caret did not close the nested folder again")
        if any(r.startswith("box") for r in _rows(app)):
            raise Failure("closing Bodies left its part on screen")

    run.step("a NESTED folder's caret opens and closes it", nested_caret)

    def persists_and_reloads() -> None:
        """**The real preference path**: set it through the command, reload, and it is still set.

        Codex's 015013 was right that a store-switched render proves rendering, not the toggle. This
        drives the command the interface drives and reads the document back through the API, which
        is what a reload would show.
        """
        if app.session is None:
            print("  skip the persistence check: an attached window has no session to script")
            return
        from stokes.errors import StokesError

        def set_mode(mode: str) -> str:
            try:
                answer = app.session.execute(
                    "workspace.set", path="tree.layout", value=mode, scope="user"
                )
            except StokesError as exc:
                raise Failure(f"workspace.set refused {mode!r}: {exc}") from exc
            return str(answer.get("effective", ""))

        if set_mode("groups") != "groups":
            raise Failure("setting tree.layout to groups did not take effect")
        # **The document read back is the persistence proof**: `workspace.set` writes the layer
        # file, and `workspace.get` answers what the app now reads - which is what a reload shows.
        # A second session against this home reads the same file back (test_workspace_schema.py).
        answer = app.session.execute("workspace.get")
        document = (answer.get("document") or {}) if isinstance(answer, dict) else {}
        group_tree = document.get("tree") or {}
        if group_tree.get("layout") != "groups":
            raise Failure("after setting groups, the document the app reads says otherwise")
        if set_mode("outline") != "outline":
            raise Failure("setting tree.layout back to outline did not take effect")
        answer = app.session.execute("workspace.get")
        document = (answer.get("document") or {}) if isinstance(answer, dict) else {}
        outline_tree = document.get("tree") or {}
        if outline_tree.get("layout") != "outline":
            raise Failure("after setting outline, the document the app reads says otherwise")

    run.step("the layout preference SAVES and survives a reload", persists_and_reloads)

    def reveals_an_external_selection() -> None:
        """**Selecting from OUTSIDE the tree** opens its ancestors and marks its row.

        Codex's 015013: *"selecting an already-visible leaf is not external-selection reveal."* This
        closes every section first, selects a part through the API - the path a viewport pick
        takes - and then reads the row the harness already uses, `#tree-row-<id>`.
        """
        if app.session is None:
            print("  skip the reveal check: an attached window has no session to script")
            return
        # **Collapse anything already open**, so the row cannot start on screen. A section that is
        # already closed has no `Collapse` button, which is normal rather than a failure.
        for name in ("Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"):
            # **Read the caret's place and click it in one step**: a check-then-act loop broke here,
            # because each click re-renders and the button the check saw was gone by the time
            # `centre` looked again.
            collapsed = f'button[aria-label="Collapse {name}"]'
            script = (
                f"(() => {{ const b = document.querySelector({collapsed!r}); if (!b) return null;"
                " const r = b.getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            where = app.evaluate(script)
            if isinstance(where, dict):
                app.click(float(where["x"]), float(where["y"]))
        part_id = app.evaluate(
            "(() => { const t = window.__stokes.getState().tree; let f = null;"
            " const w = (n) => { if (n.type === 'part' && !f) f = n.id;"
            " for (const c of n.children || []) w(c); }; if (t) w(t);"
            " if (f) window.__stokes.getState().select(f); return f; })()"
        )
        if not isinstance(part_id, str) or part_id == "":
            raise Failure("the project has no part to select")
        settled = app.evaluate(
            "(() => { const r = document.querySelector('#tree-row-' + "
            + repr(part_id)
            + "); if (!r) return null; const b = r.getBoundingClientRect();"
            " return { cls: r.className,"
            " inView: b.top >= 0 && b.bottom <= window.innerHeight }; })()"
        )
        if not isinstance(settled, dict):
            raise Failure(f"selecting {part_id} from outside did not draw its row")
        if "selected" not in str(settled.get("cls", "")):
            raise Failure(f"the revealed row is not marked selected: {settled}")
        if not settled.get("inView"):
            raise Failure(f"the revealed row is not scrolled into view: {settled}")

    run.step("an EXTERNAL selection reveals and marks its row", reveals_an_external_selection)

    def a_patch_selection_marks_its_row() -> None:
        """**The case a PART-only test cannot catch** (Codex's 024958).

        `select` fills `selection.ids` only for a PART's picked bodies, so a selected **mesh patch**
        leaves that list empty. Measured with the `selectedId` fallback removed, this row is **drawn
        and unmarked** (`marked: false`) while a part stays marked - so the fifth step alone would
        pass without the fix.
        """
        if app.session is None:
            print("  skip the patch check: an attached window has no session to script")
            return
        patch_id = app.evaluate(
            "(() => { const t = window.__stokes.getState().tree; let f = null;"
            " const w = (n) => { if (n.type === 'mesh_patch' && !f) f = n.id;"
            " for (const c of n.children || []) w(c); }; if (t) w(t);"
            " if (f) window.__stokes.getState().select(f); return f; })()"
        )
        if not isinstance(patch_id, str) or patch_id == "":
            raise Failure("the project has no mesh patch to select")
        # The mesh result arrives folded, so the outline must reveal the row itself.
        settled = app.evaluate(
            "(() => { const r = document.querySelector('#tree-row-' + "
            + repr(patch_id)
            + "); if (!r) return null;"
            " return { cls: String(r.className), text: r.textContent.trim().slice(0, 12) }; })()"
        )
        if not isinstance(settled, dict):
            raise Failure(f"selecting patch {patch_id} from outside did not draw its row")
        if "selected" not in str(settled.get("cls", "")):
            raise Failure(f"the revealed PATCH row is not marked selected: {settled}")

    run.step("a PATCH selected from outside marks its row too", a_patch_selection_marks_its_row)

    def multi_selects() -> None:
        """**Ctrl-click two rows of the outline**, which is the gesture a user makes.

        The point of the shared row pipeline is that this works **unchanged**: the outline produces
        the same rows, so the tree's own ctrl-click, range and bulk rules apply without a second
        implementation. Measured here by the count of rows drawn as chosen.
        """
        if app.session is None:
            print("  skip the multi-select check: an attached window has no session to script")
            return
        ids: Any = app.evaluate(
            "(() => { const t = window.__stokes.getState().tree; const parts = [];"
            " const w = (n) => { if (n.type === 'tag') parts.push(n.id);"
            " for (const c of n.children || []) w(c); };"
            " if (t) w(t); return parts.slice(0, 2); })()"
        )
        if not isinstance(ids, list) or len(ids) < 2:
            raise Failure("the project has fewer than two tags to multi-select")
        # Open the section that holds them, by its caret, like a person.
        # **Both levels by their carets**: the section AND the folder that holds the tags. A closed
        # folder draws no rows, so clicking `#tree-row-…` would wait for a row nobody opened.
        for label in ("Geometry", "Tags"):
            collapsed = f'button[aria-label="Collapse {label}"]'
            if app.evaluate(f"document.querySelector({collapsed!r}) !== null") is not True:
                expanded = f'button[aria-label="Expand {label}"]'
                app.click(*app.centre(expanded))
        for index, node_id in enumerate(ids):
            # **Ctrl for the SECOND and later**, a plain click for the first: that is the gesture.
            app.click_with(*_row_centre(app, str(node_id)), CTRL if index > 0 else 0)
        chosen = _chooses(app)
        if chosen < 2:
            raise Failure(f"ctrl-clicking two outline rows chose {chosen}; expected at least 2")

    run.step("two rows are multi-selected with ctrl-click", multi_selects)

    def a_long_value_is_fully_readable() -> None:
        """**A truncated row value keeps its full text**, so nothing is lost to the width.

        Codex's 035011: *"ensure full finding accessible via tooltip/accessible description"*. The
        `Tooltip` sets `aria-describedby` only while open, so `title` is what a screen reader
        gets without hovering.
        """
        truncated: Any = app.evaluate(
            "(() => { const v = Array.from(document.querySelectorAll('.tree-value'))"
            ".map((e) => ({ text: (e.textContent || '').trim(), title: e.getAttribute('title') }))"
            ".filter((x) => x.text.length > 0);"
            " return v.length ? v : null; })()"
        )
        if not isinstance(truncated, list) or len(truncated) == 0:
            raise Failure("no row values are on screen to check")
        # **Every value on screen carries its own text as `title`**, which is the row's rule.
        missing = [v for v in truncated if v.get("title") != v.get("text")]
        if missing:
            raise Failure(f"{len(missing)} row values have no full text: {missing[:2]}")

    run.step(
        "every row value carries its full text for a screen reader", a_long_value_is_fully_readable
    )

    def keyboard_reaches_the_full_text() -> None:
        """**The full value is reachable by KEYBOARD**, not only by pointing at the row.

        Codex's 042012: *"a title-attribute test establishes full text is in DOM/title; it does
        NOT establish all screen readers/keyboard users receive it ... verify keyboard focus
        semantics in the existing harness."*

        The tree is an `aria-activedescendant` list: focus stays on `[role=tree]` and the active
        row is named, so this walks with the arrow keys and reads the DESCRIPTION through that
        attribute - the same route a screen reader takes.
        """
        app.evaluate("document.querySelector('[role=tree]')?.focus()")
        found: Any = None
        for _ in range(20):
            app.press("ArrowDown", "ArrowDown", 40)
            found = app.evaluate(
                "(() => { const t = document.querySelector('[role=tree]');"
                " const id = t && t.getAttribute('aria-activedescendant');"
                " const row = id ? document.getElementById(id) : null;"
                " const d = row && row.getAttribute('aria-describedby');"
                " const v = d ? document.getElementById(d) : null;"
                " return { row: id, text: v ? v.textContent.trim() : '' }; })()"
            )
            if isinstance(found, dict) and str(found.get("text", "")) != "":
                break
        if not isinstance(found, dict) or str(found.get("text", "")) == "":
            raise Failure(f"no row reached by keyboard carries a description: {found}")

    run.step("a row reached by KEYBOARD carries its full text", keyboard_reaches_the_full_text)

    def bulk_edit_acts_on_the_selection() -> None:
        """**One bulk action, on three rows chosen in the outline**, in both directions.

        The selection was already measured; this measures that the page it opens *does something*.
        Measured before: the first attempt "passed" with no change, because the tags were **already
        hidden** - the starting state has to be set, not assumed.
        """
        # The tags folder: Geometry, then Tags, by their carets.
        for label in ("Geometry", "Tags"):
            collapsed = f'button[aria-label="Collapse {label}"]'
            if app.evaluate(f"document.querySelector({collapsed!r}) !== null") is not True:
                app.click(*app.centre(f'button[aria-label="Expand {label}"]'))
        ids: Any = app.evaluate(
            "(() => { const t = window.__stokes.getState().tree; const tags = [];"
            " const w = (n) => { if (n.type === 'tag') tags.push(n.id);"
            " for (const c of n.children || []) w(c); }; if (t) w(t); return tags.slice(0, 3); })()"
        )
        if not isinstance(ids, list) or len(ids) < 3:
            raise Failure("the project has fewer than three tags")

        def eyes() -> list[str]:
            """The eye state of the three CHOSEN rows, by their ids.

            **By id, not by name**: the boundary rows are also called `inlet`, `outlet` and
            `walls`, so a name filter picks six rows and the first attempt failed on rows the check
            did not choose.
            """
            found: Any = app.evaluate(
                "window.__eyeStates = " + repr([str(x) for x in ids]) + ";"
                "window.__eyeStates.map((id) => {"
                " const r = document.getElementById('tree-row-' + id); if (!r) return '';"
                ' const b = r.querySelector(\'button[aria-label^="Hide"],'
                ' button[aria-label^="Show"]\');'
                " return b ? b.getAttribute('aria-label') : ''; })"
            )
            return [str(x) for x in found] if isinstance(found, list) else []

        def choose() -> None:
            for index, node_id in enumerate(ids):
                app.click_with(*_row_centre(app, str(node_id)), CTRL if index > 0 else 0)

        choose()
        # **Set the starting state**: make them visible first, then hide, then show again.
        if any(e.startswith("Show ") for e in eyes()):
            spot = _bulk_action(app, "show")
            if spot is None:
                raise Failure("the bulk page offers no Show")
            app.click(*spot)
            time.sleep(2.5)
        if not all(e.startswith("Hide ") for e in eyes()):
            raise Failure(f"showing the three tags did not take: {eyes()}")
        choose()
        spot = _bulk_action(app, "hide")
        if spot is None:
            raise Failure("the bulk page offers no Hide")
        app.click(*spot)
        time.sleep(3.0)
        hidden = eyes()
        if not all(e.startswith("Show ") for e in hidden):
            raise Failure(f"the bulk Hide did not hide all three: {hidden}")
        choose()
        spot = _bulk_action(app, "show")
        if spot is None:
            raise Failure("the bulk page offers no Show")
        app.click(*spot)
        time.sleep(2.5)
        if not all(e.startswith("Hide ") for e in eyes()):
            raise Failure(f"the bulk Show did not restore all three: {eyes()}")

    run.step("a bulk action changes all three chosen rows", bulk_edit_acts_on_the_selection)

    def verbs_reach_the_model() -> None:
        """**The outline's folder verbs DISPATCH**, not merely appear (Codex's 050456).

        Each verb is driven to completion on the disposable project and the **model** is read
        afterwards - a menu that opens a form proves a form, and only the new node proves the
        command ran where the row promised. **Never deletes** anything that existed: a material and
        parameter are added.
        """
        if app.session is None:
            print("  skip the dispatch check: an attached window has no session to script")
            return

        def paths_of(kind: str) -> list[str]:
            found: Any = app.evaluate(
                "(() => { const out = []; const w = (n) => { if (n.type === "
                + repr(kind)
                + ") out.push(n.path); for (const c of n.children || []) w(c); };"
                " const t = window.__stokes.getState().tree; if (t) w(t); return out; })()"
            )
            return [str(x) for x in found] if isinstance(found, list) else []

        def type_name(label: str, value: str) -> None:
            field_box: Any = app.evaluate(
                "(() => { const i = Array.from(document.querySelectorAll('input'))"
                ".find((e) => /^New .* name$/.test(e.getAttribute('aria-label') || ''));"
                " if (!i) return null; const r = i.getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            if not isinstance(field_box, dict):
                raise Failure(f"{label}: the name field did not open")
            app.click(float(field_box["x"]), float(field_box["y"]))
            # **`Input.insertText`, the harness's own text path**: measured, per-character presses
            # left the React field EMPTY - it held `''` - so the form submitted no name and nothing
            # was created. The harness types with `insertText` elsewhere; this uses the same one.
            app.dev.call("Input.insertText", {"text": value})
            typed: Any = app.evaluate(
                "(() => { const i = Array.from(document.querySelectorAll('input'))"
                ".find((e) => /^New .* name$/.test(e.getAttribute('aria-label') || ''));"
                " return i ? i.value : '(no field)'; })()"
            )
            if typed != value:
                raise Failure(f"{label}: the name field holds {typed!r}, not {value!r}")
            add = _bulk_action(app, "add") or _button_centre(app, "Add")
            if add is None:
                raise Failure(f"{label}: no Add button")
            app.click(*add)
            time.sleep(3.0)

        # **A material**, through Materials' own verb.
        # **The tree is scrolled to the top first**: an earlier step's bulk page scrolled it, and a
        # caret outside the scroller is not laid out - `_caret_if_present` answered None for every
        # caret, which reads as "no section at all".
        app.evaluate(
            "(() => { const t = document.querySelector('[role=tree]');"
            " if (t) t.scrollTop = 0; const s = document.querySelector('.tree-scroll');"
            " if (s) s.scrollTop = 0; })()"
        )
        # **Everything folded first**, so every section row is on screen: an earlier step opens
        # Geometry and Tags, and their descendants push Physics out of the scrolled area - measured,
        # `Physics` was absent from the rows, which reads as "no section" rather than "not seen".
        for name in (
            "Geometry",
            "Mesh",
            "Physics",
            "Solve",
            "Results",
            "Explore",
            "Tags",
            "Regions",
        ):
            closing: Any = app.evaluate(
                "(() => { const b = document.querySelector("
                + repr(f'button[aria-label="Collapse {name}"]')
                + "); if (!b) return null; const r = b.getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            if isinstance(closing, dict):
                app.click(float(closing["x"]), float(closing["y"]))
        time.sleep(0.6)
        # **Physics must be OPEN**: if it is already open there is no `Expand …` caret and no
        # click is needed - but a closed section draws no child rows, so their buttons do not exist.
        if _caret_if_present(app, "Physics", open_it=False) is not None:
            # It reads `Collapse`, so it is open already. Nothing to do.
            pass
        else:
            spot = _caret_if_present(app, "Physics", open_it=True)
            if spot is None:
                state: Any = app.evaluate(
                    "Array.from(document.querySelectorAll('.tree-row'))"
                    ".map((r) => (r.textContent || '').trim().slice(0, 12) + ' | ' +"
                    " Array.from(r.querySelectorAll('button'))"
                    ".map((b) => b.getAttribute('aria-label')).join(','))"
                )
                raise Failure(f"the Physics section has no caret at all; rows: {state}")
            app.click(*spot)
            time.sleep(1.2)
        materials_menu = _row_action_button(app, "Materials")
        if materials_menu is None:
            raise Failure("the Materials row has no actions button")
        app.click(*materials_menu)
        time.sleep(0.8)
        item = _menu_item(app, "Add from library")
        if item is None:
            raise Failure("the Materials menu has no 'Add from library…' entry")
        app.click(*item)
        type_name("Add material", "outlinecheck")
        if not any(p.endswith("/outlinecheck") for p in paths_of("material")):
            raise Failure(f"Add material did not create the node: {paths_of('material')}")

        # **A parameter is NOT measured here, and the report says so.** Driving `Add parameter`
        # through the harness did not create the node in three attempts; the same path DOES work
        # in a scripted browser run (measured: `/physics/parameters/bulkcheck`), so the difference
        # is in this harness's menu handling, not the product. **Recorded, not papered over** - a
        # step that passes without proving the command ran is the defect this package keeps
        # finding.

    run.step("the outline's folder verbs create their nodes", verbs_reach_the_model)

    def keyboard_actions_act() -> None:
        """**F2 renames the FOCUSED row** - a keyboard ACTION, not just keyboard navigation.

        Codex's 050456 lists *"keyboard navigation/actions"* as separate rows, and navigation alone
        does not
        show that the keymap reaches an outline row. This selects a real node, presses **F2** (the
        `tree.rename` binding), types a new name and reads the model back.
        """
        if app.session is None:
            print("  skip the keyboard-action check: an attached window has no session to script")
            return

        def region_paths() -> list[str]:
            found: Any = app.evaluate(
                "(() => { const o = []; const w = (n) => { if (n.type === 'region') o.push(n.path);"
                " for (const c of n.children || []) w(c); };"
                " const t = window.__stokes.getState().tree; if (t) w(t); return o; })()"
            )
            return [str(x) for x in found] if isinstance(found, list) else []

        before = region_paths()
        if not before:
            raise Failure("the project has no region to rename by keyboard")
        target = before[-1]
        row_id: Any = app.evaluate(
            "(() => { let id = null; const w = (n) => { if (n.path === "
            + repr(target)
            + ") id = n.id; for (const c of n.children || []) w(c); };"
            " const t = window.__stokes.getState().tree; if (t) w(t); return id; })()"
        )
        if not isinstance(row_id, str):
            raise Failure(f"the region {target} has no id")
        # **Reveal it first**: earlier steps fold the sections, and a closed folder draws no rows -
        # so the row's element does not exist and `#tree-row-…` waits for it. The carets open the
        # way to the row, which is what a user's selection does.
        for label in ("Physics", "Regions"):
            opening = _caret_if_present(app, label, open_it=True)
            if opening is not None:
                app.click(*opening)
                time.sleep(1.0)
        # A region may be a nested row: expand whatever sits above it when its id is not drawn yet.
        shown: Any = app.evaluate(
            "document.getElementById(" + repr(f"tree-row-{row_id}") + ") !== null"
        )
        if shown is not True:
            raise Failure(f"the region row {target} is not drawn after opening Physics ▸ Regions")
        app.click(*_row_centre(app, row_id))
        time.sleep(0.8)
        # **F2 is the `tree.rename` binding**, and it acts on the FOCUSED row.
        app.press("F2", "F2", 0)
        time.sleep(1.0)
        field_box: Any = app.evaluate(
            "(() => { const i = Array.from(document.querySelectorAll('input'))"
            ".find((e) => e.getAttribute('aria-label') === 'New name'); if (!i) return null;"
            " const r = i.getBoundingClientRect();"
            " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
        )
        if not isinstance(field_box, dict):
            raise Failure("F2 did not open the rename field on a focused outline row")
        # **Clear the field first**: the rename box opens with the CURRENT name in it, so typing
        # appends - measured, the result was `fluidbykeyboard`. Backspace clears it.
        app.click(float(field_box["x"]), float(field_box["y"]))
        # **The focus is confirmed first**: measured once, the chord's effect was checked without
        # knowing where it landed.
        focus: Any = app.evaluate(
            "document.activeElement ? document.activeElement.getAttribute('aria-label') : null"
        )
        if focus != "New name":
            raise Failure(f"the rename field is not focused; the active element is {focus!r}")
        # **Cleared with Backspace, not select-all**: Ctrl+A did not apply in this harness (the
        # selection stayed `5:5` after the keyDown/keyUp pair), and the field is short.
        for _ in range(len(str(held_value(app))) + 2):
            app.press("Backspace", "Backspace", 8)
        # **The field must be EMPTY before typing**, or the name is appended to the old one.
        if held_value(app) != "":
            raise Failure(f"the rename field still holds {held_value(app)!r} after clearing it")
        app.dev.call("Input.insertText", {"text": "bykeyboard"})
        app.press("Enter", "Enter", 13)
        time.sleep(3.0)
        after = region_paths()
        if not any(p.endswith("/bykeyboard") for p in after):
            raise Failure(f"F2 did not rename the row: {after}")

    run.step("F2 renames the focused outline row", keyboard_actions_act)

    def the_stage_follows_a_click() -> None:
        """**Clicking a Physics row moves the stage chip to Physics.**

        Measured before the fix: a click set `selectedId` and left the chip on Geometry, while
        `select(id)` - the store's other entry point - moved it. **Every row click goes through
        `selectRow`, which never computed the stage**, so `stageForPath`'s rule had never run for a
        click in either tree. The harness clicks the row; the store is only read to report.
        """
        for label in ("Physics", "Regions"):
            opening = _caret_if_present(app, label, open_it=True)
            if opening is not None:
                app.click(*opening)
                time.sleep(1.0)
        # A Physics node, by its row id: this cannot be satisfied by having clicked something else.
        region_id: Any = app.evaluate(
            "(() => { let id = null; const w = (n) => { if (n.type === 'region' && !id) id = n.id;"
            " for (const c of n.children || []) w(c); };"
            " const t = window.__stokes.getState().tree; if (t) w(t); return id; })()"
        )
        if not isinstance(region_id, str):
            raise Failure("the project has no region")
        # **Start from another stage**, or "it moved" cannot be told from "it was already there".
        app.evaluate("window.__stokes.getState().setStage('Geometry')")
        time.sleep(0.6)
        app.click(*_row_centre(app, region_id))
        time.sleep(1.2)
        stage: Any = app.evaluate("window.__stokes.getState().stage")
        if stage != "Physics":
            raise Failure(f"clicking a region left the stage on {stage!r}, not Physics")

    run.step("the stage follows an outline row click", the_stage_follows_a_click)

    # **The Display ▸ Tree layout CONTROL is measured in a scripted browser, not here** (Codex's
    # 063500 asks for control -> rendered mode -> saved value -> reload -> same mode, which that run
    # does in full, both directions, with `workspace.json` read afterwards). Driving it through this
    # harness failed on the native `<select>` three ways - click-open plus ArrowDown, ArrowDown to
    # the option's index, and a first-letter jump - each leaving the document on the previous value
    # while the control looked changed. **Substituting a store write would prove the store, not the
    # control**, so no step is written.

    def a_page_row_opens_its_panel() -> None:
        """**A PAGE row opens its panel** (Codex's matrix, row 21).

        **A correction, recorded**: I reported that five page rows "opened
        nothing", wrote a fallback for the Details pane, then found **the step
        passes with the fallback removed** - the pane resolves a page row by its
        TYPE through `detailsPage`, so the tree lookup I added was never needed.
        My first probe read `[data-ws="details-body"]`, which this pane does not
        use, and saw the next-step card of an unrelated node. **The row was always
        fine; my measurement was not.** The check stays; the code was reverted.
        """
        opening = _caret_if_present(app, "Solve", open_it=True)
        if opening is not None:
            app.click(*opening)
            time.sleep(1.1)
        row_id = "virtual:outline:Solve:run"
        if app.evaluate("document.getElementById('tree-row-" + row_id + "') !== null") is not True:
            raise Failure("the Run page row is not drawn")
        # **By the element's own rect, not `#id`**: the id contains `:`, which a CSS selector cannot
        # hold - measured, `querySelector('#tree-row-virtual:outline:…')` threw.
        where: Any = app.evaluate(
            "(() => { const r = document.getElementById('tree-row-" + row_id + "');"
            " if (!r) return null; r.scrollIntoView({ block: 'center' });"
            " const b = r.getBoundingClientRect();"
            " return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; })()"
        )
        if not isinstance(where, dict):
            raise Failure("the Run page row has no rectangle")
        app.click(float(where["x"]), float(where["y"]))
        time.sleep(1.6)
        opened: Any = app.evaluate(
            "(() => { const t = document.querySelector('.properties, .details-body');"
            " return t ? (t.textContent || '').trim().slice(0, 40) : null; })()"
        )
        if not isinstance(opened, str):
            raise Failure("the pane did not render after choosing the Run page row")
        # **Not the next-step card**: that is what the pane showed when the row resolved to nothing.
        if "Import your geometry" in opened:
            raise Failure(f"the Run page row opened the next-step card, not its panel: {opened!r}")

    run.step("a PAGE row opens its panel", a_page_row_opens_its_panel)

    # **The Display ▸ Tree layout CONTROL is measured in a scripted browser, not here** (Codex's
    # 063500 asks for control -> rendered mode -> saved value -> reload -> same mode, which that run
    # does in full, both directions, with `workspace.json` read afterwards). Driving it through this
    # harness failed on the native `<select>` three ways - click-open plus ArrowDown, ArrowDown to
    # the option's index, and a first-letter jump - each leaving the document on the previous value
    # while the control looked changed. **Substituting a store write would prove the store, not the
    # control**, so no step is written.


__all__ = ["check_outline"]
