"""The driver dispatch and the snappy snapshot: what makes the Mesh button mesh the SHAPE.

**The defect these exist for.** `MeshService.generate` imported the blockMesh driver directly and
unconditionally, so `mesh_settings.mesher = "snappy"` was a value the tree showed, the result node
recorded, and the backend ignored: the owner pressed Generate on a pipe and got a bounding box,
with nothing anywhere saying why. The dispatch is now by the setting, and these tests fail if it
goes back to being hardcoded.

They also cover the snapshot the snappy driver needs, because a `MeshSnapshot` describes six box
sides and cannot express a surface-following case at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.geometry.store import GeometryStore
from stokes.mesh.drivers import DRIVER_MODULES, driver_for, meshers_with_drivers
from stokes.mesh.model import SETTINGS_PATH
from stokes.mesh.snapshot import MeshSnapshot, snapshot_for


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return tmp_path


# ------------------------------------------------------------------ the registry


def test_every_installed_mesher_has_a_driver_entry() -> None:
    """The catalogue's `built` and the registry must agree: a mesher the UI offers as built but
    with no driver would fail at Generate with "no driver", which is a worse experience than the
    catalogue saying it is not ready."""
    from stokes.mesh.catalogue import MESHERS

    built = {spec.id for spec in MESHERS if spec.built}
    assert built == set(meshers_with_drivers())


def test_the_dispatch_returns_the_module_for_the_setting() -> None:
    """The whole point: snappy gets the snappy driver, blockMesh gets blockMesh."""
    assert driver_for("snappy").__name__ == "stokes.mesh.drivers.snappy"
    assert driver_for("blockmesh").__name__ == "stokes.mesh.drivers.blockmesh"


def test_an_unknown_mesher_is_refused_by_name_listing_what_works() -> None:
    """`MeshNotBuilt`, so the code is the CLASS's. Putting `code=` in `details` instead leaves the
    error reporting `MeshInvalid`'s `mesh.invalid`, which a client would branch on wrongly - the
    mistake this test caught."""
    from stokes.mesh.errors import MeshNotBuilt

    with pytest.raises(MeshNotBuilt) as caught:
        driver_for("gmsh")
    assert caught.value.code == "mesh.not_built"
    assert "blockmesh" in str(caught.value)
    assert "snappy" in str(caught.value)


def test_both_drivers_expose_the_same_four_functions() -> None:
    """A driver is found by name, so a missing function is an AttributeError deep in a job. This is
    where the contract is stated."""
    for mesher in meshers_with_drivers():
        module = driver_for(mesher)
        # **`cell_count`, not `cells`.** blockMesh's own `cells` returns the per-axis trio for its
        # `hex` line; the shared contract is the single int, and a driver whose `cell_count`
        # returned a tuple broke 23 tests before this was separated.
        for name in ("write", "steps", "cell_count", "patch_types", "DRIVER_VERSION"):
            assert hasattr(module, name), f"{mesher} has no {name}"


def test_the_registry_does_not_import_a_driver_at_module_load() -> None:
    """`import stokes.mesh.drivers` must stay cheap: the API's import budget is measured, and the
    snappy module pulls in the dictionary machinery."""
    import sys

    assert "snappy" in DRIVER_MODULES
    # The module object is what the registry maps to, so this asserts the mapping is by NAME and
    # that asking for blockMesh does not drag snappy in.
    assert DRIVER_MODULES["snappy"].startswith("stokes.mesh.drivers")
    del sys  # only imported to make the intent obvious


# ------------------------------------------------------------------ the snapshot chooser


def test_the_chooser_returns_the_record_the_mesher_needs(home: Path) -> None:
    """**The shape mismatch that made snappy unusable.** `build_snapshot` returns a `MeshSnapshot`
    with `sides`, which is blockMesh's world; snappy needs `surfaces`. One entry point picks."""
    session = Session()
    session.execute("project.new", name="demo")
    # A box domain needs no part, so both meshers can build from it: this test is about WHICH
    # record comes back, not about the geometry.
    session.execute("mesh.configure", values={"domain": "box", "box": [0, 0, 0, 1, 1, 1]})
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None

    # **A new project defaults to snappy** (the owner's 1045 decision), so the FIRST choice here is
    # snappy and blockMesh is the switch.
    assert type(snapshot_for(session, settings)).__name__ == "SnappySnapshot"

    session.execute("mesh.configure", mesher="blockmesh")
    assert isinstance(snapshot_for(session, settings), MeshSnapshot)

    session.execute("mesh.configure", mesher="snappy")
    chosen = snapshot_for(session, settings)
    assert not isinstance(chosen, MeshSnapshot)
    assert type(chosen).__name__ == "SnappySnapshot"
    # **Both answer "who built you?" the same way**, which is what the service reads to pick the
    # driver for the basis and for the result node.
    assert chosen.mesher == "snappy"


