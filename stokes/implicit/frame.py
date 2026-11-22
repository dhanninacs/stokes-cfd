"""Affine frames of implicit geometry (ADR-0035 decision 5).

A :class:`Frame` maps a point where the field is evaluated to the point in a body's own
coordinates: ``p -> L p + b``. Transforms are composed here, in float64, and applied once at the
coordinate leaves (:meth:`Frame.leaves`), so a chain of transforms costs one affine expression per
coordinate instead of a chain of ``add`` and ``mul`` nodes, and no precision is lost to the f32
arithmetic of the mesher along the way.

Plain Python floats: the frames are 3 x 4 and composing them needs no numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from stokes.implicit.errors import ImplicitInvalid
from stokes.implicit.graph import Expr, X, Y, Z, lift

Vec3 = tuple[float, float, float]
Mat3 = tuple[Vec3, Vec3, Vec3]

_IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_ZERO: Vec3 = (0.0, 0.0, 0.0)
AXIS_VECTORS: dict[str, Vec3] = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _matmul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )  # type: ignore[return-value]  # three rows of three


def _apply(m: Mat3, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _det(m: Mat3) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _inverse(m: Mat3) -> Mat3:
    det = _det(m)
    if not math.isfinite(det) or abs(det) < 1e-300:
        raise ImplicitInvalid("a transform is singular")
    c = (
        (
            m[1][1] * m[2][2] - m[1][2] * m[2][1],
            m[0][2] * m[2][1] - m[0][1] * m[2][2],
            m[0][1] * m[1][2] - m[0][2] * m[1][1],
        ),
        (
            m[1][2] * m[2][0] - m[1][0] * m[2][2],
            m[0][0] * m[2][2] - m[0][2] * m[2][0],
            m[0][2] * m[1][0] - m[0][0] * m[1][2],
        ),
        (
            m[1][0] * m[2][1] - m[1][1] * m[2][0],
            m[0][1] * m[2][0] - m[0][0] * m[2][1],
            m[0][0] * m[1][1] - m[0][1] * m[1][0],
        ),
    )
    return tuple(tuple(v / det for v in row) for row in c)  # type: ignore[return-value]


@dataclass(frozen=True)
class Frame:
    """``p -> linear @ p + offset``: from where the field is evaluated to a body's coordinates."""

    linear: Mat3 = _IDENTITY
    offset: Vec3 = _ZERO

    def __post_init__(self) -> None:
        values = [*self.offset, *(v for row in self.linear for v in row)]
        if not all(math.isfinite(v) for v in values):
            raise ImplicitInvalid("a transform has a value that is not finite")

    def then(self, inner: Frame) -> Frame:
        """The frame that applies ``self`` first and ``inner`` after: ``inner(self(p))``."""
        return Frame(
            _matmul(inner.linear, self.linear),
            tuple(
                a + b for a, b in zip(_apply(inner.linear, self.offset), inner.offset, strict=True)
            ),  # type: ignore[arg-type]
        )

    def inverse(self) -> Frame:
        inv = _inverse(self.linear)
        return Frame(inv, tuple(-v for v in _apply(inv, self.offset)))  # type: ignore[arg-type]

    def map(self, point: Vec3) -> Vec3:
        moved = _apply(self.linear, point)
        return (moved[0] + self.offset[0], moved[1] + self.offset[1], moved[2] + self.offset[2])

    def scale(self) -> float:
        """How much the frame stretches lengths (the cube root of |det|): 1 for a rigid motion."""
        return float(abs(_det(self.linear)) ** (1.0 / 3.0))

    def leaves(self) -> tuple[Expr, Expr, Expr]:
        """The body coordinates as expressions of x, y and z. A coefficient of 0 leaves its term
        out and a coefficient of 1 takes the coordinate as it is, so the identity frame gives the
        bare leaves."""
        out: list[Expr] = []
        for row, shift in zip(self.linear, self.offset, strict=True):
            terms: list[Expr] = []
            for coefficient, leaf in zip(row, (X, Y, Z), strict=True):
                if coefficient == 0.0:
                    continue
                terms.append(leaf if coefficient == 1.0 else leaf * coefficient)
            expr: Expr = terms[0] if terms else lift(0.0)
            for term in terms[1:]:
                expr = expr + term
            if shift != 0.0:
                expr = expr + shift
            out.append(expr)
        return out[0], out[1], out[2]


IDENTITY = Frame()


def translation(offset: Vec3) -> Frame:
    """The frame of a body moved by ``offset``: a point p maps to p - offset in the body."""
    return Frame(_IDENTITY, (-offset[0], -offset[1], -offset[2]))


def rotation(axis: Vec3, angle: float, centre: Vec3 = _ZERO) -> Frame:
    """The frame of a body rotated by ``angle`` (radians) about ``axis`` through ``centre``."""
    norm = math.sqrt(sum(a * a for a in axis))
    if not norm > 0.0:
        raise ImplicitInvalid("a rotation needs an axis of non-zero length")
    kx, ky, kz = (a / norm for a in axis)
    c, s = math.cos(-angle), math.sin(-angle)  # the frame undoes the body's rotation
    t = 1.0 - c
    m: Mat3 = (
        (t * kx * kx + c, t * kx * ky - s * kz, t * kx * kz + s * ky),
        (t * kx * ky + s * kz, t * ky * ky + c, t * ky * kz - s * kx),
        (t * kx * kz - s * ky, t * ky * kz + s * kx, t * kz * kz + c),
    )
    return (
        translation(centre).then(Frame(m)).then(translation((-centre[0], -centre[1], -centre[2])))
    )


def mirror(normal: Vec3, point: Vec3 = _ZERO) -> Frame:
    """The frame of a body mirrored in the plane through ``point`` with ``normal``."""
    norm = math.sqrt(sum(a * a for a in normal))
    if not norm > 0.0:
        raise ImplicitInvalid("a mirror needs a plane normal of non-zero length")
    n = tuple(a / norm for a in normal)
    m = tuple(
        tuple((1.0 if i == j else 0.0) - 2.0 * n[i] * n[j] for j in range(3)) for i in range(3)
    )
    reflect = Frame(m)  # type: ignore[arg-type]
    return translation(point).then(reflect).then(translation((-point[0], -point[1], -point[2])))


def uniform_scale(factor: float, centre: Vec3 = _ZERO) -> Frame:
    """The frame of a body scaled by ``factor`` about ``centre``."""
    if not (math.isfinite(factor) and factor > 0.0):
        raise ImplicitInvalid("a scale factor must be positive", details={"factor": factor})
    inv = 1.0 / factor
    scale = Frame(((inv, 0.0, 0.0), (0.0, inv, 0.0), (0.0, 0.0, inv)))
    return translation(centre).then(scale).then(translation((-centre[0], -centre[1], -centre[2])))
