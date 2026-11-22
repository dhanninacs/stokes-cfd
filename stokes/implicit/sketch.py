"""Sketched bodies (ADR-0035 decision 7, `implicit_sketch`): a named 2D profile on a sketch plane,
pulled (extruded) along the plane's normal or revolved about the plane's vertical axis.

The sketch plane is one of ``xy``, ``yz`` or ``zx`` through ``origin``; in it the profile has
coordinates (u, v) and the plane's normal is w:

======  ===  ===  ===
plane    u    v    w
======  ===  ===  ===
xy       x    y    z
yz       y    z    x
zx       z    x    y
======  ===  ===  ===

Profiles, centred on the origin of the plane (on the revolve circle for a revolve):

- ``rectangle`` (width along u, height along v; sharp: a bound), ``rounded_rectangle`` (with a
  corner radius; exact);
- ``circle`` (radius; exact), ``ellipse`` (width and height; the sign is exact, the value scaled);
- ``slot`` (a stadium: two half circles of ``radius`` whose centres are ``slot_length`` apart along
  u; exact);
- ``polygon`` (``sides`` from 3 to 64, ``radius`` to the corners, a flat side at the bottom; a
  bound).

A pull goes from the plane to ``depth`` along +w (or ``depth`` centred on the plane when
symmetric). A revolve turns the profile about the plane's v axis through the origin; the profile's
centre sits ``axis_distance`` from that axis along +u, and the whole profile must stay on that side.
The revolve starts at +u and turns towards +w by ``angle`` (up to a full turn).

Lengths are kernel units (mm), as everywhere in this package. The builder rules of decision 5
hold: no ``sqrt`` of a quantity that is zero on the surface, and a sharp corner is a ``max``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from stokes.implicit.errors import ImplicitInvalid
from stokes.implicit.features import SHARP_RADIUS, Body, Box
from stokes.implicit.frame import Frame, Vec3, translation
from stokes.implicit.graph import Expr, lift, maximum, minimum
from stokes.implicit.profile2d import Profile

PROFILES: tuple[str, ...] = (
    "rectangle",
    "rounded_rectangle",
    "circle",
    "ellipse",
    "slot",
    "polygon",
)
PLANES: tuple[str, ...] = ("xy", "yz", "zx")
METHODS: tuple[str, ...] = ("extrude", "revolve")
MAX_SIDES = 64
FULL_TURN = 2.0 * math.pi

_PLANE_ROWS: dict[str, tuple[Vec3, Vec3, Vec3]] = {
    "xy": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "yz": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "zx": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}
_PLANE_AXES: dict[str, tuple[int, int, int]] = {"xy": (0, 1, 2), "yz": (1, 2, 0), "zx": (2, 0, 1)}
"""For each plane, the world axis of u, of v and of w."""

Profile2D = Callable[[Expr, Expr], Expr]


def _positive(name: str, *values: float) -> None:
    for value in values:
        if not (math.isfinite(value) and value > 0.0):
            raise ImplicitInvalid(f"{name} must be positive", details={"value": value})


# ------------------------------------------------------------------------------- profiles
def profile(  # noqa: PLR0911 - one return per profile
    kind: str,
    *,
    width: float,
    height: float,
    radius: float,
    corner_radius: float,
    slot_length: float,
    sides: int,
) -> tuple[Profile2D, float, float, float, str]:
    """The profile ``kind`` as (field of u and v, half extent along u, half extent along v, its
    smallest dimension, its metric)."""
    if kind == "rectangle":
        _positive("the rectangle's width and height", width, height)
        hu, hv = width / 2, height / 2
        return (
            (lambda u, v: maximum(u.abs() - hu, v.abs() - hv)),
            hu,
            hv,
            min(width, height),
            "bound",
        )
    if kind == "rounded_rectangle":
        _positive("the rectangle's width and height", width, height)
        if not (math.isfinite(corner_radius) and corner_radius >= 0.0):
            raise ImplicitInvalid("the corner radius must not be negative")
        hu, hv = width / 2, height / 2
        r = min(corner_radius, hu, hv)
        if r < SHARP_RADIUS:
            return profile(
                "rectangle",
                width=width,
                height=height,
                radius=radius,
                corner_radius=0.0,
                slot_length=0.0,
                sides=3,
            )

        def rounded(u: Expr, v: Expr) -> Expr:
            zero = lift(0.0)
            qu, qv = u.abs() - (hu - r), v.abs() - (hv - r)
            outside = (maximum(qu, zero).square() + maximum(qv, zero).square()).sqrt()
            return outside + minimum(maximum(qu, qv), zero) - r

        return rounded, hu, hv, min(width, height), "exact"
    if kind == "circle":
        _positive("the circle's radius", radius)
        return (
            (lambda u, v: (u.square() + v.square()).sqrt() - radius),
            radius,
            radius,
            2 * radius,
            "exact",
        )
    if kind == "ellipse":
        _positive("the ellipse's width and height", width, height)
        a, b = width / 2, height / 2
        scale = min(a, b)
        return (
            (
                lambda u, v: (
                    ((u * (1.0 / a)).square() + (v * (1.0 / b)).square()).sqrt() * scale - scale
                )
            ),
            a,
            b,
            min(width, height),
            "scaled",
        )
    if kind == "slot":
        _positive("the slot's radius", radius)
        if not (math.isfinite(slot_length) and slot_length >= 0.0):
            raise ImplicitInvalid("the slot's length must not be negative")
        half = slot_length / 2

        def slot(u: Expr, v: Expr) -> Expr:
            along = maximum(u.abs() - half, lift(0.0))
            return (along.square() + v.square()).sqrt() - radius

        return slot, half + radius, radius, 2 * radius, "exact"
    if kind == "polygon":
        _positive("the polygon's radius", radius)
        if not 3 <= sides <= MAX_SIDES:
            raise ImplicitInvalid(f"a polygon has 3 to {MAX_SIDES} sides", details={"sides": sides})
        apothem = radius * math.cos(math.pi / sides)

        def polygon(u: Expr, v: Expr) -> Expr:
            field: Expr | None = None
            for k in range(sides):
                angle = (
                    -math.pi / 2 + 2 * math.pi * k / sides
                )  # side 0 faces -v: flat at the bottom
                c, s = math.cos(angle), math.sin(angle)
                face = u * c + v * s - apothem
                field = face if field is None else maximum(field, face)
            assert field is not None
            return field

        return polygon, radius, radius, 2 * apothem, "bound"
    raise ImplicitInvalid(f"unknown profile {kind!r}", details={"profile": kind})


# ------------------------------------------------------------------------------- bodies
def _plane_frame(plane: str, origin: Vec3) -> Frame:
    rows = _PLANE_ROWS.get(plane)
    if rows is None:
        raise ImplicitInvalid(f"unknown sketch plane {plane!r}", details={"plane": plane})
    return translation(origin).then(Frame(rows))


def _world_box(
    plane: str,
    origin: Vec3,
    local: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
) -> Box:
    axes = _PLANE_AXES[plane]
    low = [0.0, 0.0, 0.0]
    high = [0.0, 0.0, 0.0]
    for (lo, hi), axis in zip(local, axes, strict=True):
        low[axis] = origin[axis] + lo
        high[axis] = origin[axis] + hi
    return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def named(kind: str, **sizes: Any) -> Profile:  # Any: the sizes of :func:`profile`
    """A named profile as a :class:`Profile`, centred on the plane's origin."""
    field, hu, hv, smallest, metric = profile(kind, **sizes)
    return Profile(field, (-hu, hu), (-hv, hv), smallest, metric)


