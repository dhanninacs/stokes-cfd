"""The guided-meshing tasks: nine steps, each with its state and what it needs.

One read-only answer for any client (`mesh.tasks`): the tasks in order, each with its `state`,
`todo` (what it still needs, as sentences a user reads), `changed` (which tasks' inputs changed
since the mesh was made), the tree row the task lives on and the commands that serve it. The
states use the outline's own vocabulary — `idle | todo | ready | running | done | stale | failed`
— so the tree's existing dots draw them and no second legend is invented.

## The shape of the machine

A settings task reads the tree; a run task reads the result and the service status. Staleness
comes from the basis parts: a part that differs names its tasks (the table in
`stokes.mesh.basis`), and **every part that differs also makes the volume task stale**, because a
mesh whose inputs changed is a mesh to run again — while `changed[]` still names only the tasks
the part belongs to. A result made before the parts existed is not diffed at all: its volume task
is stale with the one line that says why, and generating once records the parts.

**The titles and purpose lines are user text and live in one table here** — nowhere else, so a
word is changed once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The job steps mapped to the task each one runs (brief M-501). The background block and the
#: feature extraction are the surface task's work; the snapping is the volume's; the check is
#: quality's.
STEP_TO_TASKS: dict[str, tuple[str, ...]] = {
    "blockMesh": ("surface",),
    "surfaceFeatures": ("surface",),
    "snappyHexMesh": ("volume",),
    # ADR-0032: the cfMesh family builds the volume in one or two steps.
    "cartesianMesh": ("volume",),
    "tetMesh": ("volume",),
    "pMesh": ("volume",),
    "polyDualMesh": ("volume",),
    # The polyhedral type's symmetry planes, flattened and set back (`cfmesh.FLATTEN_STEP`).
    "improveSymmetryPlanes": ("volume",),
    "symmetryType": ("volume",),
    # The tetrahedral and hex-dominant types' snap (`cfmesh.SNAP_STEP`), one per planar plane.
    "flattenSymmetry": ("volume",),
    "checkMesh": ("quality",),
}

#: The line a result made before the parts existed shows on its volume task. Generating once
#: records the parts and the line goes away — that is the whole of the ask.
LEGACY_VOLUME_TODO = (
    "The mesher's inputs are now recorded in full: generate once to bring this mesh up to date."
)


@dataclass(frozen=True)
class TaskSpec:
    """One task's words and where it lives. **The words are user text** — the stepper's rows."""

    id: str
    title: str
    purpose: str
    row: str
    commands: tuple[str, ...]


TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        "geometry",
        "Geometry check",
        "Is the shape ready to mesh: a closed body, faces up to date, small enough to send?",
        "Geometry ▸ Bodies",
        ("geometry.check", "mesh.precheck"),
    ),
    TaskSpec(
        "bodies",
        "What each body is",
        "Say which body is the fluid, which are solids, which are holes in the flow, "
        "which are left out",
        "Geometry ▸ Bodies",
        ("mesh.body_role", "mesh.configure", "parameter.set"),
    ),
    TaskSpec(
        "boundaries",
        "Boundaries",
        "Which faces become which boundary patch, and which faces have no boundary yet",
        "Physics ▸ Boundary conditions",
        ("mesh.precheck", "tag.create", "boundary.assign"),
    ),
    TaskSpec(
        "sizing",
        "Sizing",
        "How fine the mesh is: everywhere, per body, per boundary, and inside boxes or spheres",
        "Mesh ▸ Global size",
        ("mesh.configure", "parameter.set"),
    ),
    TaskSpec(
        "surface",
        "Surface",
        "What the mesh will follow: the surfaces sent to the mesher, and the sharp edges it keeps",
        "Mesh ▸ Surface",
        ("mesh.precheck", "mesh.configure"),
    ),
    TaskSpec(
        "layers",
        "Boundary layers",
        "Thin cells along walls for the near-wall flow: how many, how thick, on which walls",
        "Mesh ▸ Boundary layers",
        ("mesh.configure", "parameter.set"),
    ),
    TaskSpec(
        "volume",
        "Volume mesh",
        "Build the mesh with the chosen mesher; progress, log and Stop",
        "Mesh ▸ Mesh",
        ("mesh.meshers", "mesh.generate", "mesh.cancel", "mesh.status", "mesh.log"),
    ),
    TaskSpec(
        "quality",
        "Quality",
        "How good the cells are, where the bad ones are, and what to change",
        "Mesh ▸ Mesh ▸ Quality",
        (),
    ),
    TaskSpec(
        "fidelity",
        "Fidelity to CAD",
        "How closely the mesh follows the CAD",
        "Mesh ▸ Mesh ▸ Fidelity to CAD",
        (),
    ),
)

#: The findings that block, by the task they belong to. A task shows its own finding's message;
#: the volume task waits while ANY of them stands, because a generate would stop on the first.
_GEOMETRY_BLOCKS = ("mesh.no_domain", "mesh.open_body", "mesh.no_inside_point")
_SIZING_BLOCKS = ("mesh.over_max_cells",)
_SURFACE_BLOCKS = ("mesh.too_large_to_stage",)
_BOUNDARY_BLOCKS = ("mesh.stale_tag",)


def mesh_tasks(
    project: Any,
    *,
    prepared: dict[str, Any],
    result: Any | None,
    status: dict[str, Any],
    settings: Any | None,
    host: str,
) -> dict[str, Any]:
    """The nine tasks with their states, for `mesh.tasks`.

    ``prepared`` is the service's cached record (basis parts + precheck); ``result`` the result
    node or None; ``status`` the service's own status dict (state, step, stale); ``settings`` the
    settings node or None. Pure over its inputs: nothing here writes or builds.
    """
    precheck = dict(prepared.get("precheck") or {})
    findings = [f for f in precheck.get("findings") or [] if isinstance(f, dict)]
    blocks = [f for f in findings if f.get("severity") == "block"]

    state = str(status.get("state") or "")
    step = str(status.get("step") or "")
    running = set(STEP_TO_TASKS.get(step, ()))
    result_status = str(getattr(result, "status", "") or "") if result is not None else ""
    quality = dict(result.params.get("quality") or {}) if result is not None else {}
    stored_parts = result.meta.get("basis_parts") if result is not None else None
    changed = _changed(stored_parts, prepared)
    legacy = result is not None and state == "done" and not isinstance(stored_parts, dict)
    stale_tasks = set(changed)
    if changed:
        # **Every part also makes the volume task stale** (brief M-501): a mesh whose inputs
        # changed is a mesh to run again, whatever part changed.
        stale_tasks.add("volume")

    roles = _roles(project, settings)
    unassigned = int(precheck.get("unassigned_faces") or 0)
    accept = (
        bool(settings.params.get("accept_unassigned", False)) if settings is not None else False
    )

    def first_of(codes: tuple[str, ...]) -> str | None:
        for finding in findings:
            if finding.get("code") in codes and finding.get("severity") == "block":
                return str(finding.get("message") or "")
        return None

    feature_angle = (
        float(settings.params.get("feature_angle", 30.0)) if settings is not None else 30.0
    )
    rows = {
        "geometry": _geometry_row(roles, precheck),
        "bodies": _bodies_row(roles),
        "boundaries": _boundaries_row(unassigned, accept),
        "sizing": _sizing_row(settings, project),
        "surface": _surface_row(prepared, feature_angle),
        "layers": _layers_row(settings),
        "volume": _volume_row(state, step, result_status),
        "quality": _quality_row(quality, status),
        "fidelity": _fidelity_row(quality),
    }

    states, todos = _settings_tasks(findings, roles, unassigned, accept, settings, first_of)
    states["volume"] = _volume_state(state, result_status, running, bool(blocks), legacy)
    todos["volume"] = _volume_todo(states["volume"], legacy, changed)
    states["quality"] = _quality_state(state, step, result_status, status, running)
    todos["quality"] = _quality_todo(states["quality"], quality)
    states["fidelity"] = _fidelity_state(state, quality)
    todos["fidelity"] = _fidelity_todo(states["fidelity"], quality)

    tasks = []
    for spec in TASKS:
        # **The per-task `changed`**: the tasks whose inputs changed, explaining THIS task's
        # staleness — a settings task names itself; the volume task names every changed task,
        # because it depends on them all. The volume task is stale for any diff even when the
        # changed list names only, say, sizing.
        own_changed = changed if spec.id == "volume" else ([spec.id] if spec.id in changed else [])
        tasks.append(
            {
                "id": spec.id,
                "title": spec.title,
                "purpose": spec.purpose,
                "state": states[spec.id],
                "todo": todos[spec.id],
                "changed": own_changed,
                "row": rows[spec.id],
                "commands": list(spec.commands),
            }
        )
    del stale_tasks, legacy
    return {
        "tasks": tasks,
        "current": _current(tasks, running),
        "next": _next(tasks),
        "mesher": str(settings.params.get("mesher") or "") if settings is not None else "",
        "host": host,
    }


def _changed(stored_parts: Any, prepared: dict[str, Any]) -> list[str]:
    """The tasks whose basis parts differ, from the service's own helper."""
    from stokes.mesh.basis import changed_tasks

    parts = prepared.get("parts")
    return changed_tasks(stored_parts, parts if prepared.get("ok") else None)


