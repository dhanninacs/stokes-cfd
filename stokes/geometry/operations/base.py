"""The contract of an operation module (ADR-0021 decision 8): what ``run(ctx)`` receives and
returns.

Every module in :mod:`stokes.geometry.operations` implements ``run(ctx: OpContext) -> OpResult``
and nothing else public. It runs in the geometry worker, in millimetres: every input shape is in
mm (the shape provider scaled it), parameters arrive in SI as JSON values and :meth:`OpContext.mm`
converts lengths, and every output shape is taken as mm. A module raises a
:class:`~stokes.geometry.errors.GeometryError` subclass to fail (``OperationFailed`` when OCCT
cannot make the result, ``OperationInvalid`` for inputs it cannot use).

Import rule: the standard library and :mod:`stokes.geometry.errors` at module level; OCP inside
the methods, as everywhere in the package.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from stokes.geometry.errors import OperationInvalid

MM_PER_M = 1000.0
Progress = Callable[[str, float, str], None]


@dataclass(frozen=True)
class OpInput:
    """One part an operation reads: its node id and name, and its B-rep in mm."""

    part: str
    name: str
    shape: Any  # Any: OCP TopoDS_Shape, in mm
    family: str = "brep"
    """Always ``brep``: mesh-only parts are refused before the job starts."""


@dataclass
class OpOutput:
    """One output of a run: a part named ``name`` made of ``shape`` (mm).

    ``history`` maps input faces to output faces (a ``BRepTools_History`` or an algorithm's
    ``History()``; None when the operation has none, and the session then carries no tags).
    ``tags`` names output faces to put under a tag (created, kind boundary, or extended): the
    enclosure's ``inlet``, the caps of ``cap``. ``report`` is this output's part of the report.
    """

    name: str
    shape: Any  # Any: OCP TopoDS_Shape, in mm
    history: Any | None = None  # Any: BRepTools_History or an algorithm
    tags: dict[str, list[Any]] = field(default_factory=dict)  # Any: TopoDS_Face
    report: dict[str, Any] = field(default_factory=dict)  # Any: JSON


@dataclass
class OpResult:
    """What a run made: its outputs in order (output ``i`` becomes part ``output_id(op, i)``),
    the run's report (JSON) and warnings for the user."""

    outputs: list[OpOutput]
    report: dict[str, Any] = field(default_factory=dict)  # Any: JSON
    warnings: list[str] = field(default_factory=list)


