"""Free 2D profiles for sketches (ADR-0036 decisions 1 to 3).

A sketch is a list of entities in the plane's (u, v), in kernel units (mm): lines, three-point arcs,
circles and Catmull-Rom splines. :func:`loops_of` chains them into closed loops (a circle is one by
itself), tessellates the curves, checks that no loop crosses itself or another and orients them by
nesting (even-odd: outer loops counter-clockwise, holes clockwise). :func:`profile_field` turns the
loops into a field that is exact in sign and a bound in value: each loop a union of convex pieces,
each piece the largest of its edges' half-planes, holes subtracted with ``max``; a whole circle is
the exact circle, and a drawn arc the exact circular segment between its chord and the arc
(ADR-0036, addendum of 2026-11-17). Every face of a pull is then a plane of the graph and every
edge a sharp ``max``, as for a named rectangle, so the mesher's faces come out flat and its edges
straight. (An earlier field, the winding number times the ``sqrt`` of the squared distance to the
nearest segment, broke
the builder rule of ADR-0035 decision 5: its gradient is 0/0 on the boundary, where dual contouring
samples it; see the ADR-0036 addendum of 2026-11-14.)

Pure Python and the graph; numpy only inside the crossing check and the ear clipping.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from stokes.implicit.errors import ImplicitError, ImplicitInvalid
from stokes.implicit.graph import Expr, maximum, minimum

Point = tuple[float, float]
Loop = list[Point]
"""A closed polyline: its points in order, the first not repeated at the end."""

MAX_ENTITIES = 2000
MAX_SEGMENTS = 4096
CHORD = 1e-3
"""Largest distance of a chord from its curve, as a share of the profile's size."""
JOIN = 1e-6
"""Endpoints closer than this (kernel units, 1e-9 m) are one point."""


class OpenProfile(ImplicitError):
    code = "implicit.open_profile"


class SelfIntersecting(ImplicitError):
    code = "implicit.self_intersecting"


@dataclass(frozen=True)
class ArcSpan:
    """A three-point arc's circle and its ends (kernel units), kept beside its polyline so the
    field can be the exact circular segment between the arc and its chord.

    ``interior`` are the polyline's points strictly between the ends: the loop's chord polygon is
    the loop without them."""

    centre: Point
    radius: float
    a: Point
    m: Point
    b: Point
    interior: tuple[Point, ...]


@dataclass(frozen=True)
class Curve:
    """One entity as a polyline from its start to its end (a closed one for a circle)."""

    points: list[Point]
    closed: bool
    circle: tuple[float, float, float] | None = None
    """(centre u, centre v, radius) of a whole circle, whose field is then the exact one."""
    arc: ArcSpan | None = None
    """The exact circle of a three-point arc (its polyline is what the chains and checks use)."""


# ------------------------------------------------------------------------------- entities
def _point(raw: Any, what: str) -> Point:  # Any: JSON
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise ImplicitInvalid(f"{what} is not a point [u, v]")
    u, v = raw
    if any(
        isinstance(c, bool) or not isinstance(c, int | float) or not math.isfinite(c)
        for c in (u, v)
    ):
        raise ImplicitInvalid(f"{what} has a coordinate that is not a finite number")
    return float(u), float(v)


def _segments_for_angle(radius: float, sweep: float, size: float) -> int:
    """Chords of an arc of ``radius`` over ``sweep`` radians, each at most ``CHORD * size`` off
    it."""
    tolerance = max(CHORD * size, 1e-9)
    if radius <= tolerance:
        return 4
    step = 2.0 * math.acos(max(-1.0, 1.0 - tolerance / radius))
    return max(4, min(720, math.ceil(abs(sweep) / max(step, 1e-6))))