def _current(tasks: list[dict[str, Any]], running: set[str]) -> str:
    """The task the user is on: a running one, else the first that is not done."""
    for task in tasks:
        if task["id"] in running:
            return str(task["id"])
    for task in tasks:
        if task["state"] != "done":
            return str(task["id"])
    return "fidelity"


def _next(tasks: list[dict[str, Any]]) -> str | None:
    """The first task that is not done, or None when they all are."""
    for task in tasks:
        if task["state"] != "done":
            return str(task["id"])
    return None


# ------------------------------------------------------------------ one task at a time


def _settings_tasks(
    findings: list[dict[str, Any]],
    roles: dict[str, Any],
    unassigned: int,
    accept: bool,
    settings: Any | None,
    first_of: Any,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The six settings tasks' states and todo lines: geometry, bodies, boundaries, sizing,
    surface and layers. Each is decided by the tree and the precheck alone, which is what makes
    them cheap enough to answer on every poll.
    """
    geometry_block = first_of(_GEOMETRY_BLOCKS)
    sizing_block = first_of(_SIZING_BLOCKS)
    surface_block = first_of(_SURFACE_BLOCKS)
    boundary_block = first_of(_BOUNDARY_BLOCKS)

    states: dict[str, str] = {}
    todos: dict[str, list[str]] = {}
    states["geometry"] = "todo" if geometry_block else "done"
    todos["geometry"] = [geometry_block] if geometry_block else []
    bodies_todo = _bodies_todo(roles)
    states["bodies"] = "todo" if bodies_todo else "done"
    todos["bodies"] = bodies_todo
    if boundary_block:
        states["boundaries"] = "todo"
        todos["boundaries"] = [boundary_block]
    elif unassigned:
        states["boundaries"] = "done" if accept else "todo"
        todos["boundaries"] = (
            [] if accept else [f"{unassigned} faces have no boundary yet; accept them or tag them"]
        )
    else:
        states["boundaries"] = "done"
        todos["boundaries"] = []
    states["sizing"] = "todo" if (sizing_block or settings is None) else "done"
    todos["sizing"] = (
        [sizing_block]
        if sizing_block
        else (["set the base size first"] if settings is None else [])
    )
    states["surface"] = "todo" if surface_block else "done"
    todos["surface"] = [surface_block] if surface_block else []
    # **Layers may be off** — that is a valid set-up, not a missing one, until a layer control
    # exists (the boundary-layer package adds the counts and this task's checks).
    states["layers"] = "done"
    todos["layers"] = []
    return states, todos


def _volume_state(
    state: str, result_status: str, running: set[str], blocked: bool, legacy: bool
) -> str:
    if "volume" in running:
        return "running"
    if state == "failed":
        return "failed"
    if legacy or result_status == "stale":
        return "stale"
    if state == "done":
        return "done"
    if blocked:
        return "idle"
    return "ready"


def _volume_todo(volume_state: str, legacy: bool, changed: list[str]) -> list[str]:
    if legacy:
        return [LEGACY_VOLUME_TODO]
    if volume_state == "stale":
        if changed:
            return ["the mesh's inputs changed; generate to mesh again"]
        return ["the mesh is out of date; generate to mesh again"]
    if volume_state == "failed":
        return ["the last attempt failed; read its log, change what it names and generate again"]
    if volume_state == "idle":
        return ["the tasks above must be done first"]
    return []


def _quality_state(
    state: str, step: str, result_status: str, status: dict[str, Any], running: set[str]
) -> str:
    if step in running and "quality" in running:
        return "running"
    if result_status == "stale":
        return "stale"
    if state == "failed":
        return "idle" if step and step != "checkMesh" else "failed"
    if state == "done":
        return "done" if bool(status.get("ok")) else "todo"
    return "idle"


def _quality_todo(quality_state: str, quality: dict[str, Any]) -> list[str]:
    if quality_state != "todo":
        return []
    if quality.get("error"):
        return [str(quality["error"])]
    return ["the mesh misses the project's limits; change the limits or mesh again"]


def _fidelity_state(state: str, quality: dict[str, Any]) -> str:
    if state != "done":
        return "idle"
    fidelity = quality.get("fidelity")
    if not isinstance(fidelity, dict) or not fidelity:
        return "todo"
    return "done" if str(fidelity.get("verdict") or "") == "Good" else "todo"


def _fidelity_todo(fidelity_state: str, quality: dict[str, Any]) -> list[str]:
    if fidelity_state != "todo":
        return []
    fidelity = quality.get("fidelity")
    if not isinstance(fidelity, dict) or not fidelity:
        return ["the mesh's fidelity has not been measured yet; generate to measure it"]
    return [f"the mesh follows the CAD {str(fidelity.get('verdict') or '').lower()}"]


# ------------------------------------------------------------------ the row texts


def _geometry_row(roles: dict[str, Any], precheck: dict[str, Any]) -> str:
    """`fluid · closed · 1.8 MiB to send` — the shape's readiness in three words."""
    role = str(roles.get("word") or "no domain yet")
    check = roles.get("domain_check")
    closed = "not checked"
    if isinstance(check, dict):
        raw_counts = check.get("counts")
        counts: dict[str, Any] = raw_counts if isinstance(raw_counts, dict) else {}
        closed = "open" if int(counts.get("geometry.free_edges") or 0) else "closed"
    total_bytes = sum(int(s.get("bytes") or 0) for s in precheck.get("surfaces") or [])
    send = f"{total_bytes / 1048576:.1f} MiB to send" if total_bytes else "nothing to send"
    parts = [part for part in (role, closed) if part]
    parts.append(send)
    return " · ".join(parts)


def _bodies_row(roles: dict[str, Any]) -> str:
    counts = roles.get("counts") or {}
    words = []
    if counts.get("fluid"):
        words.append(f"{counts['fluid']} fluid")
    if counts.get("hole"):
        words.append(f"{counts['hole']} hole")
    solids = int(counts.get("solid", 0))
    words.append(f"{solids} solid{'s' if solids != 1 else ''}")
    left_out = int(counts.get("left_out", 0))
    words.append(f"{left_out} left out")
    return " · ".join(words)


def _boundaries_row(unassigned: int, accept: bool) -> str:
    if not unassigned:
        return "every face has a boundary"
    if accept:
        return f"{unassigned} faces kept as a wall, as accepted"
    return f"{unassigned} faces have no boundary"


def _layers_row(settings: Any) -> str:
    """The layer count the mesher will add on walls, or "off". This row said "off" whatever the
    settings held, so Full mode contradicted the Guided card's "3 layers on every wall"."""
    if settings is None:
        return "off"
    layers = int(settings.params.get("layers") or 0)
    from stokes.mesh.model import SURFACE_MESHERS

    if layers <= 0 or settings.params.get("mesher", "snappy") not in SURFACE_MESHERS:
        return "off"
    return f"{layers} {'layer' if layers == 1 else 'layers'} on walls"


def _sizing_row(settings: Any, project: Any) -> str:
    if settings is None:
        return "no mesh settings"
    base = settings.params.get("base_size")
    value = float(getattr(base, "value", 0.0) or 0.0)
    text = f"{value * 1000:g} mm" if 0 < value < 1 else f"{value:g} m"
    local = 0
    for node in project.root.walk():
        if node.type != "part":
            continue
        size = getattr(node.params.get("mesh_size"), "value", 0.0) or 0.0
        if float(size) or int(node.params.get("refinement") or 0):
            local += 1
    return f"{text} · {local} local" if local else text


def _surface_row(prepared: dict[str, Any], feature_angle: float = 30.0) -> str:
    surfaces = (prepared.get("precheck") or {}).get("surfaces") or []
    if not surfaces:
        return "the box sides"
    # **The angle is the setting now** (brief M-515): the row reads what the dictionaries carry,
    # so the stepper and the Surface page cannot disagree about the threshold. The staged size is
    # what the page's bar shows against the limit.
    total_bytes = sum(int(s.get("bytes") or 0) for s in surfaces if isinstance(s, dict))
    staged = f" · {total_bytes / 1048576:.1f} MiB to send" if total_bytes else ""
    return f"{feature_angle:g}° edges · {len(surfaces)} surfaces{staged}"


def _volume_row(state: str, step: str, result_status: str) -> str:
    if step:
        return step
    return {
        "running": "meshing",
        "done": "done" if result_status != "stale" else "out of date",
        "failed": "failed",
        "cancelled": "cancelled",
        "interrupted": "interrupted",
        "skipped": "no host",
    }.get(state, "ready" if result_status != "stale" else "out of date")


def _quality_row(quality: dict[str, Any], status: dict[str, Any]) -> str:
    if not quality:
        return "-"
    if quality.get("error"):
        return "not read"
    non_ortho = quality.get("max_non_ortho")
    ok = bool(status.get("ok"))
    if non_ortho is None:
        return "passed" if ok else "misses the limits"
    word = "passed" if ok else "over the limit"
    return f"max non-ortho {float(non_ortho):g}° · {word}"


def _fidelity_row(quality: dict[str, Any]) -> str:
    fidelity = quality.get("fidelity")
    if not isinstance(fidelity, dict) or not fidelity:
        return "-"
    verdict = str(fidelity.get("verdict") or "")
    deviation = fidelity.get("max_deviation_mm")
    if deviation is None:
        return verdict or "measured"
    return f"{verdict} · max {float(deviation):.4g} mm"


# ------------------------------------------------------------------ the roles, from the tree


def _roles(project: Any, settings: Any) -> dict[str, Any]:
    """What each body is, read from the tree: the fluid (the domain part under `part`), the hole
    (the domain part under `enclosure`), the solids (the bodies of a solid region), the left-out.

    **This is the bodies task's whole input**, so it is read once here and both the state and the
    row text come from it.
    """
    domain = str(settings.params.get("domain") or "part") if settings is not None else ""
    ref = str(settings.params.get("domain_part") or "") if settings is not None else ""
    domain_node = project.find(ref) if ref else None
    if domain_node is not None and getattr(domain_node, "type", "") != "part":
        domain_node = None
    fluid = domain_node if (domain == "part" and domain_node is not None) else None
    hole = domain_node if (domain == "enclosure" and domain_node is not None) else None
    domain_check = domain_node.meta.get("check") if domain_node is not None else None

    solids: set[str] = set()
    for node in project.root.walk():
        if node.type != "region" or str(node.params.get("kind") or "") != "solid":
            continue
        tag = project.find(str(node.params.get("tag") or ""))
        if tag is None:
            continue
        solids |= _region_parts(tag)

    counts = {"fluid": 1 if fluid else 0, "hole": 1 if hole else 0, "solid": 0, "left_out": 0}
    unpurposed: list[str] = []
    for node in project.root.walk():
        if node.type != "part":
            continue
        if node.params.get("simulate", True) is False:
            counts["left_out"] += 1
            continue
        if node.id in solids:
            counts["solid"] += 1
        elif fluid is None and hole is None:
            unpurposed.append(str(node.name))
        elif node.id in (str(fluid.id) if fluid else "", str(hole.id) if hole else ""):
            pass
        else:
            unpurposed.append(str(node.name))
    word = "fluid" if fluid else ("hole in the flow" if hole else "")
    return {
        "fluid": fluid,
        "hole": hole,
        "solids": solids,
        "unpurposed": unpurposed,
        "counts": counts,
        "word": word,
        "domain_check": domain_check,
    }


def _bodies_todo(roles: dict[str, Any]) -> list[str]:
    """What the bodies task still needs, from the roles alone."""
    if roles["fluid"] is None and roles["hole"] is None:
        return ["say which body is the fluid, or the hole in the flow"]
    if roles["unpurposed"]:
        names = ", ".join(roles["unpurposed"])
        return [f"say what {names} {'is' if len(roles['unpurposed']) == 1 else 'are'} for"]
    return []


def _region_parts(tag: Any) -> set[str]:
    """The part ids one region tag holds whole, from its stored members."""
    text = tag.params.get("members")
    if not isinstance(text, str) or not text:
        return set()
    try:
        stored = json.loads(text)
    except ValueError:
        return set()
    found = set()
    for member in stored if isinstance(stored, list) else []:
        if isinstance(member, dict) and member.get("faces") is None and member.get("part"):
            found.add(str(member["part"]))
    return found