class OpContext:
    """What a run receives: SI parameters, the input shapes in mm, and the tools every module
    needs (unit conversion, face lookup, progress, the before-and-after check).

    ``params`` holds JSON values: quantities as SI floats, arrays as lists of SI floats, enums as
    text, ``json`` parameters as they are stored (members, lists of part ids), references as node
    ids, and, for each tag reference ``name``, the tag's stored members as ``<name>_members``.
    """

    def __init__(
        self,
        params: dict[str, Any],  # Any: JSON values
        inputs: Sequence[OpInput],
        progress: Progress | None = None,
    ) -> None:
        self.params = params
        self.inputs = list(inputs)
        self._progress = progress

    # ------------------------------------------------------------------ units
    @staticmethod
    def mm(metres: float) -> float:
        """A length from SI metres to the millimetres the shapes are in."""
        return float(metres) * MM_PER_M

    @staticmethod
    def mm2(square_metres: float) -> float:
        return float(square_metres) * MM_PER_M**2

    @staticmethod
    def mm3(cubic_metres: float) -> float:
        return float(cubic_metres) * MM_PER_M**3

    # ------------------------------------------------------------------ inputs
    def input(self, part: str) -> OpInput:
        """The input with node id ``part``; :class:`OperationInvalid` when the run has none."""
        for item in self.inputs:
            if item.part == part:
                return item
        raise OperationInvalid(f"part {part} is not an input of this run", details={"part": part})

    def faces(self, members: Sequence[dict[str, Any]]) -> list[Any]:  # Any: JSON in, faces out
        """The faces of the inputs that ``members`` name (``[{"part", "faces": "all" | [id or
        [first, last], ...]}]``, the stored form of a tag), in member order. A member on a part
        that is not an input, or a face id the input does not have, is
        :class:`OperationInvalid`."""
        from stokes.geometry.operations.faces import face_list

        found: list[Any] = []  # Any: TopoDS_Face
        for member in members:
            part = str(member.get("part", ""))
            faces = face_list(self.input(part).shape)
            wanted = member.get("faces", "all")
            if wanted == "all":
                found.extend(faces)
                continue
            if not isinstance(wanted, list):
                raise OperationInvalid(f"the faces of part {part} are not a list or 'all'")
            for entry in wanted:
                first, last = (entry, entry) if isinstance(entry, int) else (entry[0], entry[1])
                if not 0 <= first <= last < len(faces):
                    raise OperationInvalid(
                        f"part {part} has {len(faces)} faces; [{first}, {last}] is out of range",
                        details={"part": part, "faces": len(faces)},
                    )
                found.extend(faces[first : last + 1])
        return found

    def edges(self, members: Sequence[dict[str, Any]]) -> list[Any]:  # Any: JSON in, edges out
        """The B-rep edges of the inputs that ``members`` name, in member order (ADR-0022 §15.4).

        ``members`` is the stored tag form; a member's ``edges`` key holds ids or ranges, ``"all"``
        for every edge, or nothing for none. A member on a part that is not an input, or an edge id
        the input does not have, is :class:`OperationInvalid`. Numbering is ``TopExp.MapShapes``
        order — the same ids the topology record and edge tags use — so a tagged opening loop
        resolves to the very edges the user picked.
        """
        from OCP.TopAbs import TopAbs_EDGE

        return self._topology(members, "edges", TopAbs_EDGE)

    def vertices(self, members: Sequence[dict[str, Any]]) -> list[Any]:  # Any: JSON in, out
        """The B-rep vertices of the inputs that ``members`` name, in member order
        (ADR-0022 §15.4)."""
        from OCP.TopAbs import TopAbs_VERTEX

        return self._topology(members, "vertices", TopAbs_VERTEX)

    def _topology(
        self,
        members: Sequence[dict[str, Any]],
        key: str,
        kind: int,
    ) -> list[Any]:  # Any: OCP TopoDS_Edge or TopoDS_Vertex out
        """The edges or vertices of the inputs that ``members`` name under ``key``."""
        import OCP.collections as occ
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.TopExp import TopExp
        from OCP.TopoDS import TopoDS

        downcast = TopoDS.Edge if kind == TopAbs_EDGE else TopoDS.Vertex
        found: list[Any] = []
        for member in members:
            part = str(member.get("part", ""))
            mapping = occ.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
            # MapShapes, first occurrence wins: exactly how the topology record numbers them.
            TopExp.MapShapes_s(self.input(part).shape, kind, mapping)
            total = int(mapping.Extent())
            wanted = member.get(key, [])
            if wanted == "all":
                found.extend(downcast(mapping.FindKey(i)) for i in range(1, total + 1))
                continue
            if not isinstance(wanted, list):
                raise OperationInvalid(f"the {key} of part {part} are not a list or 'all'")
            for entry in wanted:
                first, last = (entry, entry) if isinstance(entry, int) else (entry[0], entry[1])
                if not 0 <= first <= last < total:
                    raise OperationInvalid(
                        f"part {part} has {total} {key}; [{first}, {last}] is out of range",
                        details={"part": part, key: total},
                    )
                found.extend(downcast(mapping.FindKey(i + 1)) for i in range(first, last + 1))
        return found

    # ------------------------------------------------------------------ reporting
    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        """Report a stage and how far the run is (0 to 1); the session shows it, at most ten
        times a second."""
        if self._progress is not None:
            self._progress(stage, min(1.0, max(0.0, float(fraction))), message)

    def check(self, shape: Any) -> dict[str, Any]:  # Any: OCP TopoDS_Shape in mm; JSON out
        """The summary every operation reports before and after: OCCT validity, whether the
        shape is closed (no free edges), its volume (m^3), bounding box (m) and counts."""
        return check_shape(shape)


def check_shape(shape: Any) -> dict[str, Any]:  # Any: OCP TopoDS_Shape in mm; JSON out
    """:meth:`OpContext.check` for any shape in mm."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_ShapeEnum

    from stokes.geometry.brep import SMALL_EDGE_M, _count, _free_edges

    faces = _count(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    free = _free_edges(shape, SMALL_EDGE_M * MM_PER_M)
    props = GProp_GProps()
    try:
        BRepGProp.VolumeProperties_s(shape, props)
        volume = float(props.Mass()) / MM_PER_M**3
    except Exception:  # OCCT's failures share no base class; an open shell has no volume
        volume = 0.0
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    bbox: list[float] = []
    if not box.IsVoid():
        low, high = box.CornerMin(), box.CornerMax()  # Bnd_Box.Get fails in OCP 8.0.1
        bbox = [v / MM_PER_M for v in (low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z())]
    return {
        "valid": bool(BRepCheck_Analyzer(shape).IsValid()),
        "closed": faces > 0 and free == 0,
        "volume": volume,
        "bbox": bbox,
        "faces": faces,
        "free_edges": free,
        "solids": _count(shape, TopAbs_ShapeEnum.TopAbs_SOLID),
        "shells": _count(shape, TopAbs_ShapeEnum.TopAbs_SHELL),
    }
