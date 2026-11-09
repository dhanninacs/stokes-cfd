"""The ``heal`` operation: fix a body's topology with Shape Healing (ADR-0021 decision 5; T-027).

One output from all the inputs, named ``<first input>_healed``. ``ShapeFix_Shape`` works the whole
shape to ``tolerance`` (its basic precision, also its smallest allowed tolerance) and lets no fix
raise a tolerance above ``max_tolerance``; its solid, shell, face and wire tools keep OCCT's
defaults (all on). With ``small_edges`` set, ``ShapeFix_Wireframe`` merges and drops the edges
below that length; ``BRepLib.SameParameter_s`` then makes every edge's 2D curves agree with its 3D
one. The report counts what the fixers' ``Status*`` flags say they did (``wires_fixed``,
``faces_fixed``, ``shells_fixed``, ``small_edges_dropped``, each 0 or 1: the flag is the count) and
``changed``.

``SetMinTolerance``/``SetMaxTolerance`` only limit the tolerances a *fix* may set; they leave the
tolerances the shape already carries alone. ``ShapeFix_ShapeTolerance.LimitTolerance`` is the
Shape Healing tool that enforces the range on a shape, so the run clamps the result's tolerances to
at most ``max_tolerance`` with it, and the report measures the largest tolerance before and after.
Measured on OCP 8.0.1: a box whose vertices carry 5 mm goes through ``ShapeFix_Shape`` unchanged
and comes out of the clamp at ``max_tolerance``.

OCP traps kept: ``ShapeFix_Shape.Perform`` returns False even when it did fix something (a box
comes back False with ``ShapeExtend_DONE4`` set), so the return value is not an error signal;
``BRepTools.MaxTolerance`` is ``BRep_Tool.MaxTolerance_s``, which wants the sub-shape kind.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.errors import OperationInvalid
from stokes.geometry.operations import repair_common as rm
from stokes.geometry.operations.base import MM_PER_M, OpContext, OpOutput, OpResult

DEFAULT_TOLERANCE = 1e-6
"""Metres: the precision the fixes work to when ``tolerance`` is not given."""
DEFAULT_MAX_TOLERANCE = 1e-3
"""Metres: the largest tolerance a fix may leave behind."""


def run(ctx: OpContext) -> OpResult:
    """Fix the inputs' topology, drop their small edges and clamp their tolerances."""
    from OCP.BRepLib import BRepLib
    from OCP.ShapeExtend import ShapeExtend_Status
    from OCP.ShapeFix import ShapeFix_Shape, ShapeFix_ShapeTolerance, ShapeFix_Wireframe
    from OCP.TopAbs import TopAbs_ShapeEnum

    tolerance = rm.length(ctx, "tolerance", DEFAULT_TOLERANCE)
    max_tolerance = rm.length(ctx, "max_tolerance", DEFAULT_MAX_TOLERANCE)
    small_edges = rm.length(ctx, "small_edges", 0.0)
    if tolerance <= 0.0:
        raise OperationInvalid("tolerance must be positive", details={"parameter": "tolerance"})
    if max_tolerance < tolerance:
        raise OperationInvalid(
            "max_tolerance cannot be below tolerance", details={"parameter": "max_tolerance"}
        )
    before = [ctx.check(item.shape) for item in ctx.inputs]
    shape = rm.joined([item.shape for item in ctx.inputs])
    largest_before = _largest(shape)
    ctx.progress("heal", 0.1, f"fixing {len(ctx.inputs)} part(s) to {tolerance:.3g} mm")
    fixer = ShapeFix_Shape(shape)
    fixer.SetPrecision(tolerance)
    fixer.SetMinTolerance(tolerance)
    fixer.SetMaxTolerance(max_tolerance)
    fixer.Perform()  # False is not a failure: it reports False on shapes it did fix
    result = fixer.Shape()
    dropped = 0
    if small_edges > 0.0:
        ctx.progress("heal", 0.5, f"merging edges below {small_edges:.3g} mm")
        wireframe = ShapeFix_Wireframe(result)
        wireframe.SetPrecision(small_edges)
        wireframe.ModeDropSmallEdges = True
        wireframe.FixSmallEdges()
        result = wireframe.Shape()
        dropped = _count(wireframe.StatusSmallEdges, ShapeExtend_Status.ShapeExtend_DONE1)
    BRepLib.SameParameter_s(result, tolerance, True)
    clamped = bool(
        ShapeFix_ShapeTolerance().LimitTolerance(
            result, 0.0, max_tolerance, TopAbs_ShapeEnum.TopAbs_SHAPE
        )
    )
    history = rm.history_from_reshape(ctx.inputs, fixer.Context())
    after = ctx.check(result)
    report = {
        "tolerance": tolerance / MM_PER_M,
        "max_tolerance": max_tolerance / MM_PER_M,
        "small_edges": small_edges / MM_PER_M,
        "max_tolerance_before": largest_before / MM_PER_M,
        "max_tolerance_after": _largest(result) / MM_PER_M,
        "wires_fixed": _count(fixer.Status, ShapeExtend_Status.ShapeExtend_DONE1)
        or _count(fixer.Status, ShapeExtend_Status.ShapeExtend_DONE2),
        "faces_fixed": _count(fixer.Status, ShapeExtend_Status.ShapeExtend_DONE3),
        "shells_fixed": _count(fixer.Status, ShapeExtend_Status.ShapeExtend_DONE4),
        "small_edges_dropped": dropped,
        "changed": bool(
            _count(fixer.Status, ShapeExtend_Status.ShapeExtend_DONE) or dropped or clamped
        ),
        "check_before": before,
        "check_after": after,
    }
    output = OpOutput(
        f"{ctx.inputs[0].name}_healed", result, history, report={"check_after": after}
    )
    ctx.progress("heal", 1.0, f"healed: {after['faces']} face(s)")
    return OpResult([output], report=report)


def _count(ask: Any, flag: Any) -> int:  # Any: OCP status question, ShapeExtend_Status
    """1 when a fixer's status carries ``flag``, else 0: the flag folded into a count. ``ask`` is
    ``ShapeFix_Shape.Status`` or a named question such as ``StatusSmallEdges``."""
    return int(bool(ask(flag)))


def _largest(shape: Any) -> float:  # Any: OCP TopoDS_Shape; mm out
    """The largest tolerance the shape's sub-shapes carry, in mm."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_ShapeEnum

    return max(
        float(BRep_Tool.MaxTolerance_s(shape, kind))
        for kind in (
            TopAbs_ShapeEnum.TopAbs_VERTEX,
            TopAbs_ShapeEnum.TopAbs_EDGE,
            TopAbs_ShapeEnum.TopAbs_FACE,
        )
    )
