"""The cfMesh volume fills and the poly-hexcore dual (ADR-0032).

What only a unit test can pin: the dictionary keys measured against the FoamWin build (patch types
through `renameBoundary`, walls-only layers, the joined surface), the step list each type runs,
the agent's allow-list for the new utilities and the OpenMP thread rule. The real meshes are in
`tests/engine/test_mesh_cfmesh_engine.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.mesh.basis import mesh_basis
from stokes.mesh.catalogue import MESHERS as CATALOGUE
from stokes.mesh.drivers import cfmesh, driver_for
from stokes.mesh.drivers.snappy import SnappySnapshot, Surface
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.model import CFMESH_MESHERS, MESHERS, SETTINGS_PATH, SURFACE_MESHERS

STL = "solid {n}\n  facet normal 0 0 1\n    outer loop\n      vertex 0 0 0\n      vertex 1 0 0\n      vertex 0 1 0\n    endloop\n  endfacet\nendsolid {n}\n"  # noqa: E501


def snap(mesher: str = "cfmesh", **over: Any) -> SnappySnapshot:
    """A cfMesh record with three surfaces, as `build_cfmesh_snapshot` would hand the driver."""
    surfaces = (
        Surface("inlet", "inlet.stl", "inlet", "patch", requested=0.005),
        Surface("outlet", "outlet.stl", "outlet", "patch", requested=0.005),
        Surface("walls", "walls.stl", "walls", "wall", requested=0.0025),
    )
    joined = "".join(STL.format(n=s.name) for s in surfaces)
    values: dict[str, Any] = {
        "mesher": mesher,
        "surfaces": surfaces,
        "stl_files": {cfmesh.SURFACE_FILE: joined},
        "base_size": 0.005,
        "background_min": (0.0, 0.0, 0.0),
        "background_max": (0.1, 0.1, 0.5),
        "domain_bbox": (0.0, 0.0, 0.0, 0.1, 0.1, 0.5),
        "layer_counts": {"walls": 0},
        "layer_settings": {"mode": "final_ratio", "expansion_ratio": 1.2},
        "domain": "part",
    }
    values.update(over)
    return SnappySnapshot(**values)


# ------------------------------------------------------------------ the catalogue and the model


def test_every_cfmesh_type_is_built_and_driven_by_the_cfmesh_driver() -> None:
    built = {spec.id for spec in CATALOGUE if spec.built}
    for mesher in CFMESH_MESHERS:
        assert mesher in MESHERS, "the settings enum must accept it"
        assert mesher in built, "the catalogue must offer it"
        assert mesher in SURFACE_MESHERS
        assert driver_for(mesher) is cfmesh


def test_the_catalogue_names_the_fluent_cell_families_and_how_cores_are_spent() -> None:
    by_id = {spec.id: spec.to_json() for spec in CATALOGUE}
    assert by_id["cfmesh_tet"]["output_structures"] == ["tetrahedral"]
    assert by_id["cfmesh_poly"]["output_structures"] == ["polyhedral"]
    assert by_id["polyhex"]["output_structures"] == ["poly-hexcore"]
    assert by_id["cfmesh"]["output_structures"] == ["hex-dominant"]
    assert by_id["snappy"]["parallel"] == "mpi"
    assert by_id["blockmesh"]["parallel"] == "serial"
    assert all(by_id[m]["parallel"] == "threads" for m in CFMESH_MESHERS)
    # The cfMesh fills mesh a part's own volume only, for now.
    assert all(by_id[m]["domains"] == ["part"] for m in CFMESH_MESHERS)
    assert by_id["gmsh"]["built"] is False and by_id["gmsh"]["why"]


# ------------------------------------------------------------------ the dictionary


def test_the_mesh_dict_names_the_joined_surface_the_sizes_and_the_patch_types() -> None:
    files = cfmesh.write(snap())
    text = files["system/meshDict"]
    assert set(files) == {"system/meshDict", "system/controlDict", cfmesh.SURFACE_FILE}
    assert 'surfaceFile     "constant/geometry/domain.stl";' in text
    assert "maxCellSize     0.005;" in text
    # **Only the surface finer than the base is refined**; the ports keep the base size.
    assert "localRefinement" in text and "cellSize        0.0025;" in text
    assert text.count("cellSize ") == 1
    # **The types are written**, because cfMesh leaves every patch `empty` otherwise (measured).
    assert "renameBoundary" in text
    assert "type            wall;" in text
    assert text.count("type            patch;") == 2
    # No layer requested -> no layer block.
    assert "boundaryLayers" not in text


def test_layers_go_on_the_walls_only_with_the_expansion_ratio() -> None:
    text = cfmesh.write(snap("polyhex", layer_counts={"walls": 3}))["system/meshDict"]
    block = text[text.index("boundaryLayers") :]
    assert "patchBoundaryLayers" in block
    assert "nLayers         3;" in block
    assert "thicknessRatio  1.2;" in block
    assert "inlet" not in block and "outlet" not in block
    # No global count: that would grow layers on the inlet and outlet too.
    assert block.index("nLayers") > block.index("walls")
    assert "maxFirstLayerThickness" not in block


def test_first_height_mode_caps_the_first_layer() -> None:
    settings = {"mode": "first_height", "expansion_ratio": 1.15, "first_layer_height": 0.0002}
    text = cfmesh.write(snap(layer_counts={"walls": 2}, layer_settings=settings))["system/meshDict"]
    assert "maxFirstLayerThickness 0.0002;" in text


def test_the_tet_type_refuses_layers_by_name() -> None:
    with pytest.raises(MeshInvalid) as caught:
        cfmesh.write(snap("cfmesh_tet", layer_counts={"walls": 3}))
    assert caught.value.details["code"] == "mesh.layers_unsupported"
    assert "poly-hexcore" in str(caught.value)
    # With no layers the tet type writes normally.
    assert "system/meshDict" in cfmesh.write(snap("cfmesh_tet"))


def test_an_absurd_layer_value_from_a_loaded_project_is_refused() -> None:
    with pytest.raises(MeshInvalid):
        cfmesh.write(
            snap(
                layer_counts={"walls": 3},
                layer_settings={"mode": "final_ratio", "expansion_ratio": 1e9},
            )
        )


def test_a_case_with_no_surface_is_refused() -> None:
    with pytest.raises(MeshInvalid):
        cfmesh.write(snap(stl_files={}))


# ------------------------------------------------------------------ the steps


@pytest.mark.parametrize(
    ("mesher", "names"),
    [
        ("cfmesh", ["cartesianMesh", "checkMesh"]),
        ("cfmesh_tet", ["tetMesh", "checkMesh"]),
        ("cfmesh_poly", ["pMesh", "checkMesh"]),
        ("polyhex", ["cartesianMesh", "polyDualMesh", "checkMesh"]),
    ],
)
def test_each_type_runs_its_utilities_then_checks(mesher: str, names: list[str]) -> None:
    steps = cfmesh.steps(snap(mesher))
    assert [s["name"] for s in steps] == names
    assert steps[-1]["parse"] == "checkMesh"
    # Serial by default: no thread count is sent.
    assert all("threads" not in s and "ranks" not in s for s in steps)


def test_cores_become_openmp_threads_on_the_cfmesh_steps_only() -> None:
    steps = {s["name"]: s for s in cfmesh.steps(snap("polyhex", cores=6))}
    assert steps["cartesianMesh"]["threads"] == 6
    assert "threads" not in steps["polyDualMesh"]
    assert "threads" not in steps["checkMesh"]
    assert all("ranks" not in s for s in steps.values())


def test_the_dual_gets_the_feature_angle_never_below_the_floor() -> None:
    dual = next(s for s in cfmesh.steps(snap("polyhex")) if s["name"] == "polyDualMesh")
    assert dual["argv"] == ["polyDualMesh", "70"]
    sharper = next(
        s for s in cfmesh.steps(snap("polyhex", feature_angle=85.5)) if s["name"] == "polyDualMesh"
    )
    assert sharper["argv"] == ["polyDualMesh", "85.5"]


def test_patch_types_and_cell_count() -> None:
    record = snap()
    assert cfmesh.patch_types(record) == {"inlet": "patch", "outlet": "patch", "walls": "wall"}
    assert cfmesh.cell_count(record) == 20 * 20 * 100


# ------------------------------------------------------------------ the basis


def test_each_type_has_its_own_basis_and_layers_change_it() -> None:
    bases = {m: mesh_basis(snap(m), cfmesh.DRIVER_VERSION, "org-14") for m in CFMESH_MESHERS}
    assert len(set(bases.values())) == len(CFMESH_MESHERS)
    layered = mesh_basis(snap(layer_counts={"walls": 3}), cfmesh.DRIVER_VERSION, "org-14")
    assert layered != bases["cfmesh"]


# ------------------------------------------------------------------ the snapshot


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return tmp_path


def test_a_box_or_enclosure_domain_is_refused_for_cfmesh(home: Path) -> None:
    from stokes.mesh.snapshot import snapshot_for

    session = Session()
    session.execute("project.new", name="demo")
    session.execute("mesh.configure", values={"domain": "box", "box": [0, 0, 0, 1, 1, 1]})
    session.execute("mesh.configure", mesher="cfmesh_poly")
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    with pytest.raises(MeshInvalid) as caught:
        snapshot_for(session, settings)
    assert caught.value.details["code"] == "mesh.domain_unsupported"
    assert "snappyHexMesh" in str(caught.value)


def test_a_real_part_gives_one_joined_surface_with_a_solid_per_tag(home: Path) -> None:
    from stokes.geometry.store import GeometryStore
    from stokes.mesh.snapshot import snapshot_for
    from tests.test_project_save_as_portability import box, write_brep

    source = write_brep(box(10.0, 20.0, 30.0), home / "part.brep")
    session = Session()
    session.execute("project.new", name="demo")
    part_id = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part_id, timeout=180), "the import did not finish"
    session.execute("mesh.configure", values={"domain": "part", "domain_part": part_id})
    session.execute("mesh.configure", mesher="polyhex")
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None

    record = snapshot_for(session, settings)
    assert record.mesher == "polyhex"
    assert list(record.stl_files) == [cfmesh.SURFACE_FILE]
    joined = record.stl_files[cfmesh.SURFACE_FILE]
    for surface in record.surfaces:
        assert f"solid {surface.name}" in joined
    files = driver_for("polyhex").write(record)
    assert cfmesh.SURFACE_FILE in files and "system/meshDict" in files


# ------------------------------------------------------------------ the agent boundary


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from stokes_agent import jobs
    from stokes_agent.engine_env import EngineEnv

    engine = EngineEnv(
        bashrc=str(tmp_path / "bashrc"),
        env={"PATH": str(tmp_path)},
        project="OpenFOAM",
        version="14",
        flavour="org",
        seconds=0.0,
    )
    monkeypatch.setattr(jobs, "resolve_utility", lambda env, name: f"/bin/{name}")
    return jobs.JobStore(tmp_path / "jobs"), engine


@pytest.mark.parametrize("utility", ["cartesianMesh", "tetMesh", "pMesh", "polyDualMesh"])
def test_the_new_utilities_are_allowed_and_their_meshes_trusted(utility: str) -> None:
    from stokes_agent import jobs

    assert utility in jobs.ALLOWED_UTILITIES
    assert utility in jobs.MESHERS


def test_the_dual_takes_one_angle_and_nothing_else(store: Any) -> None:
    from stokes_agent.jobs import JobBadParams, Step

    jobstore, engine = store
    jobstore._check_argv(Step("dual", ("polyDualMesh", "70")), engine)
    jobstore._check_argv(Step("dual", ("polyDualMesh", "85.5")), engine)
    for argv in (
        ("polyDualMesh", "70", "80"),
        ("polyDualMesh", "181"),
        ("polyDualMesh", "-case", "/tmp"),
        ("polyDualMesh", "1e2"),
        ("cartesianMesh", "70"),
    ):
        with pytest.raises(JobBadParams):
            jobstore._check_argv(Step("dual", argv), engine)


def test_threads_only_for_cfmesh_and_set_for_that_process(store: Any) -> None:
    from stokes_agent.jobs import JobBadParams, Step, _step_env

    jobstore, engine = store
    jobstore._check_argv(Step("vol", ("tetMesh",), threads=8), engine)
    with pytest.raises(JobBadParams):
        jobstore._check_argv(Step("bm", ("blockMesh",), threads=2), engine)
    with pytest.raises(JobBadParams):
        jobstore._check_argv(Step("vol", ("tetMesh",), threads=0), engine)
    env = {"PATH": "x"}
    assert _step_env(env, Step("vol", ("pMesh",), threads=4))["OMP_NUM_THREADS"] == "4"
    # **One thread is set explicitly**, so the runtime cannot take every core.
    assert _step_env(env, Step("vol", ("pMesh",)))["OMP_NUM_THREADS"] == "1"
    assert "OMP_NUM_THREADS" not in _step_env(env, Step("bm", ("blockMesh",)))
    assert env == {"PATH": "x"}, "the captured environment is not modified"


# ------------------------------------------------------------------ the symmetry planes (the elbow)

#: A bent quad: its four corners are not on one plane.
BENT = "solid {n}\n  facet normal 0 0 1\n    outer loop\n      vertex 0 0 0\n      vertex 1 0 0\n      vertex 0 1 0\n    endloop\n  endfacet\n  facet normal 0 0 1\n    outer loop\n      vertex 1 0 0\n      vertex 1 1 0.3\n      vertex 0 1 0\n    endloop\n  endfacet\nendsolid {n}\n"  # noqa: E501


def symmetric(mesher: str, solid: str = STL) -> SnappySnapshot:
    """`snap` with a fourth surface, a symmetry face, drawn from ``solid``."""
    record = snap(mesher)
    sym = Surface("mirror", "mirror.stl", "mirror", "symmetry", requested=0.005)
    joined = record.stl_files[cfmesh.SURFACE_FILE] + solid.format(n="mirror")
    return snap(
        mesher,
        surfaces=(*record.surfaces, sym),
        stl_files={cfmesh.SURFACE_FILE: joined},
    )


def test_the_polyhedral_type_flattens_a_planar_symmetry_face_and_sets_it_back() -> None:
    record = symmetric("cfmesh_poly")
    assert cfmesh.flattened_planes(record) == ("mirror",)
    steps = cfmesh.steps(record)
    assert [s["name"] for s in steps] == [
        "pMesh",
        cfmesh.FLATTEN_STEP,
        cfmesh.RESTORE_STEP,
        "checkMesh",
    ]
    restore = steps[2]["argv"]
    assert restore == [
        "foamDictionary",
        "constant/polyMesh/boundary",
        "-entry",
        "entry0/mirror/type",
        "-set",
        "symmetry",
    ]
    # Meshed as `symmetryPlane` (the only type the flattening acts on) ...
    text = cfmesh.write(record)["system/meshDict"]
    assert "type            symmetryPlane;" in text
    # ... and reported as `symmetry`, which is what the boundary file says when the run ends.
    assert cfmesh.patch_types(record)["mirror"] == "symmetry"


def test_a_curved_symmetry_face_or_another_type_is_left_alone() -> None:
    assert cfmesh.flattened_planes(symmetric("cfmesh_poly", BENT)) == ()
    for mesher in ("cfmesh", "cfmesh_tet", "polyhex"):
        record = symmetric(mesher)
        assert cfmesh.flattened_planes(record) == ()
        assert cfmesh.FLATTEN_STEP not in [s["name"] for s in cfmesh.steps(record)]
        assert "symmetryPlane" not in cfmesh.write(record)["system/meshDict"]


def test_the_tet_and_hex_dominant_types_snap_a_planar_symmetry_face_with_the_agent() -> None:
    """**`improveSymmetryPlanes` inverts cells on both**, so the agent's own `flatten` action moves
    the points as far as the cells allow (`stokes_agent.flatten`), before `checkMesh`."""
    for mesher, volume in (("cfmesh_tet", "tetMesh"), ("cfmesh", "cartesianMesh")):
        record = symmetric(mesher)
        assert cfmesh.snapped_planes(record) == ("mirror",)
        steps = cfmesh.steps(record)
        assert [s["name"] for s in steps] == [volume, cfmesh.SNAP_STEP, "checkMesh"]
        assert steps[1]["argv"] == [] and steps[1]["action"] == {"op": "flatten", "patch": "mirror"}
        # Not the polyhedral route: no `symmetryPlane`, no improveSymmetryPlanes, no foamDictionary.
        assert "symmetryPlane" not in cfmesh.write(record)["system/meshDict"]
        assert cfmesh.flattened_planes(record) == ()
    poly = symmetric("cfmesh_poly")
    assert cfmesh.snapped_planes(poly) == ()
    assert cfmesh.SNAP_STEP not in [s["name"] for s in cfmesh.steps(poly)]


def test_a_curved_symmetry_face_or_the_dual_type_is_not_snapped() -> None:
    assert cfmesh.snapped_planes(symmetric("cfmesh_tet", BENT)) == ()
    assert cfmesh.snapped_planes(symmetric("polyhex")) == ()
    assert cfmesh.SNAP_STEP not in [s["name"] for s in cfmesh.steps(symmetric("polyhex"))]


def test_the_snap_step_passes_the_agent_boundary_and_nothing_wider_does(store: Any) -> None:
    """The action is allowed for a mesh job, with one patch name and nothing else; the file actions
    stay for imported cases."""
    from stokes_agent.jobs import JobBadParams, Step

    jobstore, engine = store

    def check(action: dict[str, Any], profile: str | None = None) -> None:
        jobstore._check_steps([Step("flattenSymmetry", (), action=action)], engine, profile)

    check({"op": "flatten", "patch": "mirror"})
    for step in cfmesh.steps(symmetric("cfmesh_tet")):
        action = step.get("action")
        if action is not None:
            check(action)
    for bad in (
        {"op": "flatten"},
        {"op": "flatten", "patch": ""},
        {"op": "flatten", "patch": "../boundary"},
        {"op": "flatten", "patch": "a b"},
        {"op": "flatten", "patch": "x" * 65},
        {"op": "flatten", "patch": 7},
        {"op": "flatten", "patch": "mirror", "target": "0"},
        {"op": "flatten", "patch": "mirror", "source": "0"},
        {"op": "copy", "source": "0.orig", "target": "0"},  # a file action: external jobs only
        {"op": "remove", "target": "0"},
    ):
        with pytest.raises(JobBadParams):
            check(bad)
    with pytest.raises(JobBadParams):  # an action runs no program
        jobstore._check_steps(
            [Step("flattenSymmetry", ("checkMesh",), action={"op": "flatten", "patch": "mirror"})],
            engine,
        )


def test_every_utility_the_driver_runs_is_probed_on_the_install() -> None:
    """**The agent resolves a program only from its probe** (`engine_env.UTILITIES`): the first
    real polyhedral run stopped with "improveSymmetryPlanes is not on the engine's PATH" while
    every unit test passed, because the allow-list had it and the probe did not."""
    from stokes_agent.engine_env import UTILITIES

    for mesher in CFMESH_MESHERS:
        for step in cfmesh.steps(symmetric(mesher)):
            if step.get("action") is None:  # an action runs no program
                assert step["argv"][0] in UTILITIES, step["argv"][0]


def test_the_symmetry_steps_pass_the_agent_boundary_and_nothing_wider_does(store: Any) -> None:
    from stokes_agent import jobs
    from stokes_agent.jobs import JobBadParams, Step

    jobstore, engine = store
    for step in cfmesh.steps(symmetric("cfmesh_poly")):
        jobstore._check_argv(Step(step["name"], tuple(step["argv"])), engine)
    assert {"improveSymmetryPlanes", "foamDictionary"} <= jobs.MESHERS
    base = ("foamDictionary", "constant/polyMesh/boundary", "-entry", "entry0/mirror/type")
    for argv in (
        (*base, "-set", "wall"),
        (
            "foamDictionary",
            "system/controlDict",
            "-entry",
            "entry0/mirror/type",
            "-set",
            "symmetry",
        ),
        ("foamDictionary", "constant/polyMesh/boundary", "-entry", "libs", "-set", "symmetry"),
        (
            "foamDictionary",
            "constant/polyMesh/boundary",
            "-entry",
            "entry0/a/b/type",
            "-set",
            "symmetry",
        ),
        (*base, "-set", "symmetry", "-case", "/tmp"),
        (*base, "-remove"),
        ("improveSymmetryPlanes", "-case", "/tmp"),
    ):
        with pytest.raises(JobBadParams):
            jobstore._check_argv(Step("x", argv), engine)


# ------------------------------------------------------------------ the estimate and the precheck


def test_the_estimate_scales_by_the_measured_cell_type_factor() -> None:
    from stokes.mesh.estimate import CELL_FACTORS, estimate

    base = estimate(snap("snappy"))
    for mesher, factor in CELL_FACTORS.items():
        scaled = estimate(snap(mesher))
        assert scaled["background_cells"] == base["background_cells"]
        assert scaled["high"] == pytest.approx(base["high"] * factor, rel=0.01, abs=2)
        assert "this cell type" in scaled["method"]
    assert "this cell type" not in base["method"]


def test_the_precheck_blocks_tet_layers_before_generate(home: Path) -> None:
    from stokes.geometry.store import GeometryStore
    from tests.test_project_save_as_portability import box, write_brep

    source = write_brep(box(10.0, 20.0, 30.0), home / "part.brep")
    session = Session()
    session.execute("project.new", name="demo")
    part_id = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part_id, timeout=180), "the import did not finish"
    session.execute(
        "mesh.configure", mesher="cfmesh_tet", values={"domain_part": part_id, "layers": 3}
    )
    codes = [f["code"] for f in session.execute("mesh.precheck")["findings"]]
    assert "mesh.layers_unsupported" in codes
    session.execute("mesh.configure", values={"layers": 0})
    assert "mesh.layers_unsupported" not in [
        f["code"] for f in session.execute("mesh.precheck")["findings"]
    ]
    assert session.execute("mesh.estimate")["ok"] is True
