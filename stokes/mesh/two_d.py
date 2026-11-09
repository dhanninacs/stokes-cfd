"""The 2D mode's rules, as pure functions over plain numbers (ADR-0031).

OpenFOAM has no 2D solver: a 2D run is a 3D mesh **one cell thick** whose two faces normal to the
thickness are `empty` (planar) or `wedge` (axisymmetric). This module holds what the mesh drivers
and the snapshot builders need to agree on: which axis is the normal, which box sides are the front
and back, which patch names are reserved, and how a box becomes a wedge. It reads no tree and
imports nothing heavy, so the drivers and the tests share it.

## The record

A snapshot carries the mode as a plain dict (`TwoD.record`), empty when 2D is off, so a 3D
snapshot, its basis and its dictionaries are byte-identical to what they were before 2D existed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from stokes.mesh.errors import MeshInvalid

#: The `two_d` setting's choices.
MODES: tuple[str, ...] = ("off", "planar", "axisymmetric")

#: The `plane` setting's choices: the two in-plane axes. For `axisymmetric` the first is the
#: symmetry axis and the second the radius.
PLANES: tuple[str, ...] = ("xy", "xz", "yz")

#: The planar front/back patch, one patch for both sides (the OF tutorials' `frontAndBack`).
FRONT_AND_BACK = "frontAndBack"
#: The two wedge sides, and the collapsed on-axis side of an axisymmetric block.
FRONT = "front"
BACK = "back"
AXIS = "axis"

#: Patch names the mesher owns in 2D: a tag with one of these names is refused, because the
#: patch it would become is not the user's to assign (ADR-0031 decision 6).
RESERVED: tuple[str, ...] = (FRONT_AND_BACK, FRONT, BACK, AXIS)

#: The widest wedge accepted, in degrees. OpenFOAM's guidance is "a few degrees"; the tutorials use
#: 1 to 5. Above 10 the wedge's chord error starts to show in the answer.
MAX_WEDGE_ANGLE = 10.0

_AXES = "xyz"


@dataclass(frozen=True)
class TwoD:
    """The 2D settings of one mesh, validated."""

    mode: str
    plane: str
    #: Metres; 0 keeps the domain's own extent across the plane.
    thickness: float = 0.0
    #: Degrees, the wedge's full angle (axisymmetric only).
    wedge_angle: float = 5.0

    @property
    def planar(self) -> bool:
        return self.mode == "planar"

    @property
    def axisymmetric(self) -> bool:
        return self.mode == "axisymmetric"

    @property
    def axis(self) -> int:
        """The first in-plane axis (0, 1, 2): the symmetry axis when axisymmetric."""
        return _AXES.index(self.plane[0])

    @property
    def radial(self) -> int:
        """The second in-plane axis: the radius when axisymmetric."""
        return _AXES.index(self.plane[1])

    @property
    def normal(self) -> int:
        """The axis across the plane, which gets one cell (or the wedge's angle)."""
        return 3 - self.axis - self.radial

    def normal_sides(self) -> tuple[str, str]:
        """The two box sides normal to the plane, low then high (`zmin`, `zmax` for `xy`)."""
        letter = _AXES[self.normal]
        return (f"{letter}min", f"{letter}max")

    def radial_low_side(self) -> str:
        """The box side at the smallest radius, which collapses onto the axis at radius 0."""
        return f"{_AXES[self.radial]}min"

    def record(self) -> dict[str, Any]:
        """The plain dict a snapshot carries and the basis hashes."""
        out: dict[str, Any] = {"mode": self.mode, "plane": self.plane}
        if self.planar:
            out["thickness"] = float(self.thickness)
        else:
            out["wedge_angle"] = float(self.wedge_angle)
        return out


def from_settings(own: dict[str, Any]) -> TwoD | None:
    """The 2D settings from a settings node's unwrapped parameters, or None when 2D is off.

    A project saved before the parameters existed reads them as missing, which is `off`.
    """
    mode = str(own.get("two_d") or "off")
    if mode == "off":
        return None
    if mode not in MODES:
        raise MeshInvalid(f"unknown 2D mode {mode!r}; expected one of {', '.join(MODES)}")
    plane = str(own.get("plane") or "xy")
    if plane not in PLANES:
        raise MeshInvalid(f"unknown plane {plane!r}; expected one of {', '.join(PLANES)}")
    thickness = float(own.get("thickness") or 0.0)
    if not math.isfinite(thickness) or thickness < 0:
        raise MeshInvalid(f"the 2D thickness may not be negative; got {thickness}")
    angle = float(own.get("wedge_angle") or 5.0)
    if not 0 < angle <= MAX_WEDGE_ANGLE:
        raise MeshInvalid(
            f"the wedge angle must be above 0 and at most {MAX_WEDGE_ANGLE} degrees; got {angle}"
        )
    return TwoD(mode=mode, plane=plane, thickness=thickness, wedge_angle=angle)


def from_record(record: dict[str, Any] | None) -> TwoD | None:
    """The inverse of `TwoD.record`; None for an empty record (3D)."""
    if not record:
        return None
    return TwoD(
        mode=str(record["mode"]),
        plane=str(record["plane"]),
        thickness=float(record.get("thickness", 0.0)),
        wedge_angle=float(record.get("wedge_angle", 5.0)),
    )


def refuse_reserved(names: Any) -> None:
    """Refuse a tag whose name is a patch the 2D mesher owns."""
    taken = sorted(set(names) & set(RESERVED))
    if taken:
        raise MeshInvalid(
            f"a tag called {taken[0]!r} is in the way: in 2D that name is the front/back patch "
            "the mesher makes, so give the tag another name",
            details={"code": "mesh.invalid", "name": taken[0]},
        )


def with_thickness(
    bbox: tuple[float, float, float, float, float, float], two_d: TwoD
) -> tuple[float, float, float, float, float, float]:
    """The box with its normal extent set to ``thickness`` about its mid-plane (planar only).

    A zero thickness, or an axisymmetric mode (whose normal extent is the wedge's), keeps the box.
    """
    if not two_d.planar or two_d.thickness <= 0:
        return bbox
    lo, hi = list(bbox[:3]), list(bbox[3:])
    n = two_d.normal
    mid = (lo[n] + hi[n]) / 2
    lo[n], hi[n] = mid - two_d.thickness / 2, mid + two_d.thickness / 2
    return (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])


