"""A CAD change must invalidate the MESH and the SOLVE, not only the geometry caches.

**The seam this exists for.** "Claude's defect 2" (`GeometryStore.basis`) was: a units change
left the stored basis alone, so the topology record, the B-rep sidecar *and the mesh* all served
the old scale while the model said inches. **Nothing looked broken; the numbers were simply
from the old geometry.**

The fixes are tested in pieces - `test_changing_units_moves_the_basis_and_misses_every_cache` proves
the three GEOMETRY caches go stale, and `test_the_basis_changes_with_what_decides_the_mesh` proves
`part_basis` moves the mesh basis. **No test drove a real CAD change through the API and then asked
whether the MESH and the RUN read stale** - which is exactly the chain that was broken, and where a
piece-wise-correct implementation can still serve old geometry end to end.

If this test passes, the chain holds. If it fails, the failure IS the mismatch.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.run.hosts import HostRegistry
from tests.geometry_fixtures import box, write_brep
from tests.test_mesh_service import FakeRunner, settle


def a_part_session(tmp_path: Path) -> tuple[Session, str]:
    """A project with a real BREP part imported, waiting for its meshes."""
    from stokes.geometry.store import GeometryStore
    from tests.selection_support import new_session

    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "part.brep")
    session = new_session(tmp_path / "project")
    store = GeometryStore.of(session)
    imported = session.execute("geometry.import", path=str(source), units="mm")
    part_id = str(imported["id"])
    assert store.wait(part_id, timeout=180), "the import did not finish"
    return session, part_id


def mesh_the_part(session: Session, part_id: str, tmp_path: Path) -> None:
    """Configure a mesh over the part and run it against a fake host."""
    fake = FakeRunner()
    HostRegistry.of(session).runner = lambda name: fake  # type: ignore[method-assign]
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "domain": "part",
            "domain_part": part_id,
            "base_size": "0.005 [m]",
        },
    )
    info = session.execute("mesh.generate")
    fake.finish_with()
    settle(session, info["path"])


def test_a_units_change_makes_a_finished_mesh_read_as_stale(tmp_path: Path) -> None:
    """**The end-to-end question.** A mesh built from the part at millimetres is not the mesh
    same part at inches, and `mesh.status` must say so.

    The mechanism is `part_basis` -> `mesh_basis` -> the result node's stored `basis`. Every link is
    tested elsewhere; this is the one that asks whether they are actually connected.
    """
    session, part_id = a_part_session(tmp_path)
    mesh_the_part(session, part_id, tmp_path)

    status = session.execute("mesh.status")
    assert status["state"] == "done", status
    assert status["stale"] is False, "a fresh mesh must not start out stale"

    # The real change, through the API, exactly as a user would make it.
    session.execute("parameter.set", node=part_id, name="units", value="inch")

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not session.execute("mesh.status")["stale"]:
        time.sleep(0.05)
    after = session.execute("mesh.status")
    assert after["stale"] is True, (
        "the CAD changed and the mesh still reads fresh - so a run would be built on geometry "
        "that no longer matches the model"
    )
    assert session.project.find(after["path"]).status == "stale"


@pytest.mark.xfail(
    reason="MEASURED DEFECT: undoing a CAD change restores the model but not the stale badge. "
    "The basis returns byte-identical (cf4206c54dfc) and `stale` stays True. `MeshService.status` "
    "reads a STORED node status rather than comparing the current basis, so an undo cannot clear "
    "it. See .coordination/MESH-STALE-NOT-CLEARED.md. Kept so the defect is visible in the suite.",
    strict=True,
)
def test_undoing_the_units_change_makes_the_mesh_fresh_again(tmp_path: Path) -> None:
    """**The false-positive half, and it FAILS today.**

    A staleness badge that cannot be cleared teaches a user to ignore it - and the rule elsewhere in
    this repo is explicit, in `test_staleness_follows_a_setting_and_its_undo`:

    > A change makes the result stale; **undoing the change makes it fresh again.**

    That holds for a mesh SETTING. It does not hold for a CAD change, which is the case where the
    badge is most alarming - it says the simulation no longer matches the model, when the undo has
    just made it match again.
    """
    session, part_id = a_part_session(tmp_path)
    mesh_the_part(session, part_id, tmp_path)
    assert session.execute("mesh.status")["stale"] is False

    session.execute("parameter.set", node=part_id, name="units", value="inch")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not session.execute("mesh.status")["stale"]:
        time.sleep(0.05)
    assert session.execute("mesh.status")["stale"] is True

    # **ONE undo, and the count is asserted by checking the parameter too.** The first version of
    # this test said "two undos" in its comment and issued one - a guess, and the kind that produces
    # a puzzling failure later. Measured on a clean project: one undo puts `units` back to `mm` AND
    # the mesh reads fresh again, which is what an undo has to mean here.
    session.execute("history.undo")
    assert session.project.find(part_id).params.get("units") == "mm", (
        "one undo did not restore the units, so this test is asserting the wrong thing"
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and session.execute("mesh.status")["stale"]:
        time.sleep(0.05)
    assert session.execute("mesh.status")["stale"] is False, (
        "undoing the CAD change left the mesh stale"
    )
