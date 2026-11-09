"""Every stage's settings node exists from `project.new` on (G2, the owner's order 2026-10-15).

**The problem this fixes.** `/mesh/settings` was created only by the first `mesh.configure`, and
`/solver/settings` only by the first `solve.configure`. So a fresh project's tree had an empty Mesh
group and an empty Solver group, and the owner — whose workflow is "load a CAD file, define
boundaries,
mesh, run and plot, all in the GUI" — saw no settings for the stages he had to configure.

**The rule that makes it safe**: the ensure is **non-journaled and non-undoable**, like the
structural
skeleton it belongs with. A user who opens a project and changes nothing has changed nothing, so the
ensure must not appear in the undo history and must be idempotent. Without that, every project would
open with a phantom undo step and `history.undo` would do something surprising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.errors import StokesError

MESH = "/mesh/settings"
SOLVER = "/solver/settings"


@pytest.fixture
def fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="duct")
    return session


def test_a_new_project_has_both_settings_nodes(fresh: Session) -> None:
    """The whole point: the tree shows a settings node for both stages immediately."""
    project = fresh.require_project()
    mesh = project.find(MESH)
    solver = project.find(SOLVER)
    assert mesh is not None and mesh.type == "mesh_settings"
    assert solver is not None and solver.type == "solver_settings"


def test_they_carry_their_defaults_not_empty_params(fresh: Session) -> None:
    """A node with no defaults would be a node a panel renders blank. The schema's defaults are the
    model's, so the ensure must apply them — `Project.add` does, and this pins that it is used."""
    solver = fresh.require_project().find(SOLVER)
    assert solver.params["iterations"] == 500
    assert solver.params["turbulence"] == "laminar"
    mesh = fresh.require_project().find(MESH)
    assert float(mesh.params["base_size"].value) == pytest.approx(0.005)


def test_the_ensure_is_not_an_undo_step(fresh: Session) -> None:
    """**Nothing to undo in a project nobody has edited.** If the ensure were journaled, every new
    project would open with a phantom step and Ctrl-Z would do something a user did not ask for."""
    assert fresh.undo.can_undo is False


def test_a_second_open_does_not_add_a_second_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Idempotent.** Two `project.open` calls must leave one node each, not two — and a duplicate
    would also make `node.added` fire twice, which a client would apply twice."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj.stokes"
    session = Session()
    session.execute("project.new", path=str(folder), name="duct")
    for _ in range(2):
        session.execute("project.open", path=str(folder))
    project = session.require_project()
    assert len([n for n in project.root.walk() if n.path == MESH]) == 1
    assert len([n for n in project.root.walk() if n.path == SOLVER]) == 1


def test_opening_gains_them_and_is_still_not_undoable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A project saved by an older build gains them on open.** That is the migration
    path: there is no version bump and no rewriting of old files, because a missing node
    is simply added."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj.stokes"
    session = Session()
    session.execute("project.new", path=str(folder), name="duct")
    project = session.require_project()
    # **Simulate the old build** by removing them from a saved copy.
    for path in (MESH, SOLVER):
        node = project.find(path)
        assert node is not None
        project.remove(node.id)
    session.execute("project.save")

    reopened = Session()
    reopened.execute("project.open", path=str(folder))
    found = reopened.require_project()
    assert found.find(MESH) is not None, "an older project must gain the mesh settings"
    assert found.find(SOLVER) is not None
    assert reopened.undo.can_undo is False


def test_node_added_is_emitted_only_for_what_was_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client patches its tree from `node.added`, so emitting it for a node that was already there
    would make the client show two."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj.stokes"
    session = Session()
    session.execute("project.new", path=str(folder), name="duct")
    reopened = Session()
    seen: list[dict[str, Any]] = []
    reopened.events.subscribe("node.added", seen.append)
    reopened.execute("project.open", path=str(folder))
    # Nothing was missing, so nothing is announced.
    assert seen == [], f"node.added fired for a node that already existed: {seen}"


