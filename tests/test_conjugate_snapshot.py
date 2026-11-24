"""The multi-region snapshot builder on two channels through a block, with neither OCP nor Rust.

The regression tests of the conjugate coupling defect (ADR-0042, 2026-11-24) that CI runs: the
bodies are gridded in numpy (`tests.conjugate_support`) and handed to the geometry store, and
`build_conjugate_snapshot` stages them as it would a real project. The channels' walls and the
block's tunnels are tessellated differently, so a boundary triangle overlapping a zone surface is
found by its area on the surface, not by a triangle centre the zone repeats.

Each test fails on `stokes/mesh/conjugate.py` before the fix (commit 5c5300b~1): it wrote the
collar of every rim into both surfaces (about 59 mm2 of `unassigned` on the zones here), took
wrongly wound triangles and the walls over a pocket for outer skin (holes in the zone), and the
block's own collar stayed on the fluids' zones when its tunnels were meshed finer than the
channels (that last also on 5c5300b itself, which matched centres only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.mesh.on_surface import off_surface, on_surface
from tests import conjugate_support as cs

#: Sampled points of an `unassigned` triangle within this of a zone surface count as on it, mm:
#: 0.02 of the 0.5 mm cell.
GAP = 0.01


def _stage(tmp_path: Path, **options: Any) -> tuple[dict[str, Any], Any]:  # Any: arrays, snapshot
    """The staged surfaces, in mm, by file name, and the snapshot."""
    from stokes.geometry.store import GeometryStore
    from stokes.mesh.conjugate import build_conjugate_snapshot

    session = cs.two_channel_session(tmp_path, **options)
    try:
        project = session.require_project()
        settings = next(n for n in project.root.walk() if n.type == "mesh_settings")
        snap = build_conjugate_snapshot(session, settings)
    finally:
        GeometryStore.of(session).close()
    files = {
        path.rsplit("/", 1)[-1]: cs.stl_triangles(text) / cs.MM
        for path, text in snap.stl_files.items()
    }
    return files, snap


def _check_coupled(files: dict[str, Any], snap: Any) -> None:
    """Every zone is its channel's whole wall in the block, and no boundary lies on a zone."""
    assert {"hot_zone.stl", "cold_zone.stl", "unassigned.stl"} <= set(files)
    assert {zone.name for zone in snap.zones} == {"hot", "cold", "block"}
    assert set(snap.interfaces) == {("block", "cold"), ("block", "hot")}
    zones = np.concatenate([files["hot_zone.stl"], files["cold_zone.stl"]])
    overlap = cs.area_on(files["unassigned.stl"], zones, GAP)
    assert overlap < 1e-3 * cs.INTERFACE_AREA, f"{overlap:.2f} mm2 of unassigned on a zone"
    for name in ("hot_zone.stl", "cold_zone.stl"):
        assert cs.area(files[name]) == pytest.approx(cs.INTERFACE_AREA, rel=1e-4), name


def test_two_channels_through_a_block_are_coupled_on_their_whole_walls(tmp_path: Path) -> None:
    files, snap = _stage(tmp_path)
    _check_coupled(files, snap)
    # The outer skin: the block's six sides less the four mouths, and each channel's two 2 mm
    # stubs and end caps (40 mm2 a channel).
    assert cs.area(files["unassigned.stl"]) == pytest.approx(744.0 + 80.0, rel=1e-4)


def test_a_block_meshed_finer_than_its_channels_leaves_no_baffle_on_them(tmp_path: Path) -> None:
    """The block's tunnels at 0.25 mm make it a collar at each mouth; that collar lies on the
    channels' zones (it shares no triangle with them), and must not be staged as boundary."""
    files, snap = _stage(tmp_path, tunnel=(80, 8))
    _check_coupled(files, snap)


