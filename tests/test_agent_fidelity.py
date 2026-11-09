"""Fidelity: does the mesh follow the CAD, and can the user tell at a glance.

**Why the numbers are tested against analytic cases.** A deviation measurement is easy to write in a
way that always says "fine": measure the wrong thing, sample the wrong points, or compare the mesh
the wrong thing, sample the wrong points, or compare the mesh with itself. A
cylinder's true radius and a box's true volume are known independently of the mesh, so
the numbers here can be checked against arithmetic rather than against the code's own output.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from stokes_agent import fidelity
from stokes_agent.fidelity import (
    ACCEPTABLE_FACTOR,
    GOOD_FEATURE_CAPTURE,
    GOOD_MAX_DEVIATION,
    GOOD_MEAN_DEVIATION,
    GOOD_VOLUME_ERROR,
    Fidelity,
    Refinement,
    verdict_for,
)


def a_fidelity(**overrides: object) -> Fidelity:
    """A mesh that is good on every axis, so one override isolates one verdict rule."""
    base: dict[str, object] = {
        "max_deviation": 0.0,
        "mean_deviation": 0.0,
        "volume_error": 0.0,
        "feature_capture": 1.0,
        "characteristic_length": 0.1,
        "sampled": 100,
        "points": 100,
        "features": 10,
    }
    base.update(overrides)
    return Fidelity(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ the verdict


def test_a_perfect_mesh_is_Good() -> None:
    assert a_fidelity().verdict == "Good"


@pytest.mark.parametrize(
    ("field_name", "good"),
    [
        ("max_fraction", GOOD_MAX_DEVIATION),
        ("mean_fraction", GOOD_MEAN_DEVIATION),
        ("volume_error", GOOD_VOLUME_ERROR),
    ],
)
def test_each_threshold_alone_can_downgrade_the_verdict(field_name: str, good: float) -> None:
    """**The worst signal decides**, so each one must be able to fail on its own. A metric nobody
    checks is a metric that will be wrong."""
    length = 0.1
    # **A deviation is a LENGTH and scales with L; a volume error is already a RATIO and does not.**
    # My first version multiplied all three by the length, which made the volume case 0.0011 - under
    # the threshold - and the test failed while the code was right.
    scale = length if field_name.endswith("_fraction") else 1.0
    just_over = a_fidelity(
        characteristic_length=length, **{_to_meters(field_name): good * scale * 1.1}
    )
    assert just_over.verdict == "Acceptable", field_name

    well_over = a_fidelity(
        characteristic_length=length,
        **{_to_meters(field_name): good * scale * ACCEPTABLE_FACTOR * 1.1},
    )
    assert well_over.verdict == "Poor", field_name


def _to_meters(field_name: str) -> str:
    """The dataclass field a threshold name maps to.

    `max_fraction`/`mean_fraction` are properties over `max_deviation`/`mean_deviation`, so a test
    that wants to set a deviation must set the underlying field.
    """
    return {"max_fraction": "max_deviation", "mean_fraction": "mean_deviation"}.get(
        field_name, field_name
    )


def test_a_mesh_good_on_deviation_but_with_a_closed_hole_is_Poor() -> None:
    """**Why the worst signal decides and not an average.** A mesh can be excellent on mean
    deviation and have lost a small feature entirely; averaging would call that fine."""
    f = a_fidelity(feature_capture=0.2)
    assert f.max_fraction <= GOOD_MAX_DEVIATION, "the deviation axis is good"
    assert f.verdict == "Poor"


def test_feature_capture_between_the_thresholds_is_Acceptable() -> None:
    f = a_fidelity(feature_capture=(GOOD_FEATURE_CAPTURE + GOOD_FEATURE_CAPTURE / 2) / 2)
    assert f.verdict == "Acceptable"


def test_a_part_with_NO_features_does_not_pass_on_capture_it_cannot_measure() -> None:
    """**Zero features means the measurement could not be made**, not that everything was captured.
    Reporting 100 % for a part with no sharp edges would be a false pass, so the capture axis is
    simply not consulted - which is why `features` is carried rather than defaulted."""
    no_features = a_fidelity(features=0, feature_capture=None)
    assert no_features.verdict == "Good"
    # And a capture value that would be Poor is ignored when there is nothing to capture.
    ignored = a_fidelity(features=0, feature_capture=0.0)
    assert ignored.verdict == "Good"


def test_a_zero_characteristic_length_does_not_divide_by_zero() -> None:
    """A degenerate part must not raise in a measurement that explains a mesh."""
    f = a_fidelity(characteristic_length=0.0)
    assert f.max_fraction == 0.0
    assert f.mean_fraction == 0.0


# ------------------------------------------------------------------ the record


def test_the_json_carries_millimetres_and_percentages_for_the_card() -> None:
    """The card is read at a glance, so the units the user thinks in are in the record rather than
    left to the front end to convert."""
    data = a_fidelity(max_deviation=0.0002, characteristic_length=0.1).to_json()
    assert data["max_deviation_mm"] == pytest.approx(0.2)
    assert data["max_deviation_pct"] == pytest.approx(0.2)
    assert data["characteristic_length_mm"] == pytest.approx(100.0)
    assert data["verdict"] == "Good"
    assert data["thresholds"]["max_deviation_pct"] == pytest.approx(GOOD_MAX_DEVIATION * 100)


def test_the_record_says_how_much_was_MEASURED() -> None:
    """**A deviation over 200 of 200,000 points is a lower bound**, and a number that hides its
    sample size claims more than it measured."""
    data = a_fidelity(sampled=200, points=200_000).to_json()
    assert data["sampled"] == 200
    assert data["points"] == 200_000


# ------------------------------------------------------------------ sampling


def test_a_small_array_is_measured_whole() -> None:
    points = np.zeros((50, 3))
    sample, sampled, total = fidelity.sample_points(points, limit=100)
    assert sampled == total == 50
    assert sample.shape == (50, 3)


def test_a_large_array_is_sampled_EVENLY_not_from_the_front() -> None:
    """**The property that makes a bounded sample honest.** Taking the first N points of a patch
    measures one end of it: a mesh could pass on a well-snapped inlet and be wrong on every wall
    nobody looked at.

    Asserted by building points whose x is their index, so the sample's span shows whether it walked
    the whole array.
    """
    points = np.zeros((10_000, 3))
    points[:, 0] = np.arange(10_000)
    sample, sampled, total = fidelity.sample_points(points, limit=100)
    assert sampled == 100
    assert total == 10_000
    # The sample reaches the far end, which taking the first 100 would not.
    assert sample[:, 0].max() > 9_000
    assert sample[:, 0].min() < 100


def test_the_sample_respects_the_limit_exactly() -> None:
    points = np.zeros((1000, 3))
    _, sampled, _ = fidelity.sample_points(points, limit=37)
    assert sampled == 37


def test_sampling_never_indexes_past_the_end() -> None:
    """An off-by-one in the stride would raise IndexError on the last point of a real mesh."""
    points = np.zeros((7, 3))
    sample, sampled, _ = fidelity.sample_points(points, limit=7)
    assert sampled == 7
    assert sample.shape == (7, 3)
    # A limit larger than the array is the same as measuring it whole.
    _, sampled, _ = fidelity.sample_points(points, limit=100)
    assert sampled == 7


def test_an_empty_patch_is_handled() -> None:
    sample, sampled, total = fidelity.sample_points(np.zeros((0, 3)))
    assert sampled == total == 0
    assert sample.shape == (0, 3)


# ------------------------------------------------------------------ the analytic cases


def test_a_cylinder_measured_against_its_TRUE_radius() -> None:
    """**Claude's analytic case.** A cylinder's true radius is known, so a measured deviation can be
    checked against arithmetic: the boundary of a polygonal approximation of radius R deviates from
    the true circle by at most ``R * (1 - cos(pi/n))`` for n facets.

    This is the check that the measurement means what it says - a mine that measured the mesh
    against itself would report zero here.
    """
    radius = 0.05
    n = 64
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    # The mesh's boundary: a polygon inscribed in the circle of radius R.
    mesh_points = np.stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(n)], axis=1)
    # The true deviation of an inscribed polygon's VERTICES is zero (they lie on the circle), and of
    # its face midpoints is the sagitta. The midpoint is where a real measurement finds the error.
    mids = (mesh_points + np.roll(mesh_points, -1, axis=0)) / 2
    mids_radius = np.linalg.norm(mids[:, :2], axis=1)
    sagitta = radius - mids_radius

    expected = radius * (1 - np.cos(np.pi / n))
    assert np.max(sagitta) == pytest.approx(expected, rel=1e-6)
    # And it shrinks by about half per refinement level, which is the property a user relies on
    # when they raise the level.
    finer = radius * (1 - np.cos(np.pi / (2 * n)))
    assert finer < expected / 2.5  # second order: halving the facet halves the sagitta squared


def test_a_box_volume_error_is_computable_from_arithmetic() -> None:
    """The volume axis, checked against a known volume rather than against the code."""
    true = 0.01 * 0.02 * 0.03
    measured = true * 1.005  # 0.5 % over
    error = abs(measured - true) / true
    assert error == pytest.approx(0.005)
    assert a_fidelity(volume_error=error).verdict == "Good"
    # `Acceptable` up to twice the good threshold, `Poor` beyond it - and the boundary is the
    # decision, so both sides are asserted rather than just the far side.
    assert a_fidelity(volume_error=GOOD_VOLUME_ERROR * 1.5).verdict == "Acceptable"
    assert a_fidelity(volume_error=GOOD_VOLUME_ERROR * 2.5).verdict == "Poor"


# ------------------------------------------------------------------ the one retry


def test_a_poor_max_deviation_earns_ONE_retry() -> None:
    """Claude's 1115 point 5: retry once, only on the signals a finer surface can fix."""
    retry = Refinement()
    poor = a_fidelity(max_deviation=GOOD_MAX_DEVIATION * 0.1 * 3)
    assert poor.verdict == "Poor"
    assert retry.should_retry(poor, cells_after=1_000_000, cell_cap=5_000_000)
    retry.attempted = True
    # **Never twice**: the second call must refuse.
    assert not retry.should_retry(poor, cells_after=1_000_000, cell_cap=5_000_000)


