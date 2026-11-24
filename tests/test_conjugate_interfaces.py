"""The multi-region mesh couples every wall two bodies share (ADR-0042; fixed 2026-11-24).

The defect: the snappy boundary surface `unassigned.stl` of a conjugate case held copies of the
fluid zone surfaces (on the mini plate exchanger 241,774 of its 304,501 triangles lay on the
`hot_zone`/`cold_zone` triangles), so snappyHexMesh made most of each fluid's wall an adiabatic
wall baffle instead of an interface. Its causes, each tested here: triangles wound the wrong way
probed their own body and were taken for outer skin; the islands they left each grew a collar;
and a collar triangle was written to both surfaces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.mesh.conjugate import _islands, _off_zones, classify, coincident, columns_of
from tests.conjugate_support import area_on
from tests.test_multi_region_case import box


def test_coincident_finds_every_point_within_the_tolerance_and_none_beyond() -> None:
    rng = np.random.default_rng(7)
    reference = rng.uniform(-0.01, 0.01, size=(2000, 3))
    near = reference[:500] + rng.uniform(-0.9e-7, 0.9e-7, size=(500, 3))
    far = reference[:500] + np.array([5e-6, 0.0, 0.0])
    assert coincident(near, reference, 1e-7).all()
    assert not coincident(far, reference, 1e-7).any()
    assert coincident(np.zeros((0, 3)), reference, 1e-7).shape == (0,)


def test_a_triangle_wound_the_wrong_way_on_a_shared_wall_is_still_an_interface() -> None:
    """Its probe went into its own body, which is no other body, so it was outer skin. The
    neighbour is meshed differently here, so the coincidence test cannot be what finds it."""
    left = box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    right = box((1.0, -0.5, -0.5), (2.0, 1.5, 1.5))
    positions, triangles = left
    triangles = triangles.copy()
    on_plane = np.isclose(positions[triangles][:, :, 0], 1.0).all(axis=1)
    wrong = np.flatnonzero(on_plane)[::5]
    triangles[wrong] = triangles[wrong][:, ::-1]
    bodies = [(positions, triangles), right]
    rows = [np.arange(len(triangles)), np.arange(len(right[1]))]
    shared, neighbour = classify(bodies, rows, 0.1, columns_of(bodies, 0.1))[0]
    assert shared.tolist() == on_plane.tolist()
    assert set(neighbour[wrong].tolist()) == {1}


def test_a_small_island_of_outer_skin_in_a_shared_wall_becomes_interface() -> None:
    positions, triangles = box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    rows = np.arange(len(triangles))
    centre = positions[triangles].mean(axis=1)
    on_plane = np.isclose(positions[triangles][:, :, 0], 1.0).all(axis=1)
    neighbour = np.where(on_plane, 1, -1)
    middle = on_plane & (np.abs(centre[:, 1] - 0.5) < 0.1) & (np.abs(centre[:, 2] - 0.5) < 0.1)
    assert 0 < int(middle.sum()) <= 4
    neighbour[middle] = -1
    _islands(positions, triangles, rows, neighbour, 0.1)
    assert (neighbour[on_plane] == 1).all()  # the island was taken back
    assert (neighbour[~on_plane] == -1).all()  # the real skin, five whole sides, was not


def test_a_skin_triangle_on_a_zone_surface_is_left_out_of_the_boundary() -> None:
    positions, triangles = box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    corners = positions[triangles]
    on_plane = np.isclose(corners[:, :, 0], 1.0).all(axis=1)
    kept = _off_zones([corners, corners[on_plane]], {0: [corners[on_plane]]}, 0.1)
    assert len(kept[0]) == int((~on_plane).sum())
    assert len(kept[1]) == 0


# ------------------------------------------------------------------- a real two-zone case


def _two_channels(tmp_path: Path) -> Any:  # Any: Session
    """Two straight water channels through an aluminium block, each 1 mm proud of it at both
    ends: the block is the housing less the channels (`keep_tools`), as the plate exchanger is."""
    pytest.importorskip("OCP")
    import stokes.geometry.model  # noqa: F401 - the operation node types
    from tests.test_implicit_operations import implicit, new_session

    session = new_session(tmp_path)

    def body(size: list[float], at: list[float]) -> str:
        info = implicit(session, "implicit_primitive", kind="box", size=size, position=at)
        return str(info["outputs"][0])

    hot = body([0.024, 0.002, 0.002], [0.0, -0.0025, 0.0])
    cold = body([0.024, 0.002, 0.002], [0.0, 0.0025, 0.0])
    housing = body([0.020, 0.010, 0.006], [0.0, 0.0, 0.0])
    info = implicit(
        session, "implicit_boolean", [housing], op="difference", tools=[hot, cold], keep_tools=True
    )
    block = str(info["outputs"][0])
    for part, name in ((hot, "hot"), (cold, "cold"), (block, "block")):
        session.execute("node.rename", node=part, name=name)
    session.execute("material.add_from_library", fluid="water_liquid", name="water")
    session.execute("material.add_from_library", fluid="aluminium", name="aluminium")
    session.execute("region.create", name="hot", kind="fluid", bodies=[hot], material="water")
    session.execute("region.create", name="cold", kind="fluid", bodies=[cold], material="water")
    session.execute(
        "region.create", name="block", kind="solid", bodies=[block], material="aluminium"
    )
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={"domain": "part", "domain_part": hot, "base_size": "0.0005 [m]"},
    )
    return session


def _triangles(text: str) -> Any:  # Any: numpy array
    found = [line.split()[1:] for line in text.splitlines() if line.strip().startswith("vertex")]
    return np.asarray(found, dtype="float64").reshape(-1, 3, 3)


def _area(corners: Any) -> float:  # Any: numpy array
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    return float(0.5 * np.linalg.norm(cross, axis=1).sum())


def test_no_unassigned_triangle_lies_on_a_zone_surface(tmp_path: Path) -> None:
    """The Rust mesher's bodies (skipped without it; `tests/test_conjugate_snapshot.py` is the
    same check on gridded bodies, which CI runs)."""
    from tests.test_implicit_sketch_faces import PROGRAM

    if PROGRAM is None or PROGRAM.suffix == ".py":
        pytest.skip("stokes-implicit-mesh is not built")
    from stokes.geometry.store import GeometryStore
    from stokes.mesh.conjugate import build_conjugate_snapshot

    session = _two_channels(tmp_path)
    try:
        project = session.require_project()
        settings = next(n for n in project.root.walk() if n.type == "mesh_settings")
        snap = build_conjugate_snapshot(session, settings)
        files = {path.rsplit("/", 1)[-1]: _triangles(text) for path, text in snap.stl_files.items()}
        assert {"hot_zone.stl", "cold_zone.stl", "unassigned.stl"} <= set(files)
        zones = np.concatenate([files["hot_zone.stl"], files["cold_zone.stl"]])
        unassigned = files["unassigned.stl"]
        # By area within 10 um of the zones, not by matching centres: a boundary surface
        # triangulated differently from a zone, lying on it, is as much a baffle as a copy. What
        # is left is the block's collar triangles that straddle the edge of a fluid's own collar
        # at a mouth, their centres off the zone (measured 0.28 mm2 of 336 mm2, 0.08 %).
        overlap = area_on(unassigned, zones, 1e-5)
        assert overlap < 5e-3 * _area(zones), f"{overlap * 1e6:.3f} mm2 of unassigned on a zone"
        # Each channel's four sides inside the block (2 mm x 20 mm each) are its interface, all
        # but a collar along the block's faces; its two 1 mm stubs and end caps are its skin. A
        # box's display mesh is coarse, so a few triangles straddle the block's face (+5 %).
        for name in ("hot_zone.stl", "cold_zone.stl"):
            assert 0.9 * 160e-6 <= _area(files[name]) <= 1.1 * 160e-6, name
        assert {zone.name for zone in snap.zones} == {"hot", "cold", "block"}
        assert ("block", "hot") in snap.interfaces and ("block", "cold") in snap.interfaces
    finally:
        GeometryStore.of(session).close()
