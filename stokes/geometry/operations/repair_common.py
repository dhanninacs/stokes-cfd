"""What the repair operations share: parameters, shape walks, sewing, histories (ADR-0021
decision 5; T-027, T-028).

The eight modules ``heal``, ``sew``, ``orient``, ``solidify``, ``cap``, ``unify``, ``slivers`` and
``defeature`` need the same small set of things and nothing else: a length parameter in
millimetres, the free-edge count their reports carry, the sewing tool that ``sew``, ``solidify``
and ``cap`` drive, a history built from the fixer's re-shape context or from the sewing tool, the
one-solid-from-a-shell step, shape exploration, and (T-028) the closed free-edge loops a sealing
operation caps, the diagonal of a shape's box, the sub-shape counts that must not repeat, and a
history carried through several fixers' contexts in turn.

The free-edge count here is deliberately not the one in :mod:`stokes.geometry.brep`.
``ShapeAnalysis_FreeBounds`` with ``splitclosed`` and ``splitopen`` set reports no free edge at
all for an unsewn compound of loose faces — measured on OCP 8.0.1, a box's six copied faces give
0, exactly as the batch 4 review of 2026-10-05 found, so an open compound looks closed and
``geometry.check`` gives the user no hint. :func:`free_edges` walks the shape's faces instead and
asks how many of them each edge belongs to, so an edge of one face is free wherever that face
sits. It agrees with :mod:`stokes.geometry.brep` on shells (a box missing one face: 4 free edges)
and reports the loose faces (24) that the other count misses.

Import rule: OCP inside the functions, as everywhere in the package.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from stokes.geometry.errors import OperationFailed, OperationInvalid
from stokes.geometry.operations.base import OpContext, OpInput


# ------------------------------------------------------------------ parameters
def number(value: Any, name: str) -> float:  # Any: JSON
    """A scalar parameter as a float; anything else is :class:`OperationInvalid`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OperationInvalid(f"{name} must be a number", details={"parameter": name})
    return float(value)


def length(ctx: OpContext, name: str, default: float) -> float:
    """A length parameter in the millimetres the shapes are in; ``default`` is SI metres."""
    raw = ctx.params.get(name)
    found = ctx.mm(default if raw is None else number(raw, name))
    if found < 0.0:
        raise OperationInvalid(f"{name} cannot be negative", details={"parameter": name})
    return found


# ------------------------------------------------------------------ shapes
def joined(shapes: Sequence[Any]) -> Any:  # Any: OCP TopoDS_Shape in, one shape out
    """One shape holding every input: the shape itself when there is only one, else a compound."""
    if len(shapes) == 1:
        return shapes[0]
    return compound(shapes)


def compound(shapes: Sequence[Any]) -> Any:  # Any: OCP shapes in, TopoDS_Compound out
    """A compound of every shape, in order."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    result = TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def subshapes(shape: Any, kind: str) -> list[Any]:  # Any: OCP shape in, shapes out
    """The sub-shapes of ``shape`` of one kind (``"SOLID"``, ``"SHELL"``, ``"FACE"`` ...), in
    ``TopExp_Explorer`` order with repeats."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    found: list[Any] = []  # Any: TopoDS_Shape
    explorer = TopExp_Explorer(shape, getattr(TopAbs_ShapeEnum, f"TopAbs_{kind}"))
    while explorer.More():
        found.append(explorer.Current())
        explorer.Next()
    return found


def unique(shape: Any, kind: str) -> list[Any]:  # Any: OCP shape in, shapes out
    """The sub-shapes of ``shape`` of one kind, **each once** (``TopExp.MapShapes_s``).

    ``subshapes`` explores with repeats: a box's twelve edges are visited twenty-four times, once
    per face that uses them, so its count is not the number of edges the user sees. Edge and
    vertex counts use the map instead (ADR-0022 decision 15.1); face counts stay
    :func:`faces`, which is the id order the whole framework numbers faces in.
    """
    import OCP.collections as occ
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp

    table = occ.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, getattr(TopAbs_ShapeEnum, f"TopAbs_{kind}"), table)
    return [table.FindKey(i) for i in range(1, table.Extent() + 1)]


def faces(shape: Any) -> list[Any]:  # Any: OCP shape in, TopoDS_Face out
    """The faces of ``shape`` in face-id order (``faces.face_list``, which is the id order the
    whole framework uses)."""
    from stokes.geometry.operations.faces import face_list

    return face_list(shape)


def free_edges(shape: Any) -> int:  # Any: OCP TopoDS_Shape
    """The edges of ``shape`` that fewer than two of its faces use, each counted once.

    The faces are explored over the whole shape, so a compound of loose faces is open (every edge
    belongs to the one face that owns it) and a sewn shell is closed, whatever the shape's nesting.
    ``MapShapesAndAncestors`` lists a face twice on an edge the face meets twice (the two pcurves of
    a seam), so the test is the length of an edge's ancestor list, and a cylinder's seam is not
    free.
    """
    import OCP.collections as occ
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp

    if shape is None or shape.IsNull():
        return 0
    ancestors = occ.IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(
        shape, TopAbs_ShapeEnum.TopAbs_EDGE, TopAbs_ShapeEnum.TopAbs_FACE, ancestors
    )
    return sum(
        1 for i in range(1, ancestors.Extent() + 1) if ancestors.FindFromIndex(i).Extent() < 2
    )


