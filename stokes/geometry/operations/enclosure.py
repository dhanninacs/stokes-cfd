"""The ``enclosure`` operation: a box, cylinder or sphere around the bodies, with per-side
margins, optional symmetry half-models and the bodies cut out (ADR-0021 decision 6.2).

The primitive sits on the bodies' bounding box: with ``relative`` (the default) each side's
margin is its ``factors`` entry times the bodies' extent along that axis, otherwise it is that
side's absolute ``margins`` length. Both are ordered ``-x, +x, -y, +y, -z, +z``. ``symmetry``
holds the positive half at up to three planes through the bodies' bounding-box centre.
``subtract`` (the default) cuts the bodies out, so one run gives the fluid domain of an
external-flow case; without it the primitive alone is the output.

The output's faces are tagged by their plane: the face whose outward normal is opposite to
``flow`` is ``inlet``, the one along it ``outlet``, a face on a symmetry plane ``symmetry``, the
``-z`` face ``ground`` when ``ground``, and the rest of the enclosure's own faces ``far_field``
(the cylinder's curved side and the sphere are far field throughout); the bodies' wetted faces,
found through the cut's history, are ``walls`` when ``tag_walls``.

The report carries the volume, the bounding box and the blockage ratio (the bodies' bounding-box
area normal to the flow over the enclosure's cross-section), with a warning above 1 %.

OCP traps kept: ``Bnd_Box.Get`` fails, so ``CornerMin``/``CornerMax``; the booleans have
``IsDone`` and no ``HasErrors``; ``BRepAdaptor_Surface.Plane`` is only valid on a planar face.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from stokes.geometry.errors import OperationFailed, OperationInvalid
from stokes.geometry.operations.base import MM_PER_M, OpContext, OpInput, OpOutput, OpResult

SHAPES = ("box", "cylinder", "sphere")
"""The enclosure kinds the ``shape`` parameter names."""
AXIS_NAMES = ("x", "y", "z")
"""Coordinate indices, for messages and the report."""
AXES = {"x": 0, "y": 1, "z": 2}
"""The axis names ``axis`` and ``symmetry`` use, to a coordinate index."""
FLOWS = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}
"""The ``flow`` directions to a unit vector."""
MINUS_Z = 4
"""The ``-z`` side's index in the six-value ``factors`` and ``margins`` order."""
ALIGNED = 0.999
"""How close to parallel a face normal must be to count as along an axis."""
PLANE_TOLERANCE = 1e-6
"""Millimetres: how close to a symmetry plane a face's plane must be (a nanometre, so exact)."""
BLOCKAGE_LIMIT = 0.01
"""The blockage ratio above which the run warns (ADR-0021 decision 6.2)."""


@dataclass(frozen=True)
class _Settings:
    """The parameters of one run, converted and validated."""

    kind: str
    relative: bool
    factors: tuple[float, ...]
    margins: tuple[float, ...]  # mm
    flow_name: str
    flow: tuple[float, float, float]
    axis: int
    symmetry: tuple[int, ...]
    ground: bool
    subtract: bool
    fuzzy: float  # mm
    tag_walls: bool


def run(ctx: OpContext) -> OpResult:
    """Build the enclosure the parameters describe, cut it by the bodies and tag its faces."""
    if not ctx.inputs:
        raise OperationInvalid("an enclosure needs at least one body")
    settings = _settings(ctx)
    ctx.progress("build", 0.0, f"measuring {len(ctx.inputs)} bodies")
    before = [ctx.check(item.shape) for item in ctx.inputs]
    low, high = _bounds([item.shape for item in ctx.inputs])
    centre = [(low[i] + high[i]) / 2.0 for i in range(3)]
    margins = _margins(settings, [high[i] - low[i] for i in range(3)])
    corners = _corners(low, high, margins)
    shape = _primitive(settings, low, high, margins, centre)
    ctx.progress("build", 0.2, f"built the {settings.kind} around the bodies")
    planes = tuple((axis, centre[axis]) for axis in settings.symmetry)
    for index, (axis, at) in enumerate(planes):
        shape = _half_space(ctx, shape, axis, at, 0.3 + 0.1 * index)
    history = None
    body_ids: list[int] = []
    if settings.subtract:
        shape, history = _subtract(ctx, shape, ctx.inputs, settings.fuzzy)
        body_ids = _body_faces(history, ctx.inputs, shape)
    after = ctx.check(shape)
    if not after["faces"]:
        raise OperationFailed("the enclosure left nothing", details={"shape": settings.kind})
    tags = _tags(shape, settings, planes, body_ids)
    blockage = _blockage(settings, corners, low, high)
    warnings = [f"blockage above 1 %: {blockage:.1%}"] if blockage > BLOCKAGE_LIMIT else []
    report = {
        "shape": settings.kind,
        "relative": settings.relative,
        "subtract": settings.subtract,
        "flow": settings.flow_name,
        "axis": AXIS_NAMES[settings.axis],
        "symmetry": [AXIS_NAMES[axis] for axis in settings.symmetry],
        "ground": settings.ground,
        "margins": [value / MM_PER_M for value in margins],
        "volume": after["volume"],
        "bbox": after["bbox"],
        "blockage": blockage,
        "check_before": before,
        "check_after": after,
    }
    output = OpOutput("enclosure", shape, history, tags, report={"check_after": after})
    ctx.progress("tag", 1.0, f"enclosure done: {after['faces']} faces, {len(tags)} tags")
    return OpResult([output], report=report, warnings=warnings)