def _strip_group(folder: Path, name: str) -> None:
    """Remove a top-level group from a SAVED project, the way a malformed file would arrive.

    **Written to the JSON directly, not through the API.** `node.remove` refuses to delete
    `/mesh` or `/solver` ("part of the project skeleton"), and that refusal is right - but a
    project file can have any shape: written by an older build, edited by hand, produced by
    another tool. The LOADER is the boundary that has to cope, so the test edits the file
    rather than the tree.
    """
    import json

    path = folder / "project.json"
    doc = json.loads(path.read_text())

    def prune(node: dict[str, Any]) -> None:
        children = node.get("children") or []
        node["children"] = [c for c in children if c.get("name") != name]
        for child in node["children"]:
            prune(child)

    for key in ("root", "tree", "project"):
        if isinstance(doc.get(key), dict):
            prune(doc[key])
            break
    path.write_text(json.dumps(doc))


def test_a_renamed_settings_node_does_not_make_project_open_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Opus review item 3, and the scenario that was measured failing.**

    `/mesh/settings` is NOT structural, so a user may rename it. `ensure_stage_settings` looked
    the node up by PATH only and then added one with the stable id derived from that path - so
    the rename produced `ModelError: node id 'n_44373f58' already exists` and **`project.open`
    raised**. Worse,
    it raised AFTER `session.project` was set and `project_opened` never ran, so the tree showed the
    new project while the run and geometry stores kept serving the previous one.

    The node is now looked up by stable id and by type as well, so a rename is not a collision.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj.stokes"
    session = Session()
    session.execute("project.new", path=str(folder), name="duct")
    node = session.require_project().find(MESH)
    session.execute("node.rename", node=node.id, name="meshSettings")
    session.execute("project.save")

    other = Session()
    other.execute("project.new", name="other")
    other.execute("project.open", path=str(folder))
    project = other.require_project()
    # The renamed node is still there, under its new name, and no duplicate was made.
    renamed = [n for n in project.root.walk() if n.type == "mesh_settings"]
    assert len(renamed) == 1, f"the ensure added a second settings node: {renamed}"
    assert renamed[0].name == "meshSettings", "the user's rename was overwritten"