def closed_wires(shape: Any, tolerance: float) -> list[Any]:  # Any: OCP shape in, TopoDS_Wire out
    """The closed free-edge loops of ``shape``, in ``TopExp`` order (``TopAbs_WIRE`` each).

    ``ShapeAnalysis_FreeBounds(shape, tolerance, False, False)`` groups the free edges into wires
    and ``GetClosedWires`` answers with a compound of them; ``splitclosed``/``splitopen`` stay off,
    as the contract of the sealing operations asks, so a loop is one wire. The tolerance is in mm.
    """
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds

    bounds = ShapeAnalysis_FreeBounds(shape, tolerance, False, False)
    return subshapes(bounds.GetClosedWires(), "WIRE")


def diagonal(shape: Any) -> float:  # Any: OCP TopoDS_Shape; mm out
    """The diagonal of ``shape``'s bounding box in mm, or 0.0 when it has none (a void box).

    ``Bnd_Box.Get`` fails in OCP 8.0.1, so the corners come from ``CornerMin``/``CornerMax``.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    if box.IsVoid():
        return 0.0
    low, high = box.CornerMin(), box.CornerMax()
    return float(
        ((high.X() - low.X()) ** 2 + (high.Y() - low.Y()) ** 2 + (high.Z() - low.Z()) ** 2) ** 0.5
    )


def volume(shape: Any) -> float:  # Any: OCP TopoDS_Shape
    """The signed volume of ``shape`` in mm^3: negative for a body whose shell is inside out."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def reoriented(before: Any, after: Any) -> int:  # Any: two OCP shells
    """How many faces of a shell changed orientation: all of them when the shell itself flipped
    (what ``BRepLib.OrientClosedSolid_s`` does to a solid), else the faces whose own orientation
    differs, matched by position (what ``ShapeFix_Shell`` does to a loose shell)."""
    if before.Orientation() != after.Orientation():
        return len(faces(after))
    return sum(
        1
        for old, new in zip(faces(before), faces(after), strict=False)
        if old.Orientation() != new.Orientation()
    )


def solids(shape: Any) -> list[Any]:  # Any: OCP shape in, TopoDS_Solid out
    """The solids of ``shape``, in ``TopExp_Explorer`` order with repeats."""
    return subshapes(shape, "SOLID")


def loose_shells(shape: Any) -> list[Any]:  # Any: OCP shape in, TopoDS_Shell out
    """The shells of ``shape`` that no solid of it holds: the ones ``orient`` fixes with
    ``ShapeFix_Shell`` rather than through their solid."""
    table = _held(solids(shape))
    return [shell for shell in subshapes(shape, "SHELL") if not _holds(table, shell)]


def free_faces(shape: Any) -> list[Any]:  # Any: OCP shape in, TopoDS_Face out
    """The faces of ``shape`` that neither a solid nor a shell of it holds: the loose faces of an
    imported surface model. Nothing orients them against a neighbour, so a run keeps them as they
    are rather than dropping them."""
    table = _held([*solids(shape), *subshapes(shape, "SHELL")])
    return [face for face in faces(shape) if not _holds(table, face)]


def _held(shapes: Sequence[Any]) -> dict[int, list[Any]]:  # Any: OCP shapes in, shapes out
    """The faces and shells ``shapes`` hold, by hash, for many :func:`_holds` lookups. The hash
    ignores orientation, as ``IsSame`` does, so it only narrows (``faces.face_table`` does the
    same)."""
    table: dict[int, list[Any]] = {}
    for shape in shapes:
        for sub in (*subshapes(shape, "SHELL"), *faces(shape)):
            table.setdefault(hash(sub), []).append(sub)
    return table


def _holds(table: dict[int, list[Any]], shape: Any) -> bool:  # Any: OCP TopoDS_Shape
    """Whether one of the shapes :func:`_held` recorded is ``shape``, by ``IsSame``."""
    return any(candidate.IsSame(shape) for candidate in table.get(hash(shape), ()))


