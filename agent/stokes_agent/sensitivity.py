"""The adjoint wall sensitivity of a solved case (ADR-0038 decision 3, ADR-0035 decision 26).

For the total-pressure loss of an internal flow with frozen turbulence (Othmer 2008), the change
of the objective per unit wall area and per unit normal displacement of the wall is

    s = nu (dU/dn) . (dUa/dn)

on each wall face, with n the face's unit normal, pointing out of the fluid (OpenFOAM's boundary
normals point out of the domain). Both normal derivatives are one-sided differences between the
face value and the owner cell's value: (phi_f - phi_P) / d, where d = n . (C_f - C_P). C_P is the
mean of the cell's face centres, which is exact for parallelepipeds and first order otherwise.

**The sign was fixed by measurement** (ADR-0038 decision 4, 2026-11-12): with the adjoint inlet at
-U_in, a wall that moves out of the fluid lowers the loss, so s < 0 there. Othmer's paper writes
the formula with a minus sign for the opposite orientation of the normal; the first draft took it as
written and its gradient on a spherical obstacle came out with the wrong sign.

**The solver's own s comes first** (ADR-0041 decision 8). The open-source ``adjointFoam`` writes
the same s, in the same sign and units (m²/s³), as the ``volScalarField`` ``shapeSensitivity`` of
each solution time, with OpenFOAM's boundary snGrad: the owner cell's volume centroid instead of
the mean of its face centres. On snapped meshes that is 15 to 20 % closer to finite differences
(``of14-adjoint/tests/flowsplit.md``), so :func:`wall_sensitivity` reads it when the time has it on
every wall patch asked for, and reconstructs s from U and Ua otherwise (Foundation's
``adjointShapeOptimisationFoam`` writes none). :attr:`WallSensitivity.source` says which.

Headless: numpy only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes_agent.foamascii import (
    FoamReadError,
    PolyMesh,
    VolField,
    read_field,
    read_mesh,
    read_patch_flux,
)

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    F64 = npt.NDArray[np.float64]


@dataclass(frozen=True)
class WallSensitivity:
    """s on the wall faces of the named patches, in patch order then face order."""

    patches: tuple[str, ...]
    sizes: tuple[int, ...]
    centres: F64
    """Face centres, m, (n, 3)."""
    normals: F64
    """Unit normals out of the fluid, (n, 3)."""
    areas: F64
    """m²."""
    values: F64
    """s, in the objective's units per m³ (for the pressure loss of the solver: m⁵/s³ per m³ with
    the kinematic pressure, i.e. m²/s³, the dimensions the solver writes ``shapeSensitivity``
    in)."""
    source: str = "reconstructed"
    """Where s came from: ``solver`` (the case's written ``shapeSensitivity``) or
    ``reconstructed`` (from U and Ua here)."""

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "source": self.source,
            "patches": list(self.patches),
            "sizes": list(self.sizes),
            "faces": len(self.values),
            "min": float(self.values.min()) if len(self.values) else None,
            "max": float(self.values.max()) if len(self.values) else None,
            "integral": float((self.values * self.areas).sum()),
        }


def face_geometry(mesh: PolyMesh, start: int, size: int) -> tuple[F64, F64, F64]:
    """Centres, unit normals (by the right-hand rule on the point order) and areas of faces
    ``start`` to ``start + size``."""
    import numpy as np

    centres = np.empty((size, 3))
    normals = np.empty((size, 3))
    areas = np.empty(size)
    for k in range(size):
        p = mesh.points[mesh.faces[start + k]]
        mid = p.mean(axis=0)
        # The area vector of a polygon: half the sum of the fan's cross products about its mean.
        nxt = np.roll(p, -1, axis=0)
        vector = 0.5 * np.cross(p - mid, nxt - mid).sum(axis=0)
        area = float(np.linalg.norm(vector))
        centres[k] = mid
        normals[k] = vector / area if area > 0 else 0.0
        areas[k] = area
    return centres, normals, areas


def owner_centres(mesh: PolyMesh, cells: Any) -> F64:  # Any: array of cell ids
    """The centres of ``cells``: the mean of each cell's face centres (its owner and
    neighbour faces)."""
    import numpy as np

    wanted = {int(c) for c in np.asarray(cells).reshape(-1)}
    sums = {c: np.zeros(3) for c in wanted}
    counts = dict.fromkeys(wanted, 0)
    neighbour = mesh.neighbour
    for f, own in enumerate(mesh.owner):
        hits = [int(own)] + ([int(neighbour[f])] if f < len(neighbour) else [])
        for c in hits:
            if c in sums:
                sums[c] += mesh.points[mesh.faces[f]].mean(axis=0)
                counts[c] += 1
    return np.array([sums[int(c)] / max(counts[int(c)], 1) for c in np.asarray(cells).reshape(-1)])


SOLVER_FIELD = "shapeSensitivity"
"""The wall sensitivity the open-source adjoint writes at each solution time."""

SOURCES = ("solver", "reconstructed")


def wall_sensitivity(
    case: Path,
    time: str,
    nu: float,
    patches: Sequence[str] | None = None,
    *,
    mesh: PolyMesh | None = None,
    source: str = "auto",
) -> WallSensitivity:
    """s on the wall patches of ``case`` at ``time`` (every patch of type ``wall`` by default).

    ``source`` ``auto`` takes the solver's written ``shapeSensitivity`` when the time has it with
    values on every patch asked for, and reconstructs s from U and Ua otherwise; ``solver`` and
    ``reconstructed`` force one (``solver`` raises when the field is missing)."""
    if source not in ("auto", *SOURCES):
        raise ValueError(f"unknown sensitivity source {source!r}")
    mesh = mesh or read_mesh(case)
    names = (
        list(patches) if patches is not None else [p.name for p in mesh.patches if p.type == "wall"]
    )
    if not names:
        raise FoamReadError("the case has no wall patch to compute a sensitivity on")
    if source != "reconstructed":
        found = solver_sensitivity(case, time, names, mesh)
        if found is not None:
            return found
        if source == "solver":
            raise FoamReadError(
                f"time {time} has no {SOLVER_FIELD} with values on every wall patch",
                details={"field": SOLVER_FIELD, "time": time},
            )
    return reconstructed_sensitivity(case, time, nu, names, mesh)


def solver_sensitivity(
    case: Path, time: str, names: Sequence[str], mesh: PolyMesh
) -> WallSensitivity | None:
    """The solver's written ``shapeSensitivity`` on ``names``, in :func:`wall_sensitivity`'s
    layout; None when the time has no such file or a patch stores no face values (the caller then
    reconstructs s). A file that is there but malformed, or holds a value that is not finite, is a
    :class:`FoamReadError`: a broken solver output is reported, not silently replaced."""
    import numpy as np

    if not (case / time / SOLVER_FIELD).is_file():
        return None
    field = read_field(case, time, SOLVER_FIELD, mesh)
    if field.width != 1:
        raise FoamReadError(f"{SOLVER_FIELD} is not a scalar field", details={"time": time})
    parts: list[tuple[F64, F64, F64, F64]] = []
    sizes: list[int] = []
    for name in names:
        patch = mesh.patch(name)
        values = field.patches.get(name)
        if values is None:
            return None
        if not np.isfinite(values).all():
            raise FoamReadError(
                f"{SOLVER_FIELD} on {name} holds a value that is not finite",
                details={"field": SOLVER_FIELD, "patch": name, "time": time},
            )
        centres, normals, areas = face_geometry(mesh, patch.start, patch.size)
        parts.append((centres, normals, areas, np.asarray(values, dtype=np.float64)))
        sizes.append(patch.size)
    return _joined(names, sizes, parts, "solver")


def reconstructed_sensitivity(
    case: Path, time: str, nu: float, names: Sequence[str], mesh: PolyMesh
) -> WallSensitivity:
    """s = nu (dU/dn).(dUa/dn) from the case's U and Ua, with one-sided wall gradients."""
    import numpy as np

    u = read_field(case, time, "U", mesh)
    ua = read_field(case, time, "Ua", mesh)
    parts: list[tuple[F64, F64, F64, F64]] = []
    sizes: list[int] = []
    for name in names:
        patch = mesh.patch(name)
        centres, normals, areas = face_geometry(mesh, patch.start, patch.size)
        cells = mesh.owner[patch.start : patch.start + patch.size]
        owners = owner_centres(mesh, cells)
        d = np.einsum("ij,ij->i", normals, centres - owners)
        if (d <= 0).any():
            raise FoamReadError(
                f"patch {name}: a wall face's normal does not point away from its cell",
                details={"patch": name},
            )
        du = (_face(u, name, patch.size) - u.internal[cells]) / d[:, None]
        dua = (_face(ua, name, patch.size) - ua.internal[cells]) / d[:, None]
        s = nu * np.einsum("ij,ij->i", du, dua)
        parts.append((centres, normals, areas, s))
        sizes.append(patch.size)
    return _joined(names, sizes, parts, "reconstructed")


