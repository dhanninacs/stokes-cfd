"""The ``cap`` operation: close the openings of an open body with planar or smooth caps
(ADR-0021 decision 5; T-028).

One output from all the inputs, named ``<first input>_capped``. The closed free-edge loops of the
inputs are found with ``ShapeAnalysis_FreeBounds`` at ``tolerance``; a loop whose bounding-box
diagonal is above ``max_size`` (when ``max_size`` is set) is left alone, so a small drain hole is
capped without sealing the inlet too. Each remaining loop is closed with a planar face when
``BRepLib_FindSurface`` fits a plane through it, and, unless ``planar_only``, with a
``BRepFill_Filling`` patch when no plane does; a loop the fill cannot build is left open and
reported. The caps are then sewn onto the input (the T-027 sewing tool, which adds faces
edge-by-face) and every closed shell of the seam becomes a solid.

The cap faces are named in ``OpOutput.tags["<first input>_caps"]`` (kind ``boundary``), so a run's
output carries them under a tag the user can rename to ``inlet`` and ``outlet`` and hand to
``extract_fluid`` as its ``caps``. They are new faces with no ancestor among the inputs' faces, so
the history — the sewing tool's ``Modified`` — leaves them unmapped by design.

OCP traps kept: ``BRepBuilderAPI_Sewing.Modified`` answers with one shape, not a list, and the tool
has no ``Generated`` or ``IsDeleted`` (``repair_common.history_from_sewing`` turns it into a
``BRepTools_History``); ``Bnd_Box.Get`` fails, so ``CornerMin``/``CornerMax``; ``BRepFill_Filling``
is a class in OCP and its ``Build`` reports through ``IsDone``, not through an exception.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from typing import Any

from stokes.geometry.errors import OperationInvalid
from stokes.geometry.operations import repair_common as rm
from stokes.geometry.operations.base import MM_PER_M, OpContext, OpOutput, OpResult

DEFAULT_MAX_SIZE = 0.0
"""Metres: cap every opening; a positive value caps only the loops up to that size."""
DEFAULT_TOLERANCE = 1e-5
"""Metres: the free-edge search's and the seam's tolerance when ``tolerance`` is not given."""
FILLING = (3, 15, 2, False, 1e-5, 1e-4, 1e-2, 0.1, 8, 9)
"""``BRepFill_Filling``'s parameters (degree, points, iterations, anisotropy and the tolerances):
OCCT's own defaults, which the contract names."""


def run(ctx: OpContext) -> OpResult:
    """Close every opening the parameters allow, sew the caps on and make a body of the result.

    ``loop`` may name a tagged edge loop (ADR-0022 §15.4: "store explicit cap loops as edge members
    of a boundary or plain tag; the cap operation consumes the loop"). When it does, only the
    openings that loop names are capped: the user has said which opening they mean, and capping the
    others too would seal an inlet they meant to keep open. Without ``loop`` every closed free-edge
    loop is capped, exactly as before.
    """
    max_size = rm.length(ctx, "max_size", DEFAULT_MAX_SIZE)
    planar_only = bool(ctx.params.get("planar_only", False))
    tolerance = rm.length(ctx, "tolerance", DEFAULT_TOLERANCE)
    if tolerance <= 0.0:
        raise OperationInvalid("tolerance must be positive", details={"parameter": "tolerance"})
    if not ctx.inputs:
        raise OperationInvalid("cap needs at least one input")
    before = [ctx.check(item.shape) for item in ctx.inputs]
    shape = rm.joined([item.shape for item in ctx.inputs])
    every = rm.closed_wires(shape, tolerance)
    members = ctx.params.get("loop_members")
    loops = _tagged_loops(ctx, members, every) if isinstance(members, list) and members else every
    ctx.progress("cap", 0.1, f"{len(loops)} opening(s) to close of {len(every)}")
    caps, planar, smooth, left_open, warnings = _close(loops, max_size, planar_only)
    ctx.progress("cap", 0.6, f"capped {len(caps)} opening(s), left {left_open} open")
    if caps:
        tool = rm.sew([shape, *caps], tolerance, False)
        result = rm.bodies(tool.SewedShape(), True)
        history = rm.history_from_sewing(tool, ctx.inputs)
    else:
        result = shape
        history = None
    after = ctx.check(result)
    closed = bool(after["faces"]) and rm.free_edges(result) == 0
    report = {
        "max_size": max_size / MM_PER_M,
        "planar_only": planar_only,
        "tolerance": tolerance / MM_PER_M,
        "loops": len(loops),
        "capped_planar": planar,
        "capped_smooth": smooth,
        "left_open": left_open,
        "closed": closed,
        "check_before": before,
        "check_after": after,
    }
    tags = {f"{ctx.inputs[0].name}_caps": caps} if caps else {}
    if left_open:
        warnings.append(f"{left_open} opening(s) were left open")
    output = OpOutput(f"{ctx.inputs[0].name}_capped", result, history, tags, {"check_after": after})
    ctx.progress("cap", 1.0, f"capped: {after['faces']} face(s), closed {closed}")
    return OpResult([output], report=report, warnings=warnings)


