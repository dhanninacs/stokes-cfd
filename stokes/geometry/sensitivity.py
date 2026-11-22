"""Shape derivatives of an implicit part, from the tree (ADR-0035 decision 27, ADR-0037).

:mod:`stokes.implicit.sensitivity` holds the numerics; this module gives them the part's graph as
a function of one quantity, and its surface. The quantity is anything the part's recipe reads,
directly or through expressions: an operation's parameter (``…/cyl.radius``), one component of a
vector (``…/cyl.position.x``), a sketch's dimension (``…/sk.dimensions.W``) or a project
parameter that one of them refers to (``R``). The graph at another value is built from a private
copy of the project with the quantity set to that value, so expressions are followed exactly as a
run would follow them.

The surface is the part's display mesh (positions in metres): the triangles' centroids, unit
normals and areas. Results are SI: a speed in metres per unit of the quantity, a gradient in the
sensitivity's units times metres cubed per unit (dJ/dp for s in J/m³).

Headless: numpy and the model; no Qt, VTK or OCP.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.errors import StokesError
from stokes.geometry.errors import OperationInvalid
from stokes.model.nodes import Node, QuantityValue
from stokes.model.project import Project

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from stokes.geometry.mesh import PartMesh
    from stokes.implicit.graph import Expr
    from stokes.implicit.sensitivity import NormalVelocity, SurfaceGradient

    F64 = npt.NDArray[np.float64]

MM_PER_M = 1000.0
RELATIVE_STEP = 1e-4
"""The difference step h, as a share of the quantity's magnitude (ADR-0037)."""
MIN_STEP = 1e-7
"""The smallest step, in SI: a quantity at 0 still gets a difference."""


class NotDifferentiable(OperationInvalid):
    """The quantity is a whole number (a count): it has no derivative (ADR-0035 decision 27)."""

    code = "implicit.not_differentiable"


@dataclass(frozen=True)
class Surface:
    """The triangles of a part's display mesh: centroids (m), unit normals and areas (m²)."""

    centroids: F64
    normals: F64
    areas: F64

    @property
    def count(self) -> int:
        return len(self.areas)


def surface_of(mesh: PartMesh) -> Surface:
    """The centroids, normals and areas of every triangle of ``mesh`` (degenerate ones keep a zero
    normal and area, so the arrays line up with the mesh's triangles)."""
    import numpy as np

    centroids: list[F64] = []
    normals: list[F64] = []
    areas: list[F64] = []
    for solid in mesh.solids:
        p = solid.positions.astype(np.float64).reshape(-1, 3)
        t = solid.indices.astype(np.int64).reshape(-1, 3)
        a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
        cross = np.cross(b - a, c - a)
        twice = np.linalg.norm(cross, axis=1)
        safe = np.where(twice > 0, twice, 1.0)
        centroids.append((a + b + c) / 3.0)
        normals.append(np.where(twice[:, None] > 0, cross / safe[:, None], 0.0))
        areas.append(twice / 2.0)
    if not areas:
        raise OperationInvalid("the part has no surface mesh yet: run its operation first")
    return Surface(np.concatenate(centroids), np.concatenate(normals), np.concatenate(areas))


@dataclass(frozen=True)
class Target:
    """The quantity a derivative is taken with respect to."""

    node_id: str
    param: str
    reference: str
    value: float
    step: float


def target_of(project: Project, reference: str) -> Target:
    """Resolve ``reference`` like an expression name (``R``, ``geometry.cyl.radius``,
    ``geometry.cyl.position.x``) to a quantity, with its difference step."""
    try:
        node, param = project.resolve_param(reference.strip().strip("/").replace("/", "."))
    except StokesError as exc:
        raise OperationInvalid(exc.message, details={"parameter": reference}) from exc
    spec = project.spec_of(node).param(param)
    if spec.integer:
        raise NotDifferentiable(
            f"{reference} is a whole number (a count): it has no derivative; vary it in a study",
            details={"parameter": reference},
        )
    stored = node.params.get(param)
    if not isinstance(stored, QuantityValue):
        raise OperationInvalid(f"{reference} is not a quantity", details={"parameter": reference})
    value = float(stored.value)
    step = max(RELATIVE_STEP * abs(value), MIN_STEP)
    return Target(node.id, param, reference, value, step)


def field_builder(project: Project, part: Node, target: Target) -> Callable[[float], Expr]:
    """The part's graph with ``target`` set to a value (SI), from a private copy of the project."""
    from stokes.geometry.operations.implicit import body_of
    from stokes.geometry.opplan import implicit_recipe

    snapshot = project.to_json()

    def build(value: float) -> Expr:
        copy = Project.from_json(_fresh(snapshot), node_registry=project.registry)
        copy.set_param(target.node_id, target.param, value)
        return body_of(implicit_recipe(copy, copy.get(part.id))).field()

    return build


