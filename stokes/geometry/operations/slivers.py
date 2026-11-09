"""The ``slivers`` operation: drop the sliver faces and edges a kernel left behind
(ADR-0021 decision 5; T-028).

One output from all the inputs, named ``<first input>_slivered``. ``ShapeFix_FixSmallFace`` works
at ``size``: ``FixSpotFace`` removes the faces too small to be a face at all (the pin face a
boolean leaves) and ``FixStripFace`` the long thin strips along a step, merging each into the face
next to it. ``ShapeFix_Wireframe`` then drops the edges below ``size``. With ``holes`` every face
also goes through ``ShapeUpgrade_RemoveInternalWires`` at ``MinArea = size²``: the inner loops
that bound less than that are the tiny holes and stray wires, and each face that lost one is
replaced through a ``BRepTools_ReShape``. A last ``ShapeFix_Shape`` at OCCT's default precision
tidies what is left.

The report counts what each step dropped — ``spot_faces``, ``strip_faces``, ``small_edges`` and
``wires_removed`` — and the faces before and after. ``FixSpotFace`` and ``FixStripFace`` answer
with the shape in this OCP rather than with OCCT's ``Standard_Boolean`` flag (measured), so the
faces they dropped are counted as the change in the face count across the call; the whole run's
faces follow the fixers' shared re-shape context (``SetContext``) through
``repair_common.history_from_contexts``.

OCP traps kept: ``ShapeUpgrade_RemoveInternalWires`` has no ``RemoveAllInternalWires`` in OCP
8.0.1 (the contract named it); ``RemoveFaceMode = False`` is the same request — empty a face of
its wires but do not take the face away — and is what the run sets. ``FixSpotFace`` returning a
shape is the other. ``MinArea`` is an area in mm², which ``size²`` already is.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.errors import OperationInvalid
from stokes.geometry.operations import repair_common as rm
from stokes.geometry.operations.base import MM_PER_M, OpContext, OpOutput, OpResult

DEFAULT_SIZE = 1e-4
"""Metres: the face size and edge length below which a sliver goes."""


def run(ctx: OpContext) -> OpResult:
    """Drop the inputs' sliver faces, sliver edges and (with ``holes``) tiny inner wires."""
    from OCP.ShapeBuild import ShapeBuild_ReShape
    from OCP.ShapeFix import ShapeFix_FixSmallFace, ShapeFix_Shape, ShapeFix_Wireframe

    size = rm.length(ctx, "size", DEFAULT_SIZE)
    if size <= 0.0:
        raise OperationInvalid("size must be positive", details={"parameter": "size"})
    holes = bool(ctx.params.get("holes", False))
    if not ctx.inputs:
        raise OperationInvalid("slivers needs at least one input")
    before = [ctx.check(item.shape) for item in ctx.inputs]
    shape = rm.joined([item.shape for item in ctx.inputs])
    faces_before = len(rm.faces(shape))
    context = ShapeBuild_ReShape()
    fixer = ShapeFix_FixSmallFace()
    fixer.Init(shape)
    fixer.SetContext(context)
    fixer.SetPrecision(size)
    ctx.progress("slivers", 0.1, f"dropping faces below {size:.3g} mm")
    spot_faces = _dropped(fixer.Shape, fixer.FixSpotFace)
    strip_faces = _dropped(fixer.Shape, fixer.FixStripFace)
    wireframe = ShapeFix_Wireframe(fixer.Shape())
    wireframe.SetContext(context)
    wireframe.SetPrecision(size)
    wireframe.ModeDropSmallEdges = True
    ctx.progress("slivers", 0.4, f"dropping edges below {size:.3g} mm")
    edges_before = _wireframe_edges(wireframe)
    wireframe.FixSmallEdges()
    result = wireframe.Shape()
    small_edges = max(0, edges_before - len(rm.unique(result, "EDGE")))
    wires_removed = 0
    if holes:
        ctx.progress("slivers", 0.6, f"removing inner wires below {size * size:.3g} mm^2")
        result, wires_removed = _drop_wires(result, size)
    tidy = ShapeFix_Shape(result)
    tidy.SetContext(context)
    tidy.Perform()  # False is not a failure: it reports False on shapes it did fix
    result = tidy.Shape()
    # A guard over the WHOLE chain, because these fixers are willing to delete the body: on a part
    # whose faces all read as spots at this precision, every step reports success while the last one
    # answers with 0 faces, no shell and no volume, and the operation used to hand that back as a
    # "slivered" part while the geometry worker then died on the empty shape (found 2026-10-06 by
    # tests/test_operations_smoke.py). A repair may drop slivers; it may not drop the model.
    # Guarding one step at a time does not work here: the steps that delete everything do it on
    # shapes that still have every face, so the check has to be on what comes out of the chain. The
    # counts stay as the fixers reported them, since what they did is worth knowing even put aside.
    warnings: list[str] = []
    if faces_before and not rm.faces(result):
        warnings.append(
            f"the sliver fixers would have removed all {faces_before} faces, "
            "so the body was left as it was"
        )
        result = shape
    after = ctx.check(result)
    report = {
        "size": size / MM_PER_M,
        "holes": holes,
        "spot_faces": spot_faces,
        "strip_faces": strip_faces,
        "small_edges": small_edges,
        "wires_removed": wires_removed,
        "faces_before": faces_before,
        "faces_after": len(rm.faces(result)),
        "check_before": before,
        "check_after": after,
    }
    history = rm.history_from_reshape(ctx.inputs, context)
    output = OpOutput(
        f"{ctx.inputs[0].name}_slivered", result, history, report={"check_after": after}
    )
    ctx.progress("slivers", 1.0, f"slivers gone: {report['faces_after']} face(s)")
    return OpResult([output], report=report, warnings=warnings)


