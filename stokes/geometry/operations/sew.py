"""The ``sew`` operation: one shape out of the inputs' faces, sewn along their common edges
(ADR-0021 decision 5; T-027).

One output from **all** the inputs, named ``<first input>_sewn``. ``BRepBuilderAPI_Sewing`` runs
at ``tolerance`` with its face, same-parameter and floating-edge modes on and
``non_manifold`` as its non-manifold mode. With ``make_solid`` (the default) every closed shell of
the result becomes a solid (``BRepBuilderAPI_MakeSolid``, fixed by ``ShapeFix_Solid``) and the open
ones stay shells, so an unsewn surface model comes back ready to be a body. The report counts the
free edges before and after the seam, the shells and solids of the result, and what the tool found
(``degenerated``, ``multiple_edges``).

The free-edge counts are :func:`stokes.geometry.operations.repair_common.free_edges`, which walks
the faces of the compound: the count in :mod:`stokes.geometry.brep` reports 0 for an unsewn
compound of loose faces and would hide exactly the gap the seam closed (see that module's
docstring).

OCP traps kept: ``BRepBuilderAPI_Sewing.Modified`` gives back one shape, not a list, and the tool
has no ``Generated`` or ``IsDeleted``, so ``repair_common.history_from_sewing`` turns it into a
``BRepTools_History``; ``BRep_Tool.IsClosed_s`` wants a shell.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.operations import repair_common as rm
from stokes.geometry.operations.base import MM_PER_M, OpContext, OpOutput, OpResult

DEFAULT_TOLERANCE = 1e-5
"""Metres: the gap below which two faces are sewn when ``tolerance`` is not given."""


def run(ctx: OpContext) -> OpResult:
    """Sew the inputs into one shape, and make a solid of every closed shell it leaves."""
    tolerance = rm.length(ctx, "tolerance", DEFAULT_TOLERANCE)
    make_solid = bool(ctx.params.get("make_solid", True))
    non_manifold = bool(ctx.params.get("non_manifold", False))
    before = [ctx.check(item.shape) for item in ctx.inputs]
    free_before = rm.free_edges(rm.joined([item.shape for item in ctx.inputs]))
    ctx.progress("sew", 0.1, f"sewing {len(ctx.inputs)} part(s) at {tolerance:.3g} mm")
    tool = rm.sew([item.shape for item in ctx.inputs], tolerance, non_manifold)
    result = _bodies(tool.SewedShape(), make_solid)
    ctx.progress("sew", 0.8, "checked the shells of the sewn shape")
    free_after = rm.free_edges(result)
    after = ctx.check(result)
    report = {
        "tolerance": tolerance / MM_PER_M,
        "make_solid": make_solid,
        "non_manifold": non_manifold,
        "free_edges_before": free_before,
        "free_edges_after": free_after,
        "shells": len(rm.subshapes(result, "SHELL")),
        "solids": len(rm.subshapes(result, "SOLID")),
        "degenerated": int(tool.NbDegeneratedShapes()),
        "multiple_edges": int(tool.NbMultipleEdges()),
        "check_before": before,
        "check_after": after,
    }
    output = OpOutput(
        f"{ctx.inputs[0].name}_sewn",
        result,
        rm.history_from_sewing(tool, ctx.inputs),
        report={"check_after": after},
    )
    ctx.progress("sew", 1.0, f"sewn: {free_after} free edge(s) left")
    return OpResult([output], report=report)


def _bodies(shape: Any, make_solid: bool) -> Any:  # Any: OCP shapes in and out
    """``shape`` with every closed shell made a solid, when ``make_solid``; open shells stay.

    One body is returned as it is; several become a compound. ``extract_fluid`` orients a cavity
    shell the same way, and ``ShapeFix_Solid`` fixes the solid the maker builds.
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS

    if not make_solid:
        return shape
    parts: list[Any] = []  # Any: TopoDS_Shape
    for shell in rm.subshapes(shape, "SHELL"):
        if BRep_Tool.IsClosed_s(TopoDS.Shell(shell)):
            parts.append(rm.make_solid(shell))
        else:
            parts.append(shell)
    if not parts:
        return shape
    if len(parts) == 1:
        return parts[0]
    return rm.compound(parts)