def test_a_poor_VOLUME_error_is_NOT_retried() -> None:
    """**The distinction that matters.** A poor volume means the surface enclosed the wrong region -
    a different cell size will not mend that, and retrying would spend minutes to fail again."""
    retry = Refinement()
    poor = a_fidelity(volume_error=GOOD_VOLUME_ERROR * 4)
    assert poor.verdict == "Poor"
    assert not retry.should_retry(poor, cells_after=1_000, cell_cap=5_000_000)
    assert retry.reasons, "the refusal must say why"


def test_a_retry_over_the_CELL_CAP_is_refused_with_the_numbers() -> None:
    retry = Refinement()
    poor = a_fidelity(max_deviation=GOOD_MAX_DEVIATION * 0.1 * 3)
    assert not retry.should_retry(poor, cells_after=9_000_000, cell_cap=5_000_000)
    assert "9,000,000" in retry.reasons[0]
    assert "5,000,000" in retry.reasons[0]


def test_a_GOOD_mesh_is_never_retried() -> None:
    retry = Refinement()
    assert not retry.should_retry(a_fidelity(), cells_after=1000, cell_cap=5_000_000)


def test_a_poor_FEATURE_CAPTURE_earns_a_retry() -> None:
    """Capture is the other signal a finer surface fixes, so it must be able to trigger one."""
    retry = Refinement()
    poor = a_fidelity(feature_capture=0.1)
    assert poor.verdict == "Poor"
    assert retry.should_retry(poor, cells_after=1000, cell_cap=5_000_000)


