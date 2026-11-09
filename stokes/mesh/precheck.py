"""The dry-run precheck: everything wrong or worth knowing, before anything is built.

`mesh.precheck` builds the mesh case WITHOUT sending it and reports every finding instead of the
first refusal. The same record serves `mesh.tasks`, through the service's revision-keyed cache:
a burst of edits costs one snapshot build, not one per command.

## Findings, not refusals

A snapshot build stops at its FIRST refusal; a precheck must not. So the checks a refusal would
hide are made here against the tree and the stored reports — is a domain set, is the body closed,
do faces have boundaries — and the build is then attempted once, its one refusal reported as a
finding of its own. A refusal that carries a finding code in its details (`snapshot.py`'s raise
sites) becomes that finding; any other refusal is a block finding whose message is the refusal's
own, because a build that cannot happen is always worth reporting.

**Nothing here writes.** The precheck never runs `geometry.check` itself (that is the Geometry
task's job) and never starts a job; it reads the tree, the geometry store and the stored check
reports. Nothing here builds STL text either: a surface's size comes from the triangle count the
snapshot builder recorded and `ascii_stl_size`, never from the text itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session
    from stokes.model.nodes import Node

#: The findings that block a mesh, mapped from the refusal codes the snapshot builders put in
#: their error details. Anything else a build raises is still reported — as a block finding whose
#: code is the error's own when it is one of ours, so a client is never without a message.
_BUILD_CODES: dict[str, str] = {
    "mesh.no_domain": "mesh.no_domain",
    "mesh.no_inside_point": "mesh.no_inside_point",
    "mesh.stale_tag": "mesh.stale_tag",
    "mesh.too_large_to_stage": "mesh.too_large_to_stage",
}


def prepared_record(
    session: Session, settings: Node | None, *, snap: Any = None, engine: str = ""
) -> dict[str, Any]:
    """The record the read commands share: the basis parts, the overall basis and the precheck.

    ``snap`` passes an already-built snapshot in — the debounced stale pass owns one, and rebuilding
    here would halve the cache's worth. ``engine`` is the result's recorded engine (or the live
    one); it belongs to the `mesher` part, so a result meshed by another OpenFOAM build is told
    apart by its parts too.

    **Never raises**: a build that fails is the precheck's most interesting input, not an error of
    its own — the record carries the refusal as findings and empty payloads.
    """
    from stokes.mesh.basis import mesh_basis, mesh_basis_parts
    from stokes.mesh.drivers import driver_for
    from stokes.mesh.snapshot import snapshot_for

    if settings is None:
        return {
            "ok": False,
            "parts": {},
            "basis": "",
            "estimate": None,
            "snapshot": None,
            "precheck": _payload(
                [_finding("mesh.no_domain", "block", "no mesh settings yet; set the mesher up")],
                patches=[],
                unassigned_faces=0,
                surfaces=[],
                levels=[],
            ),
        }
    try:
        built = snapshot_for(session, settings) if snap is None else snap
    except Exception as exc:
        # **The refusal IS the precheck's input** — this function's job is to turn it into
        # findings, so catching it here without a log is the handling, not a swallow.
        return {
            "ok": False,
            "parts": {},
            "basis": "",
            "estimate": None,
            "snapshot": None,
            "precheck": from_error(session, settings, exc),
        }
    driver = driver_for(str(built.mesher))
    validate = getattr(driver, "validate", None)
    if validate is not None:
        # **The driver's own refusals belong in the precheck** (ADR-0032): the tet type refuses
        # wall layers, and finding that at Generate - after the user pressed it - is worse than
        # the Sizing and Layers pages saying so first.
        try:
            validate(built)
        except Exception as exc:
            return {
                "ok": False,
                "parts": {},
                "basis": "",
                "estimate": None,
                "snapshot": None,
                "precheck": from_error(session, settings, exc),
            }
    parts = mesh_basis_parts(built, driver.DRIVER_VERSION, engine)
    # **The estimate rides the cached record** (brief M-503): pure over the snapshot, computed
    # once per revision like the precheck beside it, and the snapshot itself is kept ON THE
    # RECORD (in memory, never in the tree) so `mesh.estimate` with tried sizes re-resolves
    # through the same precedence instead of rebuilding the case. **snappy only**: a blockMesh
    # snapshot has no surfaces to integrate, and its cell count is exact anyway.
    from stokes.mesh.estimate import estimate
    from stokes.mesh.model import SURFACE_MESHERS

    # Every surface-following mesher has surfaces to integrate (ADR-0032 scales the cfMesh ones).
    is_snappy = str(built.mesher) in SURFACE_MESHERS
    return {
        "ok": True,
        "parts": parts,
        "basis": mesh_basis(built, driver.DRIVER_VERSION, engine),
        "precheck": precheck(session, settings, built),
        "estimate": estimate(built) if is_snappy else None,
        "snapshot": built if is_snappy else None,
    }


def from_error(session: Session, settings: Node, exc: BaseException) -> dict[str, Any]:
    """The precheck payload when the snapshot build refused: the refusal as a finding, plus the
    checks that can still be made against the tree."""
    code = str(getattr(exc, "details", {}).get("code") or getattr(exc, "code", "mesh.invalid"))
    findings = [_finding(_BUILD_CODES.get(code, code), "block", str(exc))]
    project = session.require_project()
    part = _domain_part_node(project, settings)
    if part is not None:
        findings.extend(_open_body_findings(project, part))
    return _payload(findings, patches=[], unassigned_faces=0, surfaces=[], levels=[])


def precheck(session: Session, settings: Node, snap: Any) -> dict[str, Any]:
    """The precheck payload for a BUILT snapshot: findings, patches, sizes, levels."""
    from stokes.geometry.export import MAX_STAGED_BYTES, ascii_stl_size
    from stokes.mesh.levels import level_for

    project = session.require_project()
    findings: list[dict[str, Any]] = []

    domain_part = _domain_part_node(project, settings)
    if domain_part is not None:
        findings.extend(_open_body_findings(project, domain_part))

    mesher = str(snap.mesher)
    domain = str(getattr(snap, "domain", "") or "")
    surfaces = tuple(getattr(snap, "surfaces", ()) or ())
    triangles = dict(getattr(snap, "surface_triangles", {}) or {})
    faces = dict(getattr(snap, "surface_faces", {}) or {})

    surface_payload = [
        {
            "name": surface.name,
            "triangles": int(triangles.get(surface.name, 0)),
            "bytes": ascii_stl_size(int(triangles.get(surface.name, 0))),
        }
        for surface in surfaces
    ]

    # **The levels: what was asked, what the levels achieve, and when the rounding is worth a
    # warning.** `level_for` is the same arithmetic the surface list used, so the achieved size
    # and the deviation here are the mesh's own.
    levels: list[dict[str, Any]] = []
    if surfaces:
        for surface in surfaces:
            choice = level_for(snap.base_size, float(surface.requested or snap.base_size))
            levels.append(
                {
                    "surface": surface.name,
                    "requested": choice.requested,
                    "achieved": choice.achieved,
                    "level_min": int(surface.level_min),
                    "level_max": int(surface.level_max),
                    "deviation": choice.deviation,
                    "warns": choice.warns,
                }
            )
            if choice.warns:
                findings.append(
                    _finding(
                        "mesh.level_rounding",
                        "warn",
                        f"{surface.name}: the requested size is {choice.requested * 1000:.4g} mm, "
                        f"but the refinement levels achieve {choice.achieved * 1000:.4g} mm",
                        nodes=[],
                    )
                )

    # **The unassigned faces**, and the acceptance that turns the warning into a note.
    accept = bool(settings.params.get("accept_unassigned", False))
    unassigned = _unassigned_faces(project, snap, faces)
    if unassigned:
        if accept:
            findings.append(
                _finding(
                    "mesh.unassigned_faces",
                    "info",
                    f"{unassigned} faces have no boundary; they are kept as a wall, as accepted",
                )
            )
        else:
            findings.append(
                _finding(
                    "mesh.unassigned_faces",
                    "warn",
                    f"{unassigned} faces have no boundary yet and will be meshed as a wall named "
                    "unassigned",
                )
            )

    # **The cell limit, blockMesh only** (brief M-501): snappy shrinks its background to fit, so
    # its estimate over the limit is the sizing task's business once the estimate exists.
    if mesher == "blockmesh":
        from stokes.mesh.drivers.blockmesh import cell_count

        total = cell_count(snap)
        if total > int(snap.max_cells):
            findings.append(
                _finding(
                    "mesh.over_max_cells",
                    "block",
                    f"the mesh would have {total:,} cells, over the limit of "
                    f"{int(snap.max_cells):,}",
                )
            )
        if domain == "part":
            findings.append(
                _finding(
                    "mesh.blockmesh_box_only",
                    "info",
                    "blockMesh meshes the bounding box: a round part comes out a box, and faces "
                    "inside it cannot become patches. snappyHexMesh follows the shape.",
                )
            )

    findings.extend(_solids_not_meshed(project, settings, snap))

    # **Snappy's own over-the-limit finding** (brief M-503): M-501 left it out because the
    # background SHRINKS to fit the limit - but the castellated refinement above the background
    # is not free, and the estimate's band top is what a job would actually hit. The finding
    # names both numbers, and the fix it implies is the line the Sizing page shows.
    estimate_numbers: dict[str, Any] | None = None
    if mesher != "blockmesh":
        from stokes.mesh.estimate import estimate

        estimate_numbers = estimate(snap)
        if estimate_numbers["over"]:
            findings.append(
                _finding(
                    "mesh.over_max_cells",
                    "block",
                    f"the mesh would have up to {int(estimate_numbers['high']):,} cells "
                    f"(estimate), over the limit of {int(estimate_numbers['max_cells']):,}; "
                    "raise the base size or the limit",
                )
            )

    return _payload(
        findings,
        patches=_patches(snap, project, surfaces, faces),
        unassigned_faces=unassigned,
        surfaces=surface_payload,
        levels=levels,
        staging_limit=MAX_STAGED_BYTES,
        estimate=estimate_numbers,
    )


# ------------------------------------------------------------------ pieces


def _payload(
    findings: list[dict[str, Any]],
    *,
    patches: list[dict[str, Any]],
    unassigned_faces: int,
    surfaces: list[dict[str, Any]],
    levels: list[dict[str, Any]],
    estimate: dict[str, Any] | None = None,
    staging_limit: int | None = None,
) -> dict[str, Any]:
    """The payload in the command's shape, with the staging limit filled by the caller's world."""
    if staging_limit is None:
        from stokes.geometry.export import MAX_STAGED_BYTES

        staging_limit = MAX_STAGED_BYTES
    return {
        "findings": findings,
        "patches": patches,
        "unassigned_faces": unassigned_faces,
        "surfaces": surfaces,
        "staging_limit": int(staging_limit),
        "levels": levels,
        # **The estimate rides the precheck reply** (brief M-503): the Sizing page and the tasks
        # module read it here, and `mesh.estimate` answers the tried-values shape from the same
        # function. None for blockMesh, whose own cell count is exact.
        "estimate": estimate,
    }


def _finding(
    code: str, severity: str, message: str, *, nodes: list[str] | None = None
) -> dict[str, Any]:
    """One finding, in the reply's shape. **The message is the whole user text**; the code is for
    the tests and the tasks module."""
    return {"code": code, "severity": severity, "message": message, "nodes": list(nodes or [])}


def _domain_part_node(project: Any, settings: Any) -> Any:
    """The domain part node, or None (a box domain, or nothing set)."""
    if settings is None:
        return None
    domain = str(settings.params.get("domain") or "part")
    if domain != "part":
        return None
    ref = settings.params.get("domain_part")
    if not ref:
        return None
    node = project.find(str(ref))
    return node if node is not None and getattr(node, "type", "") == "part" else None


def _open_body_findings(project: Any, part: Any) -> list[dict[str, Any]]:
    """Is the domain body closed? **Read from the part's last geometry check report** — the
    precheck never runs one. No report at all is its own honest answer.
    """
    check = part.meta.get("check") if hasattr(part, "meta") else None
    counts = check.get("counts") if isinstance(check, dict) else None
    if not isinstance(counts, dict):
        return [
            {
                "code": "mesh.not_checked",
                "severity": "info",
                "message": "Run Geometry check to find out whether the body is closed",
                "nodes": [str(part.id)],
                "fix": {"command": "geometry.check", "params": {"part": str(part.id)}},
            }
        ]
    free = int(counts.get("geometry.free_edges") or 0)
    if free:
        return [
            {
                "code": "mesh.open_body",
                "severity": "block",
                "message": f"{part.name} is not closed: {free} edges belong to one triangle only. "
                "Run Geometry check on it to see where.",
                "nodes": [str(part.id)],
                "fix": {"command": "geometry.check", "params": {"part": str(part.id)}},
            }
        ]
    return []


def _unassigned_faces(project: Any, snap: Any, faces: dict[str, int]) -> int:
    """The CAD faces that will be meshed without a boundary the user named.

    For snappy that is the `unassigned` surface's face count — the snapshot builder gathers the
    faces in no tag there. For blockMesh it is the faces of the tags that reached NO box side:
    a tag the box sides do not touch is not a patch, and its faces have no boundary. The counts
    come from the tags' own stored members, restricted to the domain part, because a tag can
    name faces on several parts.
    """
    if str(snap.mesher) != "blockmesh":
        return int(faces.get("unassigned", 0))
    unmapped = set(getattr(snap, "unmapped", ()) or ())
    if not unmapped:
        return 0
    domain_id = str(snap.domain_part or "")
    total = 0
    for node in project.root.walk():
        if node.type != "tag" or str(node.name) not in unmapped:
            continue
        text = node.params.get("members")
        if isinstance(text, str) and text:
            total += _member_face_count(text, domain_id)
    return total


def _member_face_count(members_text: str, part_id: str) -> int:
    """How many faces one tag's stored members name for ONE part, without loading the geometry."""
    import json

    try:
        stored = json.loads(members_text)
    except ValueError:
        return 0
    total = 0
    for member in stored if isinstance(stored, list) else []:
        if not isinstance(member, dict) or str(member.get("part") or "") != part_id:
            continue
        faces = member.get("faces")
        if faces is None:
            continue  # a whole-part member is cell-zone membership, not a boundary patch
        for entry in faces if isinstance(faces, list) else []:
            if isinstance(entry, list) and len(entry) == 2:
                total += int(entry[1]) - int(entry[0]) + 1
            elif isinstance(entry, int):
                total += 1
    return total


