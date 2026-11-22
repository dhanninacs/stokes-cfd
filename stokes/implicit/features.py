"""Bodies and their builders (ADR-0035 decisions 5 and 7; slice 1: primitives, transforms and
sharp booleans).

A :class:`Body` is a field waiting for a frame: ``make(frame)`` builds the graph of the body seen
through ``frame`` (from where the field is evaluated to world coordinates), so a transform is a
composition of frames and costs nothing in the graph until the leaves are written. A body also
carries its bounding box in world coordinates, its smallest dimension and its **metric**:

- ``exact``: a signed distance;
- ``bound``: the sign is right and |grad| <= 1 (after ``min`` and ``max``);
- ``scaled``: the sign is right and nothing more (a TPMS field, slice 3).

Lengths are in kernel units (mm). The builder rules of decision 5 hold for every builder here: the
field changes sign across the surface only; no ``sqrt`` of a quantity that is zero on the surface (a
sharp corner is a ``max``, a rounded one a ``sqrt``, switched at :data:`SHARP_RADIUS`).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from stokes.implicit.errors import ImplicitInvalid, ImplicitUnbounded
from stokes.implicit.frame import AXIS_VECTORS, IDENTITY, Frame, Vec3, translation
from stokes.implicit.graph import Expr, lift, maximum, minimum

SHARP_RADIUS = 1e-4
"""Corner radii below this (kernel units, 1e-7 m) are sharp: a rounded form would take the square
root of zero on every flat face, whose gradient is not finite."""
METRICS: tuple[str, ...] = ("exact", "bound", "scaled")
INF = math.inf

Box = tuple[Vec3, Vec3]
"""(lowest corner, highest corner); a coordinate may be infinite."""
UNBOUNDED: Box = ((-INF, -INF, -INF), (INF, INF, INF))


@dataclass(frozen=True)
class Body:
    """A field, its world bounding box, its smallest dimension and its metric."""

    make: Callable[[Frame], Expr]
    box: Box
    smallest: float
    metric: str

    def field(self, frame: Frame = IDENTITY) -> Expr:
        """The graph of the body seen through ``frame``."""
        return self.make(frame)

    def bounded(self) -> Box:
        """The bounding box; :class:`ImplicitUnbounded` when a side is infinite."""
        low, high = self.box
        if not all(math.isfinite(v) for v in (*low, *high)):
            raise ImplicitUnbounded(
                "the part has no finite bounding box: intersect a half-space with a bounded body",
            )
        return self.box


# ------------------------------------------------------------------------------- helpers
def _positive(name: str, *values: float) -> None:
    for value in values:
        if not (math.isfinite(value) and value > 0.0):
            raise ImplicitInvalid(f"{name} must be positive", details={"value": value})


def _axis_frame(axis: str) -> Frame:
    """The frame that turns ``axis`` into the body's own w axis (u, v across it)."""
    if axis == "z":
        return IDENTITY
    if axis == "x":
        return Frame(((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)))
    if axis == "y":
        return Frame(((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
    raise ImplicitInvalid(f"unknown axis {axis!r}", details={"axis": axis})


def _placed(
    local: Callable[[Expr, Expr, Expr], Expr], centre: Vec3, axis: str = "z"
) -> Callable[[Frame], Expr]:
    """A builder that evaluates ``local(u, v, w)`` in a body frame centred on ``centre`` with its w
    axis along ``axis``."""
    own = translation(centre).then(_axis_frame(axis))

    def make(frame: Frame) -> Expr:
        u, v, w = frame.then(own).leaves()
        return local(u, v, w)

    return make


def _axis_box(centre: Vec3, axis: str, across: float, along: float) -> Box:
    """The box of a body of half-width ``across`` and half-length ``along`` on ``axis``."""
    half = [across, across, across]
    half["xyz".index(axis)] = along
    low = (centre[0] - half[0], centre[1] - half[1], centre[2] - half[2])
    high = (centre[0] + half[0], centre[1] + half[1], centre[2] + half[2])
    return low, high


def _cuboid(u: Expr, v: Expr, w: Expr, half: Vec3) -> Expr:
    """The sharp box ``max(|u| - a, |v| - b, |w| - c)``: a bound, exact inside."""
    return maximum(maximum(u.abs() - half[0], v.abs() - half[1]), w.abs() - half[2])


# ------------------------------------------------------------------------------- primitives
def box(size: Vec3, centre: Vec3) -> Body:
    """A box with edge lengths ``size``, centred on ``centre``."""
    _positive("the box's edge lengths", *size)
    half = (size[0] / 2, size[1] / 2, size[2] / 2)
    make = _placed(lambda u, v, w: _cuboid(u, v, w, half), centre)
    low = (centre[0] - half[0], centre[1] - half[1], centre[2] - half[2])
    high = (centre[0] + half[0], centre[1] + half[1], centre[2] + half[2])
    return Body(make, (low, high), min(size), "bound")


def rounded_box(size: Vec3, centre: Vec3, radius: float) -> Body:
    """A box with its edges and corners rounded to ``radius`` (exact). Below
    :data:`SHARP_RADIUS` it is the sharp box."""
    _positive("the box's edge lengths", *size)
    if not (math.isfinite(radius) and radius >= 0.0):
        raise ImplicitInvalid("the corner radius must not be negative", details={"radius": radius})
    if radius < SHARP_RADIUS:
        return box(size, centre)
    half = (size[0] / 2, size[1] / 2, size[2] / 2)
    r = min(radius, *half)

    def local(u: Expr, v: Expr, w: Expr) -> Expr:
        zero = lift(0.0)
        q = (u.abs() - (half[0] - r), v.abs() - (half[1] - r), w.abs() - (half[2] - r))
        outside = (
            maximum(q[0], zero).square()
            + maximum(q[1], zero).square()
            + maximum(q[2], zero).square()
        ).sqrt()
        inside = minimum(maximum(q[0], maximum(q[1], q[2])), zero)
        return outside + inside - r

    low = (centre[0] - half[0], centre[1] - half[1], centre[2] - half[2])
    high = (centre[0] + half[0], centre[1] + half[1], centre[2] + half[2])
    return Body(_placed(local, centre), (low, high), min(size), "exact")


def cylinder(radius: float, length: float, centre: Vec3, axis: str) -> Body:
    """A cylinder of ``radius`` and ``length`` on ``axis``, centred on ``centre`` (mid-length)."""
    _positive("the cylinder's radius and length", radius, length)

    def local(u: Expr, v: Expr, w: Expr) -> Expr:
        return maximum((u.square() + v.square()).sqrt() - radius, w.abs() - length / 2)

    box_ = _axis_box(centre, axis, radius, length / 2)
    return Body(_placed(local, centre, axis), box_, min(2 * radius, length), "bound")


def cone(radius: float, length: float, top_radius: float, centre: Vec3, axis: str) -> Body:
    """A cone or frustum on ``axis``: ``radius`` at the low end, ``top_radius`` at the high end
    (0 for a pointed cone), ``length`` long, centred on ``centre`` (mid-length)."""
    _positive("the cone's length", length)
    if not all(math.isfinite(r) and r >= 0.0 for r in (radius, top_radius)):
        raise ImplicitInvalid("the cone's radii must not be negative")
    if not max(radius, top_radius) > 0.0:
        raise ImplicitInvalid("a cone needs one radius above zero")
    slope = (top_radius - radius) / length
    norm = 1.0 / math.sqrt(1.0 + slope * slope)
    mid = (radius + top_radius) / 2

    def local(u: Expr, v: Expr, w: Expr) -> Expr:
        side = ((u.square() + v.square()).sqrt() - (w * slope + mid)) * norm
        return maximum(side, w.abs() - length / 2)

    widest = max(radius, top_radius)
    box_ = _axis_box(centre, axis, widest, length / 2)
    return Body(_placed(local, centre, axis), box_, min(length, 2 * widest), "bound")


def sphere(radius: float, centre: Vec3) -> Body:
    """A sphere (exact)."""
    _positive("the sphere's radius", radius)
    make = _placed(lambda u, v, w: (u.square() + v.square() + w.square()).sqrt() - radius, centre)
    return Body(make, _axis_box(centre, "z", radius, radius), 2 * radius, "exact")


def torus(major: float, minor: float, centre: Vec3, axis: str) -> Body:
    """A torus about ``axis``: the tube of radius ``minor`` around a circle of radius ``major``
    (exact). ``minor`` must be below ``major``, so the field has no zero on the axis."""
    _positive("the torus's radii", major, minor)
    if not minor < major:
        raise ImplicitInvalid("the tube radius must be below the ring radius")

    def local(u: Expr, v: Expr, w: Expr) -> Expr:
        ring = (u.square() + v.square()).sqrt() - major
        return (ring.square() + w.square()).sqrt() - minor

    box_ = _axis_box(centre, axis, major + minor, minor)
    return Body(_placed(local, centre, axis), box_, 2 * minor, "exact")


def capsule(radius: float, length: float, centre: Vec3, axis: str) -> Body:
    """A capsule on ``axis``: a cylinder of ``radius`` whose ends are hemispheres, ``length``
    between the centres of the two hemispheres (exact)."""
    _positive("the capsule's radius", radius)
    if not (math.isfinite(length) and length >= 0.0):
        raise ImplicitInvalid("the capsule's length must not be negative")
    half = length / 2

    def local(u: Expr, v: Expr, w: Expr) -> Expr:
        along = w - minimum(maximum(w, lift(-half)), lift(half))
        return (u.square() + v.square() + along.square()).sqrt() - radius

    box_ = _axis_box(centre, axis, radius, half + radius)
    return Body(_placed(local, centre, axis), box_, 2 * radius, "exact")


def half_space(point: Vec3, normal: str) -> Body:
    """Everything on the inner side of the plane through ``point`` whose outward normal is
    ``normal`` (``+x`` ... ``-z``): exact, and unbounded until intersected with a bounded body."""
    if len(normal) != 2 or normal[0] not in "+-" or normal[1] not in AXIS_VECTORS:
        raise ImplicitInvalid(f"unknown normal {normal!r}", details={"normal": normal})
    sign = 1.0 if normal[0] == "+" else -1.0
    index = "xyz".index(normal[1])

    def make(frame: Frame) -> Expr:
        coordinate = frame.leaves()[index]
        return (coordinate - point[index]) * sign

    low = list(UNBOUNDED[0])
    high = list(UNBOUNDED[1])
    if sign > 0:
        high[index] = point[index]
    else:
        low[index] = point[index]
    return Body(make, (tuple(low), tuple(high)), INF, "exact")  # type: ignore[arg-type]


def half_space_block(point: Vec3, normal: str, size: Vec3) -> Body:
    """The half-space as a tool that can be drawn: the block of edge lengths ``size`` on the inner
    side of the plane through ``point``, the plane being one of its faces, centred on ``point``
    across the plane. Cutting a body smaller than the block with it is cutting it with the
    half-space; the block is what the viewport shows of the tool."""
    _positive("the half-space block's size", *size)
    plane = half_space(point, normal)
    sign = 1.0 if normal[0] == "+" else -1.0
    index = "xyz".index(normal[1])
    centre = list(point)
    centre[index] -= sign * size[index] / 2
    block = box(size, (centre[0], centre[1], centre[2]))
    return intersection([plane, block])


# ------------------------------------------------------------------------------- transforms
def transformed(body: Body, motion: Frame) -> Body:
    """``body`` moved by the motion whose frame is ``motion`` (from world coordinates to the
    body's): translation, rotation, mirror or uniform scale. The field is multiplied by the
    motion's scale so a distance stays a distance."""
    stretch = 1.0 / motion.scale()

    def make(frame: Frame) -> Expr:
        inner = body.make(frame.then(motion))
        return inner if stretch == 1.0 else inner * stretch

    return Body(make, _moved_box(body.box, motion.inverse()), body.smallest * stretch, body.metric)


def _moved_box(box_: Box, motion: Frame) -> Box:
    """The world box of ``box_`` carried by ``motion`` (body to world): the box of its corners."""
    low, high = box_
    if not all(math.isfinite(v) for v in (*low, *high)):
        axis_aligned = all(sum(1 for v in row if v != 0.0) == 1 for row in motion.linear)
        if not axis_aligned:
            return UNBOUNDED
    corners = [
        motion.map((x, y, z))
        for x in (low[0], high[0])
        for y in (low[1], high[1])
        for z in (low[2], high[2])
    ]
    clean = [tuple(0.0 if math.isnan(c) else c for c in corner) for corner in corners]
    new_low = tuple(min(c[i] for c in clean) for i in range(3))
    new_high = tuple(max(c[i] for c in clean) for i in range(3))
    return new_low, new_high  # type: ignore[return-value]


# ------------------------------------------------------------------------------- booleans
def union(bodies: list[Body]) -> Body:
    """The union (sharp): ``min`` of the fields."""
    _some(bodies, "a union")
    low = tuple(min(b.box[0][i] for b in bodies) for i in range(3))
    high = tuple(max(b.box[1][i] for b in bodies) for i in range(3))
    return Body(
        _fold(bodies, minimum),
        (low, high),  # type: ignore[arg-type]
        min(b.smallest for b in bodies),
        _metric(bodies),
    )


def intersection(bodies: list[Body]) -> Body:
    """The intersection (sharp): ``max`` of the fields."""
    _some(bodies, "an intersection")
    low = tuple(max(b.box[0][i] for b in bodies) for i in range(3))
    high = tuple(min(b.box[1][i] for b in bodies) for i in range(3))
    if any(lo >= hi for lo, hi in zip(low, high, strict=True)):
        raise ImplicitInvalid("the bodies of the intersection do not overlap")
    return Body(
        _fold(bodies, maximum),
        (low, high),  # type: ignore[arg-type]
        min(b.smallest for b in bodies),
        _metric(bodies),
    )


def difference(body: Body, tools: list[Body]) -> Body:
    """``body`` with ``tools`` removed (sharp): ``max(body, -tool)``."""
    _some(tools, "a difference")

    def make(frame: Frame) -> Expr:
        field = body.make(frame)
        for tool in tools:
            field = maximum(field, -tool.make(frame))
        return field

    return Body(make, body.box, min(b.smallest for b in [body, *tools]), _metric([body, *tools]))


def _some(bodies: list[Body], what: str) -> None:
    if not bodies:
        raise ImplicitInvalid(f"{what} needs at least one body")


def _fold(bodies: list[Body], join: Callable[[Expr, Expr], Expr]) -> Callable[[Frame], Expr]:
    def make(frame: Frame) -> Expr:
        field = bodies[0].make(frame)
        for other in bodies[1:]:
            field = join(field, other.make(frame))
        return field

    return make


def _metric(bodies: list[Body]) -> str:
    """A sharp boolean of several bodies is a bound; of one body it keeps the body's metric."""
    if len(bodies) == 1:
        return bodies[0].metric
    return "scaled" if any(b.metric == "scaled" for b in bodies) else "bound"
