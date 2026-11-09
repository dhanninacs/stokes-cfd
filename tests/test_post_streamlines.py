"""Streamlines against fields whose answer is known (claude-win2's results item 4).

The point of an analytic case is that the right answer does not depend on the code:
in a **uniform** flow every streamline is a straight line along the flow, and in a field
that does not exist there is nothing to draw. A test on a real duct would show *a* line
and prove nothing about where it goes.
"""

from __future__ import annotations

import numpy as np
import pytest

from stokes.post.streamlines import MAX_LINES, StreamlinesInvalid, _uniform_shape, streamlines


def _patch(n: int = 12) -> np.ndarray:
    """An `n x n` patch in the z = 0 plane, 0..1 in x and y."""
    xs, ys = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    return np.column_stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)])


def test_a_UNIFORM_flow_gives_straight_lines_along_it() -> None:
    """**The analytic case.** Flow everywhere +x: each streamline is horizontal at its seed's y."""
    positions = _patch()
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (positions.shape[0], 1))
    seeds = np.array([[0.1, 0.5, 0.0], [0.1, 0.2, 0.0]])
    lines = streamlines(positions, vectors, seeds, max_length=0.5, step=0.01)
    assert lines, "a uniform field must produce streamlines"
    for line in lines:
        ys = {round(p[1], 6) for p in line.points}
        zs = {round(p[2], 6) for p in line.points}
        # **Constant y and z**: a straight line along the flow, which 'uniform' means.
        assert len(ys) == 1, f"the line drifted in y: {sorted(ys)}"
        assert len(zs) == 1, f"the line drifted off the patch: {sorted(zs)}"


def test_it_goes_FORWARD_and_BACKWARD_from_the_same_seed() -> None:
    """A seed in a uniform +x field traces both ways.

    **`both` returns TWO lines, not one joined line** - measured: forward runs x 0.5 -> 0.8,
    backward 0.2 -> 0.5, and "both" gives both of those. My first version assumed a single joined
    polyline and failed; the two-line answer is what the tracer produces and what a caller can draw,
    so the docstring says so rather than the test being loosened until it passed.
    """
    positions = _patch()
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (positions.shape[0], 1))
    seeds = np.array([[0.5, 0.5, 0.0]])
    forward = streamlines(positions, vectors, seeds, max_length=0.3, step=0.01, direction="forward")
    backward = streamlines(
        positions, vectors, seeds, max_length=0.3, step=0.01, direction="backward"
    )
    both = streamlines(positions, vectors, seeds, max_length=0.3, step=0.01, direction="both")
    assert forward and backward
    # Forward goes +x from the seed, backward goes -x, and neither crosses to the other side.
    assert min(p[0] for p in forward[0].points) >= 0.5 - 1e-9
    assert max(p[0] for p in backward[0].points) <= 0.5 + 1e-9
    # **"Both" is the two of them**, so it reaches past the seed in each direction.
    assert len(both) >= 2
    assert min(p[0] for p in both[0].points) <= 0.5 + 1e-9
    assert max(p[0] for p in both[-1].points) >= 0.5 - 1e-9


def test_the_speed_comes_back_with_the_line() -> None:
    """A plot colours a line by speed, so each point carries the nearest sample's magnitude."""
    positions = _patch()
    vectors = np.tile(np.array([2.0, 0.0, 0.0]), (positions.shape[0], 1))
    lines = streamlines(positions, vectors, np.array([[0.1, 0.5, 0.0]]), max_length=0.3, step=0.01)
    assert lines
    assert all(s == pytest.approx(2.0) for s in lines[0].speeds)


def test_a_ZERO_field_has_nothing_to_trace() -> None:
    """No flow is an answer, not an error: the list is empty and the caller says so."""
    positions = _patch()
    vectors = np.zeros_like(positions)
    assert streamlines(positions, vectors, np.array([[0.5, 0.5, 0.0]])) == []


def test_too_many_SEEDS_are_refused_with_the_reason() -> None:
    positions = _patch()
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (positions.shape[0], 1))
    with pytest.raises(StreamlinesInvalid, match=str(MAX_LINES)):
        streamlines(positions, vectors, np.zeros((MAX_LINES + 1, 3)))


def test_a_mismatched_vector_array_is_refused() -> None:
    positions = _patch()
    with pytest.raises(StreamlinesInvalid, match="vectors must be"):
        streamlines(positions, np.zeros((3, 3)), np.array([[0.5, 0.5, 0.0]]))


def test_a_bad_DIRECTION_is_refused() -> None:
    positions = _patch()
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (positions.shape[0], 1))
    with pytest.raises(StreamlinesInvalid, match="direction"):
        streamlines(positions, vectors, np.array([[0.5, 0.5, 0.0]]), direction="sideways")


def test_no_seeds_asks_for_nothing() -> None:
    positions = _patch()
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (positions.shape[0], 1))
    assert streamlines(positions, vectors, np.empty((0, 3))) == []