def test_the_snappy_snapshot_carries_what_the_service_reads(home: Path) -> None:
    """The service builds the result node from the snapshot: `derives_from`, warnings, cells. A
    record missing those fields is an AttributeError on the click."""
    session = Session()
    session.execute("project.new", name="demo")
    session.execute("mesh.configure", values={"domain": "box", "box": [0, 0, 0, 0.01, 0.01, 0.01]})
    session.execute("mesh.configure", mesher="snappy")
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    snap = snapshot_for(session, settings)
    for name in ("settings_id", "domain_part", "side_tags", "unmapped", "mesher"):
        assert hasattr(snap, name), f"the service reads {name} off the snapshot"


# ------------------------------------------------------------------ against a real part


@pytest.fixture
def part_session(home: Path) -> tuple[Session, str]:
    """A project with one imported box part, ready to mesh."""
    from tests.test_project_save_as_portability import box, write_brep

    source = write_brep(box(10.0, 20.0, 30.0), home / "part.brep")
    session = Session()
    session.execute("project.new", name="demo")
    part_id = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part_id, timeout=180), "the import did not finish"
    return session, part_id


def test_a_real_part_produces_a_complete_snappy_case(
    home: Path, part_session: tuple[Session, str]
) -> None:
    """**The end-to-end shape of the button.** A part in, a case out: the surfaces staged as ASCII
    STL under `constant/geometry/`, a background box, a verified inside point, and the four
    utilities in the order that works.
    """
    session, part_id = part_session
    session.execute("mesh.configure", values={"domain": "part", "domain_part": part_id})
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None

    snap = snapshot_for(session, settings)
    driver = driver_for("snappy")
    files = driver.write(snap)

    assert "system/snappyHexMeshDict" in files
    assert "system/blockMeshDict" in files
    assert "system/surfaceFeaturesDict" in files
    # **The surfaces travel with the case**, or the dictionary names a file that was never staged.
    staged = [k for k in files if k.startswith("constant/geometry/")]
    assert staged, "no STL was staged, so snappy has nothing to follow"
    assert all(k.endswith(".stl") for k in staged)
    assert "solid " in next(files[k] for k in staged)
    assert [s["name"] for s in driver.steps(snap)] == [
        "blockMesh",
        "surfaceFeatures",
        "snappyHexMesh",
        "checkMesh",
    ]


def test_the_inside_point_is_inside_the_part_not_its_centroid(
    home: Path, part_session: tuple[Session, str]
) -> None:
    """For a solid box the centroid happens to be inside, so this asserts the general property
    rather than the interesting case: the point snappy is given must lie within the part's own
    bounding box, because snappy keeps the cells reachable from it."""
    session, part_id = part_session
    session.execute("mesh.configure", values={"domain": "part", "domain_part": part_id})
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    snap = snapshot_for(session, settings)

    x, y, z = snap.inside_point
    x0, y0, z0 = snap.background_min
    x1, y1, z1 = snap.background_max
    assert x0 < x < x1
    assert y0 < y < y1
    assert z0 < z < z1


def test_the_background_block_is_written_with_its_faces(
    home: Path, part_session: tuple[Session, str]
) -> None:
    """**Measured**: a boundary entry with no `faces` makes blockMesh fail with
    `keyword faces is undefined in dictionary "background"`, and snappy then finds no polyMesh at
    all. The hex line and the six faces are what make the background real.
    """
    import re

    session, part_id = part_session
    session.execute("mesh.configure", values={"domain": "part", "domain_part": part_id})
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    text = driver_for("snappy").write(snapshot_for(session, settings))["system/blockMeshDict"]
    squashed = re.sub(r"[ \t]+", " ", text)

    assert re.search(r"hex \(0 1 2 3 4 5 6 7\) \(\d+ \d+ \d+\) simpleGrading \(1 1 1\)", squashed)
    assert "type patch;" in squashed
    assert "faces" in squashed
    assert squashed.index("faces") < squashed.index("(0 3 7 4)")


def test_every_declared_surface_has_a_staged_STL(
    home: Path, part_session: tuple[Session, str]
) -> None:
    """**The defect that reached the demo.** A surface declared in the dictionary with no file on
    disk makes `surfaceFeatures` die with `Cannot read ".../unassigned.stl"` - measured on the live
    server.

    The cause was a face-id/triangle-index mix-up: `tag_faces` holds FACE ids, while
    `len(face_ids)` counts TRIANGLES (a quad is two), so the ids above the face count looked
    unclaimed and `unassigned` was added for faces that were already tagged. The two must agree, and
    `snappy.write` now refuses a case where they do not.
    """
    import re

    session, part_id = part_session
    session.execute("mesh.configure", values={"domain": "part", "domain_part": part_id})
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    snap = snapshot_for(session, settings)
    files = driver_for("snappy").write(snap)

    declared = set(re.findall(r'"([^"]+\.stl)"', files["system/surfaceFeaturesDict"]))
    staged = {Path(p).name for p in files if p.endswith(".stl")}
    assert declared == staged, f"declared {sorted(declared)} but staged {sorted(staged)}"
    # And every declared name is a surface the writer knows about, so nothing is invented.
    assert declared == {s.file for s in snap.surfaces}