def extrude(
    shape: Profile,
    plane: str,
    origin: Vec3,
    depth: float,
    symmetric: bool,
) -> Body:
    """The profile pulled along +w by ``depth`` (centred on the plane when ``symmetric``)."""
    _positive("the pull's depth", depth)
    low, high = (-depth / 2, depth / 2) if symmetric else (0.0, depth)
    mid, half = (low + high) / 2, depth / 2
    own = _plane_frame(plane, origin)
    field = shape.field

    def make(frame: Frame) -> Expr:
        u, v, w = frame.then(own).leaves()
        return maximum(field(u, v), (w - mid).abs() - half)

    box_ = _world_box(plane, origin, (shape.u, shape.v, (low, high)))
    metric = "scaled" if shape.metric == "scaled" else "bound"
    return Body(make, box_, min(shape.smallest, depth), metric)


def shifted(shape: Profile, du: float) -> Profile:
    """``shape`` moved by ``du`` along u (a named profile placed ``du`` from a revolve axis)."""
    field = shape.field
    return Profile(
        lambda u, v: field(u - du, v),
        (shape.u[0] + du, shape.u[1] + du),
        shape.v,
        shape.smallest,
        shape.metric,
    )


def revolve(shape: Profile, plane: str, origin: Vec3, angle: float) -> Body:
    """The profile turned about the plane's v axis (u = 0) by ``angle`` (radians, up to a full
    turn), starting at +u and turning towards +w. The whole profile must lie at u > 0."""
    if not (math.isfinite(angle) and 0.0 < angle <= FULL_TURN + 1e-12):
        raise ImplicitInvalid(
            "a revolve turns by more than 0 and at most a full turn", details={"angle": angle}
        )
    if not shape.u[0] > 0.0:
        raise ImplicitInvalid(
            "the profile crosses the revolve axis: it must lie wholly on one side of the "
            "plane's second axis (for a named profile, make its distance from the axis more "
            "than half its width)",
            details={"nearest": shape.u[0]},
        )
    own = _plane_frame(plane, origin)
    outer = shape.u[1]
    field = shape.field

    def make(frame: Frame) -> Expr:
        u, v, w = frame.then(own).leaves()
        radial = (u.square() + w.square()).sqrt()  # zero only on the axis, which the profile avoids
        body = field(radial, v)
        if angle >= FULL_TURN - 1e-9:
            return body
        s, c = math.sin(angle), math.cos(angle)
        start = -w  # the half-space w >= 0, where the turn starts from +u towards +w
        end = w * c - u * s  # inside where the polar angle is at most `angle`
        wedge = maximum(start, end) if angle <= math.pi else minimum(start, end)
        return maximum(body, wedge)

    box_ = _world_box(plane, origin, ((-outer, outer), shape.v, (-outer, outer)))
    metric = "scaled" if shape.metric == "scaled" else "bound"
    return Body(make, box_, shape.smallest, metric)
