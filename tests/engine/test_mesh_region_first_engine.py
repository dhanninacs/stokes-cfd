"""The GUI's own order: define the fluid region, THEN mesh (Claude's 06:15 order, 2026-10-15).

**The bug this fixes.** `setup.defaults` and the Assign menu create the fluid region and its tag
*before* the user presses Mesh, because that is the order the GUI leads every user through:
load a CAD
file, define the boundaries and the region, then mesh. It failed with

    the xmax side holds faces of fluid and outlet; blockMesh gives a side one patch

so the owner's first press of Mesh ended in an error.

**Why.** `region.assign` gives its tag **every face of the part** (`whole_bodies`) because a region
holds whole bodies. `_tag_faces` collected that tag with the boundary tags, so it competed for
a block
side with `outlet` - and `blockMesh` really does give a side exactly one patch, so the refusal
fired on
an arrangement that is perfectly valid.

**The rule.** A **region** tag is cell-zone membership: it says which cells are fluid, not
which faces
form a patch. It must not compete for a side. Only boundary-kind tags do, and **two of those
on one side
is still refused**, because that is a genuine blockMesh limit.

**These tests need no OCP and no OpenFOAM**: the mesh snapshot is built from a synthetic box
whose six
sides are told apart by their tags.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.service import MeshService

# **Marked `engine` because the mesh really runs**: without OpenFOAM `mesh.generate` returns
# `skipped`
# and the assertions would fail for the wrong reason. `tests/conftest.py` skips these unless
# `STOKES_ENGINE_TESTS=1`, and the header shows the command.
pytestmark = pytest.mark.engine

DUCT_SIDE_MM = 20
DUCT_LENGTH_MM = 500


@pytest.fixture
def duct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Session, dict[str, str]]:
    """The duct of `tools/e2e_support`, with its boundaries tagged and no region yet."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent))
    from tools.e2e_support import duct_project

    session = Session()
    ids = duct_project(session, tmp_path / "duct.stokes")
    return session, ids


def _assign_fluid(session: Session, ids: dict[str, str]) -> None:
    """What `setup.defaults` / the Assign menu does: a material and a whole-part fluid region."""
    material = session.execute("material.create", name="air", kind="fluid", template="gas")
    session.execute("region.assign", target=ids["part"], material=material["id"], name="fluid")


def test_the_whole_part_region_tag_takes_every_face(duct: tuple[Session, dict[str, str]]) -> None:
    """**The premise of the fix, asserted rather than assumed.** If `region.assign` did not
    give its tag
    the whole part, the tag could not have collided with `outlet` and this file would be testing a
    scenario that cannot happen.
    """
    session, ids = duct
    _assign_fluid(session, ids)
    region = next(n for n in session.require_project().root.walk() if n.type == "region")
    tag = session.require_project().get(str(region.params["tag"]))
    assert tag.type == "tag"
    from stokes.api.commands.setup_plan import stored_members

    members = stored_members(tag)
    assert any(getattr(m, "whole", False) for m in members), (
        "region.assign no longer gives its tag the whole part, so the collision this file "
        "guards against cannot occur - re-check before deleting these tests"
    )


def test_a_region_assigned_before_the_mesh_still_meshes(
    duct: tuple[Session, dict[str, str]],
) -> None:
    """**The owner's first press of Mesh.** Region first, then Mesh, must give the duct and
    its three
    patches - not a refusal naming the fluid region as if it were a boundary."""
    session, ids = duct
    _assign_fluid(session, ids)
    result = session.execute("mesh.generate", wait=True)
    assert result["result"], "the mesh produced nothing"
    status = MeshService.of(session).status()
    assert status["state"] == "done", status
    assert status["cells"] == 25_000, status["cells"]
    # **The three patches, counted.** The result node records `boundary_patches` in its quality and
    # `checkMesh` passed, so three patches is what the mesh really has: the two boundary tags
    # plus the
    # default wall for the four sides no tag holds. Before the fix this call raised, so reaching the
    # assertion at all is the point.
    listed = session.execute("mesh.list")
    result = listed["results"][0]
    assert result["state"] == "done", result
    assert result["cells"] == 25_000, result
    node = session.require_project().get("/mesh/results/blockmesh")
    quality = node.params["quality"]
    assert quality["boundary_patches"] == 3, quality
    assert node.params["ok"] is True, node.params


def test_two_boundary_tags_on_one_side_are_still_refused(
    duct: tuple[Session, dict[str, str]],
) -> None:
    """**The refusal that must SURVIVE the fix.** `blockMesh` gives a side exactly one patch, so two
    genuine boundary tags on one side is a real limit - and a fix that silenced this would
    hide a mesh
    whose inlet and outlet are the same patch.
    """
    session, ids = duct
    # A second boundary tag over the whole part: a boundary-kind tag that claims every face is a
    # contradiction, which is what the message is for.
    session.execute(
        "tag.create",
        name="everywhere",
        members=[{"part": ids["part"], "faces": "all"}],
        kind="boundary",
    )
    with pytest.raises(MeshInvalid) as caught:
        session.execute("mesh.generate", wait=True)
    assert "holds faces of" in str(caught.value)
    assert "blockMesh gives a side one patch" in str(caught.value)


def test_a_region_assigned_after_the_mesh_keeps_it_fresh(
    duct: tuple[Session, dict[str, str]],
) -> None:
    """**The other order must keep working.** Assigning the region afterwards is what
    `tools/e2e_duct.py` does today, and the mesh must not go stale - `solve.check` refuses a
    stale mesh,
    so a regression here would break the end-to-end path that already passes.
    """
    session, ids = duct
    session.execute("mesh.generate", wait=True)
    service = MeshService.of(session)
    assert service.status()["stale"] is False, "fresh right after meshing"
    _assign_fluid(session, ids)
    status = service.status()
    assert status["state"] == "done"
    assert status["stale"] is False, (
        "assigning the region made the mesh stale; the region does not change the geometry, so it "
        "must not invalidate the mesh that was made from it"
    )
