"""The workspace document, schema version 1 (ADR-0015): vocabulary, rules and sanitising.

A workspace document is a JSON object with ``version`` and the sections ``theme``,
``layout``, ``islands``, ``topbar``, ``tree``, ``shortcuts``, ``viewport`` and
``engines``. The spec tree below states what every value may be. :func:`sanitise`
cleans one layer read from a file: valid values and unknown keys are kept,
invalid values are dropped so that the layer below shows through, and every
decision is reported as a :class:`Finding`. Nothing here raises for bad data;
making API writes strict is the store's job. Rules that span sections (a panel
placed twice, conflicting chords) need the merged document and live in
:mod:`stokes.workspace.merge`.

The contract for front-end implementers is ``docs/engineering/workspace-schema.md``.
Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Hashable, Mapping
from dataclasses import replace
from typing import Final, cast

from stokes.workspace.keys import normalise_binding, normalise_chord
from stokes.workspace.paths import JSON, Loc, Segment, as_index
from stokes.workspace.rules import (
    DROP,
    UNKNOWN_KEY,
    Checker,
    Choice,
    Finding,
    Flag,
    KeyVerdict,
    ListOf,
    MapOf,
    Nullable,
    Number,
    Obj,
    Prose,
    Spec,
    Text,
    json_copy,
    show,
)
from stokes.workspace.tokens import TOKENS, check_token_value, hex6, token_problem

SCHEMA_VERSION: Final = 1
SECTIONS = (
    "theme",
    "layout",
    "islands",
    "topbar",
    "tree",
    "shortcuts",
    "viewport",
    "engines",
    "preferences",
)
ARRANGEMENT = ("layout", "islands", "topbar", "tree")
"""The sections a shipped preset carries; theme, shortcuts, viewport and engines are the user's."""
METADATA = ("version", "preset")
"""Top-level keys that describe a document rather than arrange the interface; never merged."""

THEME_NAMES = (
    "dark",
    "light",
    "cupertino",
    "cupertino-dark",
    "ubuntu",
    "ubuntu-dark",
    "auto",
    "cupertino-auto",
    "ubuntu-auto",
)
BASE_THEMES = ("dark", "light", "cupertino", "cupertino-dark", "ubuntu", "ubuntu-dark")
SLOTS = ("left", "right", "bottom", "float")
SLOT_MODES = ("tabs", "split")
CORNERS = ("bl", "br", "tl", "tr")
STAGE_STYLES = ("chips", "ribbon", "hidden")
STAGES = ("Geometry", "Mesh", "Physics", "Solve", "Results", "Explore")
PROJECTIONS = ("perspective", "orthographic")
VIEW_MODES = ("shaded", "edges", "wireframe")

ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
ACTION_RE = re.compile(r"^[a-z][a-zA-Z0-9]*(?:\.[a-zA-Z0-9][a-zA-Z0-9_-]*)+$")
GROUP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GROUP_PATH_RE = re.compile(r"^(?:/[A-Za-z_][A-Za-z0-9_]*)+$")
STAGE_RE = re.compile(r"^[A-Z][A-Za-z0-9]{0,31}$")
RUN_PRESET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

SIZE_RANGE = (24, 4096)
"""Slot and panel sizes in CSS pixels; the front end clamps further to each panel's minimum."""
POSITION_RANGE = (0, 10000)
FLOAT_OFFSET = 24
FLOAT_STEP = 32


class Chord(Spec):
    """One chord, as an element of a list of chords."""

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        try:
            chord = normalise_chord(value) if isinstance(value, str) else None
        except ValueError as exc:
            return ck.invalid(loc, str(exc))
        return chord if chord else ck.invalid(loc, "must be a chord such as Ctrl+K")


class Binding(Spec):
    """A shortcut binding: one chord, a list of chords, or "" / [] for none."""

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        try:
            return normalise_binding(value)
        except ValueError as exc:
            return ck.invalid(loc, str(exc))

    def child(self, key: Segment) -> Spec | None:
        return CHORD if as_index(key) is not None else None


class Background(Spec):
    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if value == "theme":
            return value
        if isinstance(value, str) and HEX_RE.match(value):
            return hex6(value)
        return ck.invalid(
            loc, f'must be "theme" or a hex colour such as #1a2029; got {show(value)}'
        )


class Opaque(Spec):
    """Any JSON object, kept as it is: engine run settings belong to the engine adapters."""

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if isinstance(value, dict):
            return json_copy(value)
        return ck.invalid(loc, f"must be an object; got {show(value)}")