def _patches(
    snap: Any, project: Any, surfaces: tuple[Any, ...], faces: dict[str, int]
) -> list[dict[str, Any]]:
    """The boundary patches the case will have, before anything runs.

    **The same list the driver writes**, by construction: snappy's comes from the surfaces (the
    patch IS the staged surface's region), blockMesh's from the six sides. `faces` is the CAD face
    count the tag holds — the mesh's own face counts arrive with the result.
    """
    if str(snap.mesher) == "blockmesh":
        return [
            {
                "name": str(name),
                "type": str(snap.side_kinds.get(side, "") or "patch"),
                "tag_id": str(snap.side_tags.get(side, "") or ""),
                "faces": int(dict(getattr(snap, "side_faces", {}) or {}).get(side, 0)),
            }
            for side, name in sorted(snap.sides.items())
        ]
    tag_ids = dict(getattr(snap, "side_tags", {}) or {})
    return [
        {
            "name": surface.name,
            "type": str(surface.patch_type),
            "tag_id": str(tag_ids.get(surface.name, "") or ""),
            "faces": int(faces.get(surface.name, 0)),
        }
        for surface in surfaces
    ]


def _solids_not_meshed(project: Any, settings: Any, snap: Any) -> list[dict[str, Any]]:
    """The simulated bodies the mesher will not touch, as an info finding.

    Today every mesher meshes the domain part alone: another simulated body is real geometry the
    mesh says nothing about, and the honest answer is to name it — the user decides whether it
    should be a region, a hole, or left out.
    """
    domain_id = str(getattr(snap, "domain_part", "") or "")
    left_out: list[dict[str, Any]] = []
    nodes: list[str] = []
    for node in project.root.walk():
        if node.type != "part" or str(node.id) == domain_id:
            continue
        if node.params.get("simulate", True) is False:
            continue
        nodes.append(str(node.id))
        left_out.append({"name": str(node.name)})
    if not nodes:
        return []
    names = ", ".join(item["name"] for item in left_out)
    return [
        _finding(
            "mesh.solid_not_meshed",
            "info",
            f"{names} will not be meshed: only the domain part's surfaces are followed",
            nodes=nodes,
        )
    ]