def _arc(a: Point, m: Point, b: Point, size: float) -> tuple[list[Point], Point, float]:
    """The arc from a through m to b, as points from a to b, and its circle's centre and radius."""
    ax, ay = a
    bx, by = b
    mx, my = m
    d = 2.0 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if abs(d) < 1e-12 * max(1.0, size * size):
        raise ImplicitInvalid("an arc's three points lie on a line")
    a2, m2, b2 = ax * ax + ay * ay, mx * mx + my * my, bx * bx + by * by
    cx = (a2 * (my - by) + m2 * (by - ay) + b2 * (ay - my)) / d
    cy = (a2 * (bx - mx) + m2 * (ax - bx) + b2 * (mx - ax)) / d
    r = math.hypot(ax - cx, ay - cy)
    ta, tm, tb = (math.atan2(y - cy, x - cx) for x, y in (a, m, b))

    def ccw(start: float, end: float) -> float:
        return (end - start) % (2 * math.pi)

    sweep = ccw(ta, tb)
    if ccw(ta, tm) > sweep:  # m is not on the counter-clockwise way from a to b
        sweep -= 2 * math.pi
    n = _segments_for_angle(r, sweep, size)
    points = [
        (cx + r * math.cos(ta + sweep * i / n), cy + r * math.sin(ta + sweep * i / n))
        for i in range(n + 1)
    ]
    points[0], points[-1] = a, b  # the ends exactly where the user put them, so chains meet
    return points, (cx, cy), r


def _spline(points: list[Point], size: float) -> list[Point]:
    """A centripetal-free (uniform) Catmull-Rom curve through ``points``, ends clamped."""
    if len(points) < 3:
        raise ImplicitInvalid("a spline needs at least three points")
    padded = [points[0], *points, points[-1]]
    span = sum(math.dist(p, q) for p, q in itertools.pairwise(points))
    per = max(4, min(64, math.ceil(span / max(CHORD * size * 50, 1e-9) / max(1, len(points) - 1))))
    out: list[Point] = [points[0]]
    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[i - 1], padded[i], padded[i + 1], padded[i + 2]
        for k in range(1, per + 1):
            t = k / per
            t2, t3 = t * t, t * t * t
            out.append(
                tuple(  # type: ignore[arg-type]
                    0.5
                    * (
                        2 * p1[j]
                        + (-p0[j] + p2[j]) * t
                        + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                        + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3
                    )
                    for j in range(2)
                )
            )
    out[-1] = points[-1]
    return out


def curves_of(  # noqa: PLR0912 - one branch per entity kind
    entities: Sequence[Any], scale: float = 1.0
) -> list[Curve]:  # Any: JSON entities
    """The entities as polylines, coordinates multiplied by ``scale`` (metres to kernel units)."""
    if not isinstance(entities, list | tuple):
        raise ImplicitInvalid("a sketch's entities are not a list")
    if len(entities) > MAX_ENTITIES:
        raise ImplicitInvalid(f"a sketch has more than {MAX_ENTITIES} entities")
    raw: list[tuple[str, dict[str, Any]]] = []
    every: list[Point] = []
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict) or not isinstance(entity.get("kind"), str):
            raise ImplicitInvalid(f"entity {index + 1} has no kind")
        raw.append((entity["kind"], entity))
        for key in ("a", "b", "m", "c"):
            if key in entity:
                every.append(_point(entity[key], f"entity {index + 1}'s {key}"))
        for p in entity.get("points") or []:
            every.append(_point(p, f"entity {index + 1}'s point"))
    if not raw:
        raise ImplicitInvalid("the sketch is empty")
    us = [p[0] for p in every] or [0.0]
    vs = [p[1] for p in every] or [0.0]
    size = max(max(us) - min(us), max(vs) - min(vs), 1e-12) * scale
    curves: list[Curve] = []
    for index, (kind, entity) in enumerate(raw):
        what = f"entity {index + 1}"

        def pt(key: str, entity: dict[str, Any] = entity, what: str = what) -> Point:  # Any: JSON
            u, v = _point(entity.get(key), f"{what}'s {key}")
            return u * scale, v * scale

        if kind == "line":
            a, b = pt("a"), pt("b")
            if math.dist(a, b) <= JOIN:
                raise ImplicitInvalid(f"{what} is a line of no length")
            curves.append(Curve([a, b], False))
        elif kind == "arc":
            a, m, b = pt("a"), pt("m"), pt("b")
            points, centre, radius = _arc(a, m, b, size)
            span = ArcSpan(centre, radius, a, m, b, tuple(points[1:-1]))
            curves.append(Curve(points, False, arc=span))
        elif kind == "circle":
            c = pt("c")
            r = entity.get("r")
            if (
                isinstance(r, bool)
                or not isinstance(r, int | float)
                or not (math.isfinite(r) and r > 0)
            ):
                raise ImplicitInvalid(f"{what}'s radius must be positive")
            radius = float(r) * scale
            n = _segments_for_angle(radius, 2 * math.pi, size)
            points = [
                (
                    c[0] + radius * math.cos(2 * math.pi * i / n),
                    c[1] + radius * math.sin(2 * math.pi * i / n),
                )
                for i in range(n)
            ]
            curves.append(Curve(points, True, (c[0], c[1], radius)))
        elif kind == "spline":
            points = [
                (u * scale, v * scale)
                for u, v in (_point(p, f"{what}'s point") for p in entity.get("points") or [])
            ]
            curves.append(Curve(_spline(points, size), False))
        else:
            raise ImplicitInvalid(f"{what} has an unknown kind {kind!r}")
    return curves


