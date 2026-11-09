"""From layers to the effective workspace (ADR-0015 §1).

Layers, lowest first: the defaults, the user's document and the project's
overrides. The user's document is ``~/.stokes/workspace.json``, or
``$STOKES_HOME/workspace.json`` when that variable is set (tests set it so they
never touch the real home); the project's is ``<name>.stokes/workspace.json``,
beside ``project.json`` in the project folder.

Each layer is sanitised before it is merged, so an invalid value falls back to
the layer below. Objects merge key by key with the higher layer winning; lists
and the theme replace as a whole; unknown keys travel along. The rules that need
the whole picture run last (:func:`finalise`): a panel or island placed twice
keeps the place its highest layer gave it, a built-in one that is nowhere returns
to its default place, a missing top-bar item or tree group is put back, chords
bound twice are reported, and the palette keeps a chord.

Standard library only.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from stokes import workspace_root
from stokes.workspace.defaults import (
    ACTION_IDS,
    ISLANDS,
    PANELS,
    REQUIRED_ACTIONS,
    TOPBAR_ITEMS,
    TREE_GROUPS,
    binding_of,
    default_document,
)
from stokes.workspace.keys import chords_of
from stokes.workspace.paths import JSON, Loc, format_path, is_within
from stokes.workspace.rules import Checker, Finding, json_copy
from stokes.workspace.schema import ATOMIC, CORNERS, METADATA, SCHEMA_VERSION, SLOTS

HOME_ENV = "STOKES_HOME"
WORKSPACE_FILE = "workspace.json"
PRESETS_DIR = "presets"
RANK = {"default": 0, "user": 1, "project": 2}


def stokes_home() -> Path:
    """The user's Stokes folder: ``$STOKES_HOME`` when set, otherwise ``~/.stokes``."""
    override = os.environ.get(HOME_ENV, "").strip()
    return Path(override).expanduser() if override else workspace_root.home()


def user_file(home: Path) -> Path:
    return home / WORKSPACE_FILE


def project_file(project_dir: Path) -> Path:
    """``<name>.stokes/workspace.json``: the project's overrides, beside ``project.json``."""
    return project_dir / WORKSPACE_FILE


@dataclass(frozen=True)
class Effective:
    """The merged workspace, where each leaf came from, and everything noticed on the way."""

    document: dict[str, JSON]
    sources: dict[str, str]
    findings: tuple[Finding, ...]


def effective(
    layers: Sequence[tuple[str, Mapping[str, JSON]]], findings: Sequence[Finding] = ()
) -> Effective:
    """Merge sanitised layers (``("user", doc)``, ``("project", doc)``) over the defaults."""
    base = {key: value for key, value in default_document().items() if key not in METADATA}
    merged = merge(base, *(document for _, document in layers))
    origins = sources(merged, layers)
    late = finalise(merged, origins)
    return Effective({"version": SCHEMA_VERSION, **merged}, origins, (*findings, *late))


def merge(*layers: Mapping[str, JSON]) -> dict[str, JSON]:
    """Deep-merge documents, lowest first. The result shares nothing with the inputs."""
    result: dict[str, JSON] = {}
    for layer in layers:
        _merge_into(result, layer, ())
    return result


def _merge_into(target: dict[str, JSON], layer: Mapping[str, JSON], loc: Loc) -> None:
    for key, value in layer.items():
        here = (*loc, key)
        current = target.get(key)
        if isinstance(value, dict) and isinstance(current, dict) and here not in ATOMIC:
            _merge_into(current, value, here)
        else:
            target[key] = json_copy(value)


def sources(
    document: Mapping[str, JSON], layers: Sequence[tuple[str, Mapping[str, JSON]]]
) -> dict[str, str]:
    """For every leaf of a merged document (a value, a list, the theme), the layer it came from."""
    out: dict[str, str] = {}

    def walk(value: JSON, loc: Loc) -> None:
        if isinstance(value, dict) and value and loc not in ATOMIC:
            for key, item in value.items():
                walk(item, (*loc, key))
        else:
            out[format_path(loc)] = origin(loc, layers)

    for key, value in document.items():
        if key not in METADATA:
            walk(value, (key,))
    return out


def origin(loc: Loc, layers: Sequence[tuple[str, Mapping[str, JSON]]]) -> str:
    """The highest layer that holds ``loc``; ``default`` when none does."""
    for name, layer in reversed(layers):
        node: JSON = layer
        for key in loc:
            if not isinstance(node, dict) or key not in node:
                break
            node = node[key]
        else:
            return name
    return "default"