class TokenValue(Spec):
    """A token's value; the token's kind decides what is accepted (theme-tokens.md)."""

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        name = loc[-1] if loc and isinstance(loc[-1], str) else ""
        try:
            return check_token_value(name, value)
        except ValueError as exc:
            return ck.invalid(loc, str(exc))


def _token_key(name: str) -> KeyVerdict:
    problem = token_problem(name)
    return ("invalid", problem) if problem else (None, "")


class ThemeSpec(Spec):
    """A built-in theme by name, or a custom theme: a base plus token overrides."""

    FIELDS = ("name", "base", "tokens", "title")

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if not isinstance(value, dict):
            return ck.invalid(
                loc, f'must be an object such as {{"name": "dark"}}; got {show(value)}'
            )
        extra: dict[str, JSON] = {}
        for key in value:
            if key not in self.FIELDS:
                ck.unknown((*loc, key), UNKNOWN_KEY)
                extra[key] = json_copy(value[key])
        if "base" in value:
            custom = self._custom(value, loc, ck)
            return custom if custom is DROP else {**custom, **extra}
        if "name" not in value:
            return ck.invalid(loc, 'needs "name" (a built-in theme) or "base" (a custom theme)')
        name = value["name"]
        if not (isinstance(name, str) and name in THEME_NAMES):
            return ck.invalid(
                (*loc, "name"), f"must be one of {', '.join(THEME_NAMES)}; got {show(name)}"
            )
        for key in ("tokens", "title"):
            if key in value:
                ck.invalid(
                    (*loc, key),
                    f'only a custom theme has {key}: {{"base": "dark", "tokens": {{...}}}}',
                )
        return {"name": name, **extra}

    def _custom(self, value: dict[str, JSON], loc: Loc, ck: Checker) -> JSON:
        base = value["base"]
        if not (isinstance(base, str) and base in BASE_THEMES):
            return ck.invalid(
                (*loc, "base"), f"must be one of {', '.join(BASE_THEMES)}; got {show(base)}"
            )
        if "name" in value:
            ck.invalid((*loc, "name"), "a custom theme has a base, not a name")
        theme: dict[str, JSON] = {"base": base}
        if "title" in value:
            title = TITLE.check(value["title"], (*loc, "title"), ck)
            if title is not DROP:
                theme["title"] = title
        tokens = TOKEN_MAP.check(value.get("tokens", {}), (*loc, "tokens"), ck)
        theme["tokens"] = tokens if isinstance(tokens, dict) else {}
        if not base.startswith("cupertino"):
            for name in theme["tokens"]:
                if TOKENS[name].group == "cupertino":
                    ck.notice(
                        "workspace.no_effect",
                        (*loc, "tokens", name),
                        f"{name} styles only the Cupertino family; it has no effect on base {base}",
                    )
        return theme

    def child(self, key: Segment) -> Spec | None:
        return {"name": THEME_NAME, "base": BASE_NAME, "title": TITLE, "tokens": TOKEN_MAP}.get(
            key if isinstance(key, str) else ""
        )


# ------------------------------------------------------------------------------ the document
CHORD = Chord()
TITLE = Prose(64)
DESCRIPTION = Prose(240)
THEME_NAME = Choice(THEME_NAMES)
BASE_NAME = Choice(BASE_THEMES)
TOKEN_MAP = MapOf(_token_key, TokenValue(), 256)
_SIZE = Number(*SIZE_RANGE)
_POSITION = Number(*POSITION_RANGE)


def _same(value: JSON) -> Hashable:
    return cast(Hashable, value)  # entries are strings once checked


def _by_id(value: JSON) -> Hashable:
    return cast(Hashable, value["id"])  # entries are checked objects with an id


def _fill_panel(index: int, entry: JSON) -> None:
    entry.setdefault("order", index)


def _fill_float(index: int, entry: JSON) -> None:
    entry.setdefault("order", index)
    entry.setdefault("anchor", "tl")
    entry.setdefault("x", FLOAT_OFFSET + FLOAT_STEP * index)
    entry.setdefault("y", FLOAT_OFFSET + FLOAT_STEP * index)


def _fill_item(index: int, entry: JSON) -> None:
    entry.setdefault("visible", True)


def _section(fields: Mapping[str, Spec]) -> Obj:
    """A section object: every key is required in a complete document, none in a partial one."""
    return Obj(fields, required=tuple(fields))


def _section_optional(fields: Mapping[str, Spec], optional: frozenset[str]) -> Obj:
    """A section where **named keys are optional and the rest are required**.

    **For a key added after documents were already saved**: it must not turn an old document
    invalid, while every key that was already required stays required - loosening the whole section
    would stop catching a document that has genuinely lost its `groupOrder`.
    """
    return Obj(fields, required=tuple(k for k in fields if k not in optional))


