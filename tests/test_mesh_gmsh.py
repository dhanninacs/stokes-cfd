"""The Gmsh driver (ADR-0043): the script, the steps, the agent's boundary for both programs.

What only a unit test can pin: the `.geo` text measured against Gmsh 4.15.2 (the per-tag count,
the merge at shared edges, the layers' sign and heights, the planes that close the openings), the
refusals made before anything runs, the closed grammar the agent holds the script to, and the
argv each program may be given. The real meshes are in `tests/engine/test_mesh_gmsh_engine.py`.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.mesh.catalogue import MESHERS as CATALOGUE
from stokes.mesh.catalogue import mesher_needs
from stokes.mesh.drivers import driver_for, gmsh
from stokes.mesh.drivers.snappy import SnappySnapshot, Surface
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.model import GMSH_MESHERS, MESHERS, SETTINGS_PATH, SURFACE_MESHERS

#: The duct of the engine test: 20 x 10 x 10 mm, x from the inlet to the outlet.
LENGTH, WIDTH, HEIGHT = 0.02, 0.01, 0.01


def _corner(x: float, y: float, z: float) -> tuple[float, float, float]:
    return (x * LENGTH, y * WIDTH, z * HEIGHT)


def _quad(*corners: tuple[float, float, float]) -> list[tuple[tuple[float, float, float], ...]]:
    a, b, c, d = corners
    return [(a, b, c), (a, c, d)]


#: Each tag's triangles, ordered so the normals face OUT of the duct.
FACES: dict[str, list[tuple[tuple[float, float, float], ...]]] = {
    "inlet": _quad(_corner(0, 0, 0), _corner(0, 0, 1), _corner(0, 1, 1), _corner(0, 1, 0)),
    "outlet": _quad(_corner(1, 0, 0), _corner(1, 1, 0), _corner(1, 1, 1), _corner(1, 0, 1)),
    "walls": _quad(_corner(0, 0, 0), _corner(1, 0, 0), _corner(1, 0, 1), _corner(0, 0, 1))
    + _quad(_corner(0, 1, 0), _corner(0, 1, 1), _corner(1, 1, 1), _corner(1, 1, 0))
    + _quad(_corner(0, 0, 0), _corner(0, 1, 0), _corner(1, 1, 0), _corner(1, 0, 0))
    + _quad(_corner(0, 0, 1), _corner(1, 0, 1), _corner(1, 1, 1), _corner(0, 1, 1)),
}


def stl(name: str, triangles: list[tuple[tuple[float, float, float], ...]]) -> str:
    """An ASCII STL as the snapshot builder stages it."""
    lines = [f"solid {name}"]
    for triangle in triangles:
        lines += ["  facet normal 0 0 0", "    outer loop"]
        lines += [f"      vertex {x:.9g} {y:.9g} {z:.9g}" for x, y, z in triangle]
        lines += ["    endloop", "  endfacet"]
    lines.append(f"endsolid {name}")
    return "\n".join(lines) + "\n"


def flipped(triangles: list[tuple[tuple[float, float, float], ...]]) -> list[Any]:
    return [(a, c, b) for a, b, c in triangles]


def snap(layers: int = 0, *, flip: bool = False, **over: Any) -> SnappySnapshot:
    """A Gmsh record of the duct, as `build_gmsh_snapshot` would hand the driver."""
    surfaces = (
        Surface("inlet", "inlet.stl", "inlet", "patch", requested=0.0015),
        Surface("outlet", "outlet.stl", "outlet", "patch", requested=0.0015),
        Surface("walls", "walls.stl", "walls", "wall", requested=0.0015),
    )
    files = {
        f"constant/geometry/{name}.stl": stl(name, flipped(tris) if flip else tris)
        for name, tris in FACES.items()
    }
    values: dict[str, Any] = {
        "mesher": "gmsh",
        "surfaces": surfaces,
        "stl_files": files,
        "base_size": 0.0015,
        "background_min": (0.0, 0.0, 0.0),
        "background_max": (LENGTH, WIDTH, HEIGHT),
        "domain_bbox": (0.0, 0.0, 0.0, LENGTH, WIDTH, HEIGHT),
        "layer_counts": {"walls": layers},
        "layer_settings": {
            "mode": "final_ratio",
            "expansion_ratio": 1.2,
            "final_ratio": 0.3,
            "min_thickness_ratio": 0.1,
            "feature_angle": 100.0,
        },
        "feature_angle": 30.0,
        "domain": "part",
    }
    values.update(over)
    return SnappySnapshot(**values)


def script(record: SnappySnapshot) -> str:
    return gmsh.write(record)[gmsh.GEO_FILE]


# ------------------------------------------------------------------ the catalogue and the model


def test_gmsh_is_built_and_driven_by_the_gmsh_driver() -> None:
    by_id = {spec.id: spec for spec in CATALOGUE}
    spec = by_id["gmsh"]
    assert spec.built is True
    assert spec.domains == ("part",)
    assert spec.output_structures == ("tetrahedral",)
    assert spec.parallel == "serial"
    assert "Gmsh" in spec.label and spec.why
    assert "gmsh" in MESHERS and "gmsh" in GMSH_MESHERS and "gmsh" in SURFACE_MESHERS
    assert driver_for("gmsh") is gmsh


def test_the_host_is_asked_for_both_programs() -> None:
    """The Mesh card lists Gmsh as "not available" naming the program a host lacks; the names
    must be ones the agent's probe reports."""
    from stokes_agent.engine_env import UTILITIES

    assert mesher_needs("gmsh") == ("gmsh", "gmshToFoam")
    assert set(mesher_needs("gmsh")) <= set(UTILITIES)
    assert {spec.id: spec.to_json() for spec in CATALOGUE}["gmsh"]["needs"] == [
        "gmsh",
        "gmshToFoam",
    ]