def source_of(origins: Mapping[str, str], loc: Loc) -> str:
    """The layer a path's value comes from: its own entry, the highest one below it, or the
    entry of the atomic leaf (a list, the theme) that contains it."""
    canonical = format_path(loc)
    if canonical in origins:
        return origins[canonical]
    below = [layer for key, layer in origins.items() if path_under(key, canonical)]
    if below:
        return max(below, key=lambda layer: RANK.get(layer, 0))
    above = [key for key in origins if path_under(canonical, key)]
    return origins[max(above, key=len)] if above else "default"


def path_under(child: str, parent: str) -> bool:
    """True when the canonical path ``child`` is ``parent`` or lies below it."""
    if not parent:
        return True
    return child == parent or child.startswith((parent + ".", parent + "["))


# ---------------------------------------------------------------------------------------- rules
def finalise(document: dict[str, JSON], origins: Mapping[str, str]) -> list[Finding]:
    """Apply the cross-section rules to a merged document in place; returns what they changed."""
    ck = Checker(partial=False)
    layout = document["layout"]
    panels = [(("layout", slot, "panels"), layout[slot]["panels"]) for slot in SLOTS]
    _resolve([*panels, (("layout", "hidden"), layout["hidden"])], origins, ck, "panel")
    _restore_panels(document, ck)
    _collapse_empty_slots(document, ck)
    islands = document["islands"]
    corners = [(("islands", corner), islands[corner]) for corner in CORNERS]
    _resolve([*corners, (("islands", "hidden"), islands["hidden"])], origins, ck, "island")
    _restore_islands(document, ck)
    _complete_topbar(document, ck)
    _complete_groups(document)
    _check_shortcuts(document, ck)
    return [replace(finding, scope="effective") for finding in ck.findings]


def _ident(entry: JSON) -> str:
    return str(entry["id"]) if isinstance(entry, dict) else str(entry)


def _resolve(
    places: list[tuple[Loc, list[JSON]]],
    origins: Mapping[str, str],
    ck: Checker,
    what: str,
) -> None:
    """Keep each id in one place: the one from the highest layer, then the earliest listed."""
    best: dict[str, tuple[int, int]] = {}
    for index, (loc, entries) in enumerate(places):
        rank = RANK.get(origins.get(format_path(loc), "default"), 0)
        for entry in entries:
            ident = _ident(entry)
            if ident not in best or rank > best[ident][0]:
                best[ident] = (rank, index)
    for index, (loc, entries) in enumerate(places):
        rank = RANK.get(origins.get(format_path(loc), "default"), 0)
        kept: list[JSON] = []
        for entry in entries:
            ident = _ident(entry)
            if best[ident][1] == index and ident not in {_ident(k) for k in kept}:
                kept.append(entry)
            elif best[ident][0] == rank:
                winner = format_path(places[best[ident][1]][0])
                ck.notice(
                    "workspace.duplicate",
                    loc,
                    f"{what} {ident!r} is also in {winner}; it stays there and is removed here",
                )
        entries[:] = kept


def _next_order(panels: list[JSON]) -> int:
    orders = [p.get("order", 0) for p in panels if isinstance(p, dict)]
    return max(orders, default=-1) + 1


def _collapse_empty_slots(document: dict[str, JSON], ck: Checker) -> None:
    """A side slot with no panels is COLLAPSED, whatever the document says.

    **An empty open slot is a contradiction the renderer used to honour** (Claude's 2026-10-19
    1230): an older build wrote `right: {collapsed: false, panels: []}`, which draws nothing -
    `Layout` returns null for a slot with no panels - but the measure of the dock still reported
    its 320 px, so the viewport's free rectangle was 320 px narrower than what the user could
    see and the first fit framed the model into the phantom space. That is the "flat blue
    close-up".

    **Normalised on load rather than only fixed in the measurement**, because a document that
    says a slot is open while holding nothing is asking every reader to special-case it. One
    rule, one place.
    """
    layout = document["layout"]
    for slot in ("left", "right", "bottom"):
        entry = layout[slot]
        if entry["panels"] or entry["collapsed"]:
            continue
        entry["collapsed"] = True
        ck.notice(
            "workspace.empty_slot",
            ("layout", slot, "collapsed"),
            f"the {slot} slot held no panels; it is collapsed",
        )