# ------------------------------------------------------------------------------ parameters
def _settings(ctx: OpContext) -> _Settings:
    """The run's parameters, converted to mm and validated."""
    kind = str(ctx.params.get("shape", "box"))
    if kind not in SHAPES:
        raise OperationInvalid(f"unknown enclosure shape {kind!r}", details={"shape": kind})
    flow_name = str(ctx.params.get("flow", "+x"))
    if flow_name not in FLOWS:
        raise OperationInvalid(f"unknown flow direction {flow_name!r}", details={"flow": flow_name})
    axis = AXES.get(str(ctx.params.get("axis", "x")))
    if axis is None:
        raise OperationInvalid(f"unknown cylinder axis {ctx.params.get('axis')!r}")
    fuzzy = ctx.mm(float(ctx.params.get("fuzzy", 0.0) or 0.0))
    if fuzzy < 0.0:
        raise OperationInvalid("the fuzzy tolerance cannot be negative")
    factors = _six(ctx.params, "factors")
    if any(value < 0.0 for value in factors):
        raise OperationInvalid("the margin factors cannot be negative")
    margins = tuple(ctx.mm(value) for value in _six(ctx.params, "margins"))
    if any(value < 0.0 for value in margins):
        raise OperationInvalid("the margins cannot be negative")
    return _Settings(
        kind=kind,
        relative=bool(ctx.params.get("relative", True)),
        factors=factors,
        margins=margins,
        flow_name=flow_name,
        flow=FLOWS[flow_name],
        axis=axis,
        symmetry=tuple(_planes(ctx.params.get("symmetry"))),
        ground=bool(ctx.params.get("ground", False)),
        subtract=bool(ctx.params.get("subtract", True)),
        fuzzy=fuzzy,
        tag_walls=bool(ctx.params.get("tag_walls", True)),
    )


def _six(params: dict[str, Any], name: str) -> tuple[float, ...]:  # Any: JSON values
    """A six-value parameter: -x, +x, -y, +y, -z and +z."""
    raw = params.get(name)
    if not isinstance(raw, list | tuple) or len(raw) != 6:
        raise OperationInvalid(
            f"{name} must be six values: -x, +x, -y, +y, -z and +z",
            details={"parameter": name},
        )
    return tuple(float(value) for value in raw)


def _planes(raw: Any) -> list[int]:  # Any: JSON
    """The symmetry planes as coordinate indices; the model validates them too."""
    if raw is None:
        return []
    if not isinstance(raw, list | tuple):
        raise OperationInvalid("symmetry must be a list of x, y and z", details={"symmetry": raw})
    found: list[int] = []
    for value in raw:
        axis = AXES.get(str(value))
        if axis is None:
            raise OperationInvalid(f"a symmetry plane must be x, y or z; got {value!r}")
        if axis in found:
            raise OperationInvalid(f"the {AXIS_NAMES[axis]} symmetry plane is named twice")
        found.append(axis)
    return found


