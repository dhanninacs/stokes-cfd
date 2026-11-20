"""The Gmsh driver (ADR-0007 decision 5) and the agent's checks of what it stages.

What only a unit test can pin: the script's shape (and that every line of it passes the agent's
closed grammar, so the two cannot drift), the steps and their pass through the agent's argv
boundary, the snapshot builder on a real imported part, and the lint's refusals. Real meshes of
four parts were measured by hand and are recorded in `stokes.mesh.drivers.gmsh`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.mesh.catalogue import MESHERS as CATALOGUE
from stokes.mesh.drivers import driver_for, gmsh
from stokes.mesh.drivers.snappy import SnappySnapshot, Surface
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.model import MESHERS, SURFACE_MESHERS

STL = "solid {n}\n  facet normal 0 0 1\n    outer loop\n      vertex 0 0 0\n      vertex 1 0 0\n      vertex 0 1 0\n    endloop\n  endfacet\nendsolid {n}\n"  # noqa: E501

#: A 0.1 x 0.1 x 0.5 m box's six faces: inlet (x=0), outlet (x=0.5), four walls.
BOXES = [
    [0.0, 0.0, 0.0, 0.0, 0.1, 0.1],
    [0.5, 0.0, 0.0, 0.5, 0.1, 0.1],
    [0.0, 0.0, 0.0, 0.5, 0.0, 0.1],
    [0.0, 0.1, 0.0, 0.5, 0.1, 0.1],
    [0.0, 0.0, 0.0, 0.5, 0.1, 0.0],
    [0.0, 0.0, 0.1, 0.5, 0.1, 0.1],
]


def snap(**over: Any) -> SnappySnapshot:
    """A Gmsh record as `build_gmsh_snapshot` hands the driver: three patches on a box."""
    surfaces = (
        Surface("inlet", "inlet.stl", "inlet", "patch", requested=0.005),
        Surface("outlet", "outlet.stl", "outlet", "patch", requested=0.005),
        Surface("walls", "walls.stl", "walls", "wall", requested=0.0025),
    )
    values: dict[str, Any] = {
        "mesher": "gmsh",
        "surfaces": surfaces,
        "stl_files": {gmsh.SURFACE_FILE: "".join(STL.format(n=s.name) for s in surfaces)},
        "base_size": 0.005,
        "background_min": (0.0, 0.0, 0.0),
        "background_max": (0.5, 0.1, 0.1),
        "domain_bbox": (0.0, 0.0, 0.0, 0.5, 0.1, 0.1),
        "layer_counts": {},
        "domain": "part",
        "gmsh": {
            "groups": {"inlet": [1], "outlet": [2], "walls": [3, 4, 5, 6]},
            "boxes": BOXES,
            "diagonal": 0.52,
            "part_spec": {"part": "n_12345678", "name": "box", "basis": "abc"},
        },
    }
    values.update(over)
    return SnappySnapshot(**values)


# ------------------------------------------------------------------ the catalogue and registry


def test_gmsh_is_built_driven_by_its_driver_and_grows_no_layers() -> None:
    spec = next(s for s in CATALOGUE if s.id == "gmsh")
    assert spec.built and spec.domains == ("part",) and spec.output_structures == ("tetrahedral",)
    assert spec.layers is False and "Gmsh cannot grow boundary layers" in spec.layers_why
    assert "gmsh" in MESHERS and "gmsh" in SURFACE_MESHERS
    assert driver_for("gmsh") is gmsh


# ------------------------------------------------------------------ the script


def test_the_script_checks_the_numbering_then_groups_and_sizes() -> None:
    text = gmsh.geo(snap())
    assert 'v() = ShapeFromFile("domain.step");' in text
    assert "Geometry.OCCImportLabels = 0;" in text  # the plain STEP reader
    assert "Geometry.OCCBoundsUseStl = 1;" in text  # tight boxes (a torus's quick box is loose)
    assert 'Geometry.OCCTargetUnit = "M";' in text  # the export is in metres
    assert "If (#s() != 6 || #v() != 1)" in text
    assert text.count("bb() = BoundingBox Surface{") == 6
    assert 'Error("surface 2 is not where Stokes measured face 1");' in text
    assert 'Physical Surface("walls") = {3, 4, 5, 6};' in text
    assert 'Physical Volume("fluid") = v();' in text
    # walls asked for 2.5 mm under a 5 mm base: sized on its points; the 5 mm ones are not.
    assert "MeshSize{ PointsOf{ Surface{3, 4, 5, 6}; } } = 0.0025;" in text
    assert "PointsOf{ Surface{1}" not in text
    assert "Mesh.MeshSizeMax = 0.005;" in text and "Mesh.MshFileVersion = 2.2;" in text


def test_every_line_the_driver_writes_passes_the_agents_grammar(tmp_path: Path) -> None:
    """**The round trip that keeps the two halves honest**: a new line shape in the driver that
    the agent's closed grammar does not know fails here, not on a user's first Gmsh job."""
    from stokes_agent.lint_gmsh import lint_geo

    for record in (
        snap(),
        snap(gmsh={**snap().gmsh, "boxes": [[-0.01, -2e-05, 0, 1, 1e-07, 3], *BOXES[1:]]}),
    ):
        path = tmp_path / "mesh.geo"
        path.write_text(gmsh.geo(record), encoding="ascii")
        assert lint_geo("constant/geometry/mesh.geo", path) == []


def test_write_stages_the_script_and_the_surface_but_not_the_step() -> None:
    files = gmsh.write(snap())
    assert set(files) == {gmsh.GEO_FILE, "system/controlDict", gmsh.SURFACE_FILE}
    assert gmsh.geometry_exports(snap()) == [(gmsh.STEP_FILE, snap().gmsh["part_spec"])]


def test_layers_or_a_missing_record_are_refused() -> None:
    with pytest.raises(MeshInvalid, match="cannot grow boundary layers"):
        gmsh.write(snap(layer_counts={"walls": 3}))
    with pytest.raises(MeshInvalid, match="missing its face groups"):
        gmsh.write(snap(gmsh={}))


# ------------------------------------------------------------------ the steps


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


def test_the_steps_mesh_convert_retype_and_check() -> None:
    with_symmetry = snap(
        surfaces=(*snap().surfaces, Surface("mirror", "m.stl", "mirror", "symmetry")),
    )
    steps = gmsh.steps(with_symmetry)
    assert [s["name"] for s in steps] == [
        "gmsh",
        "gmshToFoam",
        "patchType",
        "patchType2",
        "checkMesh",
    ]
    assert steps[0]["argv"] == ["gmsh", gmsh.GEO_FILE, "-3", "-o", gmsh.MSH_FILE, "-nt", "1"]
    assert steps[2]["argv"][-3:] == ["entry0/mirror/type", "-set", "symmetry"]
    assert steps[3]["argv"][-3:] == ["entry0/walls/type", "-set", "wall"]
    assert gmsh.steps(snap(cores=8))[0]["argv"][-1] == "8"


def test_the_steps_pass_the_agent_boundary_and_are_probed(store: Any) -> None:
    from stokes_agent import jobs
    from stokes_agent.engine_env import UTILITIES
    from stokes_agent.jobs import JobBadParams, Step

    jobstore, engine = store
    for step in gmsh.steps(snap(cores=4)):
        jobstore._check_argv(Step(step["name"], tuple(step["argv"])), engine)
        assert step["argv"][0] in UTILITIES
    assert "gmshToFoam" in jobs.MESHERS  # its polyMesh is the agent's own
    for argv in (
        ("gmsh", "constant/geometry/mesh.geo", "-3", "-o", "/tmp/x.msh"),
        ("gmsh", "system/evil.geo", "-3"),
        ("gmsh", "constant/geometry/mesh.geo", "-nt", "0"),
        ("gmsh", "constant/geometry/mesh.geo", "-parse_and_exit"),
        ("gmshToFoam", "constant/geometry/other.msh"),
        ("gmshToFoam", "constant/geometry/mesh.msh", "-case", "/tmp"),
    ):
        with pytest.raises(JobBadParams):
            jobstore._check_argv(Step("x", argv), engine)


# ------------------------------------------------------------------ the snapshot


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return tmp_path


def _box_project(home: Path) -> tuple[Session, str]:
    from stokes.geometry.store import GeometryStore
    from tests.geometry_fixtures import box, write_brep

    source = write_brep(box(10.0, 20.0, 30.0), home / "part.brep")
    session = Session()
    session.execute("project.new", name="demo")
    part_id = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part_id, timeout=180), "the import did not finish"
    session.execute("mesh.configure", mesher="gmsh", values={"domain_part": part_id})
    return session, part_id


def test_the_snapshot_holds_every_face_box_and_the_export_spec(home: Path) -> None:
    from stokes.mesh.snapshot import snapshot_for

    session, part_id = _box_project(home)
    settings = session.require_project().find("/mesh/settings")
    record = snapshot_for(session, settings).gmsh
    assert len(record["boxes"]) == 6
    # No tags: every face is `unassigned`, numbered 1..6 for Gmsh.
    assert record["groups"] == {"unassigned": [1, 2, 3, 4, 5, 6]}
    assert record["part_spec"]["part"] == part_id
    assert record["diagonal"] == pytest.approx((0.01**2 + 0.02**2 + 0.03**2) ** 0.5, rel=1e-3)
    assert "gmsh.layers_unsupported" not in str(session.execute("mesh.precheck")["findings"])


def test_a_box_domain_or_2d_is_refused_for_gmsh(home: Path) -> None:
    from stokes.mesh.snapshot import snapshot_for

    session, _part = _box_project(home)
    settings = session.require_project().find("/mesh/settings")
    session.execute("mesh.configure", values={"two_d": "planar", "plane": "xy"})
    with pytest.raises(MeshInvalid, match="Gmsh meshes in 3D only"):
        snapshot_for(session, settings)
    session.execute("mesh.configure", values={"two_d": "off", "domain": "box"})
    session.execute("mesh.configure", values={"box": [0, 0, 0, 1, 1, 1]})
    with pytest.raises(MeshInvalid, match="the 'box' domain needs snappyHexMesh"):
        snapshot_for(session, settings)


# ------------------------------------------------------------------ the agent's lint


def _geo_lint(tmp_path: Path, text: str) -> list[str]:
    from stokes_agent.lint_gmsh import lint_geo

    path = tmp_path / "mesh.geo"
    path.write_bytes(text.encode("utf-8"))
    return [f.why for f in lint_geo("constant/geometry/mesh.geo", path)]


@pytest.mark.parametrize(
    "line",
    [
        'SystemCall "calc.exe";',
        'NonBlockingSystemCall "rm -rf /";',
        'Include "C:/Windows/win.ini";',
        'Merge "../../../secret.step";',
        'Save "C:/evil.msh";',
        'v() = ShapeFromFile("C:/elsewhere.step");',
        "Mesh.SaveAll = 1;",
        'Physical Surface("walls") = {1}; SystemCall "x";',
        "Plugin(Crack).Run;",
    ],
)
def test_the_grammar_refuses_anything_the_driver_does_not_write(tmp_path: Path, line: str) -> None:
    good = gmsh.geo(snap())
    why = _geo_lint(tmp_path, good + line + "\n")
    assert any("outside the Gmsh grammar" in w for w in why), why


def test_the_script_must_keep_the_plain_reader_and_be_ascii(tmp_path: Path) -> None:
    good = gmsh.geo(snap())
    why = _geo_lint(tmp_path, good.replace("Geometry.OCCImportLabels = 0;\n", ""))
    assert any("plain STEP reader" in w for w in why)
    assert _geo_lint(tmp_path, good + "// caf\u00e9\n") == ["a Gmsh script must be printable ASCII"]


def test_the_step_lint_reads_data_as_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from stokes_agent import lint_gmsh
    from stokes_agent.lint import lint_file

    rel = "constant/geometry/domain.step"
    path = tmp_path / "domain.step"
    good = b"ISO-10303-21;\nHEADER;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nEND-ISO-10303-21;\n"
    path.write_bytes(good)
    assert lint_file(rel, path) == []  # `#1=` is STEP, not an OpenFOAM directive
    path.write_bytes(b"solid x\nendsolid x\n")
    assert "not an ISO 10303-21 STEP file" in str(lint_file(rel, path))
    path.write_bytes(good.replace(b"END-ISO-10303-21;", b""))
    assert "does not end with" in str(lint_file(rel, path))
    path.write_bytes(good + b"\x00")
    assert "printable ASCII" in str(lint_file(rel, path))
    monkeypatch.setattr(lint_gmsh, "MAX_STEP_BYTES", 10)
    path.write_bytes(good)
    assert "over the 10 limit" in str(lint_file(rel, path))
    # Any other path under constant/geometry is still a dictionary to the main lint.
    other = tmp_path / "other.step"
    other.write_bytes(good)
    assert lint_file("constant/geometry/other.step", other) != []
