"""What shape a part is, so a mesher knows whether it can mesh it (generic blockMesh, T-180).

**Why this exists.** Our blockMesh driver wrote one hex block over the part's **bounding box**
and said nothing. A pipe came out a box; a user could mesh a sphere, get a cube, and not notice
until the physics was wrong. The docstring even claimed *"that is the whole of its ability"* -
**which is false about blockMesh**: OpenFOAM's own guide says it decomposes a domain into *"1 or
more"* blocks whose edges *"can be straight lines, arcs or splines"*, and its
`incompressibleFluid/cylinder` tutorial meshes a cylinder with 7 blocks and 12 arcs.

So the defect was never blockMesh's limit. It was that **we never asked what the part was**, and
therefore never knew whether a box was a fair description of it.

## What this module does, and what it deliberately does not

It **classifies** - and refusing is a first-class answer:

    BOX         six planar faces, axis-aligned          one block (what we already write)
    EXTRUDED    a constant cross-section along an axis  profile x extrusion
    CYLINDER    a circular bore or boss                 an O-grid
    OTHER       anything else                           REFUSED, with a reason

**It never falls back to the box.** That is the point of the module: a silent box for a shape
that is not a box is the defect, and a refusal naming snappyHexMesh is a better answer than a
mesh the user has to notice is wrong.

## Where the reading happens, and why not here

**This module holds the vocabulary and the rules; it does not touch the B-rep.** `stokes.mesh` is
headless - OCP may only be imported inside `stokes.geometry` (CLAUDE.md rule 5), and
`tools/check_forbidden_imports.py` refused my first version, which read the shape here. The reading
lives in `stokes.geometry.shape_report`, which is allowed OCP, and produces one of these records; a
driver consumes the record and needs no geometry library at all.

**This module holds the vocabulary and the rules; it does not touch the B-rep.**
the CAD model tells it *what the shape is*.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from stokes.log import get_logger

log = get_logger(__name__)

#: The analytic surface kinds we act on, spelled as OCP names them. Kept as strings so this module
#: needs no OCP import at module level - it is read by headless code paths.
PLANE = "GeomAbs_Plane"
CYLINDER = "GeomAbs_Cylinder"
CONE = "GeomAbs_Cone"
SPHERE = "GeomAbs_Sphere"
TORUS = "GeomAbs_Torus"


class ShapeClass(enum.Enum):
    """What the part is, as far as meshing goes.

    `OTHER` means "we cannot mesh this, and we say so" rather than falling back to a box.
    """

    BOX = "box"
    EXTRUDED = "extruded"
    CYLINDER = "cylinder"
    OTHER = "other"


class MeshShapeRefused(Exception):
    """The part cannot be meshed by this driver, and here is why.

    **A refusal, never a fallback.** The message names the alternative, because "cannot" without a
    next step is not an answer a user can act on.
    """

    code = "mesh.shape_unsupported"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class ShapeReport:
    """What was found, and what follows from it.

    **The kinds are carried, not just the verdict**, so a refusal can say what it saw ("a sphere and
    two planes") rather than only that it declined. A user who can read the faces can tell whether
    the classifier is wrong about their part.
    """

    kind: ShapeClass
    face_kinds: tuple[str, ...] = ()
    axis: tuple[float, float, float] | None = None
    radius: float = 0.0
    why: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def mesheable(self) -> bool:
        return self.kind is not ShapeClass.OTHER

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "face_kinds": list(self.face_kinds),
            "axis": list(self.axis) if self.axis else None,
            "radius": self.radius,
            "why": self.why,
        }