def _fresh(data: Any) -> Any:  # Any: JSON
    import copy

    return copy.deepcopy(data)


def body_size(project: Project, part: Node) -> float:
    """The largest extent of the part's body (kernel units, mm)."""
    from stokes.geometry.operations.implicit import body_of
    from stokes.geometry.opplan import implicit_recipe

    low, high = body_of(implicit_recipe(project, part)).bounded()
    return max(h - lo for lo, h in zip(low, high, strict=True))


def normal_velocity(
    project: Project, part: Node, mesh: PartMesh, reference: str
) -> tuple[Target, NormalVelocity, Surface]:
    """V_n of ``reference`` at the centroids of ``part``'s triangles, in metres per SI unit."""
    from stokes.implicit.sensitivity import NormalVelocity
    from stokes.implicit.sensitivity import normal_velocity as kernel

    target = target_of(project, reference)
    surface = surface_of(mesh)
    found = kernel(
        field_builder(project, part, target),
        target.value,
        target.step,
        surface.centroids * MM_PER_M,
        body_size(project, part),
    )
    return target, NormalVelocity(found.speed / MM_PER_M, found.normal, found.valid), surface


def gradient(
    project: Project,
    part: Node,
    mesh: PartMesh,
    references: Sequence[str],
    sensitivity: Any = None,  # Any: array-like (n,) or None for s = 1
) -> dict[str, SurfaceGradient]:
    """dJ/dp for each reference (decision 27). ``sensitivity`` is s per triangle of the part's
    mesh, against the triangles' own normals; None is s = 1, which gives dV/dp of the part."""
    import numpy as np

    from stokes.implicit.sensitivity import surface_gradient

    out: dict[str, SurfaceGradient] = {}
    for reference in references:
        _, velocity, surface = normal_velocity(project, part, mesh, reference)
        s = np.ones(surface.count) if sensitivity is None else sensitivity
        if len(np.asarray(s).reshape(-1)) != surface.count:
            raise OperationInvalid(
                f"the sensitivity has {len(np.asarray(s).reshape(-1))} values; the part has "
                f"{surface.count} triangles",
                details={"triangles": surface.count},
            )
        out[reference] = surface_gradient(velocity, s, surface.normals, surface.areas)
    return out


def finite(value: float) -> float | None:
    """A number for JSON: None for NaN or infinity."""
    return value if math.isfinite(value) else None


def wall_gradient(
    project: Project,
    part: Node,
    centres: Any,  # Any: (n, 3) array, metres: the CFD wall faces' centres
    normals: Any,  # Any: (n, 3) unit normals out of the fluid
    areas: Any,  # Any: (n,) m²
    sensitivity: Any,  # Any: (n,) s on those faces (ADR-0038 decision 3)
    references: Sequence[str],
    tolerance: float = 1e-3,
) -> dict[str, SurfaceGradient]:
    """dJ/dp from an adjoint's wall sensitivity on the CFD mesh's own wall faces.

    Only the faces that lie on the part count: those whose centre is within ``tolerance`` of the
    part's largest extent from its surface (|phi| small), so a pipe's other walls, made by other
    parts or by the enclosure, add nothing. V_n is evaluated at the face centres themselves, so
    the CFD mesh need not match the display mesh.
    """
    import numpy as np

    from stokes.geometry.operations.implicit import body_of
    from stokes.geometry.opplan import implicit_recipe
    from stokes.implicit.evaluate import evaluate
    from stokes.implicit.sensitivity import NormalVelocity, surface_gradient
    from stokes.implicit.sensitivity import normal_velocity as kernel

    points = np.asarray(centres, dtype=np.float64).reshape(-1, 3) * MM_PER_M
    size = body_size(project, part)
    phi = evaluate(body_of(implicit_recipe(project, part)).field(), points)
    on_part = np.abs(phi) <= tolerance * size
    if not on_part.any():
        raise OperationInvalid(
            f"no wall face of the case lies on {part.path}: is the case meshed from this part?",
            details={"part": part.id},
        )
    s = np.where(on_part, np.asarray(sensitivity, dtype=np.float64).reshape(-1), 0.0)
    out: dict[str, SurfaceGradient] = {}
    for reference in references:
        target = target_of(project, reference)
        found = kernel(
            field_builder(project, part, target), target.value, target.step, points, size
        )
        velocity = NormalVelocity(found.speed / MM_PER_M, found.normal, found.valid & on_part)
        out[reference] = surface_gradient(velocity, s, normals, areas)
    return out