def test_wrongly_wound_triangles_on_a_shared_wall_are_coupled(tmp_path: Path) -> None:
    """Every seventh triangle of the hot wall, in the channel and in the block, wound inwards."""

    def every_seventh(corners: Any) -> Any:  # Any: numpy arrays
        mask = np.zeros(len(corners), dtype=bool)
        mask[np.flatnonzero(cs.on_hot_wall(corners))[::7]] = True
        return mask

    files, snap = _stage(tmp_path, flip_hot=every_seventh, flip_block=every_seventh)
    hot = np.concatenate(cs.channel(*cs.CHANNELS["hot"]))
    flipped = hot[every_seventh(hot)]
    held = cs.area_on(flipped, files["hot_zone.stl"], GAP)
    assert held == pytest.approx(cs.area(flipped), rel=1e-6), "a wrongly wound triangle is a hole"
    _check_coupled(files, snap)


def test_an_island_the_probe_leaves_over_a_pocket_is_coupled(tmp_path: Path) -> None:
    """A 0.4 x 0.5 mm pocket in the block under the hot channel: the channel's wall over it probes
    no body, an island of two triangles, and is folded back into the interface."""
    cell = (29, 1)
    files, snap = _stage(tmp_path, pocket=cell)
    x0, x1, z0, z1 = cs.pocket_box(cell)
    hot = np.concatenate(cs.channel(*cs.CHANNELS["hot"]))
    centre = hot.mean(axis=1)
    over = (
        cs.on_hot_wall(hot)
        & np.isclose(centre[:, 1], cs.CHANNELS["hot"][0])
        & (centre[:, 0] > x0)
        & (centre[:, 0] < x1)
        & (centre[:, 2] > z0)
        & (centre[:, 2] < z1)
    )
    assert over.sum() == 2
    held = cs.area_on(hot[over], files["hot_zone.stl"], GAP)
    assert held == pytest.approx(cs.area(hot[over]), rel=1e-6), "the island is a hole in the zone"
    _check_coupled(files, snap)


# ------------------------------------------------------------------- a second solid

#: The closer measure of the second-solid tests, mm: the bodies' walls are flat and coplanar
#: to the staged STL's float32 noise (under 1e-6 mm here), so a sample counts as on a surface only
#: when it is; at `GAP` a sliver cut along a zone's edge counts though it lies beside it.
FINE = 1e-4

#: The channels' walls cells along x (7 across): each puts a triangle across the block's face at
#: a mouth, its centre inside the block for some and outside it for others.
STRADDLING = (66, 78, 80, 90)


@pytest.mark.parametrize("along", STRADDLING)
@pytest.mark.parametrize(
    ("second", "second_first"),
    [(None, False), ("cube", False), ("plate", False), ("plate", True)],
    ids=["one-solid", "cube-last", "plate-last", "plate-first"],
)
def test_a_second_solid_leaves_no_hole_in_the_skin_and_no_boundary_on_a_zone(
    tmp_path: Path, along: int, second: str | None, second_first: bool
) -> None:
    """With a second solid made after the block, the block is a zone surface too, and a channel's
    wall triangle crossing the block's face at a mouth is half on it. 27cc1af dropped it whole
    when its centre was on the zone (a hole of 0.3 to 1.1 mm2 in the skin outside each mouth) and
    kept it whole when it was not (up to 0.8 mm2 of boundary on the zone); it is cut now."""
    files, snap = _stage(tmp_path, grid=(along, 7), second=second, second_first=second_first)
    unassigned = files["unassigned.stl"]
    zones = np.concatenate([v for k, v in files.items() if k.endswith("_zone.stl")])
    if second is not None and not second_first:
        assert "block_zone.stl" in files
    skin = cs.outer_skin(second)
    uncovered = cs.area(skin) - cs.area_on(skin, unassigned, FINE)
    assert uncovered < 1e-6, f"{uncovered:.4f} mm2 of the outer skin is a hole"
    overlap = cs.area_on(unassigned, zones, FINE)
    assert overlap < 1e-6, f"{overlap:.4f} mm2 of unassigned on a zone"
    # The shared walls are coupled whole: what a fluid's zone leaves out (its collar) the block's
    # zone holds when there is one, and the fluid's collar (boundary) when there is not.
    tunnels = cs.block()[6:]
    for k, name in enumerate(("hot", "cold")):
        shared = np.concatenate(tunnels[4 * k : 4 * k + 4])
        assert cs.area(shared) == pytest.approx(cs.INTERFACE_AREA)
        held = cs.area_on(shared, zones, FINE)
        if "block_zone.stl" in files:
            assert held == pytest.approx(cs.INTERFACE_AREA, rel=1e-6), name
        else:
            closed = held + cs.area_on(shared, unassigned, FINE)
            assert closed == pytest.approx(cs.INTERFACE_AREA, rel=1e-6), name
    assert ("block", "cold") in snap.interfaces and ("block", "hot") in snap.interfaces