def _dropped(shape: Any, fix: Any) -> int:  # Any: OCP shape question and an OCP fixer method
    """How many faces one ``ShapeFix_FixSmallFace`` step removed.

    This OCP binds ``FixSpotFace`` and ``FixStripFace`` as returning a ``TopoDS_Shape`` rather than
    OCCT's ``Standard_Boolean`` flag (measured on OCP 8.0.1), so the answer cannot be read off the
    call; the face count across it can.
    """
    before = len(rm.faces(shape()))
    fix()
    return max(0, before - len(rm.faces(shape())))


def _wireframe_edges(wireframe: Any) -> int:  # Any: an OCP ShapeFix_Wireframe
    """The unique edge count of a wireframe fixer's shape, taken before ``FixSmallEdges``.

    Read as a before/after pair around the call, clamped at zero: ``ShapeFix_Wireframe`` merges as
    well as drops, so a run that only merged has dropped no edge.
    """
    return len(rm.unique(wireframe.Shape(), "EDGE"))


def _drop_wires(shape: Any, size: float) -> tuple[Any, int]:  # Any: OCP shape in and out
    """``shape`` with every face's inner wires under ``size²`` mm² removed, and how many went.

    Each face goes through ``ShapeUpgrade_RemoveInternalWires`` on its own and every changed one is
    recorded in a ``BRepTools_ReShape``, so the loop that bounded the tiny hole goes and the face
    itself stays where it was.
    """
    from OCP.BRepTools import BRepTools_ReShape
    from OCP.ShapeUpgrade import ShapeUpgrade_RemoveInternalWires

    reshape = BRepTools_ReShape()
    removed = 0
    changed = False
    for face in rm.faces(shape):
        remover = ShapeUpgrade_RemoveInternalWires(face)
        remover.MinArea = size * size  # mm^2 already: both are millimetres
        remover.RemoveFaceMode = False
        remover.Perform()
        removed += len(remover.RemovedWires())
        image = remover.GetResult()
        if not image.IsSame(face):
            reshape.Replace(face, image)
            changed = True
    if not changed:
        return shape, removed
    return reshape.Apply(shape), removed
