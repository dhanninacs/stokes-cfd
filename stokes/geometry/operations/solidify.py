"""The ``solidify`` operation: build closed solids out of loose faces (ADR-0021 decision 5;
T-027).

One output per solid the run builds, from all the inputs: ``BOPAlgo_MakerVolume`` is asked for the
volume the faces bound, so a surface model an import left as loose faces comes back as a body. The
seam runs first — ``BRepBuilderAPI_Sewing`` at ``tolerance``, as ``sew`` does — and its faces are
the volume maker's arguments with ``SetIntersect(True)``; ``avoid_internal`` (on by default) keeps
the faces between two volumes out of the result, and ``fuzzy`` is the maker's fuzzy value. The
outputs are named ``<first input>_solid``, ``_solid_2``, … and a run that builds no closed volume
fails with ``geometry.op_failed`` "no closed volume could be built". The history is the seam's
followed by the maker's, so a tag on a face that became an output face still finds it.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.errors import OperationFailed
from stokes.geometry.operations import repair_common as rm
from stokes.geometry.operations.base import MM_PER_M, OpContext, OpOutput, OpResult

DEFAULT_TOLERANCE = 1e-5
"""Metres: the gap below which the faces are sewn before the volume is made."""


def run(ctx: OpContext) -> OpResult:
    """Sew the inputs and make a solid of every closed volume their faces bound."""
    import OCP.collections as occ
    from OCP.BOPAlgo import BOPAlgo_MakerVolume
    from OCP.TopoDS import TopoDS

    tolerance = rm.length(ctx, "tolerance", DEFAULT_TOLERANCE)
    fuzzy = rm.length(ctx, "fuzzy", 0.0)
    avoid_internal = bool(ctx.params.get("avoid_internal", True))
    before = [ctx.check(item.shape) for item in ctx.inputs]
    ctx.progress("solidify", 0.1, f"sewing {len(ctx.inputs)} part(s) at {tolerance:.3g} mm")
    tool = rm.sew([item.shape for item in ctx.inputs], tolerance, False)
    arguments = occ.List_TopoDS_Shape()
    for face in rm.faces(tool.SewedShape()):
        arguments.Append(face)
    ctx.progress("solidify", 0.5, "making the volume the faces bound")
    maker: Any = BOPAlgo_MakerVolume()  # Any: an OCP BOPAlgo_MakerVolume
    maker.SetArguments(arguments)
    maker.SetIntersect(True)
    maker.SetAvoidInternalShapes(avoid_internal)
    maker.SetFuzzyValue(fuzzy)
    maker.SetToFillHistory(True)
    maker.Perform()
    if maker.HasErrors():
        raise OperationFailed(
            "OCCT could not build a closed volume from the faces of the inputs",
            details={"faces": arguments.Size()},
        )
    solids = [TopoDS.Solid(solid) for solid in rm.subshapes(maker.Shape(), "SOLID")]
    if not solids:
        raise OperationFailed(
            "no closed volume could be built", details={"faces": arguments.Size()}
        )
    history = rm.history_from_sewing(tool, ctx.inputs)
    history.Merge(maker.History())
    after = ctx.check(maker.Shape())
    first = ctx.inputs[0].name
    outputs: list[OpOutput] = []
    reports: list[dict[str, Any]] = []  # Any: JSON
    for index, solid in enumerate(solids):
        check = ctx.check(solid)
        name = f"{first}_solid" if index == 0 else f"{first}_solid_{index + 1}"
        reports.append({"name": name, "volume": check["volume"], "faces": check["faces"]})
        outputs.append(OpOutput(name, solid, history, report={"check_after": check}))
        ctx.progress("solidify", 0.5 + 0.5 * (index + 1) / len(solids), f"built {name}")
    report = {
        "tolerance": tolerance / MM_PER_M,
        "fuzzy": fuzzy / MM_PER_M,
        "avoid_internal": avoid_internal,
        "solids": len(solids),
        "outputs": reports,
        "check_before": before,
        "check_after": after,
    }
    return OpResult(outputs, report=report)
