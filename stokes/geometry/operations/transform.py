"""The ``transform`` operation: move, rotate, mirror or scale B-rep bodies (ADR-0021 decision 8).

The B-rep counterpart of ``implicit_transform``, with the same parameters and meaning: ``kind`` is
``translate`` (by ``offset``), ``rotate`` (by ``angle`` about the ``axis`` through ``centre``,
counter-clockwise looking down the axis), ``mirror`` (in the plane through ``centre`` whose normal
is ``axis``) or ``scale`` (by the uniform ``factor`` about ``centre``). Lengths arrive in SI and
are turned into the millimetres the shapes are in; the angle is in radians.

Every input becomes one output of its own, named after it: a transform changes where a body is,
not what it is. ``BRepBuilderAPI_Transform`` runs with ``Copy`` set, so each output is a B-rep of
its own (a mirror and a scale need the copy anyway: OCCT rebuilds the geometry for a negative or
scaling transformation), and the copy keeps the topology: the output's faces, edges and vertices
are the input's in the same order. The history says so explicitly, sub-shape for sub-shape, so
every tag on a face, an edge or a vertex follows the body.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

import math
from typing import Any

from stokes.geometry.errors import OperationFailed, OperationInvalid
from stokes.geometry.operations.base import OpContext, OpOutput, OpResult

KINDS = ("translate", "rotate", "mirror", "scale")
_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
_PAST = {"translate": "moved", "rotate": "rotated", "mirror": "mirrored", "scale": "scaled"}


def run(ctx: OpContext) -> OpResult:
    """Transform every input by the one transformation the parameters describe."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    kind = str(ctx.params.get("kind", "translate"))
    trsf = transformation(ctx, kind)
    before = [ctx.check(item.shape) for item in ctx.inputs]
    outputs: list[OpOutput] = []
    count = len(ctx.inputs)
    for index, item in enumerate(ctx.inputs):
        ctx.progress("transform", index / max(count, 1), f"{_PAST[kind]} {item.name}")
        maker = BRepBuilderAPI_Transform(item.shape, trsf, True)
        if not maker.IsDone():
            raise OperationFailed(
                f"OCCT could not transform {item.name}", details={"part": item.part, "kind": kind}
            )
        shape = maker.Shape()
        after = ctx.check(shape)
        outputs.append(
            OpOutput(item.name, shape, _history(item.shape, shape), report={"check_after": after})
        )
    after_all = [output.report["check_after"] for output in outputs]
    warnings = [
        f"the {_PAST[kind]} {output.name} is not a valid B-rep"
        for output in outputs
        if not output.report["check_after"]["valid"]
    ]
    report = {"kind": kind, "check_before": before, "check_after": after_all}
    ctx.progress("transform", 1.0, f"{_PAST[kind]} {count} bod{'y' if count == 1 else 'ies'}")
    return OpResult(outputs, report=report, warnings=warnings)


def transformation(ctx: OpContext, kind: str) -> Any:  # Any: OCP gp_Trsf
    """The ``gp_Trsf`` of ``kind`` from the parameters, in millimetres."""
    from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    if kind not in KINDS:
        raise OperationInvalid(
            f"kind must be one of {', '.join(KINDS)}; got {kind!r}", details={"kind": kind}
        )
    axis_name = str(ctx.params.get("axis", "z"))
    axis = _AXES.get(axis_name)
    if axis is None:
        raise OperationInvalid(f"unknown axis {axis_name!r}", details={"axis": axis_name})
    trsf = gp_Trsf()
    if kind == "translate":
        trsf.SetTranslation(gp_Vec(*_three(ctx, "offset")))
        return trsf
    centre = gp_Pnt(*_three(ctx, "centre"))
    if kind == "rotate":
        angle = _number(ctx, "angle", 0.0)
        trsf.SetRotation(gp_Ax1(centre, gp_Dir(*axis)), angle)
    elif kind == "mirror":
        # A gp_Ax2 mirrors in the plane through its location normal to its main direction.
        trsf.SetMirror(gp_Ax2(centre, gp_Dir(*axis)))
    else:
        factor = _number(ctx, "factor", 1.0)
        if not factor > 0.0:
            raise OperationInvalid(
                "a scale factor must be positive", details={"parameter": "factor"}
            )
        trsf.SetScale(centre, factor)
    return trsf


def _three(ctx: OpContext, name: str) -> tuple[float, float, float]:
    """A vector parameter (three SI lengths) in mm."""
    raw = ctx.params.get(name)
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise OperationInvalid(f"{name} must be three lengths", details={"parameter": name})
    values = [_finite(v, name) for v in raw]
    a, b, c = (ctx.mm(v) for v in values)
    return a, b, c


def _number(ctx: OpContext, name: str, default: float) -> float:
    raw = ctx.params.get(name)
    return default if raw is None else _finite(raw, name)


def _finite(value: Any, name: str) -> float:  # Any: JSON
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise OperationInvalid(f"{name} must be a finite number", details={"parameter": name})
    return float(value)


def _history(before: Any, after: Any) -> Any:  # Any: OCP shapes in, BRepTools_History out
    """Each face, edge and vertex of ``before`` to the one at the same place in ``after``.

    The copy keeps the topology, so the face walk and the edge and vertex maps number both shapes
    alike (:func:`stokes.geometry.operations.faces.subshape_list`); a count that differs would
    mean it did not, and the run fails rather than carrying tags to the wrong faces.
    """
    from OCP.BRepTools import BRepTools_History

    from stokes.geometry.operations.faces import subshape_list

    history = BRepTools_History()
    for kind in ("face", "edge", "vertex"):
        old, new = subshape_list(before, kind), subshape_list(after, kind)
        if len(old) != len(new):
            raise OperationFailed(
                f"the transform changed the number of {kind}s ({len(old)} to {len(new)})"
            )
        for initial, modified in zip(old, new, strict=True):
            history.AddModified(initial, modified)
    return history
