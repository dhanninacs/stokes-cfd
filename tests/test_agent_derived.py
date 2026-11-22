"""The derived quantities the agent computes (`stokes_agent.derived`): vorticity and the Q-criterion
from the velocity gradient, the wall shear stress and y+ at a wall, and the viscosity read from a
case. Checked against flows whose answers are known by hand, then on a real one-hex case through
VTK (skipped without VTK)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stokes_agent import derived
from tests.test_post_integrals import plate


def gradient(rows: list[list[float]], n: int = 1) -> np.ndarray:
    return np.tile(np.asarray(rows, dtype=np.float64), (n, 1, 1))


def test_solid_body_rotation_is_all_vorticity_and_positive_q() -> None:
    """u = (-w y, w x, 0): curl (0, 0, 2w), no strain, so Q = w^2."""
    w = 3.0
    curl, q = derived.vorticity_and_q(gradient([[0, -w, 0], [w, 0, 0], [0, 0, 0]]))
    assert curl[0] == pytest.approx([0.0, 0.0, 2 * w])
    assert q[0] == pytest.approx(w * w)


def test_simple_shear_has_vorticity_and_zero_q() -> None:
    """u = (a y, 0, 0): curl (0, 0, -a), rotation and strain balance, Q = 0."""
    curl, q = derived.vorticity_and_q(gradient([[0, 2.0, 0], [0, 0, 0], [0, 0, 0]]))
    assert curl[0] == pytest.approx([0.0, 0.0, -2.0])
    assert q[0] == pytest.approx(0.0)


def test_pure_strain_has_negative_q() -> None:
    curl, q = derived.vorticity_and_q(gradient([[1, 0, 0], [0, -1, 0], [0, 0, 0]]))
    assert curl[0] == pytest.approx([0.0, 0.0, 0.0])
    assert q[0] < 0


def test_the_wall_shear_of_a_linear_profile_and_its_y_plus() -> None:
    """Fluid above a wall at z = 0, u = (a z, 0, 0): tau/rho = nu a along x; the cell centres sit at
    z = h/2, so y+ = sqrt(nu a) (h/2) / nu. The wall's triangles face out of the fluid (-z)."""
    nu, a, h = 1.5e-5, 40.0, 0.002
    wall = plate(4, flip=True)
    n = wall.positions.shape[0]
    centres = wall.positions + np.array([0.0, 0.0, h / 2])
    out = derived.wall_fields(
        wall.positions,
        wall.triangles,
        gradient([[0, 0, a], [0, 0, 0], [0, 0, 0]], n),
        nu,
        None,
        centres,
    )
    assert out["wallShearStress_mag"] == pytest.approx(np.full(n, nu * a), rel=1e-5)
    assert out["wallShearStress"][:, 0] == pytest.approx(np.full(n, nu * a), rel=1e-5)
    assert out["yPlus"] == pytest.approx(np.full(n, np.sqrt(nu * a) * (h / 2) / nu), rel=1e-4)
    # A wall function's nut adds to the stress; a density turns it into Pa.
    turbulent = derived.wall_fields(
        wall.positions,
        wall.triangles,
        gradient([[0, 0, a], [0, 0, 0], [0, 0, 0]], n),
        nu,
        np.full(n, nu),
        centres,
        density=2.0,
    )
    assert turbulent["wallShearStress_mag"] == pytest.approx(np.full(n, 2 * 2 * nu * a), rel=1e-5)


def test_points_are_matched_to_the_mesh_or_refused() -> None:
    volume = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]])
    assert derived.match_points(volume, np.array([[0.0, 1, 0], [0, 0, 0]]), 1e-9).tolist() == [2, 0]
    assert derived.match_points(volume, np.array([[0.5, 0.5, 0]]), 1e-9) is None