def _restore_panels(document: dict[str, JSON], ck: Checker) -> None:
    """A panel a layer dropped is put back **where the default keeps it**.

    **The default's own `hidden` list decides** (the owner's "remove the right strip"): the
    stage panels have no place in the default arrangement, so a layer that says nothing about them
    gets them back HIDDEN rather than dropped into the right slot. The first version always restored
    to the slot, which was right when every panel had one and is what would have re-created the rail
    the moment a user's document did not mention a panel - measured: the documented user
    example grew `mesh`, `conditions`, `solve` and `results` back into the right slot.
    """
    from stokes.workspace.defaults import default_document

    layout = document["layout"]
    default_hidden = set(default_document()["layout"]["hidden"])
    present = {_ident(p) for slot in SLOTS for p in layout[slot]["panels"]} | set(layout["hidden"])
    for panel, slot in PANELS.items():
        if panel in present:
            continue
        if panel in default_hidden:
            # **Hidden is a place**, so the notice names it rather than claiming the slot.
            layout["hidden"].append(panel)
            ck.notice(
                "workspace.restored",
                ("layout", "hidden"),
                f"panel {panel!r} was neither placed nor hidden; it is hidden, as the default "
                "arrangement keeps it. Place it in a slot to show it",
            )
            continue
        panels = layout[slot]["panels"]
        panels.append({"id": panel, "order": _next_order(panels)})
        ck.notice(
            "workspace.restored",
            ("layout", slot, "panels"),
            f"panel {panel!r} was neither placed nor hidden; it is back in the {slot} slot "
            "(list it in layout.hidden to hide it)",
        )


def _restore_islands(document: dict[str, JSON], ck: Checker) -> None:
    islands = document["islands"]
    present = {i for corner in (*CORNERS, "hidden") for i in islands[corner]}
    for island, corner in ISLANDS.items():
        if island not in present:
            islands[corner].append(island)
            ck.notice(
                "workspace.restored",
                ("islands", corner),
                f"island {island!r} was neither placed nor hidden; it is back in corner {corner} "
                "(list it in islands.hidden to hide it)",
            )


def _complete_topbar(document: dict[str, JSON], ck: Checker) -> None:
    items: list[JSON] = document["topbar"]["items"]
    for position, item in enumerate(TOPBAR_ITEMS):
        ids = [_ident(entry) for entry in items]
        if item in ids:
            continue
        before = [i for i in TOPBAR_ITEMS[:position] if i in ids]
        at = ids.index(before[-1]) + 1 if before else 0
        items.insert(at, {"id": item, "visible": True})
        after = f"after {before[-1]!r}" if before else "at the start"
        ck.notice(
            "workspace.restored",
            ("topbar", "items"),
            f"top-bar item {item!r} was missing; it is back {after} "
            "(set visible to false to hide an item)",
        )


def _complete_groups(document: dict[str, JSON]) -> None:
    order: list[JSON] = document["tree"]["groupOrder"]
    order.extend(group for group in TREE_GROUPS if group not in order)


def _check_shortcuts(document: dict[str, JSON], ck: Checker) -> None:
    shortcuts: dict[str, JSON] = document["shortcuts"]
    for action in REQUIRED_ACTIONS:
        if not chords_of(shortcuts.get(action)):
            shortcuts[action] = binding_of(ACTION_IDS[action])
            ck.invalid(
                ("shortcuts", action),
                f"{action} must keep a chord, because the command palette can never be removed "
                f"(its default is {shortcuts[action]})",
                code="workspace.required",
            )
    bound: dict[str, list[str]] = {}
    for action, binding in shortcuts.items():
        for chord in chords_of(binding):
            bound.setdefault(chord, []).append(action)
    for chord, actions in bound.items():
        # **Only two actions of the SAME CONTEXT conflict** (T-176 §3). `tree.menu` (context
        # `tree`) and `context.menu` (context `global`) share `Shift+F10` and `ContextMenu`
        # **by design**: the tree consumes the chord while it has focus, and the global action
        # takes it otherwise. That is what a context is FOR, so reporting it would put four
        # warnings on the default document and teach the reader to ignore them.
        #
        # An id with no entry in `ACTION_IDS` counts as `global`: a shortcut the front end owns and
        # this table does not know cannot be shown to be fine, and calling it global is the reading
        # that reports the conflict rather than hiding it.
        clashing = _same_context(actions)
        for action, others in clashing.items():
            ck.notice(
                "workspace.shortcut_conflict",
                ("shortcuts", action),
                f"{chord} is also bound to {', '.join(others)}",
            )


def _same_context(actions: list[str]) -> dict[str, list[str]]:
    """``{action: [the others in its own context]}`` for the actions that actually clash.

    Empty for a chord shared only across contexts, which is a design and not a conflict.
    """
    by_context: dict[str, list[str]] = {}
    for action in actions:
        found = ACTION_IDS.get(action)
        by_context.setdefault(found.context if found is not None else "global", []).append(action)
    clashing: dict[str, list[str]] = {}
    for group in by_context.values():
        if len(group) < 2:
            continue
        for action in group:
            clashing[action] = [other for other in group if other != action]
    return clashing


def under(finding: Finding, root: Loc) -> bool:
    """True when a finding concerns ``root`` or something below it."""
    return is_within(finding.path, root)