# ------------------------------------------------------------------------------- loops
def _key(p: Point) -> tuple[int, int]:
    return round(p[0] / JOIN), round(p[1] / JOIN)


def loops_of(curves: list[Curve]) -> list[Loop]:
    """The curves chained into closed loops, oriented by nesting; :class:`OpenProfile` or
    :class:`SelfIntersecting` when they do not make a profile."""
    loops: list[Loop] = [list(c.points) for c in curves if c.closed]
    open_curves = [c for c in curves if not c.closed]
    ends: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for index, curve in enumerate(open_curves):
        for side, point in ((0, curve.points[0]), (1, curve.points[-1])):
            ends.setdefault(_key(point), []).append((index, side))
    for key, at in ends.items():
        if len(at) != 2:
            u, v = key[0] * JOIN, key[1] * JOIN
            what = "is not joined to another" if len(at) == 1 else f"joins {len(at)} entities"
            raise OpenProfile(
                f"the profile is not closed: the point ({u:.4g}, {v:.4g}) {what}",
                details={"u": u, "v": v, "entities": len(at)},
            )
    used = [False] * len(open_curves)
    for start in range(len(open_curves)):
        if used[start]:
            continue
        loop: Loop = []
        index, side = start, 0
        while not used[index]:
            used[index] = True
            points = open_curves[index].points if side == 0 else open_curves[index].points[::-1]
            loop.extend(points[:-1])
            end_key = _key(points[-1])
            (a, b) = ends[end_key]
            nxt = b if a == (index, 1 - side) else a
            index, side = nxt
        loops.append(loop)
    for loop in loops:
        if len(loop) < 3:
            raise OpenProfile("a loop of the profile has fewer than three points")
    total = sum(len(loop) for loop in loops)
    if total > MAX_SEGMENTS:
        raise ImplicitInvalid(f"the profile has {total} segments; at most {MAX_SEGMENTS}")
    _check_crossings(loops)
    return _oriented(loops)


