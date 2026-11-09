"""Face numbering and history for operations (ADR-0021 decisions 8.5 and 8.6).

A face id is the face's position in ``TopExp_Explorer`` order **with repeats**: the order in which
:func:`stokes.geometry.step._triangle_counts` numbers the triangles of a mesh, so the ids here are
the ids every triangle of the part's display mesh carries. ``TopExp.MapShapes`` (unique faces) would
number differently where a face is visited twice; edges and vertices use that map instead
(ADR-0022 decision 15.1), and face numbering must not move to it.

:func:`map_history` turns an OCCT history (a ``BRepTools_History``, or an algorithm such as
``BRepAlgoAPI_Cut`` that answers ``Modified``, ``Generated`` and ``IsDeleted``) into face ids:
for every input part, each input face id that reaches the output, with the output face ids it
became. A face the history leaves alone maps to itself when the output still holds it; a removed
face, or one whose successors are not in the output, is absent, and the session lists it as
unmapped rather than dropping it silently.

Import rule: OCP inside the functions, so importing this module costs nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stokes.geometry.operations.base import OpInput

FaceTable = dict[int, list[tuple[int, Any]]]
"""Faces of one shape by hash: (face id, face) pairs; ``IsSame`` decides between them."""


def subshape_list(shape: Any, kind: str) -> list[Any]:  # Any: OCP TopoDS_Shape in/out
    """The sub-shapes of ``shape`` of ``kind``, in **the enumeration the id means** (T-056-fix).

    The two kinds are numbered differently, and getting this wrong carried every edge and vertex id
    to the wrong sub-shape:

    * **faces** — ``TopExp_Explorer`` order **with repeats**, because that is how the mesh numbers
      a triangle's face id (`step.face_count`: "counting a face as often as the explorer visits
      it").
    * **edges and vertices** — ``TopExp.MapShapes_s`` order, **once each**, because that is how
      the topology record numbers them (`topology_brep._map_of`), and a tag member's ids are
      record ids.

    On a box the two differ sharply: the explorer sees 24 edges and 48 vertices, the map sees 12 and
    8. **Claude found this by reading the diff and I reproduced it**; my earlier docstring claimed
    the three shared one enumeration, which was false for edges and vertices.
    """
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS

    if kind == "face":
        found: list[Any] = []
        explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
        while explorer.More():
            found.append(TopoDS.Face(explorer.Current()))  # no _s: OCP 8 binds these as statics
            explorer.Next()
        return found

    import OCP.collections as occ

    # A solid, like an edge and a vertex, is enumerated ONCE each: `MapShapes` is the walk the rest
    # of the code means by "the solids of this shape", and a solid visited repeatedly for each face
    # it owns would count the same body many times.
    target = {
        "edge": TopAbs_ShapeEnum.TopAbs_EDGE,
        "vertex": TopAbs_ShapeEnum.TopAbs_VERTEX,
        "solid": TopAbs_ShapeEnum.TopAbs_SOLID,
    }[kind]
    downcast = {"edge": TopoDS.Edge, "vertex": TopoDS.Vertex, "solid": TopoDS.Solid}[kind]
    table = occ.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, target, table)
    return [downcast(table.FindKey(index)) for index in range(1, table.Extent() + 1)]


def face_list(shape: Any) -> list[Any]:  # Any: OCP TopoDS_Shape in, TopoDS_Face out
    """The faces of ``shape`` in ``TopExp_Explorer`` order with repeats: face id = list index."""
    return subshape_list(shape, "face")


def face_table(shape: Any) -> FaceTable:  # Any: OCP TopoDS_Shape
    """The faces of ``shape`` by hash, for many :func:`ids_of` lookups against one shape. The
    hash of an OCP shape ignores its orientation, as ``IsSame`` does, so it only narrows."""
    table: FaceTable = {}
    for index, face in enumerate(face_list(shape)):
        table.setdefault(hash(face), []).append((index, face))
    return table


def ids_of(
    shape: Any,  # Any: OCP TopoDS_Shape
    faces: Iterable[Any],  # Any: OCP TopoDS_Shape (faces; other kinds match nothing)
    *,
    table: FaceTable | None = None,
) -> list[int]:
    """The ids in ``shape`` of the given faces, by ``IsSame`` (orientation ignored), sorted and
    unique; a face visited twice gives both ids. Pass ``table`` (:func:`face_table`) to look up
    many face lists against one shape."""
    lookup = face_table(shape) if table is None else table
    found: set[int] = set()
    for face in faces:
        for index, candidate in lookup.get(hash(face), ()):
            if candidate.IsSame(face):
                found.add(index)
    return sorted(found)


def map_history(
    history: Any,  # Any: BRepTools_History, an algorithm with the same questions, or None
    inputs: Sequence[OpInput],
    output: Any,  # Any: OCP TopoDS_Shape
    kind: str = "face",
) -> dict[str, dict[int, list[int]]]:
    """Per input part, input sub-shape id -> output ids (``Modified``, ``Generated``,
    ``IsRemoved``). Without a history a sub-shape maps only to itself, when the output holds it.

    ``kind`` is ``"face"`` (the original behaviour), ``"edge"`` or ``"vertex"`` (T-056-fix). The
    three do **not** share one enumeration, and saying they did was wrong:

    * a **face** is its index in `TopExp_Explorer` order **with repeats** — that is how the mesh
      numbers faces, and ``face_table`` is the same walk;
    * an **edge** or a **vertex** is its index in `TopExp.MapShapes_s` order, **once each**.

    Both are the numbering a member's ids use, so a mapped id still means the same everywhere; what
    differs is which walk assigns it. The distinction matters because exploring with repeats gives
    one shape several ids, and an edge id that depended on which face happened to be walked past
    would not be stable between two runs.

    Several inputs may share one TShape (the same part twice); each input part keeps its own
    map. Sub-shapes absent from the result are unmapped: removed, or rebuilt into ones the output no
    longer holds.
    """
    lookup = subshape_table(output, kind)
    mapped: dict[str, dict[int, list[int]]] = {}
    for item in inputs:
        found: dict[int, list[int]] = {}
        for index, shape in enumerate(subshape_list(item.shape, kind)):
            successors = _successors(history, shape, kind)
            if successors is None:
                continue
            ids = ids_of(output, successors, table=lookup)
            if ids:
                found[index] = ids
        mapped[item.part] = found
    return mapped


def subshape_table(shape: Any, kind: str) -> FaceTable:  # Any: OCP TopoDS_Shape
    """The sub-shapes of ``kind`` by hash, for many :func:`ids_of` lookups against one shape."""
    table: FaceTable = {}
    for index, item in enumerate(subshape_list(shape, kind)):
        table.setdefault(hash(item), []).append((index, item))
    return table


def _successors(history: Any, shape: Any, kind: str = "face") -> list[Any] | None:  # Any: OCP
    """What ``shape`` became: its modified and generated successors of the same kind, itself when
    the
    history did not touch it, or None when it was removed."""
    from OCP.TopAbs import TopAbs_ShapeEnum

    if history is None:
        return [shape]
    removed = getattr(history, "IsRemoved", None) or getattr(history, "IsDeleted", None)
    if removed is not None and removed(shape):
        return None
    wanted = {
        "face": TopAbs_ShapeEnum.TopAbs_FACE,
        "edge": TopAbs_ShapeEnum.TopAbs_EDGE,
        "vertex": TopAbs_ShapeEnum.TopAbs_VERTEX,
    }[kind]
    found = [
        s for s in (*history.Modified(shape), *history.Generated(shape)) if s.ShapeType() == wanted
    ]
    return found or [shape]
