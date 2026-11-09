"""The ``unify`` operation: merge the faces that lie on one surface (ADR-0021 decision 5; T-028).

One output from all the inputs, named ``<first input>_unified``. ``ShapeUpgrade_UnifySameDomain``
runs with its edge and face unification on and BSpline concatenation off, at ``linear_tolerance``
(how far apart two faces on one surface may be) and ``angular_tolerance`` (how far their normals
may differ). Faces the ``keep`` tag names stay apart from the faces around them.

The core is :func:`merge`, a function of a shape and its two tolerances rather than of a run, so
``defeature`` can unify what it removed faces from and merge this history into the defeaturing's
(ADR-0021 decision 8.6). :func:`run` is the operation module's own entry point; ``merge`` is the
second, deliberate public function of this module.

OCP traps kept: ``UnifySameDomain.KeepShapes`` takes a ``TopTools_ShapeMapHasher`` map, and a face
in it is **not** enough — measured on OCP 8.0.1, a split cylinder whose two side halves are in the
map still comes out with one side face (3 faces in all), while the same run with the halves'
*edges* in the map keeps them apart (4 faces). A face is therefore recorded together with its
edges. The angular tolerance arrives in SI radians like every angle and is **not** converted by
``OpContext.mm``.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from stokes.geometry.errors import OperationInvalid
from stokes.geometry.operations import repair_common as rm
from stokes.geometry.operations.base import MM_PER_M, OpContext, OpOutput, OpResult

DEFAULT_LINEAR_TOLERANCE = 1e-6
"""Metres: the distance within which two faces count as one surface."""
DEFAULT_ANGULAR_TOLERANCE = 1e-3
"""Radians: the angle within which two faces count as one surface."""
UNIFY_EDGES = True
"""``ShapeUpgrade_UnifySameDomain``'s first flag: merge the edges that lie on one curve."""
UNIFY_FACES = True
"""Its second flag: merge the faces that lie on one surface."""
CONCAT_BSPLINES = False
"""Its third flag: leave neighbouring BSpline faces alone (the contract's ``False``)."""


def run(ctx: OpContext) -> OpResult:
    """Merge the inputs' faces that lie on one surface."""
    if not ctx.inputs:
        raise OperationInvalid("unify needs at least one input")
    linear = rm.length(ctx, "linear_tolerance", DEFAULT_LINEAR_TOLERANCE)
    angular = _angle(ctx, "angular_tolerance", DEFAULT_ANGULAR_TOLERANCE)
    keep = _keep(ctx)
    before = [ctx.check(item.shape) for item in ctx.inputs]
    shape = rm.joined([item.shape for item in ctx.inputs])
    faces_before = len(rm.faces(shape))
    edges_before = len(rm.unique(shape, "EDGE"))
    ctx.progress("unify", 0.2, f"{faces_before} face(s), {len(keep)} kept apart")
    result, history = merge(shape, linear, angular, keep, ctx)
    after = ctx.check(result)
    report = {
        "linear_tolerance": linear / MM_PER_M,
        "angular_tolerance": angular,
        "keep": len(keep),
        "faces_before": faces_before,
        "faces_after": len(rm.faces(result)),
        "edges_before": edges_before,
        "edges_after": len(rm.unique(result, "EDGE")),
        "check_before": before,
        "check_after": after,
    }
    output = OpOutput(
        f"{ctx.inputs[0].name}_unified", result, history, report={"check_after": after}
    )
    ctx.progress("unify", 1.0, f"unified: {report['faces_after']} face(s)")
    return OpResult([output], report=report)


def merge(
    shape: Any,  # Any: OCP TopoDS_Shape, in mm
    linear_tolerance: float,  # mm
    angular_tolerance: float,  # radians
    keep: Sequence[Any] = (),  # Any: TopoDS_Face, the faces to keep apart
    ctx: OpContext | None = None,
) -> tuple[Any, Any]:  # Any: one OCP shape and one BRepTools_History
    """``shape`` with its faces on one surface merged, and the history that maps the input faces.

    ``keep`` is the faces that stay apart from the faces around them: their edges go into the
    unification's keep map with them (see this module's docstring for why).
    """
    import OCP.collections as occ
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    if ctx is not None:
        ctx.progress("unify", 0.5, "merging the faces that lie on one surface")
    tool = ShapeUpgrade_UnifySameDomain(shape, UNIFY_EDGES, UNIFY_FACES, CONCAT_BSPLINES)
    if linear_tolerance > 0.0:
        tool.SetLinearTolerance(linear_tolerance)
    if angular_tolerance > 0.0:
        tool.SetAngularTolerance(angular_tolerance)
    if keep:
        table = occ.Map_TopoDS_Shape_TopTools_ShapeMapHasher()
        for face in keep:
            table.Add(face)
            for edge in rm.subshapes(face, "EDGE"):
                table.Add(edge)
        tool.KeepShapes(table)
    tool.Build()
    return tool.Shape(), tool.History()


def _angle(ctx: OpContext, name: str, default: float) -> float:
    """An angle parameter in radians: SI already, so no :meth:`OpContext.mm`."""
    raw = ctx.params.get(name)
    found = default if raw is None else rm.number(raw, name)
    if found < 0.0:
        raise OperationInvalid(f"{name} cannot be negative", details={"parameter": name})
    return found


def _keep(ctx: OpContext) -> list[Any]:  # Any: TopoDS_Face
    """The faces the ``keep`` tag names, which the core resolves as ``keep_members``."""
    raw = ctx.params.get("keep_members")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise OperationInvalid(
            "keep_members must be a list of tag members", details={"parameter": "keep"}
        )
    return ctx.faces([member for member in raw if isinstance(member, dict)])