def test_a_seed_is_flattened_with_its_surface() -> None:
    """**The bug my first version had, pinned.**

    The grid is laid out in the surface's two dominant axes with z = 0, and the seeds were
    inserted at their original 3D positions - so a seed on a slice at z = 0.0105 sat off a surface
    drawn at z = 0 and the tracer answered "no lines" for a slice full of flow. Measured on the
    audit's duct: the same data traced 2002 points once the seed was flattened with it.

    A patch at a constant z, with flow along it, is that geometry exactly.
    """
    xs, ys = np.meshgrid(np.linspace(0, 1, 12), np.linspace(0, 1, 12))
    plane = np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, 0.0105)])
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (plane.shape[0], 1))
    # **A seed at the surface's own z**, not at 0.
    lines = streamlines(plane, vectors, np.array([[0.1, 0.5, 0.0105]]), max_length=0.4, step=0.01)
    assert lines, "a seed ON a surface at z = 0.0105 must find it"
    # **The line comes back AT the surface z**, not at the flattened 0: asserting 0 here pinned
    # the bug that drew every line 10.5 mm below the model on the audit duct.
    assert all(p[2] == pytest.approx(0.0105, abs=1e-6) for p in lines[0].points)


def _volume(n: int = 10) -> np.ndarray:
    """An `n`-cubed grid of samples in 0..1, a real volume rather than a patch."""
    g = np.linspace(0, 1, n)
    x, y, z = np.meshgrid(g, g, g, indexing="ij")
    return np.column_stack([x.ravel(), y.ravel(), z.ravel()])


def test_a_VOLUME_traces_through_all_three_axes() -> None:
    """**The defect this test exists for**: `_trace` flattened EVERYTHING into two dominant axes,
    which is right for a slice and destroys a volume - measured, a 25-cubed volume produced **0
    lines**. The grid is tetrahedralised now when all three extents are real.

    The analytic check is the same as the flat case: in uniform +x flow a line keeps its seed's y
    and z and runs the whole length.
    """
    positions = _volume()
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (positions.shape[0], 1))
    # **A seed OFF the median plane on both axes** - z = 0.2, y = 0.3. This matters: the flattened
    # path restores a constant axis from the MEDIAN of the positions, so a seed at 0.5 in a cube on
    # 0.5 comes back at 0.5 whether or not the volume was flattened, and a test seeded there proves
    # nothing. Measured with the volume branch removed: this seed's line came back at z = 0.5.
    lines = streamlines(positions, vectors, np.array([[0.05, 0.3, 0.2]]), max_length=0.8, step=0.05)
    assert lines, "a volume with flow must trace"
    line = lines[0]
    # **It MOVES along x**, which the flattened version did not: it returned one stationary point.
    xs = [p[0] for p in line.points]
    assert max(xs) - min(xs) > 0.5, f"the line did not travel: x from {min(xs)} to {max(xs)}"
    # **And it keeps its seed's y and z**, because the flow has no component in either.
    #
    # **The z assertion is the one that catches a flattening.** Measured: with the volume branch
    # removed, the trace still produced a plausible line - x 0.05 to 0.85 - because a flattened 3-D
    # grid still has x and y spread. What gives it away is that the line comes back at the MEDIAN,
    # not at the seed. Only the assertions below tell the two apart.
    assert {round(p[1], 6) for p in line.points} == {0.3}
    assert {round(p[2], 6) for p in line.points} == {0.2}, (
        "the line was flattened into the z = 0 plane"
    )


def test_a_FLAT_patch_still_takes_the_surface_path() -> None:
    """The volume branch must not capture a slice: a patch with one constant axis is flattened
    exactly as before, and its line lands back on that axis."""
    positions = _patch()
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (positions.shape[0], 1))
    lines = streamlines(positions, vectors, np.array([[0.1, 0.5, 0.0]]), max_length=0.4, step=0.01)
    assert lines
    assert {round(p[2], 6) for p in lines[0].points} == {0.0}


def test_a_UNIFORM_volume_is_described_not_tetrahedralised() -> None:
    """**The optimisation, with its reason in the test**: a decimated cell-centred field IS a
    structured
    grid, and tetrahedralising one is wasted work - measured, `vtkDelaunay3D` built 82 944 cells
    from
    15 625 points in **4259 ms**, while the same samples as an image build in **0 ms** with 13 824
    cells
    and trace in **1 ms**.
    """
    positions = _volume(5)  # 125 samples: a 5-cubed grid
    shape = _uniform_shape(positions)
    assert shape is not None
    counts, origin, spacing = shape
    assert counts == (5, 5, 5)
    assert origin == (0.0, 0.0, 0.0)
    assert spacing == pytest.approx((0.25, 0.25, 0.25))


def test_a_NON_uniform_volume_is_refused_by_the_detector() -> None:
    """**Strict on purpose**: describing a grid that is not one traces through cells that do not
    exist,
    which is worse than the slow path. One moved sample is enough to refuse it."""
    positions = _volume(5)
    moved = positions.copy()
    moved[5, 0] += 0.01
    assert _uniform_shape(moved) is None


def test_an_IMAGE_volume_traces_to_the_same_answer() -> None:
    """The image path must not change the ANSWER, only the speed: uniform flow still gives a line
    that
    keeps its seed's y and z and runs the length."""
    positions = _volume(10)  # 1000 samples = 10-cubed, so it takes the image path
    vectors = np.tile(np.array([1.0, 0.0, 0.0]), (positions.shape[0], 1))
    lines = streamlines(positions, vectors, np.array([[0.05, 0.3, 0.2]]), max_length=0.8, step=0.05)
    assert lines
    line = lines[0]
    xs = [p[0] for p in line.points]
    assert max(xs) - min(xs) > 0.5
    assert {round(p[1], 5) for p in line.points} == {0.3}
    assert {round(p[2], 5) for p in line.points} == {0.2}
