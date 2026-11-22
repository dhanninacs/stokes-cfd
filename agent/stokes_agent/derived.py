"""Derived quantities the agent computes while it extracts a run's fields: what needs the volume.

The desktop can make a formula of point values (`stokes.post.variables`: total pressure, the
pressure coefficient), but not a gradient: that needs the cells. So the agent, which holds the
solved case, computes:

* **on the internal mesh** (so every slice and cut plane carries them): `vorticity` (the curl of
  `U`, 1/s), its magnitude `vorticity_mag`, and the **Q-criterion** `Q = ½(|Ω|² − |S|²)` (1/s²),
  from one velocity-gradient pass (`vtkGradientFilter` on the cell-to-point `U`);
* **on every wall patch**: the **wall shear stress** `wallShearStress` (vector and `_mag`) and
  **y+** (`yPlus`); with the energy equation, the **wall heat flux** `wallHeatFlux` (W/m²,
  positive into the fluid): `q = −(k + c·α_t) ∂T/∂n` with `n` into the fluid, `k = c·μ/Pr` from the
  case's transport properties (`c` its `Cp`, or its `Cv` for the constant-density fluids Stokes
  writes, where the two are equal) and `α_t` the turbulent thermal diffusivity where the run wrote
  `alphat`.

The wall quantities use the same gradient at the wall's points, as OpenFOAM's own
`wallShearStress` uses the wall-normal gradient: `τ = ν_eff (∇U + ∇Uᵀ)·n` with `n` into the fluid,
keeping its tangential part, and `ν_eff = ν + ν_t` where the run wrote `nut` (a wall function's
`nut` at the wall is what makes `τ` right on a coarse mesh). `y+ = u_τ y / ν` with `u_τ = √|τ|`
and `y` the wall-normal distance from the wall point to the nearest cell centre. Both are
estimates of the order of the solver's own first-cell values; the viscosity comes from the case's
`constant/physicalProperties` (`nu`, or `mu` over a constant `rho`).

An incompressible run's stress is kinematic (τ/ρ, m²/s²) like its pressure; with the energy
engine it is in Pa when the density is a constant the case states, and is left out otherwise
rather than given a guessed density.

**Nothing is invented**: a field these need and the case lacks (no `U`, no viscosity) means the
derived field is absent, not zero. numpy and VTK only, imported where used (the agent may not
import scipy): VTK's static point locator finds nearest points, and a boundary point is matched to
its mesh point exactly, by its coordinates.
"""

# ruff: noqa: RUF002 - the docstring writes the physics in its own symbols.

from __future__ import annotations

import math
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

from stokes.log import get_logger

log = get_logger(__name__)

#: The derived fields, in the order an artifact stores them after the solver's.
VOLUME_FIELDS: tuple[str, ...] = ("vorticity", "Q")
WALL_FIELDS: tuple[str, ...] = ("wallShearStress", "yPlus", "wallHeatFlux")
DERIVED_FIELDS: tuple[str, ...] = (*VOLUME_FIELDS, *WALL_FIELDS)

#: Their units; the shear stress is kinematic for an incompressible run (`units_for` overrides).
DERIVED_UNITS: dict[str, str] = {
    "vorticity": "1/s",
    "vorticity_mag": "1/s",
    "Q": "1/s2",
    "wallShearStress": "m2/s2",
    "wallShearStress_mag": "m2/s2",
    "yPlus": "",
    "wallHeatFlux": "W/m2",
}

#: Components of each derived field.
DERIVED_COMPONENTS: dict[str, int] = {
    "vorticity": 3,
    "vorticity_mag": 1,
    "Q": 1,
    "wallShearStress": 3,
    "wallShearStress_mag": 1,
    "yPlus": 1,
    "wallHeatFlux": 1,
}

#: The most a viscosity file is read: a physicalProperties is a few hundred bytes.
MAX_PROPERTIES_BYTES = 256 * 1024

