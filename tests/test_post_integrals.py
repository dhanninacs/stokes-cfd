"""The results numerics (`stokes.post.integrals`, `contours`, `calculator`) against answers worked
out by hand on a unit square, so a wrong weight or a flipped normal shows as a wrong number."""

from __future__ import annotations

import math

import numpy as np
import pytest

from stokes.errors import ExpressionError, UnitMismatch
from stokes.post.calculator import evaluate_field, foam_units, unit_text
from stokes.post.contours import contour_segments, even_levels
from stokes.post.integrals import (
    QUANTITIES,
    ReportInvalid,
    Surface,
    combine,
    coordinate,
    histogram,
    surface_integrals,
    surface_plot,
)


def plate(n: int = 20, x0: float = 0.0, width: float = 1.0, flip: bool = False) -> Surface:
    """A ``width`` x 1 plate in z = 0 split into ``2 n^2`` triangles facing +z (or -z)."""
    xs = np.linspace(x0, x0 + width, n + 1)
    ys = np.linspace(0.0, 1.0, n + 1)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    positions = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
    tris = []
    for i in range(n):
        for j in range(n):
            a, b = i * (n + 1) + j, (i + 1) * (n + 1) + j
            c, d = a + 1, b + 1
            tris += [[a, b, d], [a, d, c]]
    triangles = np.asarray(tris, dtype=np.int64)
    if flip:
        triangles = triangles[:, ::-1]
    return Surface("plate", positions, triangles)


def test_a_linear_field_is_integrated_exactly() -> None:
    s = plate(8)
    phi = 2 * s.positions[:, 0] + 3 * s.positions[:, 1]
    r = surface_integrals(s, phi)
    assert r["area"] == pytest.approx(1.0)
    assert r["area_avg"] == pytest.approx(2.5)
    assert r["integral"] == pytest.approx(2.5)
    assert r["vertex_min"] == pytest.approx(0.0) and r["vertex_max"] == pytest.approx(5.0)
    # With no velocity there is no flow and nothing mass-weighted, said as None.
    assert r["flow_rate"] is None and r["mass_avg"] is None and r["mass_flow"] is None
    assert set(r) == set(QUANTITIES)


def test_flow_rate_follows_the_triangles_and_mass_needs_a_density() -> None:
    s = plate(4)
    velocity = np.tile([0.0, 0.0, 2.0], (s.positions.shape[0], 1))
    out = surface_integrals(s, np.ones(s.positions.shape[0]), velocity, density=1000.0)
    assert out["flow_rate"] == pytest.approx(2.0)
    assert out["mass_flow"] == pytest.approx(2000.0)
    back = surface_integrals(plate(4, flip=True), None, velocity)
    assert back["flow_rate"] == pytest.approx(-2.0)
    assert back["mass_flow"] is None


def test_uniformity_index_and_standard_deviation_match_the_hand_values() -> None:
    s = plate(60)
    phi = 1.0 + s.positions[:, 0]  # mean 1.5, mean |phi - 1.5| = 0.25
    r = surface_integrals(s, phi)
    assert r["uniformity_area"] == pytest.approx(1 - 0.25 / (2 * 1.5), abs=2e-3)
    assert r["std_dev"] == pytest.approx(math.sqrt(1 / 12), rel=1e-2)
    flat = surface_integrals(s, np.full(s.positions.shape[0], 4.0))
    assert flat["uniformity_area"] == pytest.approx(1.0) and flat["std_dev"] == pytest.approx(0.0)
    zero = surface_integrals(s, np.zeros(s.positions.shape[0]))
    assert zero["uniformity_area"] is None


def test_mass_weighting_follows_the_flux() -> None:
    """Flow only through the right half: the mass-weighted mean is the right half's value."""
    s = plate(20)
    x = s.positions[:, 0]
    w = np.where(x > 0.5, 1.0, 0.0)
    velocity = np.stack([np.zeros_like(w), np.zeros_like(w), w], axis=1)
    phi = np.where(x > 0.5, 10.0, 0.0)
    r = surface_integrals(s, phi, velocity)
    assert r["mass_avg"] == pytest.approx(10.0, rel=0.06)
    assert r["area_avg"] == pytest.approx(5.0, rel=0.06)


def test_the_net_row_sums_and_reweights() -> None:
    left, right = plate(4, 0.0, 1.0), plate(4, 1.0, 3.0)
    a = surface_integrals(left, np.full(left.positions.shape[0], 1.0))
    b = surface_integrals(right, np.full(right.positions.shape[0], 5.0))
    net = combine([a, b])
    assert net["area"] == pytest.approx(4.0)
    assert net["area_avg"] == pytest.approx((1 * 1 + 5 * 3) / 4)
    assert net["vertex_max"] == pytest.approx(5.0)
    assert net["std_dev"] is None and net["uniformity_area"] is None


def test_a_bad_surface_is_refused() -> None:
    s = plate(2)
    with pytest.raises(ReportInvalid):
        surface_integrals(Surface("bad", s.positions, s.triangles + 100), None)
    with pytest.raises(ReportInvalid):
        surface_integrals(s, np.ones(3))
    with pytest.raises(ReportInvalid):
        surface_integrals(s, np.ones((s.positions.shape[0], 3)))