def _tagged_loops(
    ctx: OpContext, members: list[Any], available: list[Any]
) -> list[Any]:  # Any: JSON in, TopoDS_Wire out
    """The closed openings a tagged edge set names (ADR-0022 §15.4).

    A tag holds *edges*, but an opening is a *wire*; the two are tied by identity of the edges, not
    by counting: an opening is kept when every edge the member names lies on it and the member
    names at least one. That is deliberately stricter than "shares an edge", so a tag of two edges
    on two different openings does not silently cap both, and it is deliberately not "contains the
    wire", because a user may tag part of a long loop and still mean that opening.

    A member whose edges lie on no closed opening is reported rather than ignored: the user picked
    something, and silently capping nothing (or everything) would be the wrong answer. When no
    member matches, the run fails with a message naming the tag, because a cap that capped nothing
    while claiming success is worse than a refusal.
    """
    wanted = ctx.edges(members)
    if not wanted:
        raise OperationInvalid(
            "the loop tag names no edges; tag the opening's edges first",
            details={"parameter": "loop"},
        )
    kept: list[Any] = []
    for member_edges in _per_member(ctx, members):
        for wire in available:
            if _lies_on(wire, member_edges):
                if not any(wire.IsSame(other) for other in kept):
                    kept.append(wire)
                break
    if not kept:
        raise OperationInvalid(
            "no closed opening of the input holds every edge the loop tag names; "
            "tag the edges of one opening, or cap without a loop",
            details={"parameter": "loop", "edges": len(wanted)},
        )
    return kept


def _per_member(ctx: OpContext, members: list[Any]) -> list[list[Any]]:
    """The named edges grouped one member at a time, so one tag's parts are judged apart."""
    grouped: list[list[Any]] = []
    for member in members:
        if not isinstance(member, dict):
            continue
        edges = ctx.edges([member])
        if edges:
            grouped.append(edges)
    return grouped


def _lies_on(wire: Any, edges: list[Any]) -> bool:  # Any: TopoDS_Wire, TopoDS_Edge list
    """Whether every edge of ``edges`` is an edge of ``wire`` (by ``IsSame``)."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    on_wire: list[Any] = []
    explorer = TopExp_Explorer(wire, TopAbs_ShapeEnum.TopAbs_EDGE)
    as_edge = TopoDS.Edge
    while explorer.More():
        on_wire.append(as_edge(explorer.Current()))
        explorer.Next()
    return all(any(candidate.IsSame(edge) for candidate in on_wire) for edge in edges)


def _close(
    loops: list[Any], max_size: float, planar_only: bool
) -> tuple[list[Any], int, int, int, list[str]]:
    """A cap for every loop the parameters allow, and the counts and warnings of the walk.

    Returns the cap faces, how many were planar, how many smooth, how many were left open and the
    warnings for the ones OCCT could not fill.
    """
    caps: list[Any] = []  # Any: TopoDS_Face
    planar = 0
    smooth = 0
    left_open = 0
    warnings: list[str] = []
    for loop in loops:
        if max_size > 0.0 and rm.diagonal(loop) > max_size:
            left_open += 1
            continue
        face = _planar_cap(loop)
        if face is not None:
            caps.append(face)
            planar += 1
            continue
        if planar_only:
            left_open += 1
            continue
        filled = _smooth_cap(loop)
        if filled is None:
            left_open += 1
            warnings.append("a non-planar opening was left open: OCCT could not fill it")
            continue
        caps.append(filled)
        smooth += 1
    return caps, planar, smooth, left_open, warnings


def _planar_cap(loop: Any) -> Any:  # Any: OCP TopoDS_Wire in, TopoDS_Face or None out
    """A planar face on ``loop`` when a plane fits through it, else None."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepLib import BRepLib_FindSurface
    from OCP.TopoDS import TopoDS

    if not BRepLib_FindSurface(loop, -1, True).Found():
        return None
    return BRepBuilderAPI_MakeFace(TopoDS.Wire(loop), True).Face()


def _smooth_cap(loop: Any) -> Any:  # Any: OCP TopoDS_Wire in, TopoDS_Face or None out
    """A ``BRepFill_Filling`` patch over ``loop``'s edges: the smooth cap of a non-planar opening.

    Every edge of the loop is added as a ``GeomAbs_C0`` boundary, so the patch meets the shell
    along the whole loop. ``Build`` reports through ``IsDone`` and the run leaves the opening open
    (and says so) when OCCT cannot make the patch.
    """
    from OCP.BRepFill import BRepFill_Filling
    from OCP.GeomAbs import GeomAbs_Shape
    from OCP.TopoDS import TopoDS

    filling = BRepFill_Filling(*FILLING)
    for edge in rm.subshapes(loop, "EDGE"):
        filling.Add(TopoDS.Edge(edge), GeomAbs_Shape.GeomAbs_C0)
    filling.Build()
    if not filling.IsDone():
        return None
    return filling.Face()