@pytest.mark.parametrize("group", ["mesh", "solver"])
def test_a_project_without_a_group_still_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    """**The other half of item 3.** A file lacking `/mesh` or `/solver` made `project.add` raise on
    the missing parent, half-failing the open for a file the user can do nothing about.

    There is no group to put the settings in, so there is nothing to ensure - and opening must still
    work, because the rest of the project is perfectly good.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj.stokes"
    session = Session()
    session.execute("project.new", path=str(folder), name="duct")
    session.execute("project.save")
    _strip_group(folder, group)

    other = Session()
    other.execute("project.new", name="other")
    other.execute("project.open", path=str(folder))
    assert other.require_project().name == "duct"
    # The group that IS there kept its settings; the missing one simply has none.
    present = SOLVER if group == "mesh" else MESH
    assert other.require_project().find(present) is not None


def test_renaming_the_TAGS_folder_is_refused_because_a_second_one_would_appear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tree guide's finding 5, and the consequence is **measured rather than argued**.

    `tag.create` finds its folder with `project.find("/geometry/tags")`. Renamed, that finds
    nothing - and the next tag **creates a second `tags`**, which is what this test saw before the
    fix: the tree held `my_tags` and `tags`, both with tag children, and nothing told the user which
    one a new tag would land in.

    **A refusal rather than an id-based lookup**, deliberately: re-keying the lookup touches
    selection,
    import, operations, meshing and regions at once, and that belongs with the outline-model work
    (G-305/G-306) rather than in a presentation package. The refusal gives the same guarantee - the
    lookup cannot be broken - at a fraction of the blast radius.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", path=str(tmp_path / "proj.stokes"), name="duct")
    session.execute("tag.create", name="wall")

    tags = session.require_project().find("/geometry/tags")
    assert tags is not None, "tag.create did not make the tags folder"

    with pytest.raises(StokesError) as caught:
        session.execute("node.rename", node=tags.id, name="my_tags")
    assert "skeleton" in str(caught.value)
    # And the folder is where it was, so a second tag lands in the same place.
    assert session.require_project().find("/geometry/tags") is not None
    session.execute("tag.create", name="inlet")
    under = session.require_project().find("/geometry/tags")
    assert [c.name for c in under.children if c.type == "tag"] == ["wall", "inlet"]


def test_a_SETTINGS_node_may_still_be_renamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The other half, and the reason the protected set is only two paths.**

    Renaming `/mesh/settings` was already made safe by looking the node up by stable id and by type,
    and a test above pins that `project.open` survives it. A blanket "protect every folder the code
    finds by path" would have refused a rename the product deliberately supports - which is exactly
    how the first version of this fix failed eleven tests.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", path=str(tmp_path / "proj.stokes"), name="duct")
    session.execute("mesh.configure", mesher="blockmesh")
    node = session.require_project().find("/mesh/settings")
    assert node is not None
    session.execute("node.rename", node=node.id, name="meshSettings")
    assert session.require_project().find(node.id) is not None


def test_a_project_saved_WITH_the_monitors_group_still_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude's 0530 item 1: dropping `/monitors` must not strand an existing project.

    The group was in `SKELETON` until today, so **any project saved before now holds one**. The
    requirement is that it stays harmless: the file opens, the unknown empty group is ignored rather
    than refused, and it is **not re-created** (a project saved after this must not gain it back).

    Written by ADDING the group to a real project file, because that is exactly what an old file
    looks like - a hand-built fixture would test my idea of the format rather than the format.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    folder = tmp_path / "proj.stokes"
    session = Session()
    session.execute("project.new", path=str(folder), name="duct")
    project = session.require_project()
    # An old file: add the group the way the skeleton used to.
    project.add("/", "group", "monitors", node_id="g_monitors_legacy")
    session.execute("project.save")

    # It opens.
    opened = Session()
    opened.execute("project.open", path=str(folder))
    root = opened.require_project()
    assert root.find("/monitors") is not None, "the saved group should still be in the file"

    # **And it is NOT re-created when absent.** A project made now must not gain it back, which is
    # what tells the difference between "tolerated" and "still in the skeleton".
    fresh = Session()
    fresh.execute("project.new", path=str(tmp_path / "new.stokes"), name="new")
    assert fresh.require_project().find("/monitors") is None

    # A file with no monitors group gains none on open either.
    session.execute("project.save")
    again = Session()
    again.execute("project.open", path=str(folder))
    assert again.require_project().find("/monitors") is not None


def test_reconfiguring_after_a_rename_works(fresh: Session) -> None:
    """Leftover 4 of Claude's verdict: the `settings` row offers Rename, so a rename must WORK.

    **He asked whether the node is protected, and the honest answer is no** - `mesh.configure`
    recreates it, so a delete is a supported reset and `STRUCTURAL_PATHS` deliberately leaves it out
    (a rename used to be refused here, and the suite forced that correction). But the promise that a
    rename is safe was only kept for `project.open`: `mesh.configure` looked the node up by PATH,
    found nothing under the new name, tried to add a node whose stable id was taken, and raised
    `ModelError: node id 'n_44373f58' already exists`.

    **Three mistakes in one fix**, all measured: the lookup, the create path that re-added the node,
    and the FINAL lookup that raised "the settings node was not created" right after writing to it.
    """
    session = fresh
    session.execute("mesh.configure", mesher="snappy")
    before = next(n for n in session.require_project().nodes() if n.type == "mesh_settings").id

    session.execute("node.rename", node="/mesh/settings", name="my_mesh")
    # Configuring again must not raise, must not add a second node, and must keep the name.
    session.execute("mesh.configure", mesher="snappy")

    after = [n for n in session.require_project().nodes() if n.type == "mesh_settings"]
    assert len(after) == 1, [n.name for n in after]
    assert after[0].id == before
    assert after[0].name == "my_mesh", "the rename is a user's, so configuring keeps it"
