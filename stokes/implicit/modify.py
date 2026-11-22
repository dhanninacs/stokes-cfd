"""The direct-modelling tools on implicit bodies (ADR-0036 decisions 5 to 8): split by a plane,
offset and shell, linear and polar patterns, and smoothly blended booleans.

Lengths are kernel units (mm). Every builder keeps the rules of ADR-0035 decision 5 and says how it
changes the body's metric: an offset or a shell is exact only on an exact field, approximate on a
bound one, and refused on a scaled one, whose values are not distances.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from stokes.implicit.errors import ImplicitInvalid
from stokes.implicit.features import Body, Box, transformed
from stokes.implicit.frame import AXIS_VECTORS, Frame, Vec3, rotation, translation
from stokes.implicit.graph import Expr, lift, maximum, minimum

MAX_COPIES = 1000


def _axis_sign(normal: str) -> tuple[int, float]:
    if len(normal) != 2 or normal[0] not in "+-" or normal[1] not in AXIS_VECTORS:
        raise ImplicitInvalid(f"unknown normal {normal!r}", details={"normal": normal})
    return "xyz".index(normal[1]), 1.0 if normal[0] == "+" else -1.0


# ------------------------------------------------------------------------------- split
def split(body: Body, point: Vec3, normal: str) -> tuple[Body, Body]:
    """``body`` cut by the plane through ``point`` with ``normal``: (the side behind the normal,
    the side in front). :class:`ImplicitInvalid` when the plane misses the body's box."""
    index, sign = _axis_sign(normal)
    low, high = body.bounded()
    at = point[index]
    if not low[index] < at < high[index]:
        raise ImplicitInvalid(
            "the split plane does not cut the body: move it inside the body's extent",
            details={"plane": at, "low": low[index], "high": high[index]},
        )

    def side(keep_behind: bool) -> Body:
        def make(frame: Frame) -> Expr:
            coordinate = frame.leaves()[index]
            plane = (coordinate - at) * sign
            return maximum(body.make(frame), plane if keep_behind else -plane)

        lo, hi = list(low), list(high)
        behind_is_low = sign > 0
        if keep_behind == behind_is_low:
            hi[index] = at
        else:
            lo[index] = at
        return Body(make, ((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])), body.smallest, "bound")

    return side(True), side(False)


# ------------------------------------------------------------------------------- offset, shell
def _distance_metric(body: Body, what: str) -> None:
    if body.metric == "scaled":
        raise ImplicitInvalid(
            f"{what} needs a body whose field is a distance; this one's is not (a lattice or an "
            "ellipse): change its own size or wall thickness instead",
            details={"code": "implicit.not_a_distance"},
        )


def _grown(box: Box, by: float) -> Box:
    low, high = box
    return (
        (low[0] - by, low[1] - by, low[2] - by),
        (high[0] + by, high[1] + by, high[2] + by),
    )


def offset(body: Body, distance: float) -> Body:
    """``body`` grown by ``distance`` (shrunk when negative): ``φ - d``."""
    _distance_metric(body, "an offset")
    if not math.isfinite(distance) or distance == 0.0:
        raise ImplicitInvalid("an offset needs a distance other than zero")
    if distance < 0 and -2 * distance >= body.smallest:
        raise ImplicitInvalid(
            "the offset would shrink the body away: its smallest dimension is "
            f"{body.smallest:.4g} mm",
            details={"smallest": body.smallest},
        )

    def make(frame: Frame) -> Expr:
        return body.make(frame) - distance

    box = _grown(body.box, max(distance, 0.0))
    return Body(make, box, body.smallest + 2 * distance, body.metric)


def shell(body: Body, thickness: float, outward: bool) -> Body:
    """A wall of ``thickness`` on the inside of the surface (or the outside when ``outward``)."""
    _distance_metric(body, "a shell")
    if not (math.isfinite(thickness) and thickness > 0.0):
        raise ImplicitInvalid("a shell's thickness must be positive")
    if not outward and 2 * thickness >= body.smallest:
        raise ImplicitInvalid(
            "the wall is as thick as the body: a shell inside needs a thickness below half the "
            f"body's smallest dimension ({body.smallest:.4g} mm)",
            details={"smallest": body.smallest},
        )

    def make(frame: Frame) -> Expr:
        field = body.make(frame)
        if outward:
            return maximum(field - thickness, -field)
        return maximum(field, -field - thickness)

    box = _grown(body.box, thickness) if outward else body.box
    return Body(make, box, thickness, "bound")


# ------------------------------------------------------------------------------- patterns
def _union_of(copies: list[Body]) -> Body:
    def make(frame: Frame) -> Expr:
        field = copies[0].make(frame)
        for other in copies[1:]:
            field = minimum(field, other.make(frame))
        return field

    low = tuple(min(c.box[0][i] for c in copies) for i in range(3))
    high = tuple(max(c.box[1][i] for c in copies) for i in range(3))
    metric = copies[0].metric if len(copies) == 1 else "bound"
    return Body(make, (low, high), copies[0].smallest, metric)  # type: ignore[arg-type]