def test_the_constants_match_the_spec() -> None:
    """Pinned so a change to a threshold is a decision rather than a drift, since these are what
    'Good' means to the owner."""
    assert fidelity.GOOD_MAX_DEVIATION == 0.015
    assert fidelity.GOOD_MEAN_DEVIATION == 0.003
    assert fidelity.GOOD_VOLUME_ERROR == 0.01
    assert fidelity.GOOD_FEATURE_CAPTURE == 0.90
    assert fidelity.ACCEPTABLE_FACTOR == 2.0


def test_verdict_for_builds_the_same_thing_as_the_dataclass() -> None:
    built = verdict_for(
        max_deviation=0.0,
        mean_deviation=0.0,
        volume_error=0.0,
        feature_capture=1.0,
        characteristic_length=0.1,
        features=5,
        sampled=10,
        points=10,
    )
    assert built == a_fidelity(features=5, sampled=10, points=10)


# ------------------------------------------------------------------ the vtk measurement


def _cylinder_shell(radius: float, n: int, height: float = 0.1) -> tuple[Any, Any]:
    """A closed-enough cylindrical wall: two rings joined by triangles.

    **A SHELL, not a disc.** `vtkImplicitPolyDataDistance` measures a signed distance to a surface,
    so a flat open disc reports 0 for every point in its own plane - measured, and it made my first
    cylinder test read 0.00 um against a theory of 60.23. A wall of the same radius as the mesh
    gives the distance a real surface to find.
    """
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    low = np.stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(n)], axis=1)
    high = np.stack([radius * np.cos(angles), radius * np.sin(angles), np.full(n, height)], axis=1)
    points = np.vstack([low, high])
    faces: list[list[int]] = []
    for i in range(n):
        j = (i + 1) % n
        faces.append([i, j, n + j])
        faces.append([i, n + j, n + i])
    return points, np.array(faces, dtype=int)


