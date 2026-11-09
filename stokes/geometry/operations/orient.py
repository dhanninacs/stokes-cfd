"""The ``orient`` operation: make every body's matter face outwards (ADR-0021 decision 5; T-027).

One output from all the inputs, named ``<first input>_oriented``. Every solid goes through
``BRepLib.OrientClosedSolid_s``: it turns the solid forward and orients its shell so the matter is
inside, which flips a body built inside out. Every shell that no solid holds goes through
``ShapeFix_Shell.FixFaceOrientation``, which makes the faces of a loose shell agree with their
neighbours. A face is never replaced: the output's faces are the input's, so the history maps each
face to itself and the tags on them survive.

The report counts ``solids_flipped`` (the volume's sign, which ``OrientClosedSolid`` does not tell
apart from "already right") and ``faces_reoriented``: the faces of a shell whose own orientation
flipped (what an inside-out solid's shell does, so a box built inside out reports its six faces)
plus the faces of a loose shell whose own orientation the fix changed.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.operations import repair_common as rm
from stokes.geometry.operations.base import OpContext, OpOutput, OpResult
from stokes.geometry.operations.faces import face_list


def run(ctx: OpContext) -> OpResult:
    """Orient every solid and every loose shell of the inputs."""
    from OCP.BRepLib import BRepLib
    from OCP.BRepTools import BRepTools_History
    from OCP.ShapeFix import ShapeFix_Shell
    from OCP.TopoDS import TopoDS

    before = [ctx.check(item.shape) for item in ctx.inputs]
    shape = rm.joined([item.shape for item in ctx.inputs])
    bodies = rm.solids(shape)
    ctx.progress("orient", 0.1, f"orienting {len(bodies)} solid(s)")
    parts: list[Any] = []  # Any: TopoDS_Shape
    flipped = 0
    reoriented = 0
    for solid in bodies:
        body = TopoDS.Solid(solid)
        shells_before = rm.subshapes(body, "SHELL")
        volume_before = rm.volume(body)
        BRepLib.OrientClosedSolid_s(body)
        if volume_before < 0.0 <= rm.volume(body):
            flipped += 1
        for old, new in zip(shells_before, rm.subshapes(body, "SHELL"), strict=False):
            reoriented += rm.reoriented(old, new)
        parts.append(body)
    ctx.progress("orient", 0.6, "orienting the shells that no solid holds")
    for shell in rm.loose_shells(shape):
        loose = TopoDS.Shell(shell)
        fixer = ShapeFix_Shell(loose)
        fixer.FixFaceOrientation(loose)
        fixed = TopoDS.Shell(fixer.Shell())
        reoriented += rm.reoriented(loose, fixed)
        parts.append(fixed)
    # A face no shell holds has no neighbour to agree with: it is kept, and the run reports none
    # of it as reoriented.
    parts.extend(rm.free_faces(shape))
    result = parts[0] if len(parts) == 1 else rm.compound(parts)
    history = BRepTools_History()
    for face in face_list(result):
        history.AddModified(face, face)
    after = ctx.check(result)
    report = {
        "solids_flipped": flipped,
        "faces_reoriented": reoriented,
        "solids": len(rm.solids(result)),
        "shells": len(rm.subshapes(result, "SHELL")),
        "check_before": before,
        "check_after": after,
    }
    output = OpOutput(
        f"{ctx.inputs[0].name}_oriented", result, history, report={"check_after": after}
    )
    ctx.progress("orient", 1.0, f"oriented: {flipped} solid(s) flipped")
    return OpResult([output], report=report)
