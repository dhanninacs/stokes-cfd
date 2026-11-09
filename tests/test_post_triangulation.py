"""Barycentric interpolation on a cached triangulation: EXACT for a linear field.

Claude's 2026-10-20 decision: *"a displayed probe value with units must be right, not
within 2.5 %"* - a user compares it with a hand calculation, and the linear-field
case is exactly that comparison. `stokes/post/probe.py`'s inverse-distance answer
was 2.5 % out on `f = 2x + 3y`; this must be 1e-9.
"""

from __future__ import annotations

import numpy as np
import pytest

from stokes.post.triangulation import (
    OutsideSurface,
    TriangulationCache,
    TriangulationNotReady,
    triangulate,
)


def _patch(n: int = 6) -> np.ndarray:
    """An `n x n` patch in the z = 0 plane, 0..1 in x and y."""
    xs, ys = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    return np.column_stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)])


def test_a_LINEAR_field_is_reproduced_exactly() -> None:
    """**The number this feature was built for.** Two points, two hand calculations."""
    positions = _patch()
    values = 2.0 * positions[:, 0] + 3.0 * positions[:, 1]
    tri = triangulate(positions)
    for point in ([0.25, 0.5, 0.0], [0.75, 0.25, 0.0], [0.1, 0.9, 0.0]):
        expected = 2.0 * point[0] + 3.0 * point[1]
        assert tri.interpolate(np.array(point), values) == pytest.approx(expected, abs=1e-9)


def test_a_uniform_field_comes_back_exactly() -> None:
    positions = _patch()
    tri = triangulate(positions)
    assert tri.interpolate(
        np.array([0.31, 0.62, 0.0]), np.full(positions.shape[0], 3.5)
    ) == pytest.approx(3.5, abs=1e-12)


def test_a_point_ON_a_vertex_returns_that_vertex() -> None:
    positions = _patch(5)
    values = 2.0 * positions[:, 0] + 3.0 * positions[:, 1]
    tri = triangulate(positions)
    # 0.25 is a grid line at n=5, so this point IS a vertex.
    assert tri.interpolate(np.array([0.25, 0.5, 0.0]), values) == pytest.approx(
        2.0 * 0.25 + 3.0 * 0.5, abs=1e-12
    )


def test_a_point_OUTSIDE_the_patch_is_REFUSED_not_extrapolated() -> None:
    """**Said plainly, never extrapolated.** A value guessed past the edge of the data is a number a
    user would believe and the surface never measured."""
    tri = triangulate(_patch())
    with pytest.raises(OutsideSurface, match="outside the surface"):
        tri.interpolate(np.array([1.5, 0.5, 0.0]), np.zeros(36))


def test_a_degenerate_patch_is_REFUSED() -> None:
    """Every point on one line: no surface to interpolate on."""
    line = np.column_stack([np.linspace(0, 1, 10), np.zeros(10), np.zeros(10)])
    with pytest.raises(OutsideSurface, match="cannot be triangulated"):
        triangulate(line)


def test_too_few_points_is_REFUSED() -> None:
    with pytest.raises(OutsideSurface, match="too few points"):
        triangulate(np.zeros((3, 3)))


def test_it_FINDS_the_plane_a_patch_is_closest_to() -> None:
    """A patch thin in y - a wall facing the camera - is triangulated in (x, z), not (x, y).

    Without this a patch lying in the xz plane would project to a LINE in (x, y) and could not be
    triangulated at all, which is most of the walls of most models.
    """
    xs, zs = np.meshgrid(np.linspace(0, 1, 5), np.linspace(0, 1, 5))
    wall = np.column_stack([xs.ravel(), np.zeros(xs.size), zs.ravel()])
    tri = triangulate(wall)
    assert tri.axes == (0, 2)
    values = 2.0 * wall[:, 0] + 5.0 * wall[:, 2]
    assert tri.interpolate(np.array([0.3, 0.0, 0.6]), values) == pytest.approx(
        2.0 * 0.3 + 5.0 * 0.6, abs=1e-9
    )


def _wait_for(cache: TriangulationCache, key: str, timeout: float = 5.0) -> object:
    """Poll until the cache answers, so a background build is waited for without a fixed sleep."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return cache.get(key)
        except TriangulationNotReady:
            time.sleep(0.01)
    raise AssertionError(f"the triangulation for {key} was never built")


def test_a_probe_before_the_build_says_PREPARING_and_does_not_build_inline() -> None:
    """**The contract, not a fallback.** Building inline would put 319 ms inside the command's
    200 ms budget, so a miss starts a build and says "preparing" - never a less accurate number."""
    cache = TriangulationCache()
    with pytest.raises(TriangulationNotReady, match="preparing"):
        cache.get("patch-a")
    # **Nothing was built by the failed `get`**: the second call says "preparing" too, until the
    # background build lands.
    cache.request("patch-a", _patch())
    built = _wait_for(cache, "patch-a")
    assert built.interpolate(np.array([0.25, 0.5, 0.0]), np.full(36, 7.0)) == pytest.approx(7.0)


def test_requesting_the_same_surface_TWICE_builds_it_once() -> None:
    """A line plot asks per point; without idempotence it would start a build per probe."""
    cache = TriangulationCache()
    cache.request("patch-a", _patch())
    cache.request("patch-a", _patch())
    cache.request("patch-a", _patch())
    first = _wait_for(cache, "patch-a")
    assert cache.get("patch-a") is first


def test_a_surface_that_CANNOT_be_triangulated_is_remembered_as_failed() -> None:
    """Otherwise every probe re-attempts 319 ms on a patch that can never work."""
    cache = TriangulationCache()
    line = np.column_stack([np.linspace(0, 1, 10), np.zeros(10), np.zeros(10)])
    cache.request("line", line)
    import time

    for _ in range(300):
        try:
            cache.get("line")
        except OutsideSurface:
            break
        except TriangulationNotReady:
            time.sleep(0.01)
    else:
        raise AssertionError("the degenerate patch was never marked failed")
    with pytest.raises(OutsideSurface, match="cannot be triangulated"):
        cache.get("line")


def test_forgetting_one_surface_leaves_the_others() -> None:
    cache = TriangulationCache()
    cache.request("a", _patch())
    cache.request("b", _patch())
    _wait_for(cache, "a")
    _wait_for(cache, "b")
    cache.forget("a")
    with pytest.raises(TriangulationNotReady):
        cache.get("a")
    assert cache.get("b") is not None


def test_forgetting_EVERYTHING_clears_both_maps() -> None:
    cache = TriangulationCache()
    cache.request("a", _patch())
    _wait_for(cache, "a")
    cache.forget()
    with pytest.raises(TriangulationNotReady):
        cache.get("a")