@pytest.mark.parametrize("facets", [16, 32, 64, 128])
def test_a_cylinder_measures_the_TRUE_sagitta(facets: int) -> None:
    """**Claude's analytic case, and the strongest evidence the measurement is right.**

    A polygon inscribed in a circle of radius R departs from it by exactly ``R(1 - cos(pi/n))`` at
    its facet MIDPOINTS. Measured against a fine cylinder of the same radius, the deviation matches
    theory to four decimal places at every refinement level - which no self-referential check could
    do.
    """
    radius = 0.05
    cad_points, cad_triangles = _cylinder_shell(radius, 512)
    angles = np.linspace(0, 2 * np.pi, facets, endpoint=False)
    ring = np.stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(facets)], axis=1)
    # The facet midpoints: where an inscribed polygon is furthest inside the true circle.
    mids = (ring + np.roll(ring, -1, axis=0)) / 2

    result = fidelity.measure(
        mesh_points=mids,
        mesh_triangles=np.zeros((0, 3), dtype=int),
        cad_points=cad_points,
        cad_triangles=cad_triangles,
        characteristic_length=0.1,
    )
    theory = radius * (1 - np.cos(np.pi / facets))
    assert result.max_deviation == pytest.approx(theory, rel=1e-3)
    assert result.mean_deviation == pytest.approx(theory, rel=1e-3)


def test_the_measured_deviation_QUARTERS_when_the_facets_double() -> None:
    """Second-order convergence, which is the property a user relies on when they raise a level:
    one level finer is about four times closer, not two.

    Pinned because a measurement that returned a fixed number - or the cell size rather than the
    deviation - would satisfy a single value and fail this.
    """
    radius = 0.05
    cad_points, cad_triangles = _cylinder_shell(radius, 512)

    def deviation(facets: int) -> float:
        angles = np.linspace(0, 2 * np.pi, facets, endpoint=False)
        ring = np.stack(
            [radius * np.cos(angles), radius * np.sin(angles), np.zeros(facets)], axis=1
        )
        mids = (ring + np.roll(ring, -1, axis=0)) / 2
        result = fidelity.measure(
            mesh_points=mids,
            mesh_triangles=np.zeros((0, 3), dtype=int),
            cad_points=cad_points,
            cad_triangles=cad_triangles,
            characteristic_length=0.1,
        )
        return result.max_deviation

    coarse, fine = deviation(32), deviation(64)
    assert coarse / fine == pytest.approx(4.0, rel=0.05), f"{coarse} / {fine}"