# ------------------------------------------------------------------ the script


def test_the_files_are_the_script_the_control_dict_and_the_tag_surfaces() -> None:
    files = gmsh.write(snap())
    assert set(files) == {
        "system/domain.geo",
        "system/controlDict",
        "constant/geometry/inlet.stl",
        "constant/geometry/outlet.stl",
        "constant/geometry/walls.stl",
    }


def test_each_tag_is_counted_alone_then_all_are_joined_and_classified() -> None:
    text = script(snap())
    angle = f"{math.radians(30.0):.12g}"
    counting = (
        'Merge "../constant/geometry/inlet.stl";\n'
        f"ClassifySurfaces{{{angle}, 1, 1, Pi}};\n"
        "t() = Surface{:};\n"
        "n_0 = #t();\n"
        "Delete Model;\n"
    )
    assert counting in text
    joined = (
        'Merge "../constant/geometry/inlet.stl";\n'
        'Merge "../constant/geometry/outlet.stl";\n'
        'Merge "../constant/geometry/walls.stl";\n'
        "Coherence Mesh;\n"
        # A curve on every tag boundary first, so classification keeps the tags apart.
        "CreateTopology;\n"
        f"ClassifySurfaces{{{angle}, 1, 1, Pi}};\n"
        "CreateGeometry;\n"
        "s() = Surface{:};\n"
        "If (#s() != n_0 + n_1 + n_2)\n"
    )
    assert joined in text
    assert "tag_0() = s({k:k + n_0 - 1});\nk += n_0;\n" in text
    assert "tag_2() = s({k:k + n_2 - 1});\n" in text
    assert "Mesh.MshFileVersion = 2.2;" in text
    assert "Mesh.OptimizeNetgen = 1;" in text


def test_without_layers_the_volume_is_the_whole_closed_surface() -> None:
    text = script(snap())
    assert "Surface Loop(sl) = {s()};\ncore = newv;\nVolume(core) = {sl};\n" in text
    assert "Extrude" not in text and "Plane Surface" not in text
    assert 'Physical Surface("inlet") = {tag_0()};' in text
    assert 'Physical Surface("outlet") = {tag_1()};' in text
    assert 'Physical Surface("walls") = {tag_2()};' in text
    assert text.endswith(
        'Physical Volume("domain") = {core};\nMesh 3;\nSave "../constant/domain.msh";\n'
    )
    assert "Mesh.MeshSizeMax = 0.0015;" in text
    # No surface asks for less than the base size, so no field is written.
    assert "Field[" not in text


