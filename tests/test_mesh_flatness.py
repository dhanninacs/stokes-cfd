"""How flat a symmetry plane came out (`stokes.mesh.flatness`; the mixing elbow, 2026-11-08).

The polyhedral and tetrahedral meshes of the elbow left points of the z = 0 symmetry plane up to
3.3 mm off it with 5 mm cells, and two runs diverged there; the snappy mesh held the plane to 1e-17.
"""

from __future__ import annotations

import numpy as np
import pytest

from stokes.mesh import flatness


def plane(count: int = 400) -> np.ndarray:
    """Points on z = 0 over a 0.4 m x 0.3 m face, as float32 like the surface artifact."""
    rng = np.random.default_rng(7)
    xy = rng.uniform((-0.2, -0.15), (0.2, 0.15), size=(count, 2))
    return np.column_stack((xy, np.zeros(count))).astype(np.float32)


def test_a_flat_plane_has_nothing_off_and_no_warning() -> None:
    flat = flatness.measure("symmetry", plane())
    assert flat is not None
    assert flat.off == 0
    assert flatness.warning(flat, 0.005) is None


def test_points_off_the_plane_are_counted_and_named_against_the_cell_size() -> None:
    points = plane()
    points[:5, 2] = (-0.0028, -0.001, 0.0005, -0.0002, 0.0033)
    flat = flatness.measure("symmetry", points)
    assert flat is not None
    assert flat.off == 5
    assert flat.max_offset == pytest.approx(0.0033, rel=0.02)
    text = flatness.warning(flat, 0.005)
    assert text is not None
    assert "5 points lie up to 3.3 mm off it" in text
    assert "66 % of the cell size" in text
    assert "snappyHexMesh" in text


def test_an_offset_under_two_percent_of_a_cell_is_not_a_warning() -> None:
    """The flattened elbow mesh: 0.04 mm off with 5 mm cells, and the run converged."""
    points = plane()
    points[:3, 2] = 4e-5
    flat = flatness.measure("symmetry", points)
    assert flat is not None and flat.off == 3
    assert flatness.warning(flat, 0.005) is None
    # Without a cell size any measurable offset is reported.
    assert flatness.warning(flat, None) is not None


def test_a_tilted_plane_is_still_a_plane() -> None:
    points = plane().astype(np.float64)
    points[:, 2] = 0.3 * points[:, 0] + 0.1 * points[:, 1] + 0.05
    flat = flatness.measure("symmetry", points)
    assert flat is not None and flat.off == 0


def test_a_curved_symmetry_surface_is_not_judged_as_a_plane() -> None:
    points = plane().astype(np.float64)
    points[:, 2] = points[:, 0] ** 2
    assert flatness.measure("symmetry", points) is None


def test_the_service_measures_the_symmetry_patches_of_a_surface_artifact() -> None:
    """From the agent's own artifact format: only symmetry patches, only planar ones."""
    from stokes.mesh.service import _symmetry_flatness
    from stokes.mesh.surfaces import read_header
    from stokes_agent.extract import pack_artifact

    bent = plane()
    bent[:2, 2] = (-0.0028, 0.001)
    curved = plane().astype(np.float64)
    curved[:, 2] = curved[:, 0] ** 2

    def patch(name: str, kind: str, points: np.ndarray) -> dict[str, object]:
        return {
            "name": name,
            "patch_type": kind,
            "points": points.astype(np.float32),
            "triangles": np.array([[0, 1, 2]], dtype=np.uint32),
            "edges": np.array([[0, 1]], dtype=np.uint32),
            "faces": 1,
        }

    data, _ = pack_artifact(
        [
            patch("walls", "wall", bent),
            patch("mirror", "symmetry", bent),
            patch("flat", "symmetryPlane", plane()),
            patch("dome", "symmetry", curved),
        ]
    )
    view = memoryview(data)
    found = {item["patch"]: item for item in _symmetry_flatness(view, read_header(view))}
    assert set(found) == {"mirror", "flat"}
    assert found["mirror"]["off"] == 2
    assert found["mirror"]["max_offset"] == pytest.approx(0.0028, rel=0.02)
    assert found["flat"]["off"] == 0


@pytest.mark.parametrize("count", [0, 3])
def test_too_few_points_say_nothing(count: int) -> None:
    assert flatness.measure("symmetry", plane(400)[:count]) is None