def _check_crossings(loops: list[Loop]) -> None:
    import numpy as np

    starts, ends_, owner, order = [], [], [], []
    for li, loop in enumerate(loops):
        for i, p in enumerate(loop):
            starts.append(p)
            ends_.append(loop[(i + 1) % len(loop)])
            owner.append(li)
            order.append(i)
    a = np.asarray(starts)
    b = np.asarray(ends_)
    own = np.asarray(owner)
    idx = np.asarray(order)
    lengths = np.asarray([len(loops[o]) for o in owner])
    d = b - a
    n = len(a)
    for i in range(n):
        j = np.arange(i + 1, n)
        if j.size == 0:
            break
        same = own[j] == own[i]
        gap = np.abs(idx[j] - idx[i])
        neighbour = same & ((gap == 1) | (gap == lengths[i] - 1))
        j = j[~neighbour]
        if j.size == 0:
            continue
        r = d[i]
        s = d[j]
        qp = a[j] - a[i]
        denom = r[0] * s[:, 1] - r[1] * s[:, 0]
        t = (qp[:, 0] * s[:, 1] - qp[:, 1] * s[:, 0]) / np.where(denom == 0, np.inf, denom)
        w = (qp[:, 0] * r[1] - qp[:, 1] * r[0]) / np.where(denom == 0, np.inf, denom)
        hit = (denom != 0) & (t >= 0) & (t <= 1) & (w >= 0) & (w <= 1)
        if bool(hit.any()):
            k = int(j[np.argmax(hit)])
            same_loop = owner[k] == owner[i]
            raise SelfIntersecting(
                "a loop of the profile crosses itself"
                if same_loop
                else "two loops of the profile cross",
                details={"near": [float(a[i][0]), float(a[i][1])]},
            )


def _area(loop: Loop) -> float:
    return 0.5 * sum(
        p[0] * q[1] - q[0] * p[1] for p, q in zip(loop, loop[1:] + loop[:1], strict=True)
    )