def test_a_finer_wall_gets_a_distance_threshold_field() -> None:
    record = snap(
        surfaces=(
            Surface("inlet", "inlet.stl", "inlet", "patch", requested=0.0015),
            Surface("outlet", "outlet.stl", "outlet", "patch", requested=0.0015),
            Surface("walls", "walls.stl", "walls", "wall", requested=0.0005),
        )
    )
    text = script(record)
    for line in (
        "Mesh.MeshSizeFromPoints = 0;",
        "Mesh.MeshSizeExtendFromBoundary = 0;",
        "Field[1] = Distance;",
        "Field[1].SurfacesList = {tag_2()};",
        "Field[2] = Threshold;",
        "Field[2].InField = 1;",
        "Field[2].SizeMin = 0.0005;",
        "Field[2].SizeMax = 0.0015;",
        "Field[2].DistMin = 0;",
        "Field[2].DistMax = 0.0045;",
        "Field[3] = Min;",
        "Field[3].FieldsList = {2};",
        "Background Field = 3;",
    ):
        assert line in text, line
    # The duct's walls span 24.5 mm, so 2 x 24.5 / 0.5 = 98 samples per direction.
    assert "Field[1].Sampling = 98;" in text


def test_layers_grow_inwards_on_the_walls_with_the_final_ratio_heights() -> None:
    text = script(snap(3))
    # final ratio 0.3 of the 1.5 mm wall cell: 0.45 mm last, 0.375 and 0.3125 mm below it.
    expected = "-0.0003125, -0.0006875, -0.0011375"
    assert (
        f"out() = Extrude{{Surface{{walls()}}; Layers{{{{1, 1, 1}}, {{{expected}}}}}; Recombine;}};"
        in text
    )
    assert "walls() = {tag_2()};" in text
    assert 'Printf("stokes layers %g", 3);' in text
    # The openings are rebuilt as planes inside the layers; their patch includes the layers' sides.
    assert text.count("Plane Surface(ps) = {cl};") == 2
    assert 'Physical Surface("inlet") = {side_0()};' in text
    assert 'Physical Surface("outlet") = {side_1()};' in text
    assert 'Physical Surface("walls") = {tag_2()};' in text
    assert "Surface Loop(sl) = {tops(), caps()};" in text
    assert 'Physical Volume("domain") = {core, cells()};' in text


def test_the_layer_sign_follows_the_surface_orientation() -> None:
    """**Measured**: Gmsh extrudes along the STL normals, so a surface staged facing inwards needs
    positive heights to keep the layers inside the part."""
    outward = script(snap(2))
    inward = script(snap(2, flip=True))
    assert "{-" in outward.split("Layers{", 1)[1].split("}", 2)[1]
    assert "Layers{{1, 1}, {0." in inward


def test_first_height_mode_starts_from_the_given_height() -> None:
    settings = {"mode": "first_height", "expansion_ratio": 1.5, "first_layer_height": 0.0001}
    text = script(snap(3, layer_settings=settings))
    assert "Layers{{1, 1, 1}, {-0.0001, -0.00025, -0.000475}}" in text


def test_every_line_the_driver_writes_passes_the_agents_grammar() -> None:
    """The agent refuses any statement its closed grammar does not hold; the driver must write
    nothing else, with and without layers, fields and symmetry patches."""
    from stokes_agent.geo_lint import lint_geo

    finer = (
        Surface("inlet", "inlet.stl", "inlet", "symmetry", requested=0.0005),
        Surface("outlet", "outlet.stl", "outlet", "patch", requested=0.001),
        Surface("walls", "walls.stl", "walls", "wall", requested=0.0005),
    )
    first = {"mode": "first_height", "expansion_ratio": 1.1, "first_layer_height": 5e-5}
    for record in (
        snap(),
        snap(3),
        snap(5, surfaces=finer),
        snap(2, flip=True, layer_settings=first),
    ):
        assert lint_geo(script(record)) == []


# ------------------------------------------------------------------ the refusals


def test_walls_with_different_layer_counts_are_refused() -> None:
    surfaces = (
        Surface("inlet", "inlet.stl", "inlet", "patch"),
        Surface("outlet", "outlet.stl", "outlet", "wall"),
        Surface("walls", "walls.stl", "walls", "wall"),
    )
    with pytest.raises(MeshInvalid) as caught:
        gmsh.write(snap(surfaces=surfaces, layer_counts={"walls": 3, "outlet": 0}))
    assert caught.value.details["code"] == "mesh.layers_unsupported"
    assert "same layer count" in str(caught.value)
    assert "outlet 0" in str(caught.value) and "walls 3" in str(caught.value)


