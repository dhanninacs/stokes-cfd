"""The shape a part is, carried into the mesh snapshot (T-180).

**Why this is in the snapshot and not in the driver.** The driver's job is to write a dictionary; it
**Why this is in the snapshot and not in the driver.** The driver writes a dictionary; it should
not be reading B-reps. `build_snapshot` already opens the geometry store, so the classification
happens there and travels as one field - and a driver that needs it refuses when it is absent
rather than guessing.

**The rule this supports**: a box must never be a silent answer for a part that is not a box. A
sphere meshed to a cube is a mesh the user may not notice is wrong until the physics is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.geometry.store import GeometryStore
from stokes.mesh.shape_class import ShapeClass
from stokes.mesh.snapshot import build_snapshot
from tests.geometry_fixtures import box, write_brep


def a_snapshot(session: Session, source: Path, tmp_path: Path):
    """Import `source` as the domain part and build its snapshot."""
    part = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part, timeout=180), "the import did not finish"
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={"domain": "part", "domain_part": part, "base_size": "0.005 [m]"},
    )
    settings = session.require_project().find("/mesh/settings")
    assert settings is not None
    return build_snapshot(session, settings)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    made = Session()
    made.execute("project.new", name="shapes")
    return made


def test_a_box_part_is_classified_as_a_box(session: Session, tmp_path: Path) -> None:
    """The case the current driver already meshes, so the classification must agree with it."""
    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "part.brep")
    snap = a_snapshot(session, source, tmp_path)
    assert snap.shape is not None, "the part was not classified at all"
    assert snap.shape.kind is ShapeClass.BOX
    assert snap.shape.mesheable is True
    # Six planar faces is what makes a single block a faithful description.
    assert set(snap.shape.face_kinds) == {"GeomAbs_Plane"}


def test_a_cylinder_part_is_classified_with_its_radius(session: Session, tmp_path: Path) -> None:
    """**The owner's pipe case.** The radius and axis are what an O-grid is built from, so they must
    survive the trip into the snapshot rather than being re-derived by the driver."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    source = write_brep(BRepPrimAPI_MakeCylinder(5.0, 40.0).Shape(), tmp_path / "part.brep")
    snap = a_snapshot(session, source, tmp_path)
    assert snap.shape is not None
    assert snap.shape.kind is ShapeClass.CYLINDER
    # Millimetres in, metres out: the store records SI, so 5 mm is 0.005 m.
    assert snap.shape.radius == pytest.approx(0.005, rel=1e-6)
    assert snap.shape.axis is not None
    assert abs(snap.shape.axis[2]) == pytest.approx(1.0)


def test_a_pipe_is_classified_with_BOTH_its_radii(session: Session, tmp_path: Path) -> None:
    """**The case my own first rule refused.** A pipe has two concentric cylinders - a wall and a
    bore - and the earlier rule demanded exactly one, so the owner's own pipe fell through to
    "unsupported". Both radii are kept: the outer one is the block structure and the bore is what
    the centre block is carved by."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    outer = BRepPrimAPI_MakeCylinder(10.0, 40.0).Shape()
    axis = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    bore = BRepPrimAPI_MakeCylinder(axis, 6.0, 40.0).Shape()
    source = write_brep(BRepAlgoAPI_Cut(outer, bore).Shape(), tmp_path / "part.brep")

    snap = a_snapshot(session, source, tmp_path)
    assert snap.shape is not None
    assert snap.shape.kind is ShapeClass.CYLINDER
    radii = snap.shape.extra.get("radii") or []
    assert len(radii) == 2, f"a pipe has two radii, got {radii}"
    assert radii[0] == pytest.approx(0.006, rel=1e-6), "the bore"
    assert radii[1] == pytest.approx(0.010, rel=1e-6), "the outer wall"

    outer = BRepPrimAPI_MakeCylinder(10.0, 40.0).Shape()


def test_a_sphere_is_REFUSED_rather_than_called_a_box(session: Session, tmp_path: Path) -> None:
    """**The defect this whole change exists for.** Before it, a sphere was meshed as the box that
    contains it and nothing said so. The classification now refuses, and the reason names what it
    saw so a user can tell whether the classifier is wrong about their part."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    source = write_brep(BRepPrimAPI_MakeSphere(8.0).Shape(), tmp_path / "part.brep")
    snap = a_snapshot(session, source, tmp_path)
    assert snap.shape is not None
    assert snap.shape.kind is ShapeClass.OTHER
    assert snap.shape.mesheable is False
    assert "Sphere" in snap.shape.why, snap.shape.why


def test_two_cylinders_that_do_not_share_an_axis_are_refused(
    session: Session, tmp_path: Path
) -> None:
    """An O-grid describes a **revolved** solid. Two bores at an angle need a decomposition that
    puts a block between them, and a naive O-grid there would produce a plausible mesh of the wrong
    thing - the failure mode the refusal exists to prevent."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    first = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 5.0, 30.0).Shape()
    second = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), 5.0, 30.0).Shape()
    source = write_brep(BRepAlgoAPI_Fuse(first, second).Shape(), tmp_path / "part.brep")

    snap = a_snapshot(session, source, tmp_path)
    assert snap.shape is not None
    assert snap.shape.kind is ShapeClass.OTHER
    assert "axis" in snap.shape.why


def test_a_box_DOMAIN_has_no_shape_and_that_is_honest(session: Session, tmp_path: Path) -> None:
    """A box domain has no part to classify. `None` says that; a fabricated `BOX` would claim the
    domain part was checked when it was not."""
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "domain": "box",
            "box": ["0 [m]", "0 [m]", "0 [m]", "1 [m]", "1 [m]", "1 [m]"],
            "base_size": "0.1 [m]",
        },
    )
    settings = session.require_project().find("/mesh/settings")
    assert settings is not None
    snap = build_snapshot(session, settings)
    assert snap.shape is None
