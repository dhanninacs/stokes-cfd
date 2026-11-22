"""Boundary tags across a rebuild of an implicit part (ADR-0038 decision 5's cycle).

An implicit part's face ids are those of its last run: a new parameter value can renumber them,
and stable face keys are ADR-0035 slice 2, which is not built. A shape-optimisation cycle therefore
finds the faces of each boundary tag again, by a rule taken from the part as the user tagged it:

- a **plane** tag (all its faces lie in one plane with one normal: an inlet, an outlet, a
  symmetry plane) keeps the faces of the rebuilt part that lie in that plane, facing that way;
- the one **rest** tag of the part (the walls: faces in no plane rule) takes every face left.

**Coplanar plane tags** (four outlets whose caps lie in one plane, ADR-0040) each match every face
of that plane; a face matched by several plane rules goes to the rule whose tagged faces' centre
(area-weighted, taken from the live part) is nearest, so each outlet keeps its own cap.

The rules are pure data, taken once from the live part and applied to each rebuilt mesh.

Headless: numpy only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.geometry.errors import OperationInvalid

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from stokes.geometry.mesh import PartMesh

    F64 = npt.NDArray[np.float64]

ANGLE_COS = 0.996
"""Faces within about 5 degrees of the rule's normal face the same way."""
PLANE_TOLERANCE = 1e-3
"""How far from the plane a face centroid may be, as a share of the part's largest extent."""


@dataclass(frozen=True)
class Face:
    centroid: tuple[float, float, float]
    normal: tuple[float, float, float]
    area: float


def faces_of(mesh: PartMesh) -> tuple[dict[int, Face], float]:
    """Each face id's area-weighted centroid and unit normal, and the part's largest extent (m)."""
    import numpy as np

    sums: dict[int, Any] = {}  # Any: [area-weighted centroid, area vector, area]
    lows, highs = [], []
    for solid in mesh.solids:
        p = solid.positions.astype(np.float64).reshape(-1, 3)
        t = solid.indices.astype(np.int64).reshape(-1, 3)
        ids = np.asarray(solid.face_ids).reshape(-1)
        a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
        vec = 0.5 * np.cross(b - a, c - a)
        area = np.linalg.norm(vec, axis=1)
        mid = (a + b + c) / 3.0
        for face in np.unique(ids):
            sel = ids == face
            entry = sums.setdefault(int(face), [np.zeros(3), np.zeros(3), 0.0])
            entry[0] += (mid[sel] * area[sel, None]).sum(axis=0)
            entry[1] += vec[sel].sum(axis=0)
            entry[2] += float(area[sel].sum())
        lows.append(p.min(axis=0))
        highs.append(p.max(axis=0))
    if not sums:
        raise OperationInvalid("the part has no faces to tag")
    out: dict[int, Face] = {}
    for face, (moment, vector, area) in sums.items():
        size = float(np.linalg.norm(vector))
        normal = vector / size if size > 0 else np.zeros(3)
        centre = moment / area if area > 0 else np.zeros(3)
        out[face] = Face(tuple(centre.tolist()), tuple(normal.tolist()), area)
    extent = float((np.max(highs, axis=0) - np.min(lows, axis=0)).max())
    return out, extent


@dataclass(frozen=True)
class Rule:
    """How a tag finds its faces again: on a plane, or the rest."""

    tag: str
    kind: str  # "plane" or "rest"
    normal: tuple[float, float, float] = (0.0, 0.0, 0.0)
    offset: float = 0.0  # n . x of the plane
    centre: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """The area-weighted centre of the tagged faces (m): which of several coplanar rules a face
    of their plane belongs to."""


def rules_for(mesh: PartMesh, tags: Sequence[tuple[str, Sequence[int]]]) -> list[Rule]:
    """The rule of each ``(tag id, face ids on this part)``: a plane where every face lies in one
    plane with one normal, the rest otherwise. More than one rest tag is refused: the rebuilt
    faces could not be shared out between them."""
    import numpy as np

    faces, extent = faces_of(mesh)
    rules: list[Rule] = []
    for tag, ids in tags:
        chosen = [faces[i] for i in ids if i in faces]
        if not chosen:
            continue
        area = sum(f.area for f in chosen)
        normal = np.sum([np.asarray(f.normal) * f.area for f in chosen], axis=0)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        offsets = [float(np.dot(normal, f.centroid)) for f in chosen]
        planar = all(float(np.dot(normal, f.normal)) >= ANGLE_COS for f in chosen) and (
            max(offsets) - min(offsets) <= PLANE_TOLERANCE * extent
        )
        if planar and area > 0:
            centre = np.sum([np.asarray(f.centroid) * f.area for f in chosen], axis=0) / area
            rules.append(
                Rule(
                    tag,
                    "plane",
                    tuple(normal.tolist()),
                    float(np.mean(offsets)),
                    (float(centre[0]), float(centre[1]), float(centre[2])),
                )
            )
        else:
            rules.append(Rule(tag, "rest"))
    if sum(1 for r in rules if r.kind == "rest") > 1:
        raise OperationInvalid(
            "the part has more than one boundary that is not a plane: an optimisation can find "
            "planar inlets and outlets again, and one boundary for the rest of the surface",
            details={"tags": [r.tag for r in rules if r.kind == "rest"]},
        )
    return rules


def untagged(mesh: PartMesh, assigned: dict[str, list[int]]) -> list[int]:
    """The faces of ``mesh`` that no rule claimed: in the guided flow, the faces the user left as
    wall (``wall by default``), which the adjoint needs as a patch of their own."""
    faces, _ = faces_of(mesh)
    taken = {i for ids in assigned.values() for i in ids}
    return sorted(set(faces) - taken)


def apply_rules(mesh: PartMesh, rules: Sequence[Rule]) -> dict[str, list[int]]:
    """Each rule's face ids on the rebuilt ``mesh``. A face in the plane of several plane rules
    goes to the one whose centre is nearest. A plane rule left with no face is refused: the inlet
    would be lost, and the run would answer another question."""
    import numpy as np

    faces, extent = faces_of(mesh)
    planes = [rule for rule in rules if rule.kind == "plane"]
    owner: dict[int, Rule] = {}
    for i, f in faces.items():
        matched = [
            rule
            for rule in planes
            if float(np.dot(rule.normal, f.normal)) >= ANGLE_COS
            and abs(float(np.dot(rule.normal, f.centroid)) - rule.offset)
            <= PLANE_TOLERANCE * extent
        ]
        if matched:
            owner[i] = min(
                matched,
                key=lambda r: float(np.linalg.norm(np.subtract(f.centroid, r.centre))),
            )
    out: dict[str, list[int]] = {}
    for rule in planes:
        found = sorted(i for i, r in owner.items() if r is rule)
        if not found:
            raise OperationInvalid(
                "a boundary plane is gone from the rebuilt part: the variables moved it",
                details={"tag": rule.tag},
            )
        out[rule.tag] = found
    for rule in rules:
        if rule.kind == "rest":
            out[rule.tag] = sorted(set(faces) - set(owner))
    return out