def test_a_bent_opening_is_refused_under_layers_only() -> None:
    bent = [
        (_corner(0, 0, 0), _corner(0, 0, 1), _corner(0, 1, 1)),
        (_corner(0, 0, 0), _corner(0, 1, 1), (0.002, WIDTH, 0.0)),
    ]
    record = snap(3)
    files = dict(record.stl_files)
    files["constant/geometry/inlet.stl"] = stl("inlet", bent)
    with pytest.raises(MeshInvalid) as caught:
        gmsh.write(snap(3, stl_files=files))
    assert caught.value.details["code"] == "mesh.layers_unsupported"
    assert "'inlet' is not planar" in str(caught.value)
    # Without layers a bent opening is meshed like any surface.
    assert gmsh.GEO_FILE in gmsh.write(snap(0, stl_files=files))


@pytest.mark.parametrize(
    ("over", "words"),
    [
        ({"surfaces": ()}, "no surface"),
        ({"base_size": 0.0}, "positive length"),
        ({"base_size": float("nan")}, "positive length"),
        ({"mesher": "cfmesh"}, "does not build"),
        ({"stl_files": {}}, "was not staged"),
        (
            {"surfaces": (Surface("in let", "in let.stl", "in let", "patch"),)},
            "cannot be a Gmsh patch name",
        ),
    ],
)
def test_what_gmsh_cannot_mesh_is_refused_by_name(over: dict[str, Any], words: str) -> None:
    with pytest.raises(MeshInvalid) as caught:
        gmsh.write(snap(**over))
    assert words in str(caught.value)


def test_an_absurd_layer_value_from_a_loaded_project_is_refused() -> None:
    with pytest.raises(MeshInvalid):
        gmsh.write(snap(3, layer_settings={"mode": "final_ratio", "expansion_ratio": 1e9}))


# ------------------------------------------------------------------ the steps


def test_the_steps_run_gmsh_convert_type_the_walls_and_check() -> None:
    steps = gmsh.steps(snap(3))
    assert [s["name"] for s in steps] == ["gmsh", "gmshToFoam", "patchType", "checkMesh"]
    assert steps[0]["argv"] == ["gmsh", "-nopopup", "system/domain.geo", "-"]
    assert steps[0]["parse"] == "gmsh"
    assert steps[1]["argv"] == ["gmshToFoam", "constant/domain.msh"]
    assert steps[2]["argv"] == [
        "foamDictionary",
        "constant/polyMesh/boundary",
        "-entry",
        "entry0/walls/type",
        "-set",
        "wall",
    ]
    assert steps[-1]["parse"] == "checkMesh"
    # Serial: the cores setting is checked, never spent.
    assert all("threads" not in s and "ranks" not in s for s in gmsh.steps(snap(cores=8)))


def test_a_symmetry_patch_gets_its_own_type_step() -> None:
    surfaces = (
        Surface("inlet", "inlet.stl", "inlet", "symmetry"),
        Surface("outlet", "outlet.stl", "outlet", "patch"),
        Surface("walls", "walls.stl", "walls", "wall"),
    )
    steps = gmsh.steps(snap(surfaces=surfaces))
    assert [s["name"] for s in steps] == [
        "gmsh",
        "gmshToFoam",
        "patchType",
        "patchType2",
        "checkMesh",
    ]
    assert steps[2]["argv"][-3:] == ["entry0/inlet/type", "-set", "symmetry"]
    assert steps[3]["argv"][-3:] == ["entry0/walls/type", "-set", "wall"]


def test_patch_types_cell_count_and_basis() -> None:
    from stokes.mesh.basis import mesh_basis

    record = snap()
    assert gmsh.patch_types(record) == {"inlet": "patch", "outlet": "patch", "walls": "wall"}
    # 14 x 7 x 7 base cubes of the duct's box, about 5.4 tetrahedra each.
    assert gmsh.cell_count(record) == int(14 * 7 * 7 * gmsh.TETS_PER_CUBE)
    plain = mesh_basis(record, gmsh.DRIVER_VERSION, "org-14")
    assert mesh_basis(snap(3), gmsh.DRIVER_VERSION, "org-14") != plain


