"""What shape a part is, read from its B-rep (T-180).

**The reading half of `stokes.mesh.shape_class`, and it lives here because OCP does.**
`tools/check_forbidden_imports.py` refused the first version, which read the B-rep inside
`stokes.mesh` - a headless package. The vocabulary and the rules are in `stokes.mesh.shape_class`;
this module turns a `TopoDS_Shape` into one of its records.

`BRepAdaptor_Surface`, not the tessellation: a shape guessed from triangles is a guess, and the
adaptor reports what the model actually says the surface is.
"""

from __future__ import annotations

from typing import Any

from stokes.mesh.shape_class import (
    CYLINDER,
    PLANE,
    ShapeClass,
    ShapeReport,
)


def face_kinds(shape: Any) -> tuple[str, ...]:  # Any: OCP TopoDS_Shape
    """Every face's analytic kind, in the B-rep's own order.

    **`BRepAdaptor_Surface`, not the triangles.** A tessellation can only be guessed at; the adaptor
    reports what the model actually says the surface is. `TopoDS.Face` is the cast OCCT needs - the
    explorer yields a bare `TopoDS_Shape`, and the adaptor refuses it.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    kinds: list[str] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        kinds.append(BRepAdaptor_Surface(face).GetType().name)
        explorer.Next()
    return tuple(kinds)


def classify(shape: Any) -> ShapeReport:  # Any: OCP TopoDS_Shape
    """What the part is, or a `ShapeReport` saying `OTHER` and why.

    **The rules are conservative on purpose.** A classification claiming a shape it cannot
    actually decompose is worse than one that refuses, because the user gets a plausible mesh of
    the wrong thing. So each rule demands a fact that can be checked against the faces.
    """
    kinds = face_kinds(shape)
    if not kinds:
        return ShapeReport(
            kind=ShapeClass.OTHER,
            why="the part has no faces, so there is nothing to mesh",
        )
    return _classify_kinds(shape, kinds)


def _classify_kinds(shape: Any, kinds: tuple[str, ...]) -> ShapeReport:
    """The rules, in order, over a part that has faces. Split out so `classify` stays one shape."""
    counts = {kind: kinds.count(kind) for kind in set(kinds)}

    # **All planar is either a box or a general polyhedron.** We can mesh a box in one block; a
    # wedge or an L-shape needs a decomposition we do not have yet, so it is refused rather than
    # filled with a box it is not.
    if set(kinds) == {PLANE}:
        box = _is_axis_aligned_box(shape)
        if box is not None:
            return ShapeReport(kind=ShapeClass.BOX, face_kinds=kinds, why="", extra=box)
        return ShapeReport(
            kind=ShapeClass.OTHER,
            face_kinds=kinds,
            why=(
                f"the part is {len(kinds)} planar faces but not an axis-aligned box; "
                f"a decomposition for this shape is not built yet"
            ),
        )

    # **Concentric cylinders are ONE case, however many of them there are.** A boss has one; a pipe
    # or a tube has TWO - the outer wall and the bore - and a multi-stage tube has more. My first
    # rule demanded exactly one cylinder, so **it refused the owner's own pipe**, which is the shape
    # this work exists for. Measured: a cylinder with a bore classifies as
    # `(Cylinder, Plane, Plane, Cylinder)` and fell through to OTHER.
    #
    # The test is that every face is a cylinder or a plane AND the cylinders share an axis - a
    # part with two cylinders at an angle to each other is not a revolved solid and an O-grid would
    # describe it wrongly.
    if set(kinds) <= {CYLINDER, PLANE} and counts.get(CYLINDER, 0) >= 1:
        return _classify_cylinder(shape, kinds)

    # **Anything with a sphere, torus, cone or spline is refused HERE**, with the shapes named, so
    # the message is about the user's part rather than about our limits in the abstract.
    curved = sorted(k for k in counts if k not in (PLANE, CYLINDER))
    if curved:
        return ShapeReport(
            kind=ShapeClass.OTHER,
            face_kinds=kinds,
            why=(
                f"the part has {', '.join(curved)} surfaces; a block decomposition of "
                f"these is not built yet"
            ),
        )
    return ShapeReport(
        kind=ShapeClass.OTHER,
        face_kinds=kinds,
        why=(f"no decomposition is built for a part with these faces: {', '.join(sorted(counts))}"),
    )


def _classify_cylinder(shape: Any, kinds: tuple[str, ...]) -> ShapeReport:
    """A cylinder is only an O-grid if its axis is straight and its caps are perpendicular.

    **Measured from the surface, not assumed from the kind.** A `GeomAbs_Cylinder` carries its own
    axis and radius, and those are what the O-grid generator needs; a cylinder whose caps are not
    parallel planes is not the shape an O-grid describes.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    axis: tuple[float, float, float] | None = None
    radii: list[float] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType().name == CYLINDER:
            cylinder = surface.Cylinder()
            direction = cylinder.Axis().Direction()
            here = _rounded_direction((direction.X(), direction.Y(), direction.Z()))
            if axis is None:
                axis = here
            elif here != axis:
                # **Coaxial or it is not a revolved solid.** Two cylinders at an angle need a
                # decomposition that puts a block between them, which this is not.
                return ShapeReport(
                    kind=ShapeClass.OTHER,
                    face_kinds=kinds,
                    why=(
                        "the part has cylindrical faces that do not share an axis; an O-grid "
                        "describes a revolved solid and this is not one"
                    ),
                )
            radii.append(float(cylinder.Radius()))
        explorer.Next()
    if axis is None or not radii or min(radii) <= 0.0:
        return ShapeReport(
            kind=ShapeClass.OTHER,
            face_kinds=kinds,
            why="the part has a cylindrical face but no usable axis or radius",
        )
    radii.sort()
    return ShapeReport(
        kind=ShapeClass.CYLINDER,
        face_kinds=kinds,
        axis=axis,
        # **The OUTER radius is what the block structure is built on**; the inner ones are bores
        # that the O-grid's centre block must be carved by. Kept all, because the centre block's
        # size depends on the smallest bore, not the largest wall.
        radius=radii[-1],
        why="",
        extra={"radii": radii, "bores": radii[:-1]},
    )