_NUMBER = r"([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|[-+]?\.\d+(?:[eE][-+]?\d+)?)"


def _entry(text: str, key: str) -> float | None:
    """The last number of `key <optional dimensions> <number>;`, or None."""
    pattern = re.compile(rf"(?<![A-Za-z_]){key}\s+(?:\[[^\]]*\]\s*)?{_NUMBER}\s*;")
    found = pattern.findall(text)
    if not found:
        return None
    value = float(found[-1])
    return value if math.isfinite(value) else None


def read_viscosity(case_dir: Path) -> tuple[float | None, float | None]:
    """`(nu, rho)` from `constant/physicalProperties`: the kinematic viscosity (m²/s), and the
    constant density (kg/m³) when the case states one. Either is None when the file does not say.

    `nu` is read directly, or as `mu / rho` with a constant `rho`. **Read as text, never through a
    link**, and at most `MAX_PROPERTIES_BYTES`.
    """
    path = Path(case_dir) / "constant" / "physicalProperties"
    try:
        if path.is_symlink() or not path.is_file():
            return None, None
        with path.open("rb") as handle:
            text = handle.read(MAX_PROPERTIES_BYTES).decode("latin-1")
    except OSError:
        return None, None
    text = re.sub(r"//[^\n]*", "", text)
    rho = _entry(text, "rho")
    nu = _entry(text, "nu")
    if nu is None:
        mu = _entry(text, "mu")
        if mu is not None and rho is not None and rho > 0:
            nu = mu / rho
    return (nu if nu is not None and nu > 0 else None), (
        rho if rho is not None and rho > 0 else None
    )


def read_conductivity(case_dir: Path) -> tuple[float | None, float | None]:
    """`(k, c)`: the molecular thermal conductivity `c·μ/Pr` (W/m/K) and the specific heat `c`
    (J/kg/K) from `constant/physicalProperties`, or None for what the file does not say."""
    path = Path(case_dir) / "constant" / "physicalProperties"
    try:
        if path.is_symlink() or not path.is_file():
            return None, None
        with path.open("rb") as handle:
            raw = handle.read(MAX_PROPERTIES_BYTES).decode("latin-1")
    except OSError:
        return None, None
    text = re.sub(r"//[^\n]*", "", raw)
    c = _entry(text, "Cp") or _entry(text, "Cv")
    mu = _entry(text, "mu")
    pr = _entry(text, "Pr")
    if c is None or c <= 0:
        return None, None
    if mu is None or pr is None or pr <= 0:
        return None, c
    return c * mu / pr, c


#: The last gradients computed, by `_fingerprint`: one extraction reads the same case at the same
#: time twice (the cut plane's reader and the walls'), and `vtkGradientFilter` costs 150 s for `∇U`
#: on a 526 k-cell mesh with polyhedra (measured on the gyroid heat exchanger, ADR-0042).
_GRADIENTS: OrderedDict[tuple[Any, ...], np.ndarray | None] = OrderedDict()
_GRADIENTS_KEPT = 4


def _fingerprint(mesh: Any, name: str) -> tuple[Any, ...] | None:
    """What identifies a mesh and one of its point arrays, cheaply: counts and sums."""
    from vtkmodules.util.numpy_support import vtk_to_numpy

    points = mesh.GetPoints()
    array = mesh.GetPointData().GetArray(name)
    if points is None or array is None:
        return None
    xyz = vtk_to_numpy(points.GetData())  # type: ignore[no-untyped-call]
    values = vtk_to_numpy(array)  # type: ignore[no-untyped-call]
    return (
        name,
        int(mesh.GetNumberOfPoints()),
        int(mesh.GetNumberOfCells()),
        float(np.asarray(xyz, dtype=np.float64).sum()),
        float(np.asarray(values, dtype=np.float64).sum()),
    )