def _ids(what: str, max_items: int) -> ListOf:
    return ListOf(Text(ID_RE, what), max_items, key=_same)


def _stage_key(key: str) -> KeyVerdict:
    if key in STAGES:
        return None, ""
    if STAGE_RE.match(key):
        return "unknown", f"{key!r} is not a stage of this version ({', '.join(STAGES)})"
    return "invalid", f"{key!r} is not a stage name ({', '.join(STAGES)})"


def _action_key(key: str) -> KeyVerdict:
    if len(key) <= 64 and ACTION_RE.match(key):
        return None, ""
    return "invalid", f"{key!r} is not an action id (area.verb, for example view.fit)"


def _run_preset_key(key: str) -> KeyVerdict:
    if RUN_PRESET_RE.match(key):
        return None, ""
    return "invalid", f"{key!r} is not a run preset name (a letter, then letters, digits, _ or -)"


_PANEL_FIELDS: dict[str, Spec] = {
    "id": Text(ID_RE, "a panel id such as tree"),
    "order": Number(0, 1000, integer=True),
    "size": _SIZE,
    "collapsed": Flag(),
    "tabs": ListOf(Text(ID_RE, "a tab id such as console"), 16, key=_same, min_items=1),
}
DOCKED_PANEL = Obj(_PANEL_FIELDS, required=("id",), complete=True)
FLOAT_PANEL = Obj(
    {**_PANEL_FIELDS, "anchor": Choice(CORNERS), "x": _POSITION, "y": _POSITION},
    required=("id",),
    complete=True,
)


def _slot(panel: Obj, fill: Callable[[int, JSON], None]) -> Obj:
    return _section(
        {
            "mode": Choice(SLOT_MODES),
            "size": _SIZE,
            "collapsed": Flag(),
            "panels": ListOf(panel, 32, key=_by_id, fill=fill),
        }
    )


LAYOUT = _section(
    {
        "left": _slot(DOCKED_PANEL, _fill_panel),
        "right": _slot(DOCKED_PANEL, _fill_panel),
        "bottom": _slot(DOCKED_PANEL, _fill_panel),
        "float": _slot(FLOAT_PANEL, _fill_float),
        "hidden": _ids("a panel id such as tree", 64),
    }
)
ISLANDS = _section(
    {
        **{corner: _ids("an island id such as view", 16) for corner in CORNERS},
        "hidden": _ids("an island id such as view", 16),
    }
)
TOPBAR_ITEM = Obj(
    {"id": Text(ID_RE, "a top-bar item id such as run"), "visible": Flag()},
    required=("id",),
    complete=True,
)
TOPBAR = _section(
    {
        "items": ListOf(TOPBAR_ITEM, 64, key=_by_id, fill=_fill_item),
        "stages": _section({"style": Choice(STAGE_STYLES)}),
    }
)
_GROUP = Text(GROUP_RE, "a top-level group name such as physics")
#: The two ways the outline can be arranged. **"outline" is the target** and the default an old
#: document reads as; "groups" is exactly today's tree, kept so a user who has arranged their own is
#: not forced onto a new one by an upgrade.
TREE_LAYOUTS = ("outline", "groups")
TREE = _section_optional(
    optional=frozenset({"layout"}),
    fields={
        "groupOrder": ListOf(_GROUP, 64, key=_same),
        "hidden": ListOf(_GROUP, 64, key=_same),
        "stages": MapOf(
            _stage_key,
            ListOf(Text(GROUP_PATH_RE, "a group path such as /physics", 256), 32, key=_same),
            16,
        ),
        "linkStage": Flag(),
        "defaultExpanded": Number(0, 8, integer=True),
        # **The outline or the groups** (G-305), and **OPTIONAL where the other keys here are
        # required**: it is new, so a document saved before it existed - and every shipped preset -
        # has none, and must read as "outline" rather than be invalid. Measured: required, it failed
        # both preset validation and the default's own check.
        "layout": Choice(TREE_LAYOUTS),
    },
)
SHORTCUTS = MapOf(_action_key, Binding(), 512, dotted=True)
VIEWPORT = _section_optional(
    {
        "projection": Choice(PROJECTIONS),
        "viewMode": Choice(VIEW_MODES),
        "grid": Flag(),
        # **What the viewer draws besides the model** (the owner, via claude-win2 2000: *"display
        # settings must let me switch off the axis planes, shadows and reflections"*).
        #
        # **Every key here corresponds to something `SceneManager` actually constructs**, which is
        # what the owner asked for rather than a list of what a viewer might have: the ground grid
        # (`GroundGrid`) and the red/green/blue axis lines it draws, the contact shadow
        # (`ContactShadow`), the environment map used for reflections (`scene.environment` with
        # `environmentIntensity`), and the part edge lines (`edgeMaterial`).
        #
        # **The orientation gizmo and the scale bar are here too**, though they are DOM rather than
        # three.js: `gizmoPlacement()` and `<ScaleBar>` decide whether they show, and both are
        # decoration a user may want gone.
        "axisLines": Flag(),
        "shadow": Flag(),
        "reflections": Flag(),
        "edges": Flag(),
        # **An explicit display quality profile**: the frontend keeps full-resolution geometry and
        # suppresses expensive edge, shadow and reflection rendering when this is enabled.
        "lowDetail": Flag(),
        "gizmo": Flag(),
        "scaleBar": Flag(),
        "background": Background(),
    },
    frozenset({"lowDetail"}),
)
#: The mouse schemes and import units the Settings dialog offers. **The scheme ids must match the
#: front end's `NAV_MODES`** (`web/src/app/navModes.ts`), which is what actually applies them -
#: measured there as `["cad", "paraview", "blender", "toggle"]`. A name this file invents would
#: validate on write and do nothing on read.
MOUSE_SCHEMES = ("cad", "paraview", "blender", "toggle")
IMPORT_UNITS = ("mm", "cm", "m", "in", "ft")