def test_explain_failure_names_the_layer_fold_only_when_layers_grew() -> None:
    errors = [
        "PLC Error:  A segment and a facet intersect at point",
        "Invalid boundary mesh (segment-facet intersection) on surface 91",
    ]
    layered = gmsh.explain_failure({"errors": errors, "layers": 3})
    assert layered is not None
    assert layered.startswith("Gmsh could not fill the mesh: PLC Error")
    assert "fold into each other at a sharp inside corner" in layered
    plain = gmsh.explain_failure({"errors": errors, "layers": 0})
    assert plain == "Gmsh failed: PLC Error:  A segment and a facet intersect at point"
    assert gmsh.explain_failure({"errors": [], "layers": 3}) is None


def test_the_service_replaces_the_bare_exit_code_with_gmshs_words() -> None:
    from stokes.mesh.service import _gmsh_error

    error = {"code": "agent.step_failed", "message": "gmsh exited 1"}
    status = {"reports": {"gmsh": {"errors": ["Invalid boundary mesh (overlapping facets)"]}}}
    assert _gmsh_error(status, error) == {
        "code": "agent.step_failed",
        "message": "Gmsh failed: Invalid boundary mesh (overlapping facets)",
    }
    assert _gmsh_error({"reports": {}}, error) is error
    assert _gmsh_error(status, None) is None


# ------------------------------------------------------------------ the snapshot


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return tmp_path


def test_a_box_domain_is_refused_for_gmsh(home: Path) -> None:
    from stokes.mesh.snapshot import snapshot_for

    session = Session()
    session.execute("project.new", name="demo")
    session.execute("mesh.configure", values={"domain": "box", "box": [0, 0, 0, 1, 1, 1]})
    session.execute("mesh.configure", mesher="gmsh")
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    with pytest.raises(MeshInvalid) as caught:
        snapshot_for(session, settings)
    assert caught.value.details["code"] == "mesh.domain_unsupported"


def test_a_real_part_keeps_one_staged_surface_per_tag(home: Path) -> None:
    from stokes.geometry.store import GeometryStore
    from stokes.mesh.snapshot import snapshot_for
    from stokes_agent.geo_lint import lint_geo
    from tests.test_project_save_as_portability import box, write_brep

    source = write_brep(box(10.0, 20.0, 30.0), home / "part.brep")
    session = Session()
    session.execute("project.new", name="demo")
    part_id = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part_id, timeout=180), "the import did not finish"
    session.execute("mesh.configure", mesher="gmsh", values={"domain_part": part_id, "layers": 2})
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    record = snapshot_for(session, settings)
    assert record.mesher == "gmsh"
    assert sorted(record.stl_files) == sorted(
        f"constant/geometry/{s.file}" for s in record.surfaces
    )
    files = driver_for("gmsh").write(record)
    assert lint_geo(files[gmsh.GEO_FILE]) == []
    assert "Extrude{Surface{walls()}" in files[gmsh.GEO_FILE]


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


def test_both_programs_are_allowed_and_the_conversion_is_trusted() -> None:
    from stokes_agent import jobs
    from stokes_agent.parsers import PARSERS

    assert {"gmsh", "gmshToFoam"} <= jobs.ALLOWED_UTILITIES
    assert "gmshToFoam" in jobs.MESHERS
    assert "gmsh" not in jobs.THREADED_UTILITIES
    assert "gmsh" in PARSERS


def test_the_driver_steps_pass_the_agent_boundary(store: Any) -> None:
    from stokes_agent.jobs import Step

    jobstore, engine = store
    surfaces = (
        Surface("inlet", "inlet.stl", "inlet", "symmetry"),
        Surface("outlet", "outlet.stl", "outlet", "patch"),
        Surface("walls", "walls.stl", "walls", "wall"),
    )
    for step in gmsh.steps(snap(surfaces=surfaces)):
        jobstore._check_argv(Step(step["name"], tuple(step["argv"])), engine)
    jobstore._check_steps(
        [
            Step(s["name"], tuple(s["argv"]), timeout_s=s["timeout_s"], parse=s.get("parse"))
            for s in gmsh.steps(snap())
        ],
        engine,
    )