def _remembered(key: tuple[Any, ...] | None, compute: Any) -> np.ndarray | None:
    """`compute()` once per key, the last few kept."""
    if key is not None and key in _GRADIENTS:
        _GRADIENTS.move_to_end(key)
        return _GRADIENTS[key]
    found: np.ndarray | None = compute()
    if key is not None:
        _GRADIENTS[key] = found
        while len(_GRADIENTS) > _GRADIENTS_KEPT:
            _GRADIENTS.popitem(last=False)
    return found


def scalar_gradient(mesh: Any, name: str) -> np.ndarray | None:
    """`∇f` of a point scalar at the mesh's points, `(P, 3)`; None without the field."""
    if mesh is None or mesh.GetPointData().GetArray(name) is None:
        return None
    return _remembered(_fingerprint(mesh, name), lambda: _scalar_gradient(mesh, name))


def _scalar_gradient(mesh: Any, name: str) -> np.ndarray | None:
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkDataObject
    from vtkmodules.vtkFiltersGeneral import vtkGradientFilter

    if mesh is None or mesh.GetPointData().GetArray(name) is None:
        return None
    gradient = vtkGradientFilter()
    gradient.SetInputData(mesh)
    gradient.SetInputArrayToProcess(0, 0, 0, vtkDataObject.FIELD_ASSOCIATION_POINTS, name)
    gradient.SetResultArrayName(f"grad_{name}")
    gradient.Update()
    array = gradient.GetOutput().GetPointData().GetArray(f"grad_{name}")
    if array is None or int(array.GetNumberOfComponents()) != 3:
        return None
    raw = vtk_to_numpy(array)  # type: ignore[no-untyped-call]
    values = np.asarray(raw, dtype=np.float64).reshape(-1, 3)
    if values.shape[0] != mesh.GetNumberOfPoints():
        return None
    filled = _fill_non_finite(values.reshape(-1, 1, 3), mesh)
    return None if filled is None else filled.reshape(-1, 3)


def wall_heat_flux(
    points: np.ndarray,
    triangles: np.ndarray,
    grad_t: np.ndarray,
    conductivity: float,
    specific_heat: float,
    alphat: np.ndarray | None,
) -> np.ndarray:
    """The heat flux into the fluid at a wall's points, W/m²: `−(k + c·α_t) ∂T/∂n_in`."""
    n_in = -vertex_normals(points, triangles)
    k_eff = np.full(points.shape[0], float(conductivity))
    if alphat is not None and alphat.shape[0] == points.shape[0]:
        turbulent = np.clip(np.asarray(alphat, dtype=np.float64).reshape(-1), 0.0, None)
        k_eff = k_eff + float(specific_heat) * turbulent
    flux: np.ndarray = (-k_eff * np.einsum("pi,pi->p", grad_t, n_in)).astype(np.float32)
    return flux


def velocity_gradient(mesh: Any) -> np.ndarray | None:
    """`∇U` at the mesh's points, `(P, 3, 3)`, `[i, j] = ∂U_i/∂x_j`; None without a point `U`."""
    if mesh is None or mesh.GetNumberOfPoints() == 0 or mesh.GetPointData().GetArray("U") is None:
        return None
    return _remembered(_fingerprint(mesh, "U"), lambda: _velocity_gradient(mesh))


def _velocity_gradient(mesh: Any) -> np.ndarray | None:
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkCommonDataModel import vtkDataObject
    from vtkmodules.vtkFiltersGeneral import vtkGradientFilter

    if mesh is None or mesh.GetNumberOfPoints() == 0 or mesh.GetPointData().GetArray("U") is None:
        return None
    gradient = vtkGradientFilter()
    gradient.SetInputData(mesh)
    gradient.SetInputArrayToProcess(0, 0, 0, vtkDataObject.FIELD_ASSOCIATION_POINTS, "U")
    gradient.SetResultArrayName("gradU")
    gradient.Update()
    array = gradient.GetOutput().GetPointData().GetArray("gradU")
    if array is None or int(array.GetNumberOfComponents()) != 9:
        return None
    raw = vtk_to_numpy(array)  # type: ignore[no-untyped-call]
    values = np.asarray(raw, dtype=np.float64).reshape(-1, 3, 3)
    if values.shape[0] != mesh.GetNumberOfPoints():
        return None
    return _fill_non_finite(values, mesh)


