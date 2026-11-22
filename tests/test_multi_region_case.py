"""The multi-region case writer and the mesh driver's zones (ADR-0042), without an engine."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from stokes.compile.errors import CompileInvalid, CompileUnsupported
from stokes.compile.of14 import multi_region
from stokes.compile.snapshot import (
    MULTI_REGION_ENGINE,
    PatchCondition,
    RegionCase,
    SolveSnapshot,
)
from stokes.mesh.conjugate import classify, collar, columns_of, region_word
from stokes.mesh.drivers import snappy
from stokes.mesh.drivers.snappy import SnappySnapshot, Surface, Zone


def fluid(name: str, inlet: str, outlet: str, temperature: float) -> SolveSnapshot:
    """One fluid region's own case: an inlet, an outlet and the wall it shares with `wall`."""
    return SolveSnapshot(
        settings_id="n_1",
        engine="of14.fluid",
        turbulence="laminar",
        iterations=100,
        write_interval=100,
        residual_target=1e-4,
        nu=1e-6,
        rho=998.0,
        energy=True,
        mu=1e-3,
        cp=4182.0,
        kappa=0.6,
        patches=(
            PatchCondition(
                inlet,
                "patch",
                "velocity_inlet",
                velocity=(0.05, 0.0, 0.0),
                thermal="fixedValue",
                temperature=temperature,
            ),
            PatchCondition(
                outlet,
                "patch",
                "pressure_outlet",
                pressure=0.0,
                thermal="inletOutlet",
                temperature=temperature,
                centroid=(0.0, 0.0, 0.0),
            ),
            PatchCondition(f"{name}_to_wall", "wall", "wall", thermal="coupled"),
        ),
        monitor_patches=(inlet, outlet),
    )


def case() -> SolveSnapshot:
    hot = fluid("hot", "hot_in", "hot_out", 350.0)
    cold = fluid("cold", "cold_in", "cold_out", 300.0)
    regions = (
        RegionCase("hot", "fluid", fluid=hot, neighbours=("wall",)),
        RegionCase("cold", "fluid", fluid=cold, neighbours=("wall",)),
        RegionCase(
            "wall",
            "solid",
            rho=2700.0,
            cp=900.0,
            kappa=200.0,
            T_init=300.0,
            patches=("cold_in", "cold_out", "hot_in", "hot_out", "unassigned"),
            neighbours=("cold", "hot"),
        ),
    )
    return replace(
        hot,
        engine=MULTI_REGION_ENGINE,
        regions=regions,
        region_order=("hot", "cold", "wall"),
        monitor_patches=("hot_in", "hot_out", "cold_in", "cold_out"),
    )


def test_each_region_gets_its_own_folders_and_one_control_dict_names_them() -> None:
    files = multi_region.write(case())
    for region in ("hot", "cold"):
        for path in ("0/{r}/U", "0/{r}/T", "0/{r}/p_rgh", "constant/{r}/physicalProperties"):
            assert path.format(r=region) in files, path
        assert f"system/{region}/fvSolution" in files and f"system/{region}/fvSchemes" in files
        assert f"system/{region}/controlDict" not in files
    assert {"0/wall/T", "constant/wall/physicalProperties", "system/wall/fvSolution"} <= set(files)
    assert "0/wall/U" not in files
    control = files["system/controlDict"]
    assert "regionSolvers" in control
    for line in ("hot             fluid;", "cold            fluid;", "wall            solid;"):
        assert line in control
    assert "patchFlowRate(patch=cold_in, region=cold)" in control
    assert "solver " not in control  # no single solver module beside the regions'


def test_the_shared_walls_are_coupled_on_both_sides() -> None:
    files = multi_region.write(case())
    hot = files["0/hot/T"]
    assert "hot_to_wall" in hot and "coupledTemperature" in hot
    wall = files["0/wall/T"]
    assert "wall_to_hot" in wall and "wall_to_cold" in wall
    assert wall.count("coupledTemperature") == 2
    # The solid's outer faces are adiabatic, whatever patch they fell on.
    assert wall.count("zeroGradient") == 5
    properties = files["constant/wall/physicalProperties"]
    assert "heSolidThermo" in properties and "constIsoSolid" in properties
    assert "200" in properties and "2700" in properties and "900" in properties