def _is_axis_aligned_box(shape: Any) -> dict[str, Any] | None:
    """The box's extents when the part is a rectangular box aligned with the axes, else None.

    **Six faces, in three opposing pairs, each pair normal to a different axis.** That is what makes
    a single hex block a faithful description: the block's own faces land on the part's faces, and
    nothing of the part is left outside. A box rotated a few degrees is NOT this, and gets refused -
    the block would not lie on the part.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    normals: list[tuple[float, float, float]] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType().name != PLANE:
            return None
        direction = surface.Plane().Axis().Direction()
        normals.append(_rounded_direction((direction.X(), direction.Y(), direction.Z())))
        explorer.Next()
    if len(normals) != 6:
        return None
    # **Each axis must appear exactly twice, opposing.** `abs` folds +x and -x into one, so six
    # faces give three axes used twice each.
    folded = sorted(_canonical_axis(n) for n in normals)
    if folded != [(0, 0, 1), (0, 0, 1), (0, 1, 0), (0, 1, 0), (1, 0, 0), (1, 0, 0)]:
        return None
    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape, bounds)
    if bounds.IsVoid():
        return None
    # **`CornerMin`/`CornerMax`, not `Get()`**: it returns OCCT's `Limits` struct, which
    # nanobind will not convert to a Python tuple - measured as "Unable to convert function
    # return value". The corners are what the repo's own bounding-box code reads.
    low, high = bounds.CornerMin(), bounds.CornerMax()
    return {"bbox": (low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z())}


def _rounded_direction(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    """A direction with each component snapped to -1, 0 or 1 when it is within a tolerance.

    **OCCT reports a plane normal as a unit vector**, so an axis-aligned face gives
    1.0000000000000002 as often as 1.0. Comparing raw floats would refuse every real box.
    """
    out: list[float] = []
    for value in vector:
        if abs(value) < 1e-9:
            out.append(0.0)
        elif abs(abs(value) - 1.0) < 1e-9:
            out.append(1.0 if value > 0 else -1.0)
        else:
            out.append(value)
    return (out[0], out[1], out[2])


def _canonical_axis(normal: tuple[float, float, float]) -> tuple[int, int, int]:
    """An axis as an unsigned unit triple, so +x and -x fold together."""
    return (
        1 if abs(normal[0]) > 0.5 else 0,
        1 if abs(normal[1]) > 0.5 else 0,
        1 if abs(normal[2]) > 0.5 else 0,
    )
