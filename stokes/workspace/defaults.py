"""The built-in panels, islands, top-bar items and actions, and the default workspace.

The default is the Stokes arrangement that ``web/DESIGN.md`` governs: tree left,
properties right, the output drawer below and collapsed, stage chips in the top
bar, modes island bottom-left, view island and view cube bottom-right, the
keyboard of DESIGN.md §3. ``presets/stokes.json`` carries its four arrangement
sections and a test keeps the two identical.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from stokes.workspace.paths import JSON
from stokes.workspace.schema import SCHEMA_VERSION, STAGES

PANELS: dict[str, str] = {
    "tree": "left",
    # **The Details pane** (the layout decision 2026-10-18): the full page for whatever the tree
    # selects, beside the tree in the left dock. It is a panel like the others, so a user can move
    # it - the decision's placement is the DEFAULT, not a rule the layout enforces.
    "details": "left",
    "properties": "right",
    "mesh": "right",
    "conditions": "right",
    "solve": "right",
    "results": "right",
    "plots": "right",
    "output": "bottom",
    "design-table": "bottom",
}
"""Built-in panel ids and the slot each returns to when it is neither placed nor hidden.

**`mesh`, `solve` and `results` joined the right slot on 2026-10-15.** The client had registered the
three panels since T-211/T-217/T-218, but **this list is what the layout is built from** — so the
Mesh settings, the Solve panel and the field viewer were unreachable in the browser even though they
existed. Found by reading a screenshot's right-slot tabs, which showed only
`Properties | Conditions | Plots`.
"""
"""Built-in panel ids and the slot each returns to when it is neither placed nor hidden."""
OUTPUT_TABS = ("console", "log", "residuals", "reports")
ISLANDS: dict[str, str] = {"modes": "bl", "view": "br", "gizmo": "br"}
"""Built-in island groups and their default corners (the gizmo is the view cube)."""
TOPBAR_ITEMS = (
    "logo",
    "project",
    "status.connection",
    "status.units",
    "status.engine",
    "status.licence",
    "stages",
    "commands",
    "appearance",
    "run",
    "window",
)
"""Top-bar items in their default order. Items before ``stages`` sit left, items after it right."""
TREE_GROUPS = (
    "geometry",
    "mesh",
    "physics",
    "regions",
    "solver",
    # `monitors` is gone (claude-win2's 0530 item 1): nothing created or read a node in it.
    "scenes",
    "runs",
    "studies",
)
STAGE_GROUPS: dict[str, tuple[str, ...]] = {
    "Geometry": ("/geometry",),
    "Mesh": ("/mesh",),
    "Physics": ("/physics", "/regions"),
    "Solve": ("/solver", "/runs"),
    "Results": ("/scenes", "/runs"),
    "Explore": ("/studies",),
}
SLOT_SIZES: dict[str, int] = {"left": 300, "right": 320, "bottom": 240, "float": 320}
DEFAULT_EXPANDED = 2
"""Tree levels open on load: the top-level groups and the groups directly inside them."""

Context = Literal["global", "tree"]


@dataclass(frozen=True)
class Action:
    """A user action with an id the shortcuts section binds; ``keys[0]`` is shown in tooltips."""

    id: str
    keys: tuple[str, ...]
    context: Context
    label: str


ACTIONS: tuple[Action, ...] = (
    *(
        Action(f"stage.{n}", (str(n),), "global", f"Go to the {s} stage")
        for n, s in enumerate(STAGES, 1)
    ),
    Action("view.fit", ("F",), "global", "Fit the model to the view"),
    # **Fit everything, the fluid included** (Claude's fit decision (a), 2026-10-19): `F` frames
    # the shapes a user recognises, and the fluid is the CFD domain, so a user who wants to see it
    # frames it deliberately - which is why it has its own key rather than a hidden rule.
    #
    # **`Alt+F`, not the `Shift+F` the decision asked for**: `Shift+F` is already `view.showAll`,
    # and the schema refuses a chord two actions share - the rule that moved `view.fit` off `G`.
    # The palette entry carries the words, so a user who looks for "Fit all bodies" finds it
    # whatever the key is.
    Action("view.fitAll", ("Alt+F",), "global", "Fit every body, the fluid included"),
    Action("view.projection", ("O",), "global", "Switch between perspective and orthographic"),
    Action("view.mode.cycle", ("V",), "global", "Cycle shaded, shaded with edges, wireframe"),
    Action(
        "view.nav.cycle",
        ("Shift+N",),
        "global",
        "Cycle the mouse scheme: CAD, Orbit, Blender, Select/Orbit",
    ),
    Action("view.section", ("S",), "global", "Section view (not built yet)"),
    Action("view.measure", ("M",), "global", "Measure (not built yet)"),
    # **The package-two chords** (claude-win2 approved 2026-10-17, owner's priority list). Every one
    # is a letter or a Ctrl chord for an action that already exists; the keymap refuses a plain key
    # while a text field has the focus, so none of these can steal a keystroke from a name being
    # typed - the rule Claude set out and the keymap already implements.
    Action("selection.name", ("Ctrl+Shift+T",), "global", "Tag the picked faces or bodies"),
    Action("mesh.generate", ("Ctrl+G",), "global", "Generate the mesh"),
    # **The guided-meshing stepper's chords** (brief M-502): between the nine tasks in the Mesh
    # stage. `Alt+ArrowUp/Down` is free - no default action claims it - and an arrow chord cannot
    # type into a text field, so it cannot steal a keystroke from a name being typed.
    Action("mesh.task.next", ("Alt+ArrowDown",), "global", "Move to the next meshing task"),
    Action("mesh.task.previous", ("Alt+ArrowUp",), "global", "Move to the previous meshing task"),
    Action("pick.mode.cycle", ("B",), "global", "Switch between picking bodies and faces"),
    Action("selection.grow", ("G",), "global", "Grow the selection along smooth surfaces"),
    Action("selection.lasso", ("L",), "global", "Lasso: draw a box around what to pick"),
    Action("selection.isolate", ("I",), "global", "Isolate the selected patch, or show all again"),
    Action("selection.hide", ("H",), "global", "Hide or show the selected patch"),
    Action("selection.clear", ("Escape",), "global", "Clear the selection and close the palette"),
    Action("selection.mode", (), "global", "Toggle body and face picking"),
    Action("selection.previous", (), "global", "Previous selection"),
    Action(
        "selection.paint",
        ("P",),
        "global",
        "Paint select: drag over faces to add them, Ctrl to remove them",
    ),
    # The geometry operations of ADR-0021 decision 8 (T-029): the Prepare menu's entries as
    # actions, with no default chord. web/src/app/PrepareMenu.tsx installs the handlers.
    Action("prepare.enclosure", (), "global", "Enclose the picked bodies"),
    Action("prepare.extractFluid", (), "global", "Extract the fluid around a seed point"),
    Action("prepare.boolean", (), "global", "Fuse, cut or intersect the picked bodies"),
    Action("prepare.repair", (), "global", "Heal, sew or solidify the picked bodies"),
    Action("palette.open", ("Ctrl+K",), "global", "Open the command palette"),
    Action("history.undo", ("Ctrl+Z",), "global", "Undo"),
    Action("history.redo", ("Ctrl+Y", "Ctrl+Shift+Z"), "global", "Redo"),
    Action("project.save", ("Ctrl+S",), "global", "Save the project"),
    Action("project.saveAs", ("Ctrl+Shift+S",), "global", "Save the project to a new folder"),
    Action("project.open", ("Ctrl+O",), "global", "Open a project"),
    Action("project.importStep", ("Ctrl+I",), "global", "Import a geometry file into the project"),
    # **The menu spec's numbers.** `Save a copy` has NO chord: it is the same dialog as `Save as`
    # and a second chord for one dialog is a way to press the wrong one, so it is reached from the
    # menu where the two are named side by side.
    Action("project.new", ("Ctrl+N",), "global", "Create a new project"),
    Action("project.saveCopy", (), "global", "Copy the project without switching to it"),
    Action("project.close", ("Ctrl+W",), "global", "Close the project"),
    Action("project.startScreen", (), "global", "Go to the Start screen"),
    # **`Ctrl+,` stays on `display.settings`** (T-176's OWNER DECISIONS block, which overrides the
    # plan): Settings gets `Ctrl+Shift+,`, and no `display.settings` migration is emitted. The plan
    # wanted Display settings stripped of its chord; the owner kept it, so both bindings exist and
    # neither is a conflict.
    Action("display.settings", ("Ctrl+,",), "global", "Open Display settings"),
    Action("workspace.customise", ("Ctrl+Shift+C",), "global", "Enter or leave Customise mode"),
    # **The two panels that hold the work can be put away entirely** (the owner, via
    # claude-win2 1930: *"a collapse button in their header that removes the card completely...
    # and a way back that does not need a visible rail"*). One action each, because the header
    # button, the chord, the View menu entry and the palette entry are four ways to ONE command
    # - and a fifth implementation would be the one that behaves differently.
    Action("layout.toggleTree", ("Ctrl+B",), "global", "Show or hide the tree"),
    Action("layout.toggleDetails", ("Ctrl+Shift+B",), "global", "Show or hide the Details pane"),
    # **The way out when an arrangement will not behave** (claude-win2's 0900 bug report: the
    # owner can also reset it from the palette). Clears the saved ARRANGEMENT and keeps every
    # preference - a theme or a mouse scheme is not what a stuck panel is about.
    Action("layout.reset", (), "global", "Reset the panel layout"),
    # --- T-176 §2: the actions the context menu and Settings add. Their default chords come from
    # the plan; the ones with no chord are reached from a menu, which is where a chord would only
    # be memorised by the person who chose it.
    Action("app.settings", ("Ctrl+Shift+,",), "global", "Open Settings"),
    Action("selection.clearPick", (), "global", "Clear the pick"),
    Action("context.menu", ("Shift+F10", "ContextMenu"), "global", "Open the context menu"),
    Action("view.fitSelection", (), "global", "Fit the selection to the view"),
    Action("view.showAll", ("Shift+F",), "global", "Show every hidden body again"),
    Action("view.from.front", ("Alt+1",), "global", "Look from the front"),
    Action("view.from.back", ("Alt+2",), "global", "Look from the back"),
    Action("view.from.left", ("Alt+3",), "global", "Look from the left"),
    Action("view.from.right", ("Alt+4",), "global", "Look from the right"),
    Action("view.from.top", ("Alt+5",), "global", "Look from the top"),
    Action("view.from.bottom", ("Alt+6",), "global", "Look from the bottom"),
    Action("view.from.iso", ("Alt+0",), "global", "Look from an isometric corner"),
    Action("selection.siblings", (), "global", "Select the parts of the same file"),
    Action("selection.allFaces", (), "global", "Select every face of the body"),
    Action("edit.rename", (), "global", "Rename the selection"),
    Action("edit.delete", (), "global", "Delete the selection (with an Undo toast)"),
    Action("edit.copyName", (), "global", "Copy the selection's name"),
    Action("edit.copyPath", (), "global", "Copy the selection's path"),
    Action("tree.rename", ("F2",), "tree", "Rename the selected node"),
    Action("tree.delete", ("Delete",), "tree", "Delete the selected node (with an Undo toast)"),
    Action("tree.menu", ("Shift+F10", "ContextMenu"), "tree", "Open the row menu"),
)
ACTION_IDS = {action.id: action for action in ACTIONS}
REQUIRED_ACTIONS = ("palette.open",)
"""Actions that must keep a chord: the command palette can never be removed (ADR-0015 §5)."""


def binding_of(action: Action) -> JSON:
    """The default binding as the document stores it: a chord, a list when there are several, or an
    empty list when the action has no default chord."""
    if not action.keys:
        return []
    return action.keys[0] if len(action.keys) == 1 else list(action.keys)


def default_layout() -> dict[str, JSON]:
    def slot(
        name: str, panels: list[JSON], *, collapsed: bool = False, mode: str = "tabs"
    ) -> dict[str, JSON]:
        return {"mode": mode, "size": SLOT_SIZES[name], "collapsed": collapsed, "panels": panels}

    return {
        # **Split, not tabs** (the layout decision 2026-10-18): the tree and the Details pane sit
        # SIDE BY SIDE, which is what makes the selection-as-tab idea work - a tab strip would hide
        # one behind the other, and the pane would vanish whenever the tree was in front.
        "left": slot(
            "left", [{"id": "tree", "order": 0}, {"id": "details", "order": 1}], mode="split"
        ),
        # **The right slot's order is the user's path through a project**: what is
        # selected, then the stage you are working in, then its inputs, then the outcome.
        # `conditions` (ADR-0022 9.1, T-048) has been here since M0; `mesh`, `solve` and
        # `results` are T-211/T-217/T-218 and were missing from this list until
        # 2026-10-15, which is why their panels had no tab to live in.
        # **The right slot is EMPTY in the default arrangement** (the owner, via claude-win2: *"the
        # collapsed right-hand icon strip is not needed. Remove it."*).
        #
        # It held the six stage panels collapsed to a 40 px rail of icons. Three of them are pages
        # for a selected node and live in the Details pane beside the tree; the rest are reachable
        # from the Commands palette and the View menu. **Nothing placed means nothing drawn** -
        # `slotGeometry` gives an empty slot an `occupied` of 0 - so the viewport takes the full
        # width and no rail appears.
        #
        # **Nothing was deleted from the panel registry**: `classic` keeps its right column, a user
        # who moves a panel to the right gets a real dock with a close button, and a saved layout
        # that had the rail opens without it because the slot renders what it holds.
        "right": slot("right", [], collapsed=True),
        "bottom": slot(
            "bottom",
            [
                {"id": "output", "order": 0, "tabs": list(OUTPUT_TABS)},
                {"id": "design-table", "order": 1},
            ],
            collapsed=True,
        ),
        "float": slot("float", []),
        # **The stage panels are HIDDEN, not placed** (the owner's "remove the right strip").
        # `hidden`
        # is the list the registry uses for "a panel that exists and is not drawn", so every
        # built-in
        # item is still accounted for exactly once - which is what the preset test checks - while
        # nothing is drawn on the right. They are reachable from the Commands palette and the View
        # menu, which is what the owner's request 2 asks for.
        "hidden": ["properties", "mesh", "conditions", "solve", "results", "plots"],
    }


def default_islands() -> dict[str, JSON]:
    return {"bl": ["modes"], "br": ["view", "gizmo"], "tl": [], "tr": [], "hidden": []}


def default_topbar() -> dict[str, JSON]:
    return {
        "items": [{"id": item, "visible": True} for item in TOPBAR_ITEMS],
        "stages": {"style": "chips"},
    }


def default_tree() -> dict[str, JSON]:
    return {
        "groupOrder": list(TREE_GROUPS),
        "hidden": [],
        "stages": {stage: list(groups) for stage, groups in STAGE_GROUPS.items()},
        "linkStage": True,
        "defaultExpanded": DEFAULT_EXPANDED,
        # **The outline is what a user sees** (G-305); "groups" keeps today's arrangement available.
        "layout": "outline",
    }


def default_document() -> dict[str, JSON]:
    """A fresh copy of the complete default workspace."""
    return {
        "version": SCHEMA_VERSION,
        "theme": {"name": "auto"},
        "layout": default_layout(),
        "islands": default_islands(),
        "topbar": default_topbar(),
        "tree": default_tree(),
        "shortcuts": {action.id: binding_of(action) for action in ACTIONS},
        "viewport": {
            "projection": "perspective",
            "viewMode": "shaded",
            "grid": True,
            # **Today's look, as the defaults** (claude-win2 2000: *"defaults stay as today"*): the
            # axis lines, the contact shadow, the reflections, the edges, the orientation gizmo and
            # the scale bar all draw now, so every switch starts on and turning one off is a change
            # the user made.
            "axisLines": True,
            "shadow": True,
            "reflections": True,
            "edges": True,
            "lowDetail": False,
            "gizmo": True,
            "scaleBar": True,
            "background": "theme",
        },
        "engines": {"default": None, "runPresets": {}},
        # **The user's own settings** (T-176). The scheme ids match the front end's `NAV_MODES`;
        # `mm` is the import unit a CAD file is usually drawn in.
        "preferences": {
            "mouse": {"scheme": "paraview", "orbitLeft": False},
            "import": {"units": "mm"},
            # **Empty means Auto**, which is the shipped behaviour: pick the best host that answers
            # and its newest supported install. A user who never opens Compute still meshes, and the
            # section shows them what was chosen.
            "compute": {"host": "", "install": "", "auto": True},
            # **The guided-meshing stepper's visibility** (ASK Q2, brief M-502): shown by default,
            # collapsible to one line in the Details pane's fixed header. The schema
            # (`schema.py` `PREFERENCES`) validates the value; the default lives here so the shipped
            # document carries it and both sides agree on it.
            "mesh": {"stepper": "shown"},
        },
    }