def _joined(
    names: Sequence[str],
    sizes: Sequence[int],
    parts: Sequence[tuple[F64, F64, F64, F64]],
    source: str,
) -> WallSensitivity:
    import numpy as np

    return WallSensitivity(
        tuple(names),
        tuple(sizes),
        np.concatenate([p[0] for p in parts]),
        np.concatenate([p[1] for p in parts]),
        np.concatenate([p[2] for p in parts]),
        np.concatenate([p[3] for p in parts]),
        source,
    )


def _face(field: VolField, patch: str, size: int) -> F64:
    """A vector field's face values on a wall: stored ones, or zero for a no-slip wall."""
    import numpy as np

    values = field.patches.get(patch)
    if values is not None:
        return values
    if field.types.get(patch) in ("noSlip", ""):
        return np.zeros((size, field.width))
    raise FoamReadError(
        f"{field.name} on {patch} is {field.types.get(patch)!r} with no stored value",
        details={"field": field.name, "patch": patch},
    )


def latest_time(case: Path) -> str:
    """The newest time directory of a case (not ``0``), as written."""
    best: tuple[float, str] | None = None
    for entry in case.iterdir() if case.is_dir() else []:
        if not entry.is_dir():
            continue
        try:
            value = float(entry.name)
        except ValueError:
            continue
        if value > 0 and (best is None or value > best[0]):
            best = (value, entry.name)
    if best is None:
        raise FoamReadError("the case has no solution time yet")
    return best[1]


