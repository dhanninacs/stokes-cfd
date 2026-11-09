"""`stokes.post.seeds`: where streamlines start (ADR-0030)."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stokes.post.seeds import (
    MAX_SEEDS,
    SeedsInvalid,
    line_seeds,
    patch_seeds,
    plane_seeds,
    sphere_seeds,
)

finite = st.floats(min_value=-10, max_value=10, allow_nan=False, allow_infinity=False)
point = st.tuples(finite, finite, finite)


@given(point, point, st.integers(1, MAX_SEEDS))
def test_a_line_has_n_points_between_its_ends(
    a: tuple[float, ...], b: tuple[float, ...], n: int
) -> None:
    seeds = line_seeds(a, b, n)
    assert seeds.shape == (n, 3)
    low = np.minimum(a, b) - 1e-9
    high = np.maximum(a, b) + 1e-9
    assert np.all(seeds >= low) and np.all(seeds <= high)
    if n > 1:
        assert np.allclose(seeds[0], a) and np.allclose(seeds[-1], b)


@settings(max_examples=60)
@given(
    point,
    point.filter(lambda p: float(np.linalg.norm(p)) > 1e-3),
    st.floats(0.01, 5),
    st.integers(1, MAX_SEEDS),
)
def test_a_plane_grid_lies_in_its_plane_inside_its_size(
    origin: tuple[float, ...], normal: tuple[float, ...], size: float, n: int
) -> None:
    seeds = plane_seeds(origin, normal, size, n)
    assert 1 <= len(seeds) <= n
    unit = np.asarray(normal) / np.linalg.norm(normal)
    assert np.allclose((seeds - origin) @ unit, 0.0, atol=1e-9)
    assert np.all(np.linalg.norm(seeds - origin, axis=1) <= size * 0.7072 + 1e-9)


@given(point, st.floats(0.01, 5), st.integers(1, MAX_SEEDS))
def test_a_sphere_is_on_its_shell(centre: tuple[float, ...], radius: float, n: int) -> None:
    seeds = sphere_seeds(centre, radius, n)
    assert seeds.shape == (n, 3)
    assert np.allclose(np.linalg.norm(seeds - centre, axis=1), radius)


def test_a_patch_is_sampled_by_area_and_moved_into_the_fluid() -> None:
    # two triangles in the plane z = 0, the first four times the area of the second; normals +z
    positions = np.array(
        [[0, 0, 0], [4, 0, 0], [0, 2, 0], [10, 0, 0], [11, 0, 0], [10, 1, 0]], float
    )
    triangles = np.array([[0, 1, 2], [3, 4, 5]])
    seeds = patch_seeds(positions, triangles, 10, inward=0.25)
    assert seeds.shape == (10, 3)
    assert np.allclose(seeds[:, 2], -0.25)  # against the +z normal
    assert int(np.sum(seeds[:, 0] < 5)) >= 8  # the larger triangle takes the large share
    assert np.array_equal(seeds, patch_seeds(positions, triangles, 10, inward=0.25))


@pytest.mark.parametrize("n", [0, -1, MAX_SEEDS + 1, True, 2.5])
def test_a_bad_count_is_refused(n: object) -> None:
    with pytest.raises(SeedsInvalid):
        line_seeds((0, 0, 0), (1, 0, 0), n)  # type: ignore[arg-type]


def test_bad_inputs_are_refused_by_name() -> None:
    with pytest.raises(SeedsInvalid):
        line_seeds((0, 0), (1, 0, 0), 3)
    with pytest.raises(SeedsInvalid):
        plane_seeds((0, 0, 0), (0, 0, 0), 1.0, 9)
    with pytest.raises(SeedsInvalid):
        sphere_seeds((0, 0, 0), -1.0, 9)
    with pytest.raises(SeedsInvalid):
        patch_seeds(np.zeros((3, 3)), np.array([[0, 1, 2]]), 4)  # a zero-area triangle
    with pytest.raises(SeedsInvalid):
        patch_seeds(np.zeros((3, 3)), np.array([[0, 1, 9]]), 4)