@pytest.mark.parametrize(
    "argv",
    [
        ("gmsh", "system/other.geo", "-"),
        ("gmsh", "/etc/passwd"),
        ("gmsh", "system/domain.geo", "-", "-o", "x.msh"),
        ("gmsh", "system/domain.geo", "system/domain.geo"),
        ("gmsh", "system/domain.geo", "-nt", "4"),
        ("gmsh", "-case", "/tmp"),
        ("gmshToFoam", "constant/other.msh"),
        ("gmshToFoam", "constant/domain.msh", "-case", "/tmp"),
        ("gmshToFoam", "constant/domain.msh", "-keepOrientation"),
    ],
)
def test_nothing_wider_passes(store: Any, argv: tuple[str, ...]) -> None:
    from stokes_agent.jobs import JobBadParams, Step

    jobstore, engine = store
    with pytest.raises(JobBadParams):
        jobstore._check_argv(Step("x", argv), engine)


def test_gmsh_takes_no_threads(store: Any) -> None:
    from stokes_agent.jobs import JobBadParams, Step

    jobstore, engine = store
    with pytest.raises(JobBadParams):
        jobstore._check_argv(
            Step("gmsh", ("gmsh", "-nopopup", "system/domain.geo", "-"), threads=4), engine
        )


def test_every_program_the_driver_runs_is_probed_on_the_install() -> None:
    from stokes_agent.engine_env import UTILITIES

    for step in gmsh.steps(snap(3)):
        assert step["argv"][0] in UTILITIES, step["argv"][0]


# ------------------------------------------------------------------ a tessellation kept as it is


def _subdivided(triangles: list[Any], times: int) -> list[Any]:
    """Each triangle split into four, ``times`` over; a shared edge splits alike, so it conforms."""
    for _ in range(times):
        out = []
        for a, b, c in triangles:
            ab, bc, ca = (
                tuple((p + q) / 2 for p, q in zip(x, y, strict=True))
                for x, y in ((a, b), (b, c), (c, a))
            )
            out += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        triangles = out
    return triangles


def fine(layers: int = 0, **over: Any) -> SnappySnapshot:
    """The duct tessellated finer than its 1.5 mm cells (edges about 1.25 mm)."""
    files = {
        f"constant/geometry/{name}.stl": stl(name, _subdivided(tris, 4))
        for name, tris in FACES.items()
    }
    return snap(layers, stl_files=files, **over)


def test_a_tessellation_finer_than_the_sizes_is_kept_and_only_split() -> None:
    from stokes_agent.geo_lint import lint_geo

    assert gmsh.uses_tessellation(fine()) is True
    assert gmsh.uses_tessellation(snap()) is False
    for record in (fine(), fine(3)):
        text = script(record)
        assert "ClassifySurfaces" not in text and "CreateGeometry" not in text
        assert 'Merge "../constant/geometry/inlet.stl";\nCreateTopology;\nt() = Surface{:};' in text
        assert "Coherence Mesh;\nCreateTopology;\ns() = Surface{:};" in text
        # The triangles are the wall cells: no size field.
        assert "Field[" not in text
        assert lint_geo(text) == []
    assert "Extrude{Surface{walls()}" in script(fine(3))


def test_layers_on_a_kept_tessellation_with_knife_folds_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The count itself is tested in `test_geometry_surface_repair`; here, what the driver does."""
    from stokes.geometry import surface_repair

    assert gmsh.GEO_FILE in gmsh.write(fine(3))  # the clean duct has no fold: layers allowed
    monkeypatch.setattr(surface_repair, "knife_folds", lambda texts: 8152)
    with pytest.raises(MeshInvalid) as caught:
        gmsh.write(fine(3))
    assert caught.value.details["code"] == "mesh.layers_unsupported"
    assert "8152 of their edges fold back sharper than 25 degrees" in str(caught.value)
    # Without layers, or on a remeshed surface, the folds are no reason to refuse.
    assert gmsh.GEO_FILE in gmsh.write(fine(0))
    assert gmsh.GEO_FILE in gmsh.write(snap(3))