def test_the_histogram_is_weighted_by_area_not_by_count() -> None:
    """A fine left half and a coarse right half of equal area weigh the same."""
    fine, coarse = plate(20, 0.0, 1.0), plate(2, 1.0, 1.0)
    out = histogram(
        [
            (fine, np.zeros(fine.positions.shape[0])),
            (coarse, np.ones(coarse.positions.shape[0])),
        ],
        bins=2,
    )
    assert out["fraction"] == pytest.approx([0.5, 0.5])
    assert out["total"] == pytest.approx(2.0)
    assert len(out["edges"]) == 3
    with pytest.raises(ReportInvalid):
        histogram([(fine, np.zeros(fine.positions.shape[0]))], bins=0)


def test_a_constant_field_fills_one_bin() -> None:
    s = plate(3)
    out = histogram([(s, np.full(s.positions.shape[0], 7.0))], bins=5)
    assert sum(out["fraction"]) == pytest.approx(1.0)
    assert max(out["fraction"]) == pytest.approx(1.0)


def test_xy_plot_axes() -> None:
    s = plate(10)
    pos = s.positions
    assert coordinate(pos, "y") == pytest.approx(pos[:, 1])
    along = coordinate(pos, "direction", [2.0, 0.0, 0.0], [0.5, 0.0, 0.0])
    assert along == pytest.approx(pos[:, 0] - 0.5)
    radius = coordinate(pos, "radius", [0.0, 0.0, 1.0], [0.0, 0.0, 0.0])
    assert radius == pytest.approx(np.hypot(pos[:, 0], pos[:, 1]))
    with pytest.raises(ReportInvalid):
        coordinate(pos, "radius", [0.0, 0.0, 0.0])
    with pytest.raises(ReportInvalid):
        coordinate(pos, "theta")
    plot = surface_plot(s, pos[:, 0] * 2, "x")
    assert plot["x"] == sorted(plot["x"])
    assert plot["y"] == pytest.approx([2 * x for x in plot["x"]])


def test_contour_lines_lie_on_the_level_and_have_its_length() -> None:
    s = plate(9)
    lines = contour_segments(s.positions, s.triangles, s.positions[:, 0], [0.5])
    seg = np.asarray(lines[0]["segments"]).reshape(-1, 2, 3)
    assert seg[:, :, 0] == pytest.approx(0.5)
    assert np.linalg.norm(seg[:, 1] - seg[:, 0], axis=1).sum() == pytest.approx(1.0)
    # A level outside the field draws nothing; a level exactly on vertices still draws its line.
    empty = contour_segments(s.positions, s.triangles, s.positions[:, 0], [5.0])
    assert empty[0]["segments"] == []
    on = contour_segments(s.positions, s.triangles, s.positions[:, 0], [1 / 9])
    assert len(on[0]["segments"]) > 0


def test_even_levels_split_the_range_into_bands() -> None:
    assert even_levels(0.0, 10.0, 4) == pytest.approx([2.0, 4.0, 6.0, 8.0])
    with pytest.raises(ReportInvalid):
        even_levels(1.0, 1.0, 3)
    with pytest.raises(ReportInvalid):
        even_levels(0.0, 1.0, 1000)


def test_the_calculator_keeps_units_and_refuses_mismatches() -> None:
    names = {
        "U_mag": (np.array([1.0, 2.0]), "m/s"),
        "p": (np.array([0.5, 1.0]), "m2/s2"),
        "x": (np.array([0.0, 1.0]), "m"),
        "rho": (1000.0, "kg/m3"),
        "k": (np.array([1.0, 2.0]), ""),
    }
    total = evaluate_field("p + 0.5 * U_mag^2", names, 2)
    assert total.values == pytest.approx([1.0, 3.0]) and total.units == "m2/s2"
    assert evaluate_field("rho * p", names, 2).units == "Pa"
    assert evaluate_field("x / 2 [mm]", names, 2).values == pytest.approx([0.0, 500.0])
    assert evaluate_field("2", names, 2).values == pytest.approx([2.0, 2.0])
    assert evaluate_field("max(U_mag, 1.5 [m/s])", names, 2).values == pytest.approx([1.5, 2.0])
    with pytest.raises(UnitMismatch):
        evaluate_field("U_mag + p", names, 2)
    with pytest.raises(ExpressionError, match="unknown name"):
        evaluate_field("T + 1", names, 2)
    with pytest.raises(ExpressionError):
        evaluate_field("U_mag ^ k", names, 2)
    with pytest.raises(ExpressionError):
        evaluate_field("U_mag +", names, 2)


def test_unit_text_round_trip() -> None:
    assert unit_text("m2/s2") == "m**2/s**2"
    assert foam_units("m ** 2 / s ** 2") == "m2/s2"


def test_a_flat_surface_has_a_centre_and_a_normal() -> None:
    from stokes.post.integrals import centre_and_normal

    centre, normal = centre_and_normal(plate(6))
    assert centre == pytest.approx([0.5, 0.5, 0.0])
    assert normal == pytest.approx([0.0, 0.0, 1.0])


def test_the_robust_range_follows_the_area_not_the_point_count() -> None:
    """A thin strip of zeros along one edge (no-slip) moves min but not the robust low end."""
    from stokes.post.integrals import weighted_range

    s = plate(200)
    x = s.positions[:, 0]
    values = np.where(x < 1e-9, 0.0, 2.0 + 0.2 * x)
    out = weighted_range([(s, values)], 0.02)
    assert out["min"] == 0.0 and out["max"] == pytest.approx(2.2)
    assert out["low"] > 1.9 and out["high"] <= 2.2
    with pytest.raises(ReportInvalid):
        weighted_range([(s, values)], 0.6)
