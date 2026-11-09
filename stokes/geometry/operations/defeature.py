"""The ``defeature`` operation: remove the faces the user picked (ADR-0021 decision 5; T-028).

One output from all the inputs, named ``<first input>_defeatured``. ``BRepAlgoAPI_Defeaturing``
takes the inputs as one shape and the picked faces — ``faces``, the same stored members a tag
holds, resolved against the inputs — and rebuilds the body without them, closing what they left
behind: pick a hole's bore and the hole goes, pick a fillet and the corner squares up. The run is
not parallel (``SetRunParallel(False)``: the worker's jobs are the parallelism this release has)
and it keeps the history (``SetToFillHistory(True)``), so tags on the faces that stay follow onto
the output. With ``unify`` (the default) the result then goes through :func:`unify.merge`, and its
history is merged into the defeaturing's with ``BRepTools_History.Merge``, so a tag whose faces
were split by the removal still reaches the merged face.

``IsDone`` false is ``geometry.op_failed``. The contract asks for OCCT's ``HasWarnings`` and
``DumpWarnings`` text in that error, and this OCP build does not bind them: measured on OCP 8.0.1,
``BRepAlgoAPI_Defeaturing`` and its base ``BRepAlgoAPI_Algo`` carry ``Build``, ``Check``,
``IsDone``, ``Shape`` and the history questions and nothing else, so the message names the faces
and the algorithm's own state instead of the missing text (reported to the T-028 reviewer).

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.errors import OperationFailed, OperationInvalid
from stokes.geometry.operations import repair_common as rm
from stokes.geometry.operations import unify as unify_op
from stokes.geometry.operations.base import OpContext, OpOutput, OpResult

NO_WARNING_TEXT = (
    "OCCT reported no warning text: this OCP build binds no BRepAlgoAPI_Algo.DumpWarnings"
)
"""What the error says when the failed algorithm cannot be asked what went wrong."""


def run(ctx: OpContext) -> OpResult:
    """Remove the picked faces and (by default) unify what the removal left on one surface."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

    if not ctx.inputs:
        raise OperationInvalid("defeature needs at least one input")
    picked = _faces(ctx)
    if not picked:
        raise OperationInvalid(
            "defeature needs at least one face to remove", details={"parameter": "faces"}
        )
    simplify = bool(ctx.params.get("unify", True))
    before = [ctx.check(item.shape) for item in ctx.inputs]
    shape = rm.joined([item.shape for item in ctx.inputs])
    faces_before = len(rm.faces(shape))
    ctx.progress("defeature", 0.1, f"removing {len(picked)} face(s)")
    tool = BRepAlgoAPI_Defeaturing()
    tool.SetShape(shape)
    tool.AddFacesToRemove(_listed(picked))
    tool.SetRunParallel(False)
    tool.SetToFillHistory(True)
    tool.Build()
    if not tool.IsDone():
        raise OperationFailed(
            f"OCCT could not remove {len(picked)} face(s) from {ctx.inputs[0].name}: "
            f"{NO_WARNING_TEXT}",
            details={"faces": len(picked), "inputs": [item.part for item in ctx.inputs]},
        )
    result = tool.Shape()
    history = tool.History()
    faces_removed = sum(1 for face in picked if rm.removed(history, face))
    ctx.progress("defeature", 0.6, f"removed {faces_removed} face(s)")
    if simplify:
        result, extra = unify_op.merge(
            result,
            ctx.mm(unify_op.DEFAULT_LINEAR_TOLERANCE),
            unify_op.DEFAULT_ANGULAR_TOLERANCE,
            (),
            ctx,
        )
        history.Merge(extra)
    after = ctx.check(result)
    report = {
        "faces_removed": faces_removed,
        "unified": simplify,
        "faces_before": faces_before,
        "faces_after": len(rm.faces(result)),
        "check_before": before,
        "check_after": after,
    }
    output = OpOutput(
        f"{ctx.inputs[0].name}_defeatured", result, history, report={"check_after": after}
    )
    ctx.progress("defeature", 1.0, f"defeatured: {report['faces_after']} face(s)")
    return OpResult([output], report=report)


def _faces(ctx: OpContext) -> list[Any]:  # Any: TopoDS_Face
    """The faces the ``faces`` parameter names, resolved against the run's inputs."""
    raw = ctx.params.get("faces")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise OperationInvalid(
            "faces must be a list of tag members", details={"parameter": "faces"}
        )
    return ctx.faces([member for member in raw if isinstance(member, dict)])


def _listed(faces: list[Any]) -> Any:  # Any: OCP List_TopoDS_Shape
    """The picked faces as the list ``AddFacesToRemove`` wants."""
    import OCP.collections as occ

    listed = occ.List_TopoDS_Shape()
    for face in faces:
        listed.Append(face)
    return listed
