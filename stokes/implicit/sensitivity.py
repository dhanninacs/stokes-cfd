"""Shape derivatives of an implicit body (ADR-0035 decisions 26 and 27).

A parameter p moves the surface φ(x; p) = 0 along its normal at the speed

    V_n = -(∂φ/∂p) / |∇φ|

(kernel units per unit of p), with n_φ = ∇φ/|∇φ| the normal that points out of the body. An
adjoint's surface sensitivity s (objective per unit area and per unit wall displacement along the
patch normal n, decision 26) then gives the parameter gradient by one surface sum,

    dJ/dp = Σ over triangles of  s · V_n · (n_φ · n) · A.

Both derivatives are central differences in float64 with the numpy evaluator: ∂φ/∂p of the graph
rebuilt at p ± h, ∇φ in space at a step of 1e-6 of the body's size. Points where |∇φ| is not finite
or below :data:`MIN_GRADIENT` are left out and counted, and the result carries the share of the
area they cover. With s = 1 and n = n_φ the sum is the derivative of the body's volume, which is
how the tests check it.

Pure functions over numpy arrays: no tree, no files. :mod:`stokes.geometry.sensitivity` builds the
graphs from the operation nodes. Import rule: numpy inside the functions (the package's rule).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.implicit.errors import ImplicitInvalid
from stokes.implicit.evaluate import evaluate
from stokes.implicit.graph import Expr

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    F64 = npt.NDArray[np.float64]
    Mask = npt.NDArray[np.bool_]

MIN_GRADIENT = 1e-6
"""|∇φ| below this (or not finite) leaves a point out: the normal is not defined there."""

SPACE_STEP = 1e-6
"""The spatial step of ∇φ, as a share of the body's largest extent."""


@dataclass(frozen=True)
class NormalVelocity:
    """The surface speed of one parameter at a set of points."""

    speed: F64
    """V_n at each point, kernel units per unit of the parameter; 0 where ``valid`` is False."""
    normal: F64
    """n_φ, (n, 3); zeros where ``valid`` is False."""
    valid: Mask
    """Where |∇φ| is finite and at least :data:`MIN_GRADIENT`."""


def gradient_of_field(field: Expr, points: Any, step: float) -> F64:  # Any: array-like (n, 3)
    """∇φ at ``points`` by central differences of ``step`` (kernel units)."""
    import numpy as np

    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    out = np.empty_like(pts)
    for axis in range(3):
        shift = np.zeros(3)
        shift[axis] = step
        out[:, axis] = (evaluate(field, pts + shift) - evaluate(field, pts - shift)) / (2 * step)
    return out


def normal_velocity(
    field_at: Callable[[float], Expr],
    value: float,
    step: float,
    points: Any,  # Any: array-like (n, 3), kernel units
    size: float,
) -> NormalVelocity:
    """V_n of the parameter whose value is ``value`` at ``points`` on the surface.

    ``field_at(v)`` is the body's graph with the parameter set to ``v``; ``step`` is the
    parameter's difference step h (in its own units) and ``size`` the body's largest extent, which
    scales the spatial step.
    """
    import numpy as np

    if not (math.isfinite(step) and step > 0.0):
        raise ImplicitInvalid("a sensitivity needs a positive difference step")
    if not (math.isfinite(size) and size > 0.0):
        raise ImplicitInvalid("a sensitivity needs the body's size")
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    d_phi = (evaluate(field_at(value + step), pts) - evaluate(field_at(value - step), pts)) / (
        2 * step
    )
    grad = gradient_of_field(field_at(value), pts, SPACE_STEP * size)
    norm = np.linalg.norm(grad, axis=1)
    valid = np.isfinite(norm) & (norm >= MIN_GRADIENT) & np.isfinite(d_phi)
    safe = np.where(valid, norm, 1.0)
    speed = np.where(valid, -d_phi / safe, 0.0)
    normal = np.where(valid[:, None], grad / safe[:, None], 0.0)
    return NormalVelocity(speed, normal, valid)


@dataclass(frozen=True)
class SurfaceGradient:
    """dJ/dp from one surface sum, with what it covered."""

    value: float
    covered: float
    """The share of the area where the normal was defined (0 to 1)."""
    left_out: int
    """Triangles left out because |∇φ| was not defined at their centroid."""


def surface_gradient(
    velocity: NormalVelocity,
    sensitivity: Any,  # Any: array-like (n,)
    normals: Any,  # Any: array-like (n, 3), unit patch normals
    areas: Any,  # Any: array-like (n,)
) -> SurfaceGradient:
    """Σ s · V_n · (n_φ · n) · A over the triangles (decision 27).

    The sign factor n_φ · n is +1 where the patch normal points out of the body and -1 where it
    points into it, so the same sum serves a body that is the fluid and one that sits in it.
    """
    import numpy as np

    s = np.asarray(sensitivity, dtype=np.float64).reshape(-1)
    n = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
    a = np.asarray(areas, dtype=np.float64).reshape(-1)
    count = len(velocity.speed)
    if not (len(s) == len(a) == len(n) == count):
        raise ImplicitInvalid(
            "the sensitivity, the normals and the areas need one value per triangle",
            details={"triangles": count, "sensitivity": len(s), "areas": len(a)},
        )
    keep = velocity.valid & np.isfinite(s)
    facing = np.einsum("ij,ij->i", velocity.normal, n)
    total = float(np.sum(np.where(keep, s * velocity.speed * facing * a, 0.0)))
    area = float(np.sum(a))
    covered = float(np.sum(a[keep])) / area if area > 0 else 0.0
    return SurfaceGradient(total, covered, int(count - np.count_nonzero(keep)))