def _inside(point: Point, loop: Loop) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(loop, loop[1:] + loop[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _oriented(loops: list[Loop]) -> list[Loop]:
    out: list[Loop] = []
    for i, loop in enumerate(loops):
        depth = sum(1 for j, other in enumerate(loops) if j != i and _inside(loop[0], other))
        counter_clockwise = _area(loop) > 0
        hole = depth % 2 == 1
        out.append(loop if counter_clockwise != hole else loop[::-1])
    return out


# ------------------------------------------------------------------------------- the field
@dataclass(frozen=True)
class Profile:
    """A profile's field in (u, v), its box, its smallest dimension and its metric."""

    field: Callable[[Expr, Expr], Expr]
    u: tuple[float, float]
    v: tuple[float, float]
    smallest: float
    metric: str


Circle = tuple[float, float, float]
"""(centre u, centre v, radius)."""
COLLINEAR = 1e-9
"""A vertex whose two edges turn by less than this (the sine of the angle) is dropped: its plane
is its neighbour's."""
SEAM = 1e-5
"""How far, as a share of the profile's largest coordinate or size, two convex pieces of one loop
overlap across the diagonal they share. Well above float32 rounding (about 1e-7), so the field is
negative on the diagonal and never zero inside the material (the touching-bodies problem of
ADR-0035 decision 19); far below a meshing cell (at least 1e-3 of the size), so it cannot show."""


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _cleaned(loop: Loop) -> Loop:
    """``loop`` counter-clockwise, without the vertices that lie on the line of their neighbours."""
    out = list(loop) if _area(loop) > 0 else list(loop[::-1])
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, p, b = out[i - 1], out[i], out[(i + 1) % len(out)]
            if abs(_cross(a, p, b)) <= COLLINEAR * math.dist(a, p) * math.dist(p, b):
                del out[i]
                changed = True
                break
    return out


def _triangles(loop: Loop) -> list[tuple[int, int, int]]:
    """Ear clipping of a simple counter-clockwise polygon: triangles of vertex indices."""
    import numpy as np

    n = len(loop)
    pts = np.asarray(loop, dtype=np.float64)
    size = float(np.ptp(pts, axis=0).max())
    tol = 1e-12 * size * size
    prev = [(i - 1) % n for i in range(n)]
    nxt = [(i + 1) % n for i in range(n)]
    alive = np.ones(n, dtype=bool)
    reflex = np.zeros(n, dtype=bool)

    def turn(i: int) -> float:
        return _cross(loop[prev[i]], loop[i], loop[nxt[i]])

    for i in range(n):
        reflex[i] = turn(i) <= tol

    def ear(i: int) -> bool:
        if reflex[i]:
            return False
        a, b, c = loop[prev[i]], loop[i], loop[nxt[i]]
        others = np.flatnonzero(reflex & alive)
        others = others[(others != prev[i]) & (others != nxt[i])]
        if others.size == 0:
            return True
        q = pts[others]

        def side(o: Point, d: Point) -> Any:  # Any: a float64 array
            return (d[0] - o[0]) * (q[:, 1] - o[1]) - (d[1] - o[1]) * (q[:, 0] - o[0])

        inside = (side(a, b) >= -tol) & (side(b, c) >= -tol) & (side(c, a) >= -tol)
        return not bool(inside.any())

    out: list[tuple[int, int, int]] = []
    remaining, i, misses = n, 0, 0
    while remaining > 3:
        if ear(i):
            p, q = prev[i], nxt[i]
            out.append((p, i, q))
            alive[i] = False
            nxt[p], prev[q] = q, p
            remaining -= 1
            reflex[p], reflex[q] = turn(p) <= tol, turn(q) <= tol
            i, misses = q, 0
            continue
        i, misses = nxt[i], misses + 1
        if misses > remaining:
            raise ImplicitInvalid(
                "a loop of the profile is too thin to be split into convex pieces",
                details={"near": [loop[i][0], loop[i][1]]},
            )
    out.append((prev[i], i, nxt[i]))
    return out


def _convex_pieces(loop: Loop) -> list[list[int]]:
    """A simple counter-clockwise polygon as convex polygons of its vertex indices
    (Hertel-Mehlhorn: ear clipping, then every diagonal removed, longest first, whose removal keeps
    both of its ends convex)."""
    n = len(loop)
    if all(_cross(loop[i - 1], loop[i], loop[(i + 1) % n]) > 0 for i in range(n)):
        return [list(range(n))]
    size = max(
        max(p[0] for p in loop) - min(p[0] for p in loop),
        max(p[1] for p in loop) - min(p[1] for p in loop),
    )
    tol = 1e-12 * size * size
    pieces: dict[int, list[int]] = {}
    owner: dict[tuple[int, int], int] = {}
    for index, (a, b, c) in enumerate(_triangles(loop)):
        pieces[index] = [a, b, c]
        for edge in ((a, b), (b, c), (c, a)):
            owner[edge] = index
    diagonals = sorted(
        {(min(a, b), max(a, b)) for a, b in owner if (b, a) in owner},
        key=lambda e: (-math.dist(loop[e[0]], loop[e[1]]), e),
    )
    for a, b in diagonals:
        first, second = owner[(a, b)], owner[(b, a)]
        one, two = pieces[first], pieces[second]
        k = one.index(b)
        one_rot = one[k:] + one[:k]  # b ... a: the piece that holds the edge a -> b
        k = two.index(a)
        two_rot = two[k:] + two[:k]  # a ... b: the piece that holds b -> a
        if _cross(loop[one_rot[-2]], loop[a], loop[two_rot[1]]) < -tol:
            continue
        if _cross(loop[two_rot[-2]], loop[b], loop[one_rot[1]]) < -tol:
            continue
        pieces[first] = one_rot + two_rot[1:-1]
        del pieces[second], owner[(a, b)], owner[(b, a)]
        for i, p in enumerate(two):
            edge = (p, two[(i + 1) % len(two)])
            if edge in owner:
                owner[edge] = first
    return list(pieces.values())


def _plane(u: Expr, v: Expr, a: Point, b: Point, slack: float) -> Expr:
    """The signed distance to the line of the counter-clockwise edge a -> b, positive outside,
    less ``slack``. An axis-aligned edge reads one coordinate, as a named rectangle does."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    nx, ny = dy / length, -dx / length
    if dx == 0.0:
        nx, ny = math.copysign(1.0, dy), 0.0
    elif dy == 0.0:
        nx, ny = 0.0, -math.copysign(1.0, dx)
    offset = nx * a[0] + ny * a[1] + slack
    terms: list[Expr] = []
    for coordinate, factor in ((u, nx), (v, ny)):
        if factor == 1.0:
            terms.append(coordinate)
        elif factor == -1.0:
            terms.append(-coordinate)
        elif factor != 0.0:
            terms.append(coordinate * factor)
    value = terms[0] if len(terms) == 1 else terms[0] + terms[1]
    return value if offset == 0.0 else value - offset


def _solid(
    u: Expr, v: Expr, loop: Loop, circle: Circle | None, slack: float, arcs: Sequence[ArcSpan] = ()
) -> Expr:
    """The field of the region one loop bounds, its holes aside: the exact circle, or the polygon
    through the loop's ends (each arc replaced by its chord) with each arc's exact circular segment
    added (it bulges out) or removed (it bulges in)."""
    if circle is not None:
        cu, cv, r = circle
        return ((u - cu).square() + (v - cv).square()).sqrt() - r
    if not arcs:
        return _polygon(u, v, loop, slack)
    removed = {p for arc in arcs for p in arc.interior}
    chord = [p for p in loop if p not in removed]
    extent = max(
        max(p[0] for p in loop) - min(p[0] for p in loop),
        max(p[1] for p in loop) - min(p[1] for p in loop),
    )
    field: Expr | None = None
    if len(chord) >= 3 and abs(_area(chord)) > 1e-12 * extent * extent:
        field = _polygon(u, v, chord, slack)
    for arc in arcs:
        inward = _bulges_in(arc, chord)
        # An added segment reaches `slack` past its chord into the polygon, as the polygon's own
        # pieces overlap on their seams: meeting exactly on the chord left the field zero along it,
        # a level set inside the solid.
        segment = _segment(u, v, arc, 0.0 if inward or field is None else slack)
        if inward:
            assert field is not None  # a segment inside the chord polygon needs that polygon
            field = maximum(field, -segment)
        else:
            field = segment if field is None else minimum(field, segment)
    assert field is not None
    return field


def _segment(u: Expr, v: Expr, arc: ArcSpan, slack: float) -> Expr:
    """The circular segment between an arc and its chord, the side of the chord the arc's middle
    point is on: the disc of the arc's circle intersected with that half-plane (so a major arc's
    segment is the disc less the minor cap). ``slack`` moves the chord's plane that far away from
    the arc, so the segment overlaps what lies behind its chord."""
    cu, cv = arc.centre
    disc = ((u - cu).square() + (v - cv).square()).sqrt() - arc.radius
    if _cross(arc.a, arc.b, arc.m) > 0:  # m left of a -> b: the plane is positive away from m
        half = _plane(u, v, arc.a, arc.b, slack)
    else:
        half = _plane(u, v, arc.b, arc.a, slack)
    return maximum(disc, half)


def _bulges_in(arc: ArcSpan, chord: Loop) -> bool:
    """Whether the arc lies inside the chord polygon (its segment is removed, not added). The point
    halfway between the chord's middle and the arc's middle point is in the segment: inside the
    polygon exactly when the arc bulges in. A polygon of fewer than three points has no inside."""
    if len(chord) < 3:
        return False
    mid = (
        ((arc.a[0] + arc.b[0]) / 2 + arc.m[0]) / 2,
        ((arc.a[1] + arc.b[1]) / 2 + arc.m[1]) / 2,
    )
    return _inside(mid, chord)


def _polygon(u: Expr, v: Expr, loop: Loop, slack: float) -> Expr:
    """The field of a polygon: the union of convex pieces, each the largest of its edges'
    half-planes."""
    clean = _cleaned(loop)
    n = len(clean)
    field: Expr | None = None
    for piece in _convex_pieces(clean):
        part: Expr | None = None
        for i, a in enumerate(piece):
            b = piece[(i + 1) % len(piece)]
            seam = (b - a) % n != 1  # a diagonal between two pieces, not an edge of the loop
            plane = _plane(u, v, clean[a], clean[b], slack if seam else 0.0)
            part = plane if part is None else maximum(part, plane)
        assert part is not None
        field = part if field is None else minimum(field, part)
    assert field is not None
    return field


def profile_field(
    loops: list[Loop],
    circles: Sequence[Circle | None] = (),
    arcs: Sequence[Sequence[ArcSpan]] = (),
) -> Profile:
    """The field of the loops (negative inside, even-odd), as a graph builder.

    Each loop is a union of convex pieces and each piece the largest of its edges' half-planes (a
    bound, exact inside a convex piece). So every flat face of a pull is a plane of the graph and
    every edge between two faces a sharp ``max`` or ``min``, which dual contouring places its
    vertices on exactly (ADR-0036, addendum of 2026-11-14). A loop that is a whole circle
    (``circles[i]`` for loop ``i``) is the exact circle. A loop's arcs (``arcs[i]``) are exact too:
    the polygon through the arcs' ends, plus or minus each arc's exact circular segment (see
    :func:`_segment`). A loop inside an odd number of others is a hole: an outer loop's region is
    ``max(outer, -hole, ...)`` over the holes directly inside it, and the regions are joined with
    ``min``.
    """
    us = [p[0] for loop in loops for p in loop]
    vs = [p[1] for loop in loops for p in loop]
    smallest = min(
        min(
            max(p[0] for p in lp) - min(p[0] for p in lp),
            max(p[1] for p in lp) - min(p[1] for p in lp),
        )
        for lp in loops
    )
    rounds = [*circles, *([None] * (len(loops) - len(circles)))]
    reach = max(*(abs(c) for c in (*us, *vs)), max(us) - min(us), max(vs) - min(vs))
    slack = SEAM * reach
    containers = [
        [j for j, other in enumerate(loops) if j != i and _inside(loop[0], other)]
        for i, loop in enumerate(loops)
    ]
    depth = [len(c) for c in containers]
    parent = [max(c, key=lambda j: depth[j]) if c else None for c in containers]

    loop_arcs = [arcs[i] if i < len(arcs) else () for i in range(len(loops))]

    def field(u: Expr, v: Expr) -> Expr:
        solids = [
            _solid(u, v, loop, rounds[i], slack, loop_arcs[i]) for i, loop in enumerate(loops)
        ]
        joined: Expr | None = None
        for i, solid in enumerate(solids):
            if depth[i] % 2:
                continue
            region = solid
            for j, hole in enumerate(solids):
                if parent[j] == i:
                    region = maximum(region, -hole)
            joined = region if joined is None else minimum(joined, region)
        assert joined is not None
        return joined

    exact = len(loops) == 1 and rounds[0] is not None
    box_u, box_v = (min(us), max(us)), (min(vs), max(vs))
    return Profile(field, box_u, box_v, smallest, "exact" if exact else "bound")


def custom_profile(entities: Sequence[Any], scale: float) -> Profile:  # Any: JSON
    """The profile of a sketch's entities (metres, scaled to kernel units)."""
    curves = curves_of(entities, scale)
    loops = loops_of(curves)
    # loops_of puts the loops of the closed curves first, in their order.
    circles = [c.circle for c in curves if c.closed]
    owner = {p: index for index, loop in enumerate(loops) for p in loop}
    arcs: list[list[ArcSpan]] = [[] for _ in loops]
    for curve in curves:
        # Its interior points are in exactly one loop; an arc too short to have one stays a chord.
        if curve.arc is not None and curve.arc.interior and curve.arc.interior[0] in owner:
            arcs[owner[curve.arc.interior[0]]].append(curve.arc)
    return profile_field(loops, circles, arcs)
