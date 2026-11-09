"""Shared steps for the end-to-end scripts (T-212, T-214).

`duct_project` builds the skeleton's case through the API commands, in the order a user would:
create a
box, tag its three faces, give them conditions, and configure the mesh. T-214 reuses it for the
solve, so
the two scripts cannot drift apart in what the duct IS.

**Everything goes through `session.execute`**, never through the model directly: that is what
makes the
result reproducible from a journal, and what proves the commands are sufficient to build a case.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.api.session import Session

#: The duct: 0.5 m long, 20 mm square, meshed at 2 mm. 250 x 10 x 10 = 25,000 cells.
LENGTH_MM = 500
SIDE_MM = 20
BASE_MM = 2
INFLOW = "0.075 [m/s]"


def duct_project(session: Session, folder: Path) -> dict[str, str]:
    """Build the duct and return the ids a caller needs.

    Returns ``{"part", "inlet", "outlet", "walls", "settings"}``.
    """
    session.execute("project.new", path=str(folder), name="duct")
    created = session.execute(
        "operation.create",
        type="primitive",
        params={
            "shape": "box",
            "size": [f"{LENGTH_MM} [mm]", f"{SIDE_MM} [mm]", f"{SIDE_MM} [mm]"],
            "position": ["0 [mm]", "0 [mm]", "0 [mm]"],
        },
        wait=True,
    )
    part_id = _output_part(session, created)
    faces = _faces_by_side(session, part_id)

    inlet = session.execute(
        "tag.create",
        name="inlet",
        members=[{"part": part_id, "faces": faces["inlet"]}],
        kind="boundary",
    )["id"]
    outlet = session.execute(
        "tag.create",
        name="outlet",
        members=[{"part": part_id, "faces": faces["outlet"]}],
        kind="boundary",
    )["id"]
    walls = session.execute(
        "tag.create",
        name="walls",
        members=[{"part": part_id, "faces": faces["walls"]}],
        kind="boundary",
    )["id"]

    session.execute(
        "boundary.assign",
        target=inlet,
        kind="velocity_inlet",
        values={"velocity": INFLOW},
    )
    session.execute(
        "boundary.assign", target=outlet, kind="pressure_outlet", values={"pressure": "0 [Pa]"}
    )
    session.execute("boundary.assign", target=walls, kind="wall")
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "domain": "part",
            "domain_part": part_id,
            "base_size": f"{BASE_MM} [mm]",
        },
    )
    settings = session.execute("mesh.list")["settings"]["id"]
    return {
        "part": part_id,
        "inlet": inlet,
        "outlet": outlet,
        "walls": walls,
        "settings": settings,
    }


#: How long to wait for an operation's outputs to appear. The worker reports before the store's
#: `outputs` is always populated, so a caller that reads the reply immediately can see it empty.
OPERATION_WAIT_S = 10.0


def _output_part(session: Session, created: dict[str, Any]) -> str:
    """The part an operation made: the one whose `derived` names it."""
    # **The reply already names its outputs.** The first version walked the tree looking for a
    # part whose
    # `derived` was the operation id — which is what `opstore` does internally — and it failed for a
    # reason the reply makes obvious: the id is right there under `outputs`. Reading it removes a
    # traversal and the class of bug that comes with one.
    #
    # **But the reply is not always populated when this runs.** `operation.create` with `wait=True`
    # returns as soon as the worker reports, and the store's `outputs` can still be empty for
    # a moment
    # — intermittently, and only in some callers. So this waits for the part rather than
    # assuming, and
    # says what it saw when it gives up.
    op_id = str(created.get("id") or created.get("op") or "")
    deadline = time.monotonic() + OPERATION_WAIT_S
    while True:
        outputs = created.get("outputs") or []
        if outputs:
            return str(outputs[0])
        node = session.require_project().find(op_id) if op_id else None
        if node is not None and (node.params.get("outputs") or []):
            return str(node.params["outputs"][0])
        if time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    from stokes.geometry.store import parts_under

    project = session.require_project()
    for node in parts_under(project.root):
        if str(node.params.get("derived") or "") == op_id:
            return str(node.id)
    raise RuntimeError(
        f"the operation {op_id} produced no part (outputs={created.get('outputs')!r}, "
        f"state={(node.params.get('state') if node is not None else None)!r})"
    )


def _faces_by_side(session: Session, part_id: str) -> dict[str, list[list[int]]]:
    """Each box side's B-rep face ids, **ranged** so a tag holds a compact list.

    A side is decided by its face centroid: the x minimum, the x maximum, or everything else.
    That is what
    makes `inlet` the face the flow enters rather than whichever face the mesher happened to
    number first.
    """
    import numpy as np

    from stokes.geometry.store import GeometryStore

    mesh = GeometryStore.of(session).mesh(part_id)
    if mesh is None:
        raise RuntimeError("the part has no display mesh")
    positions, indices, face_ids = [], [], []
    offset = 0
    for solid in mesh.solids:
        pos = np.asarray(solid.positions, dtype="float64")
        positions.append(pos)
        indices.append(np.asarray(solid.indices) + offset)
        face_ids.append(np.asarray(solid.face_ids))
        offset += len(pos)
    corners = np.concatenate(indices)
    verts = np.concatenate(positions)
    faces = np.concatenate(face_ids)
    xmin = float(verts[:, 0].min())
    xmax = float(verts[:, 0].max())
    tol = 1e-6 * max(1.0, xmax - xmin)

    side: dict[str, list[int]] = {"inlet": [], "outlet": [], "walls": []}
    for face in sorted({int(f) for f in faces}):
        used = corners[faces == face]
        if used.size == 0:
            continue
        centroid_x = float(verts[np.unique(used)][:, 0].mean())
        if abs(centroid_x - xmin) <= tol:
            side["inlet"].append(face)
        elif abs(centroid_x - xmax) <= tol:
            side["outlet"].append(face)
        else:
            side["walls"].append(face)
    return {name: _ranges(ids) for name, ids in side.items() if ids}


def _ranges(ids: list[int]) -> list[list[int]]:
    """Inclusive id ranges, which is the form the tag commands take."""
    out: list[list[int]] = []
    for value in sorted(ids):
        if out and value == out[-1][1] + 1:
            out[-1][1] = value
        else:
            out.append([value, value])
    return out