def check_radius(bbox: tuple[float, ...], two_d: TwoD, tol: float) -> None:
    """An axisymmetric domain may not reach a negative radius."""
    if two_d.axisymmetric and bbox[two_d.radial] < -tol:
        letter = _AXES[two_d.radial]
        raise MeshInvalid(
            f"an axisymmetric domain lies at {letter} >= 0 ({letter} is the radius for plane "
            f"{two_d.plane}); this one starts at {letter} = {bbox[two_d.radial]:.6g}",
            details={"code": "mesh.invalid", "radial_min": bbox[two_d.radial]},
        )


def wedge_vertex(
    point: tuple[float, float, float], high: bool, two_d: TwoD
) -> tuple[float, float, float]:
    """A box corner moved onto the wedge: rotated by ±angle/2 about the axis.

    The corner's radial coordinate is its radius. The low side of the normal axis goes to
    -angle/2 (`back`), the high side to +angle/2 (`front`), exactly as the OF14 wedge tutorials lay
    their blocks out (`multiphaseEuler/bubblePipe`). A corner at radius 0 lands on the axis for
    both sides, and blockMesh merges the two.
    """
    half = math.radians(two_d.wedge_angle) / 2
    r = point[two_d.radial]
    out = list(point)
    out[two_d.radial] = r * math.cos(half)
    # `+ 0.0` turns the on-axis corner's `-0.0` into `0.0`, so the dictionary has no negative zero.
    out[two_d.normal] = (1.0 if high else -1.0) * r * math.sin(half) + 0.0
    return (out[0], out[1], out[2])