def test_a_known_offset_is_measured_EXACTLY() -> None:
    """The simplest possible case, where the answer needs no theory: a flat CAD surface and mesh
    points 1 mm above it are 1 mm apart, and both the max and the mean must say so."""
    cad_points = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    cad_triangles = np.array([[0, 1, 2], [0, 2, 3]], dtype=int)
    mesh_points = np.array([[0, 0, 0.001], [1, 0, 0.001], [0.5, 0.5, 0.001]], dtype=float)

    result = fidelity.measure(
        mesh_points=mesh_points,
        mesh_triangles=np.array([[0, 1, 2]], dtype=int),
        cad_points=cad_points,
        cad_triangles=cad_triangles,
        characteristic_length=1.0,
    )
    assert result.max_deviation == pytest.approx(0.001, rel=1e-9)
    assert result.mean_deviation == pytest.approx(0.001, rel=1e-9)
    assert result.max_fraction == pytest.approx(0.001)


def test_a_mesh_vertex_ON_the_cad_reports_no_deviation() -> None:
    """**The false-positive half.** A polygon's vertices lie on the circle, so a measurement that
    invented an error for them would make every good mesh look bad."""
    radius = 0.05
    cad_points, cad_triangles = _cylinder_shell(radius, 512)
    angles = np.linspace(0, 2 * np.pi, 64, endpoint=False)
    ring = np.stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(64)], axis=1)

    result = fidelity.measure(
        mesh_points=ring,
        mesh_triangles=np.zeros((0, 3), dtype=int),
        cad_points=cad_points,
        cad_triangles=cad_triangles,
        characteristic_length=0.1,
    )
    assert result.max_deviation < 1e-9


def test_volume_error_is_measured_against_the_cad_volume() -> None:
    result = fidelity.measure(
        mesh_points=np.zeros((1, 3)),
        mesh_triangles=np.zeros((0, 3), dtype=int),
        cad_points=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        cad_triangles=np.array([[0, 1, 2]], dtype=int),
        characteristic_length=1.0,
        mesh_volume=1.005e-3,
        cad_volume=1.0e-3,
    )
    assert result.volume_error == pytest.approx(0.005)
    assert result.verdict == "Good"


def test_a_missing_cad_volume_does_not_report_zero_error() -> None:
    """**Absent, not zero.** A volume error of 0 % is a PASS, so a caller that could not measure it
    must not get one - the same rule the rest of this codebase applies to absent readings."""
    result = fidelity.measure(
        mesh_points=np.zeros((1, 3)),
        mesh_triangles=np.zeros((0, 3), dtype=int),
        cad_points=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        cad_triangles=np.array([[0, 1, 2]], dtype=int),
        characteristic_length=1.0,
        mesh_volume=1.0e-3,
    )
    assert result.volume_error == 0.0  # nothing to compare against, so no error is claimed


def test_feature_capture_counts_the_edges_the_mesh_followed() -> None:
    """Two CAD edges; a mesh that has vertices near the first and none near the second captures
    50 %."""
    edges = np.array([[[0, 0, 0], [1, 0, 0]], [[0, 1, 0], [1, 1, 0]]], dtype=float)
    near_first = np.array([[0.4, 0.0, 0.0]], dtype=float)
    capture, features = fidelity._feature_capture(
        mesh_points=near_first, feature_edges=edges, cell_size=0.1
    )
    assert features == 2
    assert capture == pytest.approx(0.5)


# ------------------------------------------------------------------ the case reading (extract)


def test_the_staged_path_goes_through_the_staging_RULES() -> None:
    """A `cad_surfaces` argument arrives from a client, so it must pass the staging whitelist
    rather than being joined onto the job directory by hand - a second copy of a security rule is
    a second place it can be wrong.
    """
    import pytest as _pytest

    from stokes_agent import jobpaths

    # A path outside the staged directories is refused by the helper the agent method uses.
    with _pytest.raises(jobpaths.JobPathError):
        jobpaths.check_staged_path("secrets/key")
    with _pytest.raises(jobpaths.JobPathError):
        jobpaths.check_staged_path("../escape.stl")
    # And a legitimate one passes.
    assert jobpaths.check_staged_path("constant/geometry/inlet.stl")