def _count(count: int) -> None:
    if not 1 <= count <= MAX_COPIES:
        raise ImplicitInvalid(f"a pattern has 1 to {MAX_COPIES} copies", details={"count": count})


def linear_pattern(body: Body, axis: str, count: int, pitch: float) -> Body:
    """``count`` copies of ``body``, ``pitch`` apart along ``axis`` (the first where it is)."""
    _count(count)
    direction = AXIS_VECTORS.get(axis)
    if direction is None:
        raise ImplicitInvalid(f"unknown axis {axis!r}", details={"axis": axis})
    if not math.isfinite(pitch) or (count > 1 and pitch == 0.0):
        raise ImplicitInvalid("a pattern's pitch must be a distance other than zero")
    copies = [
        transformed(
            body,
            translation(
                (direction[0] * k * pitch, direction[1] * k * pitch, direction[2] * k * pitch)
            ),
        )
        for k in range(count)
    ]
    return _union_of(copies)


def polar_pattern(body: Body, axis: str, count: int, centre: Vec3, angle: float) -> Body:
    """``count`` copies of ``body`` about ``axis`` through ``centre``: over a full turn (equal
    spacing, ``angle`` >= 2 pi) or over ``angle`` from the first copy to the last."""
    _count(count)
    direction = AXIS_VECTORS.get(axis)
    if direction is None:
        raise ImplicitInvalid(f"unknown axis {axis!r}", details={"axis": axis})
    if not (math.isfinite(angle) and angle > 0.0):
        raise ImplicitInvalid("a polar pattern's angle must be positive")
    full = angle >= 2 * math.pi - 1e-9
    step = (2 * math.pi / count) if full else (angle / max(1, count - 1))
    copies = [transformed(body, rotation(direction, k * step, centre)) for k in range(count)]
    return _union_of(copies)


# ------------------------------------------------------------------------------- blends
def _smooth(join: str, k: float) -> Callable[[Expr, Expr], Expr]:
    """The polynomial smooth minimum (``min``) or maximum (``max``) of radius ``k``:
    ``min(a, b) - h² k / 4`` with ``h = max(k - |a - b|, 0) / k``."""

    def blend(a: Expr, b: Expr) -> Expr:
        h = maximum(lift(k) - (a - b).abs(), lift(0.0)) * (1.0 / k)
        bump = h.square() * (k / 4.0)
        return minimum(a, b) - bump if join == "min" else maximum(a, b) + bump

    return blend


def blended_union(bodies: list[Body], radius: float) -> Body:
    """The union with every seam rounded by ``radius``."""
    blend = _smooth("min", radius)

    def make(frame: Frame) -> Expr:
        field = bodies[0].make(frame)
        for other in bodies[1:]:
            field = blend(field, other.make(frame))
        return field

    grow = radius / 4
    low = tuple(min(b.box[0][i] for b in bodies) - grow for i in range(3))
    high = tuple(max(b.box[1][i] for b in bodies) + grow for i in range(3))
    return Body(make, (low, high), min(b.smallest for b in bodies), "bound")  # type: ignore[arg-type]


def blended_intersection(bodies: list[Body], radius: float) -> Body:
    blend = _smooth("max", radius)

    def make(frame: Frame) -> Expr:
        field = bodies[0].make(frame)
        for other in bodies[1:]:
            field = blend(field, other.make(frame))
        return field

    low = tuple(max(b.box[0][i] for b in bodies) for i in range(3))
    high = tuple(min(b.box[1][i] for b in bodies) for i in range(3))
    if any(lo >= hi for lo, hi in zip(low, high, strict=True)):
        raise ImplicitInvalid("the bodies of the intersection do not overlap")
    return Body(make, (low, high), min(b.smallest for b in bodies), "bound")  # type: ignore[arg-type]


def blended_difference(body: Body, tools: list[Body], radius: float) -> Body:
    blend = _smooth("max", radius)

    def make(frame: Frame) -> Expr:
        field = body.make(frame)
        for tool in tools:
            field = blend(field, -tool.make(frame))
        return field

    return Body(make, body.box, min(b.smallest for b in [body, *tools]), "bound")


# ------------------------------------------------------------------------------- lattices
LATTICES: tuple[str, ...] = ("gyroid", "schwarz_p", "diamond")
LATTICE_FORMS: tuple[str, ...] = ("sheet", "network", "channel_a", "channel_b")
LATTICE_CHANNELS: tuple[str, ...] = ("channel_a", "channel_b")
"""The two channel networks either side of a sheet of the same cell and thickness: with the sheet
they fill the body exactly: a two-fluid heat exchanger's wall, hot side and cold side."""
MAX_LATTICE_CELLS = 48
"""Cells along the longest side of the body: beyond it the surface is too fine to mesh and draw."""
MAX_LATTICE_BIAS = 0.8
"""How far a network's level may move, as a share of the field's range: at 1 one side vanishes."""
_LATTICE_SLOPE = {"gyroid": 1.525, "schwarz_p": 1.306, "diamond": 1.489}
"""The mean of ``|grad f|`` on ``f = 0`` for a period of 2 pi (measured on a 160^3 grid):
dividing the field by it and by ``2 pi / cell`` turns a level into about a distance in mm. It varies
over the surface (gyroid and diamond 1.41 to 1.73, Schwarz P 1.00 to 1.73), so a sheet's wall is
its thickness on average, thinner and thicker by that much in places."""
_LATTICE_RANGE = {"gyroid": 1.5, "schwarz_p": 3.0, "diamond": math.sqrt(2.0)}
"""The largest value of each field: a network's bias is a share of it."""