def test_the_steps_solve_then_merge_in_the_splits_own_order() -> None:
    snap = case()
    run = multi_region.steps(snap, snap.region_order)
    assert [step["name"] for step in run] == ["solve", multi_region.MERGE_STEP]
    assert run[0]["argv"] == ["foamMultiRun"]
    assert run[1]["action"] == {"op": "merge_regions", "regions": ["hot", "cold", "wall"]}
    assert [step["name"] for step in multi_region.steps(snap)] == ["solve"]


def test_the_writer_refuses_what_is_not_a_multi_region_case() -> None:
    snap = case()
    with pytest.raises(CompileUnsupported):
        multi_region.write(replace(snap, engine="of14.fluid"))
    with pytest.raises(CompileInvalid):
        multi_region.write(replace(snap, regions=snap.regions[:1]))
    with pytest.raises(CompileUnsupported):
        multi_region.write(replace(snap, transient=True))
    bad = replace(snap.regions[2], kappa=0.0)
    with pytest.raises(CompileInvalid):
        multi_region.write(replace(snap, regions=(*snap.regions[:2], bad)))


# ---------------------------------------------------------------------------- the mesh


def box(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> tuple[Any, Any]:
    """A closed box as (positions, triangles), outward facing, each side cut into small squares so
    a triangle is smaller than the probes' offset matters for."""
    n = 6
    positions: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    for axis in range(3):
        u, v = [a for a in range(3) if a != axis]
        for side, outward in ((lo[axis], -1.0), (hi[axis], 1.0)):
            start = len(positions)
            for i in range(n + 1):
                for j in range(n + 1):
                    point = [0.0, 0.0, 0.0]
                    point[axis] = side
                    point[u] = lo[u] + (hi[u] - lo[u]) * i / n
                    point[v] = lo[v] + (hi[v] - lo[v]) * j / n
                    positions.append((point[0], point[1], point[2]))
            for i in range(n):
                for j in range(n):
                    a = start + i * (n + 1) + j
                    b, c, d = a + n + 1, a + 1, a + n + 2
                    first, second = (a, b, c), (c, b, d)
                    normal = np.cross(
                        np.subtract(positions[b], positions[a]),
                        np.subtract(positions[c], positions[a]),
                    )
                    if normal[axis] * outward < 0:
                        first, second = (a, c, b), (c, d, b)
                    triangles += [first, second]
    return np.array(positions), np.array(triangles)


def test_triangles_two_bodies_share_are_interfaces_and_the_rest_are_outer() -> None:
    # A fluid box and a solid box side by side, touching on the plane x = 1.
    left = box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    right = box((1.0, 0.0, 0.0), (2.0, 1.0, 1.0))
    bodies = [left, right]
    rows = [np.arange(len(left[1])), np.arange(len(right[1]))]
    verdicts = classify(bodies, rows, 0.1, columns_of(bodies, 0.1))
    for index, (positions, triangles) in enumerate(bodies):
        shared, neighbour = verdicts[index]
        on_plane = np.isclose(positions[triangles][:, :, 0], 1.0).all(axis=1)
        assert shared.tolist() == on_plane.tolist()
        assert set(neighbour[shared].tolist()) == {1 - index}
        assert set(neighbour[~shared].tolist()) == {-1}


def test_the_outer_skin_takes_a_collar_of_the_shared_wall_along_its_rim() -> None:
    # The shared side is 6 x 6 squares. Two rings of triangles in from its rim leaves the middle
    # 2 x 2 squares as the interface alone; everything nearer the rim is skin as well.
    left = box((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    right = box((1.0, 0.0, 0.0), (2.0, 1.0, 1.0))
    bodies = [left, right]
    positions, triangles = left
    rows = np.arange(len(triangles))
    shared, _neighbour = classify(
        bodies, [rows, np.arange(len(right[1]))], 0.1, columns_of(bodies, 0.1)
    )[0]
    ring = collar(positions, triangles, rows, shared)
    assert not ring[~shared].any()  # only interface triangles are ever a collar
    centre = positions[triangles].mean(axis=1)
    middle = shared & (np.abs(centre[:, 1] - 0.5) < 1 / 6) & (np.abs(centre[:, 2] - 0.5) < 1 / 6)
    assert int(middle.sum()) == 8 and not ring[middle].any()
    assert ring[shared & ~middle].all()


def test_a_region_name_becomes_a_word() -> None:
    assert region_word("hot side") == "hot_side"
    assert region_word("2nd") == "r_2nd"
    assert region_word("wall") == "wall"


def snapshot() -> SnappySnapshot:
    stl = "solid x\nendsolid x\n"
    return SnappySnapshot(
        surfaces=(Surface("inlet", "inlet.stl", region="inlet", patch_type="patch"),),
        background_min=(-1.0, -1.0, -1.0),
        background_max=(1.0, 1.0, 1.0),
        background_cells=(4, 4, 4),
        inside_point=(0.1, 0.1, 0.1),
        base_size=0.5,
        stl_files={"constant/geometry/inlet.stl": stl, "constant/geometry/hot_zone.stl": stl},
        zones=(
            Zone("hot", "fluid", "n_1", "hot_zone.stl", 0, 1, (0.1, 0.1, 0.1), ((0.2, 0.2, 0.2),)),
            Zone("wall", "solid", "n_2"),
        ),
        default_region="wall",
        interfaces=(("hot", "wall"),),
    )


def test_a_zone_is_an_open_surface_with_its_own_inside_point() -> None:
    files = snappy.write(snapshot())
    text = files["system/snappyHexMeshDict"]
    zone = text[text.index("hot_zone\n", text.index("refinementSurfaces")) :]
    for line in (
        "faceZone        hot_zone;",
        "cellZone        hot;",
        "mode            insidePoint;",
    ):
        assert line in zone
    assert "insidePoint     (0.1 0.1 0.1);" in zone
    # The zone surface has no feature edges: it is open, and its whole rim would be one.
    assert "hot_zone" not in files["system/surfaceFeaturesDict"]
    assert "regionToCell" in files["system/topoSetDict"]
    assert "(0.2 0.2 0.2)" in files["system/topoSetDict"]


def test_the_steps_rebuild_the_fluid_then_split_by_zone() -> None:
    names = [step["name"] for step in snappy.steps(snapshot())]
    assert names[-3:] == ["checkMesh", "topoSet", "splitMeshRegions"]
    split = snappy.steps(snapshot())[-1]
    assert split["argv"] == ["splitMeshRegions", "-cellZones", "all", "-defaultRegion", "wall"]
    assert split["parse"] == "splitMeshRegions"


def test_one_region_writes_the_case_it_always_did() -> None:
    plain = replace(snapshot(), zones=(), default_region="", interfaces=())
    files = snappy.write(plain)
    assert "system/topoSetDict" not in files and "system/fvSchemes" not in files
    assert [step["name"] for step in snappy.steps(plain)][-1] == "checkMesh"
    assert "faceZone" not in files["system/snappyHexMeshDict"]


def test_a_transient_run_keeps_every_written_time_and_a_steady_one_its_last_two() -> None:
    from stokes.compile.of14 import fluid as fluid_writer

    steady = fluid("hot", "hot_in", "hot_out", 350.0)
    steady = replace(steady, patches=steady.patches[:2])
    assert "purgeWrite      2;" in fluid_writer.write(steady)["system/controlDict"]
    moving = replace(steady, transient=True, delta_t=0.01, end_time=1.0)
    assert "purgeWrite      0;" in fluid_writer.write(moving)["system/controlDict"]