@dataclass(frozen=True)
class PortFlow:
    """The volumetric flow through one port (m³/s, positive out of the domain) and how much of
    the port's area carries flow back in."""

    flow: float
    backflow: float
    """The share of the port's |flux| that enters the domain where the net flow leaves (0 to 1)."""
    pressure: float | None = None
    """The area-weighted mean of p on the port (kinematic, m²/s²), when the time has p."""

    def to_json(self) -> dict[str, float | None]:
        return {"flow": self.flow, "backflow": self.backflow, "pressure": self.pressure}


def port_flows(
    case: Path, time: str, ports: Sequence[str], *, mesh: PolyMesh | None = None
) -> dict[str, PortFlow]:
    """Q = sum (U . S_f) on each port, outward positive: from the solver's own face flux ``phi``
    when the time has it (exact, what continuity was solved with), else from the face values of U
    (the owner cells' where the patch stores none). The flow-split objective (ADR-0040) is built
    from these on the desktop."""
    import numpy as np

    mesh = mesh or read_mesh(case)
    try:
        flux = read_patch_flux(case, time, mesh)
    except FoamReadError:
        flux = {}
    try:
        p: VolField | None = read_field(case, time, "p", mesh)
    except FoamReadError:
        p = None
    u: VolField | None = None
    out: dict[str, PortFlow] = {}
    for name in ports:
        patch = mesh.patch(name)
        _, normals, areas = face_geometry(mesh, patch.start, patch.size)
        cells = mesh.owner[patch.start : patch.start + patch.size]
        faces = flux.get(name)
        if faces is None:
            u = u or read_field(case, time, "U", mesh)
            uf = u.patches.get(name)
            uf = u.internal[cells] if uf is None else uf
            faces = np.einsum("ij,ij->i", uf, normals * areas[:, None])
        total = float(np.sum(faces))
        magnitude = float(np.sum(np.abs(faces)))
        against = float(np.sum(faces[faces < 0])) if total >= 0 else float(np.sum(faces[faces > 0]))
        pressure = None
        if p is not None and float(areas.sum()) > 0:
            pf = p.patches.get(name)
            pf = p.internal[cells] if pf is None else pf
            pressure = float(np.sum(pf * areas) / areas.sum())
        out[name] = PortFlow(total, abs(against) / magnitude if magnitude > 0 else 0.0, pressure)
    return out


def power_loss(
    case: Path, time: str, ports: Sequence[str], *, mesh: PolyMesh | None = None
) -> float:
    """The objective of Foundation's adjoint (Othmer 2008): the total-pressure power through the
    inlets and outlets, J = -sum (p + |U|^2 / 2) (U . S_f), with the kinematic pressure and S_f the
    outward face area vector; m^5/s^3 (W per unit density). A patch that stores no face value
    (zero gradient) takes its owner cells' values."""
    import numpy as np

    mesh = mesh or read_mesh(case)
    u = read_field(case, time, "U", mesh)
    p = read_field(case, time, "p", mesh)
    total = 0.0
    for name in ports:
        patch = mesh.patch(name)
        _, normals, areas = face_geometry(mesh, patch.start, patch.size)
        cells = mesh.owner[patch.start : patch.start + patch.size]
        uf = u.patches.get(name)
        pf = p.patches.get(name)
        uf = u.internal[cells] if uf is None else uf
        pf = p.internal[cells] if pf is None else pf
        flux = np.einsum("ij,ij->i", uf, normals * areas[:, None])
        total -= float(np.sum((pf + 0.5 * np.einsum("ij,ij->i", uf, uf)) * flux))
    return total
