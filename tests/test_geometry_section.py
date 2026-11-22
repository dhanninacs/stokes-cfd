"""The section of a modeller body, filled from its field (`stokes.geometry.section`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from stokes.geometry import section


def _area(positions: np.ndarray, triangles: np.ndarray) -> float:
    a = positions[triangles[:, 1]] - positions[triangles[:, 0]]
    b = positions[triangles[:, 2]] - positions[triangles[:, 0]]
    return float(0.5 * np.abs(np.cross(a, b)).sum())


def test_a_disc_fills_to_its_area_with_its_hole_left_open() -> None:
    u = np.linspace(-2.0, 2.0, 161)
    v = np.linspace(-2.0, 2.0, 161)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    radius = np.hypot(uu, vv)
    # An annulus: inside between radii 0.5 and 1.5.
    values = np.maximum(radius - 1.5, 0.5 - radius)
    points, triangles = section.fill(values, u, v)
    area = _area(np.column_stack([points, np.zeros(len(points))]), triangles)
    assert abs(area - np.pi * (1.5**2 - 0.5**2)) < 0.01 * np.pi * 2.0
    # No triangle of the fill sits in the hole.
    centres = points[triangles].mean(axis=1)
    assert np.hypot(centres[:, 0], centres[:, 1]).min() > 0.45


@dataclass
class _Sphere:
    """A body of radius 10 mm at the origin, as `stokes.implicit.features.Body` answers it."""

    def bounded(self) -> Any:
        return (-10.0, -10.0, -10.0), (10.0, 10.0, 10.0)

    def field(self) -> Any:
        from stokes.implicit.graph import X, Y, Z

        return (X * X + Y * Y + Z * Z).sqrt() - 10.0


def test_a_body_cut_through_its_middle_is_a_disc_in_the_plane_in_metres() -> None:
    body = _Sphere()
    positions, triangles = section.cut_body(body, "z", 0.0)
    assert positions.dtype == np.float32 and triangles.dtype == np.uint32
    assert np.allclose(positions[:, 2], 0.0)
    assert abs(_area(positions.astype(float), triangles.astype(int)) - np.pi * 0.01**2) < 2e-6
    missed, none = section.cut_body(body, "z", 0.02)
    assert missed.shape == (0, 3) and none.shape == (0, 3)