@pytest.mark.parametrize(
    ("text", "nu", "rho"),
    [
        ("viscosityModel  constant;\nnu              [0 2 -1 0 0 0 0] 1.5e-05;\n", 1.5e-5, None),
        ("nu 1e-06; // water\n", 1e-6, None),
        ("mixture { transport { mu 1.8e-05; } equationOfState { rho 1.2; } }", 1.8e-5 / 1.2, 1.2),
        ("mixture { transport { mu 1.8e-05; } }", None, None),
    ],
)
def test_the_viscosity_is_read_from_the_case(
    tmp_path: Path, text: str, nu: float | None, rho: float | None
) -> None:
    (tmp_path / "constant").mkdir()
    (tmp_path / "constant" / "physicalProperties").write_text(text)
    got_nu, got_rho = derived.read_viscosity(tmp_path)
    assert got_nu == (pytest.approx(nu) if nu is not None else None)
    assert got_rho == (pytest.approx(rho) if rho is not None else None)
    assert derived.read_viscosity(tmp_path / "missing") == (None, None)


def test_a_case_gets_derived_fields_through_vtk(tmp_path: Path) -> None:
    """The one-hex case: U is uniform, so vorticity and the wall shear are zero, and every patch
    carries vorticity and Q while only the wall carries the stress and y+."""
    pytest.importorskip("vtkmodules")
    from stokes_agent.extract import extract_fields, unpack_fields
    from tests.test_agent_fields import surfaces_header_for, write_case

    case = tmp_path / "hex"
    write_case(case)
    (case / "constant" / "physicalProperties").write_text("nu [0 2 -1 0 0 0 0] 1e-05;\n")
    data, header = extract_fields(case, surfaces_header_for(case), None)
    by_name = {p["name"]: p for p in header["patches"]}
    assert {"vorticity", "vorticity_mag", "Q", "wallShearStress_mag", "yPlus"} <= set(
        by_name["a"]["fields"]
    )
    assert "yPlus" not in by_name["b"]["fields"]
    _, arrays = unpack_fields(data)
    assert np.allclose(arrays["a"]["vorticity_mag"], 0.0, atol=1e-6)
    assert np.allclose(arrays["a"]["wallShearStress_mag"], 0.0, atol=1e-9)
    assert header["units"]["Q"] == "1/s2" and header["units"]["yPlus"] == ""


def test_heat_flows_into_the_fluid_from_a_hotter_wall() -> None:
    """Fluid above a wall at z = 0 with T falling away from it (dT/dz = -g): q = k g into the fluid,
    plus c * alphat where the run has a turbulent diffusivity."""
    wall = plate(3, flip=True)
    n = wall.positions.shape[0]
    grad_t = np.tile([0.0, 0.0, -50.0], (n, 1))
    q = derived.wall_heat_flux(wall.positions, wall.triangles, grad_t, 0.6, 4180.0, None)
    assert q == pytest.approx(np.full(n, 0.6 * 50.0), rel=1e-5)
    turbulent = derived.wall_heat_flux(
        wall.positions, wall.triangles, grad_t, 0.6, 4180.0, np.full(n, 1e-4)
    )
    assert turbulent == pytest.approx(np.full(n, (0.6 + 4180.0 * 1e-4) * 50.0), rel=1e-5)


def test_the_conductivity_is_c_mu_over_pr(tmp_path: Path) -> None:
    (tmp_path / "constant").mkdir()
    props = tmp_path / "constant" / "physicalProperties"
    props.write_text("mixture { thermodynamics { Cv 4180; } transport { mu 0.001; Pr 7; } }")
    k, c = derived.read_conductivity(tmp_path)
    assert k == pytest.approx(4180 * 0.001 / 7) and c == pytest.approx(4180)
    props.write_text("mixture { thermodynamics { Cp 1005; } transport { mu 1.8e-5; } }")
    assert derived.read_conductivity(tmp_path) == (None, pytest.approx(1005))
    props.write_text("nu 1e-5;")
    assert derived.read_conductivity(tmp_path) == (None, None)