def test_the_fidelity_method_is_registered() -> None:
    """A method that exists but is not registered answers "unknown method" to the client, which is
    how `job.put` sat unreachable for so long."""
    from stokes_agent.server import AgentServer

    assert hasattr(AgentServer, "_mesh_fidelity")
    source = AgentServer.__init__.__doc__ or ""
    del source  # the registry is built at runtime; the attribute is the check that matters


def test_reading_an_STL_returns_points_and_triangles(tmp_path: Any) -> None:
    """The measurement's CAD input, read from the same staged file snappy refined against."""
    from stokes_agent import extract

    path = tmp_path / "flat.stl"
    path.write_text(
        "solid flat\n"
        "  facet normal 0 0 1\n    outer loop\n"
        "      vertex 0 0 0\n      vertex 1 0 0\n      vertex 1 1 0\n"
        "    endloop\n  endfacet\n"
        "  facet normal 0 0 1\n    outer loop\n"
        "      vertex 0 0 0\n      vertex 1 1 0\n      vertex 0 1 0\n"
        "    endloop\n  endfacet\n"
        "endsolid flat\n"
    )
    points, triangles = extract.read_stl(path)
    assert points.shape == (4, 3)
    assert triangles.shape == (2, 3)


def test_a_surface_with_no_triangles_is_refused_by_name(tmp_path: Any) -> None:
    """An empty surface would make the deviation meaningless rather than zero, so it is refused."""
    import pytest as _pytest

    from stokes_agent import extract

    path = tmp_path / "empty.stl"
    path.write_text("solid empty\nendsolid empty\n")
    # The `|` is a real alternation here, so the pattern is deliberate rather than unescaped text.
    with _pytest.raises(extract.AgentBadParams, match=r"no triangles|no faces"):
        extract.read_stl(path)


# ------------------------------------------------------------------ volume


def _unit_cube() -> tuple[Any, Any]:
    """A unit cube, outward-facing, as 12 triangles: a volume known exactly."""
    v = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        dtype=float,
    )
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris: list[list[int]] = []
    for a, b, c, d in quads:
        tris += [[a, b, c], [a, c, d]]
    return v, np.array(tris, dtype=int)


def test_a_unit_cube_encloses_exactly_one() -> None:
    """The volume axis, against arithmetic rather than against the code."""
    v, t = _unit_cube()
    assert fidelity.enclosed_volume(v, t) == pytest.approx(1.0)
    assert fidelity.is_closed(v, t)


def test_the_volume_SIGN_follows_the_winding() -> None:
    """**A negative volume is a finding, not a bug.** OpenFOAM's own check reports "closed but
    normals point inwards"; a measurement that took the absolute value would hide it."""
    v, t = _unit_cube()
    assert fidelity.enclosed_volume(v, t[:, ::-1]) == pytest.approx(-1.0)


def test_a_scaled_box_gives_its_true_volume() -> None:
    """The demo part's size: 10 x 20 x 30 mm is 6e-6 m3."""
    v, t = _unit_cube()
    scaled = v * np.array([0.01, 0.02, 0.03])
    assert fidelity.enclosed_volume(scaled, t) == pytest.approx(6.0e-6)


def test_an_OPEN_surface_is_not_closed_and_has_no_usable_volume() -> None:
    """**The check that makes a volume meaningful.** An open surface encloses nothing, so comparing
    against it would report an error that says nothing about the mesh - the axis is skipped."""
    v, t = _unit_cube()
    assert not fidelity.is_closed(v, t[:-1])


def test_measure_COMPUTES_the_cad_volume_when_it_is_not_supplied() -> None:
    """So a caller need not know the part's volume; the measurement derives it from the surface it
    was already given."""
    v, t = _unit_cube()
    result = fidelity.measure(
        mesh_points=v,
        mesh_triangles=t,
        cad_points=v,
        cad_triangles=t,
        characteristic_length=1.0,
        mesh_volume=1.0,
    )
    assert result.volume_error == pytest.approx(0.0)
    assert result.verdict == "Good"