def _margins(settings: _Settings, extent: Sequence[float]) -> list[float]:
    """The margin of each of the six sides, in mm.

    A ground face's ``-z`` margin is ignored when the margins are relative: the floor sits at
    the bodies' lowest point, as ADR-0021 decision 6.2 asks.
    """
    found: list[float] = []
    for side in range(6):
        if not settings.relative:
            found.append(settings.margins[side])
        elif settings.ground and side == MINUS_Z:
            found.append(0.0)
        else:
            found.append(settings.factors[side] * extent[side // 2])
    return found


def _bounds(shapes: Sequence[Any]) -> tuple[list[float], list[float]]:  # Any: OCP shapes
    """The bounding box of every shape together, in mm; the box's own gap is dropped, so a
    margin of 3 is exactly three times the extent."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    low = [math.inf] * 3
    high = [-math.inf] * 3
    for shape in shapes:
        box = Bnd_Box()  # Any: OCP Bnd_Box
        box.SetGap(0.0)
        BRepBndLib.Add_s(shape, box, True)
        if box.IsVoid():
            raise OperationInvalid("a body of the enclosure has no bounding box")
        minimum, maximum = box.CornerMin(), box.CornerMax()  # Bnd_Box.Get fails in OCP 8.0.1
        for index, (a, b) in enumerate(
            ((minimum.X(), maximum.X()), (minimum.Y(), maximum.Y()), (minimum.Z(), maximum.Z()))
        ):
            low[index] = min(low[index], float(a))
            high[index] = max(high[index], float(b))
    return low, high


def _corners(
    low: Sequence[float], high: Sequence[float], margins: Sequence[float]
) -> tuple[list[float], list[float]]:
    """The enclosure's lowest and highest corner along each axis, from the bodies' box."""
    lo = [low[axis] - margins[2 * axis] for axis in range(3)]
    hi = [high[axis] + margins[2 * axis + 1] for axis in range(3)]
    return lo, hi


# ------------------------------------------------------------------------------ geometry
def _primitive(
    settings: _Settings,
    low: Sequence[float],
    high: Sequence[float],
    margins: Sequence[float],
    centre: Sequence[float],
) -> Any:  # Any: OCP TopoDS_Shape in mm
    """The box, cylinder or sphere the settings ask for, in mm."""
    from OCP.BRepPrimAPI import (
        BRepPrimAPI_MakeBox,
        BRepPrimAPI_MakeCylinder,
        BRepPrimAPI_MakeSphere,
    )
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    lo, hi = _corners(low, high, margins)
    size = [hi[i] - lo[i] for i in range(3)]
    if settings.kind == "sphere":
        radius = 0.5 * math.dist(low, high) + max(margins)
        if radius <= 0.0:
            raise OperationInvalid("the sphere enclosure has no radius")
        maker: Any = BRepPrimAPI_MakeSphere(gp_Pnt(*centre), radius)  # Any: an OCP maker
    elif settings.kind == "cylinder":
        axis = settings.axis
        lateral = [i for i in range(3) if i != axis]
        radius = 0.5 * max(high[i] - low[i] for i in lateral) + max(
            margins[2 * i + side] for i in lateral for side in (0, 1)
        )
        if radius <= 0.0 or size[axis] <= 0.0:
            raise OperationInvalid(
                "the cylinder enclosure has no radius or no length",
                details={"radius": radius, "length": size[axis]},
            )
        origin = [centre[i] for i in range(3)]
        origin[axis] = lo[axis]
        direction = gp_Dir(*[1.0 if i == axis else 0.0 for i in range(3)])
        maker = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*origin), direction), radius, size[axis])
    else:
        if any(value <= 0.0 for value in size):
            raise OperationInvalid(
                "the box enclosure is flat: every axis needs a positive size",
                details={"size": size},
            )
        maker = BRepPrimAPI_MakeBox(gp_Pnt(*lo), size[0], size[1], size[2])
    maker.Build()
    if not maker.IsDone():
        raise OperationFailed(
            f"OCCT could not build the {settings.kind}", details={"shape": settings.kind}
        )
    return maker.Shape()


def _half_space(
    ctx: OpContext, shape: Any, axis: int, at: float, fraction: float
) -> Any:  # Any: OCP shapes in mm
    """The positive half of ``shape`` at the plane ``axis = at``: a common with a half-space box
    ten times the enclosure's size."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    low, high = _bounds([shape])
    size = max(high[i] - low[i] for i in range(3))
    if size <= 0.0:
        raise OperationFailed("the enclosure has no extent to cut at a symmetry plane")
    lo = [low[i] - 10.0 * size for i in range(3)]
    hi = [high[i] + 10.0 * size for i in range(3)]
    lo[axis] = at
    hi[axis] = at + 10.0 * size
    half = BRepPrimAPI_MakeBox(
        gp_Pnt(lo[0], lo[1], lo[2]), hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    ).Shape()
    common = BRepAlgoAPI_Common(shape, half)
    common.SetToFillHistory(True)
    ctx.progress(
        "symmetry",
        fraction,
        f"keeping the positive half at the {AXIS_NAMES[axis]} symmetry plane",
    )
    common.Build()
    if not common.IsDone():
        raise OperationFailed(
            f"OCCT could not cut the enclosure at the {AXIS_NAMES[axis]} symmetry plane",
            details={"plane": AXIS_NAMES[axis]},
        )
    return common.Shape()


def _subtract(
    ctx: OpContext, shape: Any, inputs: Sequence[OpInput], fuzzy: float
) -> tuple[Any, Any]:  # Any: OCP shape and history
    """``shape`` cut by the bodies, with the history that maps their faces."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for item in inputs:
        builder.Add(compound, item.shape)
    cut = BRepAlgoAPI_Cut(shape, compound)
    if fuzzy > 0.0:
        cut.SetFuzzyValue(fuzzy)
    cut.SetToFillHistory(True)
    ctx.progress("subtract", 0.6, f"cutting {len(inputs)} bodies out of the enclosure")
    cut.Build()
    if not cut.IsDone():
        raise OperationFailed(
            "OCCT could not cut the bodies out of the enclosure",
            details={"bodies": [item.part for item in inputs]},
        )
    return cut.Shape(), cut.History()


# ------------------------------------------------------------------------------ tags
def _body_faces(
    history: Any, inputs: Sequence[OpInput], shape: Any
) -> list[int]:  # Any: OCP history and shape
    """The ids of the output faces the bodies' own faces became, through the cut's history."""
    from stokes.geometry.operations.faces import map_history

    mapped = map_history(history, inputs, shape)
    return sorted({index for faces in mapped.values() for ids in faces.values() for index in ids})


def _tags(
    shape: Any, settings: _Settings, planes: Sequence[tuple[int, float]], body_ids: Sequence[int]
) -> dict[str, list[Any]]:
    """The output's faces under the tag each belongs to (``OpOutput.tags``)."""
    from stokes.geometry.operations.faces import face_list

    faces = face_list(shape)
    wet = set(body_ids)
    tags: dict[str, list[Any]] = {}
    for index, face in enumerate(faces):
        if index in wet:
            continue
        name = _classify(face, settings, planes)
        tags.setdefault(name, []).append(face)
    # A face the cut made from a body's own face carries no tag of its own: it is a wall. A body
    # face lying exactly on the enclosure's boundary is removed by the cut, not reported here
    # (the floor of a grounded enclosure keeps its `ground` face and gains the body's outline).
    if settings.tag_walls and wet:
        tags["walls"] = [faces[index] for index in sorted(wet)]
    return tags


def _classify(face: Any, settings: _Settings, planes: Sequence[tuple[int, float]]) -> str:
    """The tag of one of the enclosure's own faces, by its plane and the outward normal.

    A curved face is the cylinder's side or the sphere: far field throughout.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopAbs import TopAbs_Orientation

    surface = BRepAdaptor_Surface(face, True)
    if surface.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
        return "far_field"
    plane = surface.Plane()
    direction = plane.Axis().Direction()
    normal = [direction.X(), direction.Y(), direction.Z()]
    if face.Orientation() == TopAbs_Orientation.TopAbs_REVERSED:
        normal = [-value for value in normal]
    at = plane.Location()
    point = (at.X(), at.Y(), at.Z())
    for axis, coordinate in planes:
        if abs(normal[axis]) > ALIGNED and abs(point[axis] - coordinate) <= PLANE_TOLERANCE:
            return "symmetry"
    if settings.ground and normal[2] < -ALIGNED:
        return "ground"
    along = sum(value * flow for value, flow in zip(normal, settings.flow, strict=True))
    if along < -ALIGNED:
        return "inlet"
    if along > ALIGNED:
        return "outlet"
    return "far_field"


# ------------------------------------------------------------------------------ report
def _blockage(
    settings: _Settings,
    corners: tuple[Sequence[float], Sequence[float]],
    low: Sequence[float],
    high: Sequence[float],
) -> float:
    """The bodies' bounding-box area normal to the flow over the enclosure's cross-section."""
    axis = next(i for i, value in enumerate(settings.flow) if value != 0.0)
    section = 1.0
    bodies = 1.0
    lo, hi = corners
    for i in range(3):
        if i != axis:
            section *= hi[i] - lo[i]
            bodies *= high[i] - low[i]
    if section <= 0.0:
        return 0.0
    return bodies / section