# ---------------------------------------------------------------------------- on_surface


def test_on_surface_finds_a_copy_tessellated_differently_and_nothing_across_or_apart() -> None:
    plane = cs.rectangle(2, 0.0, 1.0, (0.0, 4.0, 8), (0.0, 2.0, 4))
    copy = cs.rectangle(2, 0.0, -1.0, (0.0, 4.0, 7), (0.0, 2.0, 5))
    assert on_surface(copy, plane, 1e-3).all()
    apart = copy + np.array([0.0, 0.0, 0.01])
    assert not on_surface(apart, plane, 1e-3).any()
    beside = copy + np.array([4.5, 0.0, 0.0])
    assert not on_surface(beside, plane, 1e-3).any()
    # A wall standing on the plane's edge, its first row of triangles a hair above it.
    across = cs.rectangle(1, 0.0, -1.0, (0.0, 4.0, 8), (0.0, 1.0, 50))
    assert not on_surface(across, plane, 1e-3).any()
    assert on_surface(np.zeros((0, 3, 3)), plane, 1e-3).shape == (0,)


def test_off_surface_cuts_a_triangle_partly_on_the_surface_and_keeps_its_winding() -> None:
    plane = cs.rectangle(2, 0.0, 1.0, (0.0, 4.0, 8), (0.0, 2.0, 4))
    # Two triangles across the plane's edge at x = 4, one each way round, and one clear of it.
    across = np.array(
        [
            [[3.7, 0.2, 0.0], [4.3, 0.2, 0.0], [4.0, 0.9, 0.0]],
            [[3.7, 1.2, 0.0], [4.0, 1.9, 0.0], [4.3, 1.2, 0.0]],
            [[5.0, 0.0, 0.0], [6.0, 0.0, 0.0], [5.0, 1.0, 0.0]],
        ]
    )
    rest = off_surface(across, plane, 1e-3)
    assert cs.area(rest) == pytest.approx(0.5 * cs.area(across[:2]) + 0.5, rel=1e-9)
    assert (rest.reshape(-1, 3)[:, 0] >= 4.0 - 1e-12).all()
    assert cs.area_on(rest, plane, 1e-6) == pytest.approx(0.0, abs=1e-9)
    # Each piece faces as the triangle it came from did.
    up = np.cross(rest[:, 1] - rest[:, 0], rest[:, 2] - rest[:, 0])[:, 2]
    source = np.cross(across[:, 1] - across[:, 0], across[:, 2] - across[:, 0])[:, 2]
    centre = rest.mean(axis=1)
    came = np.where(centre[:, 0] >= 4.9, 2, np.where(centre[:, 1] < 1.0, 0, 1))
    assert (np.sign(up) == np.sign(source[came])).all()
    # Wholly on it (over several of its triangles): nothing left; apart or across: kept whole.
    over = np.array([[[0.2, 0.2, 0.0], [3.0, 0.3, 0.0], [1.0, 1.8, 0.0]]])
    assert len(off_surface(over, plane, 1e-3)) == 0
    lifted = over + np.array([0.0, 0.0, 0.01])
    assert np.array_equal(off_surface(lifted, plane, 1e-3), lifted)
    wall = cs.rectangle(1, 0.0, -1.0, (0.0, 4.0, 8), (0.0, 1.0, 50))
    assert np.array_equal(off_surface(wall, plane, 1e-3), wall)