def test_a_part_whose_faces_are_ALL_tagged_has_no_unassigned_surface(
    home: Path, part_session: tuple[Session, str]
) -> None:
    """**The demo's exact shape, and the test that catches the bug.** Measured on the live server:
    `surfaceFeatures exited 1` with `Cannot read ".../unassigned.stl"`.

    When EVERY face is tagged there is no untagged skin, so no `unassigned` surface and no
    `unassigned.stl`. The bug compared a FACE id against the TRIANGLE count, so the ids above the
    face count looked unclaimed, `unassigned` was declared, and no file was written for it.

    **Why this takes a fully-tagged part to fail**: with no tags at all, every face is genuinely
    unassigned and the file IS written, so the mismatch is invisible. The demo's
    inlet/outlet/walls between them cover the whole box - which is what this reproduces.
    """
    import re

    session, part_id = part_session
    # Tag EVERY face (the box's 6), so nothing is left for `unassigned`.
    session.execute(
        "tag.create",
        name="all",
        members=[{"part": part_id, "faces": [0, 1, 2, 3, 4, 5]}],
    )
    session.execute("mesh.configure", values={"domain": "part", "domain_part": part_id})
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    snap = snapshot_for(session, settings)

    names = [s.name for s in snap.surfaces]
    assert "unassigned" not in names, (
        f"every face is tagged, so there is no untagged skin; surfaces were {names}"
    )
    staged = {Path(p).name for p in snap.stl_files}
    assert "unassigned.stl" not in staged

    files = driver_for("snappy").write(snap)
    declared = set(re.findall(r'"([^"]+\.stl)"', files["system/surfaceFeaturesDict"]))
    assert declared == staged == {s.file for s in snap.surfaces}


def test_a_scripted_edit_to_the_settings_would_change_the_case(
    home: Path, part_session: tuple[Session, str]
) -> None:
    """An open store's save must reflect the CURRENT model: a mesh configured after the snapshot
    must appear, which is what stops a stale case being meshed."""
    session, part_id = part_session
    session.execute("mesh.configure", values={"domain": "part", "domain_part": part_id})
    settings = session.require_project().find(SETTINGS_PATH)
    assert settings is not None
    before = snapshot_for(session, settings)
    session.execute("mesh.configure", values={"base_size": "0.002 [m]"})
    after = snapshot_for(session, settings)
    assert after.base_size != before.base_size or after.background_cells != before.background_cells


def test_a_domain_part_left_out_of_the_simulation_refuses_to_mesh(home: Path) -> None:
    """T-221: a body left out of the simulation cannot be the one a mesh is built on.

    **An explicit refusal rather than a silent fallback**, and the two failures it avoids are
    different: meshing the excluded body gives a mesh the user did not ask for, and silently
    choosing another part gives a mesh that looks right while describing something else.

    The check lives where the domain part is resolved, which is the one place every mesher passes
    through - so this asserts on the exclusion rather than on whatever error the synthetic geometry
    would raise next.
    """
    from stokes.mesh.snapshot import MeshInvalid, snapshot_for

    session = Session()
    session.execute("project.new", name="demo")
    project = session.require_project()
    parts = project.add("/geometry", "group", "parts", node_id="g_parts")
    assert parts is not None
    part = project.add(
        "g_parts",
        "part",
        "housing",
        {"bbox": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]},
        node_id="n_h",
    )
    session.execute(
        "mesh.configure",
        values={"domain": "part", "domain_part": "/geometry/parts/housing"},
    )
    session.execute("geometry.simulate", nodes=[part.id], simulate=False)

    settings = project.find(SETTINGS_PATH)
    assert settings is not None
    with pytest.raises(MeshInvalid) as caught:
        snapshot_for(session, settings)
    assert "left out of the simulation" in str(caught.value)
    assert caught.value.code == "mesh.part_excluded"

    # **And putting it back clears the refusal**: the check is the flag, not a one-way latch.
    session.execute("geometry.simulate", nodes=[part.id], simulate=True)
    with pytest.raises(MeshInvalid) as after:
        snapshot_for(session, settings)
    assert "left out of the simulation" not in str(after.value)