#: How the guided-meshing stepper shows (ASK Q2): the full block, or collapsed to its one line.
STEPPER_PREFS = ("shown", "hidden")

PREFERENCES = _section(
    {
        "mouse": Obj(
            {
                "scheme": Choice(MOUSE_SCHEMES),
                #: Left-drag orbits instead of picking, without changing the scheme.
                "orbitLeft": Flag(),
            }
        ),
        "import": Obj({"units": Choice(IMPORT_UNITS)}),
        # **Where OpenFOAM runs and which install of it** (the Settings spec §2; claude-win2's 1330
        # answer item 3: "install location as `preferences.compute` referencing a trusted host:
        # approved").
        #
        # `host` names a machine the user has ALREADY trusted (`hosts.json` / `host.trust`), and
        # `install` names one environment script on it. The two are kept apart on purpose: "I trust
        # this machine" and "use this OpenFOAM" are independent decisions, and folding the install
        # into the trust record would make untrusting a host silently discard a configuration the
        # user chose.
        #
        # **A path, not an id.** The id the probe reports is a hash of the path (`engine_id`), so
        # storing the path is what survives a rescan; the id is derived from it wherever one is
        # needed. A path that no longer exists is a finding the Compute section reports rather than
        # a value that quietly stops working.
        "compute": Obj(
            {
                "host": Prose(120),
                "install": Prose(1000),
                # **"Auto" is the default and the reason a user needs no opinion here.** The spec
                # wants the best available chosen for them; an explicit empty host means "pick for
                # me", which is different from "no host is configured".
                "auto": Flag(),
            }
        ),
        # **The guided-meshing stepper's visibility** (ASK Q2, brief M-502): shown by default,
        # collapsible to one line in the Details pane's fixed header. **In this schema because
        # preferences are validated here** — a web-only key would be refused on write, so the key
        # has to exist server-side for the front end to store it at all.
        "mesh": Obj({"stepper": Choice(STEPPER_PREFS)}),
    }
)
ENGINES = _section(
    {
        "default": Nullable(Text(ID_RE, "an engine id such as openfoam")),
        "runPresets": MapOf(_run_preset_key, Opaque(), 128),
    }
)
DOCUMENT = _section(
    {
        "theme": ThemeSpec(),
        "layout": LAYOUT,
        "islands": ISLANDS,
        "topbar": TOPBAR,
        "tree": TREE,
        "shortcuts": SHORTCUTS,
        "viewport": VIEWPORT,
        "engines": ENGINES,
        "preferences": PREFERENCES,
    }
)
PRESET_META = Obj({"title": TITLE, "description": DESCRIPTION})
"""The ``preset`` object of a preset file: a title and a one-line description."""

#: Sections that are the USER's and never the project's (T-176 correction 1). A project file must
#: not force a mouse scheme on whoever opens it, so `workspace.set` with `scope=project` refuses a
#: path under one of these, `sanitise(..., "project")` drops it with a finding, and presets never
#: carry it.
USER_ONLY: tuple[str, ...] = ("preferences",)