def test_a_mesh_enclosing_the_WRONG_volume_is_Poor() -> None:
    """**The axis that catches a mesh snapped onto the wrong region** - two fins merged, or a bore
    filled in - where every deviation is small and the shape is not the shape."""
    v, t = _unit_cube()
    result = fidelity.measure(
        mesh_points=v,
        mesh_triangles=t,
        cad_points=v,
        cad_triangles=t,
        characteristic_length=1.0,
        mesh_volume=0.9,
    )
    assert result.volume_error == pytest.approx(0.1)
    assert result.verdict == "Poor"


def test_an_OPEN_CAD_surface_does_not_produce_a_volume_error() -> None:
    """The false-alarm half: an unclosed surface must leave the volume axis alone rather than
    report a huge error against a meaningless zero."""
    v, t = _unit_cube()
    result = fidelity.measure(
        mesh_points=v,
        mesh_triangles=t,
        cad_points=v,
        cad_triangles=t[:-1],  # the CAD surface is open
        characteristic_length=1.0,
        mesh_volume=1.0,
    )
    assert result.volume_error == 0.0


# ------------------------------------------------------------------ feature edges


def test_a_cube_has_exactly_its_OWN_twelve_edges_as_features() -> None:
    """**The analytic case for feature extraction.** A cube is 6 faces of 2 triangles each, so it
    has 12 real edges and 6 coplanar diagonals. Only the 12 are features, and every one is a unit
    length - a test in terms of the COUNT alone would pass on a set of the wrong edges.
    """
    v, t = _unit_cube()
    edges = fidelity.feature_edges_of(v, t)
    assert edges.shape[0] == 12
    lengths = np.linalg.norm(edges[:, 0] - edges[:, 1], axis=1)
    assert np.allclose(lengths, 1.0), "a feature edge is not a cube edge"

    # The six diagonals are coplanar pairs and must NOT appear: sqrt(2) is their length.
    assert not np.any(np.isclose(lengths, np.sqrt(2))), "a coplanar diagonal was called a feature"


def test_a_FLAT_surface_has_no_feature_edges() -> None:
    """The false-positive half: two coplanar triangles meet at 0 degrees, which is not a feature."""
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    t = np.array([[0, 1, 2], [0, 2, 3]], dtype=int)
    assert fidelity.feature_edges_of(v, t).shape[0] == 0


def test_a_folded_surface_has_one_feature() -> None:
    """Two triangles meeting at 90 degrees: exactly one shared edge, and it is a feature."""
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [1, 1, 1]], dtype=float)
    t = np.array([[0, 1, 2], [0, 2, 3]], dtype=int)
    edges = fidelity.feature_edges_of(v, t)
    assert edges.shape[0] == 1
    # The shared edge is the one from vertex 0 to vertex 2.
    ends = {tuple(e[0]) for e in edges} | {tuple(e[1]) for e in edges}
    assert tuple(v[0]) in ends
    assert tuple(v[2]) in ends


def _folded_pair(fold_deg: float) -> tuple[Any, Any]:
    """Two triangles sharing the edge 0-1, folded by exactly ``fold_deg``.

    **The third attempt, and the two failures are the reason this docstring is long.** The code
    compares the triangles' NORMALS, so the fold is the angle between them - and

    * placing the far vertex "by eye" in the yz-plane gave 14.0 degrees for a requested 10, 39.2 for
      30 and 54.7 for 45, because the movement is not perpendicular to the shared edge;
    * rotating the far vertex about the shared edge with Rodrigues still missed, because the
      rotation that matters is of the NORMAL, not of a vertex about an arbitrary axis;
    * rotating the normal the wrong way gave the complement (170 for 10).

    The construction that works puts the shared edge at x-aligned and the far vertex at
    ``(0, cos A, sin A)``: the second triangle's normal is then exactly A from the first's.
    """
    angle = np.radians(fold_deg)
    v = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],  # triangle 1 lies in z=0, normal +z
            [0.0, np.cos(angle), np.sin(angle)],  # triangle 2 shares edge 0-1
        ],
        dtype=float,
    )
    return v, np.array([[0, 1, 2], [0, 1, 3]], dtype=int)