# ------------------------------------------------------------------ sewing
def sew(shapes: Sequence[Any], tolerance: float, non_manifold: bool) -> Any:  # Any: OCP shapes in
    """A performed ``BRepBuilderAPI_Sewing`` over every shape, with its tolerance and modes.

    The faces of every shape are added one by one rather than the shape itself: OCCT records which
    output face each *added* shape became (``Modified``), and a compound added whole leaves that
    record empty, so a tag on a loose face would not follow the seam (measured on OCP 8.0.1). A
    shape with no face at all (a wire) is added as it is. ``BRepBuilderAPI_Sewing.Modified`` gives
    back a single shape, not a list, so callers turn it into a history themselves
    (:func:`history_from_sewing`).
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing

    tool = BRepBuilderAPI_Sewing(tolerance, True, True, True, non_manifold)
    for shape in shapes:
        found = faces(shape)
        for face in found or [shape]:
            tool.Add(face)
    tool.Perform()
    if tool.SewedShape().IsNull():
        raise OperationFailed("OCCT could not sew the inputs", details={"shapes": len(shapes)})
    return tool


def history_from_sewing(
    tool: Any, inputs: Sequence[OpInput]
) -> Any:  # Any: OCP sewing, history out
    """A ``BRepTools_History`` of the face each input face became through the sewing tool.

    The sewing tool answers ``Modified(face)`` with the face itself when it kept it and with the
    rebuilt one when it did not, and has no ``Generated`` or ``IsDeleted``, so it cannot be handed
    to :func:`stokes.geometry.operations.faces.map_history` directly. A face the tool drops comes
    back unmapped, which is the outcome the session reports as an unmapped tag member.
    """
    from OCP.BRepTools import BRepTools_History

    history = BRepTools_History()
    for item in inputs:
        for face in faces(item.shape):
            image = tool.Modified(face)
            if image is None or image.IsNull():
                history.Remove(face)
            elif not image.IsSame(face):
                history.AddModified(face, image)
    return history


def history_from_reshape(inputs: Sequence[OpInput], context: Any) -> Any:  # Any: OCP re-shape
    """A ``BRepTools_History`` of the face each input face became through a fixer's re-shape
    context (``ShapeFix_Shape.Context()``): the face it became, or a removal when the fix dropped
    it. A face the context left alone is left out, and the history map then keeps it as itself."""
    return history_from_contexts(inputs, [context])


def history_from_contexts(inputs: Sequence[OpInput], contexts: Sequence[Any]) -> Any:
    """A ``BRepTools_History`` of the face each input face became through several re-shape
    contexts, applied in order (T-028).

    A repair that runs more than one fixer (``slivers``: ``ShapeFix_FixSmallFace`` then
    ``ShapeFix_Wireframe`` then ``ShapeFix_Shape``) can share one context between them
    (``SetContext``), and this is the same walk over it; when they keep their own contexts, an
    input face is carried through each in turn, because a later context is built from the earlier
    one's output. A face no context touched is left out, and the history map then keeps it as
    itself.
    """
    from OCP.BRepTools import BRepTools_History

    history = BRepTools_History()
    for item in inputs:
        for face in faces(item.shape):
            image = _through(face, contexts)
            if image is None or image.IsNull():
                history.Remove(face)
            elif not image.IsSame(face):
                history.AddModified(face, image)
    return history


def _through(face: Any, contexts: Sequence[Any]) -> Any:  # Any: OCP face and re-shape contexts
    """``face`` after every context in turn, or None when one of them dropped it."""
    image = face
    for context in contexts:
        if context is None:
            continue
        image = context.Apply(image)
        if image is None or image.IsNull():
            return None
    return image


def removed(history: Any, shape: Any) -> bool:  # Any: OCP history and shape
    """Whether ``history`` says ``shape`` was removed.

    A ``BRepTools_History`` answers ``IsRemoved`` and the algorithms derived from
    ``BRepBuilderAPI_MakeShape`` (``BRepAlgoAPI_Defeaturing``) answer ``IsDeleted``; the two names
    are the one question (``faces._successors`` asks it the same way).
    """
    ask = getattr(history, "IsRemoved", None) or getattr(history, "IsDeleted", None)
    return bool(ask(shape)) if ask is not None else False


def make_solid(shell: Any) -> Any:  # Any: OCP TopoDS_Shell in, TopoDS_Solid out
    """A closed shell as a solid (``BRepBuilderAPI_MakeSolid``) fixed by ``ShapeFix_Solid``, the
    way ``extract_fluid`` makes its cavities."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.ShapeFix import ShapeFix_Solid
    from OCP.TopoDS import TopoDS

    maker = BRepBuilderAPI_MakeSolid(TopoDS.Shell(shell))
    if not maker.IsDone():
        raise OperationFailed("OCCT could not make a solid from a closed shell")
    fixer = ShapeFix_Solid(maker.Solid())
    fixer.Perform()
    return fixer.Solid()


def bodies(shape: Any, as_solid: bool) -> Any:  # Any: OCP shapes in and out
    """``shape`` with every closed shell made a solid, when ``as_solid``; open shells stay.

    One body is returned as it is; several become a compound. ``cap`` closes an opening and then
    wants a body of what it sewed; ``sew`` keeps its own copy of this walk, and ``extract_fluid``
    orients a cavity shell the same way. (The parameter is not called ``make_solid``: that name is
    this module's own function.)
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS

    if not as_solid:
        return shape
    parts: list[Any] = []  # Any: TopoDS_Shape
    for shell in subshapes(shape, "SHELL"):
        if BRep_Tool.IsClosed_s(TopoDS.Shell(shell)):
            parts.append(make_solid(shell))
        else:
            parts.append(shell)
    if not parts:
        return shape
    if len(parts) == 1:
        return parts[0]
    return compound(parts)