ATOMIC: frozenset[Loc] = frozenset({("theme",)})
"""Objects that replace as a whole when layers merge (lists always do)."""


# ---------------------------------------------------------------------------------------- cleaning
Migration = Callable[[dict[str, JSON]], dict[str, JSON]]
MIGRATIONS: dict[int, Migration] = {}
"""Maps a schema version to the function that upgrades a document to the next version."""


def sanitise(raw: JSON, scope: str) -> tuple[dict[str, JSON], list[Finding]]:
    """Clean one layer document: keep valid values and unknown keys, drop invalid values.

    The result has no ``version`` or ``preset`` key; findings carry ``scope``.
    """
    ck = Checker(partial=True)
    if not isinstance(raw, dict):
        ck.invalid((), f"a workspace document must be a JSON object; got {show(raw)}")
        return {}, _scoped(ck, scope)
    document = _migrate(raw, ck)
    body = {key: value for key, value in document.items() if key not in METADATA}
    # **A project may not carry the user's own settings** (T-176 correction 1). A project file is
    # shared, so a `preferences` section in one would force a mouse scheme on whoever opens it -
    # decide it for them, silently, from a file they were sent. Dropped with a finding rather than
    # refused, because the rest of the document is still good and the reader deserves to know what
    # was ignored.
    if scope == "project":
        body = _drop_user_only(body, ck)
    clean = DOCUMENT.check(body, (), ck)
    return (clean if isinstance(clean, dict) else {}), _scoped(ck, scope)


def _drop_user_only(body: dict[str, JSON], ck: Checker) -> dict[str, JSON]:
    """Remove every `USER_ONLY` section, reporting each.

    **The finding is reported at the VALUE, not at the section.** `_check` keeps a finding only
    when it lies at or below the path being written (`is_within(finding.path, root)`), and a write
    goes to `preferences.mouse.scheme` while the section is `preferences` - an ANCESTOR, which
    that test rejects. Reported at the ancestor alone the finding was filtered out, `_check` saw
    nothing bad, and **the project-scope write was accepted**: measured,
    `preferences.mouse.scheme = "paraview"` at project scope wrote without complaint. So the
    finding names the section but sits at every leaf path below it, where a refusal is looked
    for.
    """
    for name in USER_ONLY:
        value = body.get(name)
        if name not in body:
            continue
        body = {key: item for key, item in body.items() if key != name}
        for leaf in _leaf_paths((name,), value):
            ck.invalid(
                leaf,
                f"{name} are the user's, not the project's",
                code="workspace.user_only",
            )
    return body


def _leaf_paths(loc: Loc, value: JSON) -> list[Loc]:
    """Every path a value's leaves sit at, so a refusal is found wherever the write aims.

    A scalar is its own leaf; a container's are its children's. An empty container is still a leaf -
    a write to it must be refused too.
    """
    if isinstance(value, dict) and value:
        return [leaf for key, item in value.items() for leaf in _leaf_paths((*loc, key), item)]
    if isinstance(value, list) and value:
        return [
            leaf for index, item in enumerate(value) for leaf in _leaf_paths((*loc, index), item)
        ]
    return [loc]


def validate_complete(document: JSON, scope: str = "effective") -> list[Finding]:
    """Findings for a document that must be complete and valid: defaults, an effective workspace."""
    ck = Checker(partial=False)
    body = document
    if isinstance(document, dict):
        body = {key: value for key, value in document.items() if key not in METADATA}
    DOCUMENT.check(body, (), ck)
    return _scoped(ck, scope)


def _scoped(ck: Checker, scope: str) -> list[Finding]:
    return [replace(finding, scope=scope) for finding in ck.findings]


def _migrate(raw: dict[str, JSON], ck: Checker) -> dict[str, JSON]:
    if "version" not in raw:
        ck.notice(
            "workspace.version", ("version",), f"no version; read as version {SCHEMA_VERSION}"
        )
        return raw
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        ck.invalid(
            ("version",),
            f"must be a positive integer; read as version {SCHEMA_VERSION}",
            code="workspace.version",
        )
        return raw
    if version > SCHEMA_VERSION:
        ck.notice(
            "workspace.version",
            ("version",),
            f"written by a newer Stokes (version {version}); read as version {SCHEMA_VERSION}, "
            "keys this version does not know are kept",
        )
        return raw
    document = raw
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            ck.invalid(
                ("version",), f"no migration from version {version}", code="workspace.version"
            )
            return document
        document = step(document)
        version += 1
    return document