def _tpms(kind: str, u: Expr, v: Expr, w: Expr) -> Expr:
    """The triply periodic field of ``kind`` at the phase angles ``u``, ``v``, ``w`` (radians)."""
    if kind == "gyroid":
        return u.sin() * v.cos() + v.sin() * w.cos() + w.sin() * u.cos()
    if kind == "schwarz_p":
        return u.cos() + v.cos() + w.cos()
    if kind == "diamond":
        su, sv, sw = u.sin(), v.sin(), w.sin()
        cu, cv, cw = u.cos(), v.cos(), w.cos()
        return su * sv * sw + su * cv * cw + cu * sv * cw + cu * cv * sw
    raise ImplicitInvalid(f"unknown lattice {kind!r}", details={"kind": kind})


def lattice(
    body: Body,
    kind: str,
    form: str,
    cell: float,
    thickness: float,
    bias: float,
    origin: Vec3,
) -> Body:
    """``body`` filled with a triply periodic minimal surface: what of the lattice lies inside it.

    ``sheet`` keeps a wall of about ``thickness`` on the surface itself (two separate channel
    networks either side of it, the heat-exchanger form); ``network`` keeps one side of the
    surface, a single solid network and a single void, with ``bias`` in -0.8 to 0.8 moving the
    surface towards one side (0 is half the volume each); ``channel_a`` and ``channel_b`` are the
    two channel networks either side of the sheet of the same ``thickness``, so the three together
    fill the body with no gap and no overlap. ``cell`` is the period along every axis
    and ``origin`` a point where a cell starts.

    The lattice's field is not a distance (``scaled``): it is divided by its mean slope so its
    values are about millimetres, which is what lets the mesher size its cells, and the result
    cannot be offset or shelled; the wall thickness is this operation's own parameter.
    """
    if kind not in LATTICES:
        raise ImplicitInvalid(f"unknown lattice {kind!r}", details={"kind": kind})
    if form not in LATTICE_FORMS:
        raise ImplicitInvalid(f"unknown lattice form {form!r}", details={"form": form})
    if not (math.isfinite(cell) and cell > 0.0):
        raise ImplicitInvalid("a lattice's cell size must be positive")
    low, high = body.bounded()
    longest = max(high[i] - low[i] for i in range(3))
    if longest / cell > MAX_LATTICE_CELLS:
        raise ImplicitInvalid(
            f"the lattice would have {longest / cell:.0f} cells along the body: at most "
            f"{MAX_LATTICE_CELLS}; use a cell of at least {longest / MAX_LATTICE_CELLS:.3g} mm",
            details={"cells": longest / cell, "limit": MAX_LATTICE_CELLS},
        )
    k = 2.0 * math.pi / cell
    slope = k * _LATTICE_SLOPE[kind]
    if form == "sheet" or form in LATTICE_CHANNELS:
        if not (math.isfinite(thickness) and thickness > 0.0):
            raise ImplicitInvalid("a sheet lattice's wall thickness must be positive")
        # The wall may not fill the cell: beyond about a third of it the channels close.
        if thickness >= cell / 3.0:
            raise ImplicitInvalid(
                "the wall is too thick for the cell: a sheet lattice needs a thickness below a "
                f"third of the cell size ({cell / 3.0:.3g} mm)",
                details={"thickness": thickness, "cell": cell},
            )
        # A channel is what the wall leaves: about a quarter of the cell across at its throats.
        smallest = thickness if form == "sheet" else min(thickness, cell / 8.0)
    else:
        if not (math.isfinite(bias) and abs(bias) <= MAX_LATTICE_BIAS):
            raise ImplicitInvalid(
                f"a network lattice's bias is between -{MAX_LATTICE_BIAS} and {MAX_LATTICE_BIAS}",
                details={"bias": bias},
            )
        smallest = cell / 8.0
    level = bias * _LATTICE_RANGE[kind] if form == "network" else 0.0

    def make(frame: Frame) -> Expr:
        x, y, z = frame.leaves()
        u, v, w = (x - origin[0]) * k, (y - origin[1]) * k, (z - origin[2]) * k
        field = _tpms(kind, u, v, w)
        if form == "sheet":
            cells = field.abs() * (1.0 / slope) - thickness / 2.0
        elif form == "channel_a":  # the side where the field is below the wall
            cells = field * (1.0 / slope) + thickness / 2.0
        elif form == "channel_b":  # the side above it
            cells = thickness / 2.0 - field * (1.0 / slope)
        else:
            cells = (field - level) * (1.0 / slope)
        return maximum(body.make(frame), cells)

    return Body(make, body.box, min(smallest, body.smallest), "scaled")