def _fill_non_finite(values: np.ndarray, mesh: Any) -> np.ndarray | None:
    """`values` with each point whose gradient is not finite given its nearest finite neighbour's.

    VTK answers NaN at the odd point of a degenerate polyhedron (measured: 1 point of 43,904 on a
    snapped duct bend). The artifact refuses NaN, and dropping vorticity everywhere for one point
    would be the worse answer; the nearest point's gradient is the local estimate and the count is
    logged. None when no point is finite.
    """
    from vtkmodules.util.numpy_support import vtk_to_numpy

    good = np.isfinite(values).reshape(values.shape[0], -1).all(axis=1)
    if good.all():
        return values
    if not good.any():
        return None
    points = np.asarray(vtk_to_numpy(mesh.GetPoints().GetData()), dtype=np.float64)  # type: ignore[no-untyped-call]
    points = points.reshape(-1, 3)
    nearest = nearest_points(points[good], points[~good])
    out = values.copy()
    out[~good] = values[good][nearest]
    log.info("filled non-finite velocity gradients", extra={"points": int((~good).sum())})
    return out


def vorticity_and_q(grad: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The curl of `U` `(P, 3)` and the Q-criterion `(P,)` from `∇U` `(P, 3, 3)`."""
    curl = np.stack(
        [
            grad[:, 2, 1] - grad[:, 1, 2],
            grad[:, 0, 2] - grad[:, 2, 0],
            grad[:, 1, 0] - grad[:, 0, 1],
        ],
        axis=1,
    )
    strain = 0.5 * (grad + np.transpose(grad, (0, 2, 1)))
    spin = 0.5 * (grad - np.transpose(grad, (0, 2, 1)))
    q = 0.5 * (np.einsum("pij,pij->p", spin, spin) - np.einsum("pij,pij->p", strain, strain))
    return curl, q


def add_volume_fields(mesh: Any) -> np.ndarray | None:
    """Add `vorticity`, `vorticity_mag` and `Q` to the mesh's point data; answer `∇U` (or None).

    In place, so a cutter given the mesh afterwards interpolates them onto its cut like `U`.
    """
    from vtkmodules.util.numpy_support import numpy_to_vtk

    grad = velocity_gradient(mesh)
    if grad is None:
        return None
    curl, q = vorticity_and_q(grad)
    point_data = mesh.GetPointData()
    for name, values in (
        ("vorticity", curl),
        ("vorticity_mag", np.linalg.norm(curl, axis=1)),
        ("Q", q),
    ):
        data = np.ascontiguousarray(values, dtype=np.float32)
        array = numpy_to_vtk(data, deep=True)  # type: ignore[no-untyped-call]
        array.SetName(name)
        point_data.AddArray(array)
    return grad


def vertex_normals(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Area-weighted unit normals at the points, in the triangles' winding (out of the fluid on an
    OpenFOAM boundary); zero where a point has no triangle."""
    normals = np.zeros((points.shape[0], 3), dtype=np.float64)
    if triangles.shape[0]:
        tri = triangles.astype(np.int64)
        p = points.astype(np.float64)
        face = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
        for k in range(3):
            np.add.at(normals, tri[:, k], face)
    length = np.linalg.norm(normals, axis=1)
    safe = np.where(length > 0, length, 1.0)
    out: np.ndarray = normals / safe[:, None]
    return out


def wall_fields(
    points: np.ndarray,
    triangles: np.ndarray,
    grad: np.ndarray,
    nu: float,
    nut: np.ndarray | None,
    centres: np.ndarray,
    density: float | None = None,
) -> dict[str, np.ndarray]:
    """The wall shear stress and y+ at a wall patch's points.

    `grad` is `∇U` at these points `(N, 3, 3)`; `centres` the internal mesh's cell centres; `nut`
    the turbulent viscosity at the points when the run has it. With `density`, the stress is in Pa
    (τ = ρ ν_eff ...) and y+ still uses the kinematic `u_τ`.
    """
    n_in = -vertex_normals(points, triangles)
    nu_eff = np.full(points.shape[0], float(nu))
    if nut is not None and nut.shape[0] == points.shape[0]:
        nu_eff = nu_eff + np.clip(np.asarray(nut, dtype=np.float64).reshape(-1), 0.0, None)
    sym = grad + np.transpose(grad, (0, 2, 1))
    traction = np.einsum("pij,pj->pi", sym, n_in) * nu_eff[:, None]
    tangential = traction - np.einsum("pi,pi->p", traction, n_in)[:, None] * n_in
    magnitude = np.linalg.norm(tangential, axis=1)
    nearest = nearest_points(centres, points.astype(np.float64))
    offset = centres[nearest] - points
    distance = np.abs(np.einsum("pi,pi->p", offset, n_in))
    y_plus = np.sqrt(magnitude) * distance / float(nu)
    scale = float(density) if density is not None else 1.0
    return {
        "wallShearStress": (tangential * scale).astype(np.float32),
        "wallShearStress_mag": (magnitude * scale).astype(np.float32),
        "yPlus": y_plus.astype(np.float32),
    }


def cell_centres(mesh: Any) -> np.ndarray:
    """The internal mesh's cell centres `(C, 3)`."""
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkFiltersCore import vtkCellCenters

    centres = vtkCellCenters()
    centres.SetInputData(mesh)
    centres.VertexCellsOff()
    centres.Update()
    points = centres.GetOutput().GetPoints()
    if points is None:
        return np.zeros((0, 3), dtype=np.float64)
    raw = vtk_to_numpy(points.GetData())  # type: ignore[no-untyped-call]
    return np.asarray(raw, dtype=np.float64).reshape(-1, 3)


def nearest_points(cloud: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """For each query point, the index of the nearest point of `cloud` (VTK's static locator)."""
    from vtkmodules.util.numpy_support import numpy_to_vtk
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkStaticPointLocator

    points = vtkPoints()
    points.SetData(numpy_to_vtk(np.ascontiguousarray(cloud, dtype=np.float64), deep=True))  # type: ignore[no-untyped-call]
    data = vtkPolyData()
    data.SetPoints(points)
    locator = vtkStaticPointLocator()
    locator.SetDataSet(data)
    locator.BuildLocator()
    out = np.empty(queries.shape[0], dtype=np.int64)
    for i, q in enumerate(np.asarray(queries, dtype=np.float64)):
        out[i] = locator.FindClosestPoint(float(q[0]), float(q[1]), float(q[2]))
    return out


def match_points(
    volume_points: np.ndarray, points: np.ndarray, tolerance: float
) -> np.ndarray | None:
    """Each point's index among the volume's points, or None when any point is not one of them.

    A boundary point IS a mesh point - the reader gives the same coordinates for both - so the match
    is exact and vectorised (rows compared as bytes after sorting); `tolerance` is kept for callers
    and is not needed. None means the two are not the same mesh.
    """
    del tolerance
    if volume_points.shape[0] == 0 or points.shape[0] == 0:
        return None
    row = np.dtype((np.void, 3 * np.dtype(np.float32).itemsize))
    volume = np.ascontiguousarray(volume_points, dtype=np.float32).view(row).ravel()
    asked = np.ascontiguousarray(points, dtype=np.float32).view(row).ravel()
    order = np.argsort(volume, kind="stable")
    ranked = volume[order]
    at = np.searchsorted(ranked, asked)
    if np.any(at >= ranked.shape[0]) or np.any(
        ranked[np.minimum(at, ranked.shape[0] - 1)] != asked
    ):
        return None
    out: np.ndarray = order[at].astype(np.int64)
    return out
