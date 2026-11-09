"""The ``primitive`` operation: a box, cylinder or sphere placed in the model (ADR-0021
decision 6.3, as ADR-0020 decision 5 describes it). It reads no parts; its one output is the
solid, named after its shape.

``size`` and ``position`` are three SI lengths each (see the node type in
:mod:`stokes.geometry.model`): a box takes its edge lengths and its lowest corner, a cylinder its
radius and length and the centre of its base, on ``axis``, a sphere its radius and centre.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.errors import OperationFailed, OperationInvalid
from stokes.geometry.operations.base import OpContext, OpOutput, OpResult

_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def run(ctx: OpContext) -> OpResult:
    """Build the primitive the parameters describe."""
    from OCP.BRepPrimAPI import (
        BRepPrimAPI_MakeBox,
        BRepPrimAPI_MakeCylinder,
        BRepPrimAPI_MakeSphere,
    )
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    kind = str(ctx.params.get("shape", "box"))
    size = _three(ctx, "size")
    x, y, z = _three(ctx, "position")
    axis = _AXES.get(str(ctx.params.get("axis", "z")), _AXES["z"])
    ctx.progress("build", 0.0, f"building the {kind}")
    maker: Any  # Any: an OCP BRepPrimAPI maker
    if kind == "box":
        _positive(size, "the box's edge lengths")
        maker = BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), size[0], size[1], size[2])
    elif kind == "cylinder":
        _positive(size[:2], "the cylinder's radius and length")
        maker = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(*axis)), size[0], size[1])
    elif kind == "sphere":
        _positive(size[:1], "the sphere's radius")
        maker = BRepPrimAPI_MakeSphere(gp_Pnt(x, y, z), size[0])
    else:
        raise OperationInvalid(f"unknown primitive {kind!r}", details={"shape": kind})
    maker.Build()
    if not maker.IsDone():
        raise OperationFailed(f"OCCT could not build the {kind}", details={"shape": kind})
    shape = maker.Shape()
    after = ctx.check(shape)
    ctx.progress("build", 1.0, f"built the {kind}")
    return OpResult(
        [OpOutput(kind, shape, None, report={"check_after": after})],
        report={"shape": kind, "check_after": after},
    )


def _three(ctx: OpContext, name: str) -> tuple[float, float, float]:
    """A three-length parameter in mm."""
    raw = ctx.params.get(name)
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise OperationInvalid(f"{name} must be three lengths", details={"parameter": name})
    a, b, c = (ctx.mm(float(v)) for v in raw)
    return a, b, c


def _positive(values: tuple[float, ...], what: str) -> None:
    if not all(v > 0.0 for v in values):
        raise OperationInvalid(f"{what} must be positive", details={"values": list(values)})