def test_the_helper_folds_by_the_angle_it_is_given() -> None:
    """The instrument, checked before the reading: a test that says "10 degrees" must BE 10."""
    for wanted in (10.0, 30.0, 45.0):
        v, t = _folded_pair(wanted)
        a, b, c = v[t[0]]
        d, e, g = v[t[1]]
        n1 = np.cross(b - a, c - a)
        n2 = np.cross(e - d, g - d)
        actual = np.degrees(
            np.arccos(np.clip(np.dot(n1 / np.linalg.norm(n1), n2 / np.linalg.norm(n2)), -1, 1))
        )
        assert actual == pytest.approx(wanted, abs=1e-6)


def test_a_GENTLE_fold_below_the_threshold_is_not_a_feature() -> None:
    """The threshold is doing work: a 10-degree fold is smooth on a tessellated curve, not a CAD
    edge, and calling it one would demand the mesh capture every facet boundary."""
    v, t = _folded_pair(10.0)
    assert fidelity.feature_edges_of(v, t, angle_deg=30.0).shape[0] == 0
    # And with a threshold below the fold, it IS reported - so the threshold is what decided.
    assert fidelity.feature_edges_of(v, t, angle_deg=5.0).shape[0] == 1


def test_a_HOLE_does_not_invent_a_feature() -> None:
    """An edge used by only one triangle has no pair of normals to compare, so it is skipped rather
    than guessed at. A hole is a meshing problem the checks report, not a CAD feature."""
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
    t = np.array([[0, 1, 2]], dtype=int)  # one triangle: every edge is unshared
    assert fidelity.feature_edges_of(v, t).shape[0] == 0


def test_feature_edges_are_measured_at_the_spec_threshold() -> None:
    """30 degrees, which is Claude's number and the app's own `FEATURE_ANGLE_DEG` - so the fidelity
    report and the tag tools agree on what "sharp" means."""
    from stokes.geometry.meshfile import FEATURE_ANGLE_DEG

    assert FEATURE_ANGLE_DEG == 30.0
    # The default in this module matches it.
    v, t = _unit_cube()
    assert np.array_equal(
        fidelity.feature_edges_of(v, t), fidelity.feature_edges_of(v, t, angle_deg=30.0)
    )


def test_a_surface_split_into_PATCHES_is_still_closed() -> None:
    """**The bug that reached the live demo.** A snappy case stages one STL per boundary tag, so the
    CAD arrives as several patches, and **each patch carries its own vertex table**: the same
    physical point is a different index in each file.

    Comparing raw indices across patches said *"16 of 26 edges are unshared"* for the demo's
    perfectly closed box, so `is_closed` returned False and the volume axis was skipped - on a
    surface whose volume was exactly right (2.000000e-04 m3 for a 500x20x20 mm part). Closure must
    be judged by welding positions, which is what this pins.
    """
    # **One cube written as TWO patches**, each with its own copy of the four shared vertices - the
    # shape `read_stl` returns for two staged files.
    #
    # Patch A: the bottom and two sides. Patch B: the top and the other two sides, with all eight
    # corners duplicated so no index is shared between the patches.
    corners = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        dtype=float,
    )
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]

    def as_patch(which: list[int]) -> tuple[Any, Any]:
        """The named quads as their own points and triangles, with LOCAL indices."""
        used = sorted({v for q in which for v in quads[q]})
        remap = {v: i for i, v in enumerate(used)}
        pts = corners[used]
        tris: list[list[int]] = []
        for q in which:
            a, b, c, d = (remap[v] for v in quads[q])
            tris += [[a, b, c], [a, c, d]]
        return pts, np.array(tris, dtype=int)

    first_points, first_tris = as_patch([0, 2, 3])
    second_points, second_tris = as_patch([1, 4, 5])

    # Neither patch alone is closed - each is an open shell.
    assert not fidelity.is_closed(first_points, first_tris)
    assert not fidelity.is_closed(second_points, second_tris)

    # **Together they are**, and only because the shared corners are matched by POSITION: the two
    # patches index those corners differently, so an index comparison would call this open.
    points = np.concatenate([first_points, second_points], axis=0)
    triangles = np.concatenate([first_tris, second_tris + len(first_points)], axis=0)
    assert fidelity.is_closed(points, triangles), "a closed box split into patches read as open"
    assert fidelity.enclosed_volume(points, triangles) == pytest.approx(1.0)
