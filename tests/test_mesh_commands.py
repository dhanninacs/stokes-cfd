"""The ``mesh.*`` commands (T-143).

Every command goes through `Session.execute`, and the undo, replay and refusal behaviour is
asserted on the tree itself rather than on the reply — a reply can be right while the tree is
wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session, journal
from stokes.api.commands.mesh import SETTINGS_ID
from stokes.errors import StokesError


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """A session on a fresh project, with a home of its own so nothing reads a real one."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    made = Session()
    made.execute("project.new", name="mesh")
    return made


def _settings(session: Session) -> Any:
    return session.require_project().find("/mesh/settings")


def _params(session: Session) -> dict[str, Any]:
    node = _settings(session)
    assert node is not None
    return {
        name: value.value if hasattr(value, "value") else value
        for name, value in node.params.items()
    }


def test_meshers_needs_no_project() -> None:
    """**`requires_project=False`.** The palette offers meshers before a project exists, and the
    schema is
    what a client builds the settings form from."""
    fresh = Session()
    reply = fresh.execute("mesh.meshers")
    assert [item["id"] for item in reply["meshers"]] == [
        "blockmesh",
        "snappy",
        "cfmesh",
        "cfmesh_tet",
        "cfmesh_poly",
        "polyhex",
        "gmsh",
    ]
    # **Six are built** (ADR-0032 adds the four cfMesh fills); Gmsh is listed with its reason
    # rather than hidden.
    assert [item["built"] for item in reply["meshers"]] == [True] * 6 + [False]
    assert all(item["why"] for item in reply["meshers"]), "every mesher must say why"
    assert "properties" in reply["schema"]


def test_configure_creates_the_settings_with_the_stable_id(session: Session) -> None:
    """The id is fixed by the path, so a journal replaying into a fresh session lands on the same
    node."""
    reply = session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "base_size": "2 [mm]",
            "domain": "box",
            "box": ["0 [mm]", "0 [mm]", "0 [mm]", "10 [mm]", "20 [mm]", "30 [mm]"],
        },
    )
    # **`created` is False now (G2)**: `/mesh/settings` exists from `project.new`, so
    # configure edits
    # the node it finds. The stable id is still what this test is about, and it is asserted below.
    assert reply["created"] is False, "G2: the node is already there from project.new"
    assert reply["settings"]["id"] == _settings(session).id
    assert reply["settings"]["id"] == SETTINGS_ID
    assert reply["settings"]["path"] == "/mesh/settings"
    params = _params(session)
    assert params["base_size"] == pytest.approx(0.002)
    assert params["box"] == pytest.approx([0.0, 0.0, 0.0, 0.01, 0.02, 0.03])


def test_the_reply_is_shaped_like_node_get(session: Session) -> None:
    """**One reader for one object.** The parameters live under `params`, as `node.summary()` puts
    them;
    spreading them to the top level would have made this the one command whose reply a client reads
    differently."""
    reply = session.execute("mesh.configure", values={"cores": 4})
    settings = reply["settings"]
    assert set(settings) >= {"id", "type", "name", "params", "path"}
    assert settings["params"]["cores"] == 4
    assert settings["type"] == "mesh_settings"
    # And the same shape comes back from mesh.list.
    listed = session.execute("mesh.list")["settings"]
    assert listed["params"]["cores"] == 4


def test_one_undo_removes_the_node_and_redo_restores_the_same_id(session: Session) -> None:
    session.execute("mesh.configure", values={"base_size": "2 [mm]", "cores": 4})
    assert _settings(session) is not None
    session.execute("history.undo")
    # **G2 changes what one undo means here.** The node is created by `project.new`, not by
    # `mesh.configure`, so a single undo reverses the *configure* — the parameters go back to their
    # defaults — and the node itself stays, because removing it would undo something the user never
    # did. The node's disappearance is covered by
    # `test_list_reports_null_when_the_node_was_removed`.
    after = _settings(session)
    assert after is not None, "the settings node is not the configure's to create or remove"
    assert float(after.params["base_size"].value) == pytest.approx(0.005), "base_size not restored"
    session.execute("history.redo")
    node = _settings(session)
    assert node is not None and node.id == SETTINGS_ID
    assert node.params["base_size"].value == pytest.approx(0.002)


def test_a_second_configure_is_one_more_undo_step(session: Session) -> None:
    """Changing a setting is one step, and undoing it leaves the first change in place."""
    session.execute("mesh.configure", values={"base_size": "2 [mm]", "cores": 4})
    session.execute("mesh.configure", values={"cores": 8})
    assert _params(session)["cores"] == 8
    session.execute("history.undo")
    params = _params(session)
    assert params["cores"] == 4
    assert params["base_size"] == pytest.approx(0.002), "the earlier change must survive"


def test_configure_refuses_a_mesher_that_is_not_built(session: Session) -> None:
    """**Repointed from cfMesh to Gmsh** (ADR-0032 built cfMesh; before that, from snappy).

    snappyHexMesh is a built mesher and the DEFAULT now, so the old form of this test asserted the
    opposite of the decision. cfMesh is the one that is still unbuilt, and the test's real subject
    is unchanged: an unbuilt mesher is refused with the catalogue's reason, and the refusal leaves
    the settings alone.
    """
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.configure", mesher="gmsh")
    assert caught.value.code == "mesh.not_built"
    # The catalogue's reason reaches the message, so the user learns when it arrives.
    # **The reason names what a user can do** (claude-win2's review item 9): it read
    # "cfMesh waits for its OpenFOAM 14 build gate (T-162)", and neither the ticket nor the
    # gate means anything outside this repository.
    assert "no driver yet" in str(caught.value)
    assert "tetrahedral" in str(caught.value)
    found = _settings(session)
    assert found is not None, "G2: the node exists from project.new"
    assert found.params["mesher"] == "snappy", "the refusal changed the mesher anyway"


def test_configure_ACCEPTS_snappy_because_it_is_the_default(session: Session) -> None:
    """The other side of the 1045 decision: snappyHexMesh is selectable, and is what a new project
    starts on."""
    session.execute("mesh.configure", mesher="snappy")
    assert _params(session)["mesher"] == "snappy"


def test_configure_ACCEPTS_blockMesh_because_it_stays_user_selectable(session: Session) -> None:
    """*"blockMesh is user selectable"* — the choice is kept, not removed."""
    session.execute("mesh.configure", mesher="blockmesh")
    assert _params(session)["mesher"] == "blockmesh"


def test_configure_refuses_an_unknown_parameter(session: Session) -> None:
    """The model's own code, so a client sees one error family for an unknown parameter whether it
    came
    from `parameter.set` or here."""
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.configure", values={"nope": 1})
    assert caught.value.code == "model.unknown_parameter"
    # **G2: the node exists from `project.new`**, so "no node" is no longer the assertion. What a
    # refusal must not do is CHANGE it, and `nope` must not have become a parameter.
    found = _settings(session)
    assert found is not None, "G2: the node exists from project.new"
    assert "nope" not in found.params, "the refusal wrote the bad parameter anyway"


@pytest.mark.parametrize(
    ("values", "why"),
    (
        ({"box": [1, 0, 0, 1, 2, 3]}, "the box is empty on x"),
        # **ymin == ymax**, written out because my first version of this row was `[0, 1, 1, 1,
        # 2, 3]`,
        # which is a perfectly valid box (y runs 1 to 2) and so proved nothing. The order is
        # xmin ymin zmin xmax ymax zmax, and it is easy to empty the wrong axis by accident.
        ({"box": [0, 1, 1, 1, 1, 3]}, "the box is empty on y"),
        ({"box": [0, 0, 1, 1, 2, 1]}, "the box is empty on z"),
        ({"box": [0, 0, 0, 1, 2]}, "six numbers"),
        ({"base_size": "0 [m]"}, "greater than zero"),
        ({"base_size": "-1 [mm]"}, "greater than zero"),
        ({"surface_size": "-1 [mm]"}, "may not be negative"),
    ),
)
def test_configure_refuses_a_bad_value_and_leaves_the_tree_alone(
    session: Session, values: dict[str, Any], why: str
) -> None:
    """**The tree is compared, not the reply.** "A refusal anywhere leaves the tree unchanged" is
    the
    brief's wording, and only the tree can prove it."""
    before = session.require_project().to_json()
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.configure", values=values)
    assert caught.value.code == "mesh.invalid"
    assert why in str(caught.value)
    assert session.require_project().to_json() == before


def test_a_zero_surface_size_is_allowed_because_it_means_the_base_size(session: Session) -> None:
    """T-205 amendment 4 refuses only a NEGATIVE one; zero is the documented "same as base_size"."""
    session.execute("mesh.configure", values={"surface_size": 0.0})
    assert _params(session)["surface_size"] == 0.0


def test_configure_refuses_mesher_inside_values(session: Session) -> None:
    """`mesher` is the command's own parameter. **This check must not be conditional on `mesher`
    being
    given** — when it was, `values={"mesher": ...}` slipped past and wrote the value as an ordinary
    parameter, with no refusal and a settings node created through the wrong path."""
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.configure", values={"mesher": "snappy"})
    assert caught.value.code == "mesh.invalid"
    assert "own parameter" in str(caught.value)
    found = _settings(session)
    assert found is not None, "G2: the node exists from project.new"
    assert found.params["mesher"] == "snappy", "the refusal changed the mesher anyway"


def test_letter_order_of_the_box_axes_is_checked_independently(session: Session) -> None:
    """A box can be fine on x and empty on z, so each axis is checked rather than the first."""
    with pytest.raises(StokesError) as caught:
        session.execute("mesh.configure", values={"box": [0, 0, 5, 1, 2, 5]})
    assert "empty on z" in str(caught.value)


def test_the_domain_part_ref_blocks_the_delete_until_it_is_cleared(session: Session) -> None:
    """**Deliberate, and the brief says to test it.** Settings naming a part that no longer exists
    would
    mesh nothing, and failing at the delete is where the user can still act."""
    session.execute("node.add", parent="/geometry", type="group", name="parts")
    part = session.execute("node.add", parent="/geometry/parts", type="part", name="plate")["id"]
    session.execute("mesh.configure", values={"domain": "part", "domain_part": part})
    assert _params(session)["domain_part"] == part

    with pytest.raises(StokesError) as caught:
        session.execute("node.remove", node=part)
    assert caught.value.code == "model.referenced"

    session.execute("mesh.configure", values={"domain_part": None})
    session.execute("node.remove", node=part)
    assert session.require_project().find("/geometry/parts/plate") is None


def test_parameter_set_edits_the_settings_because_there_is_no_mesh_set(session: Session) -> None:
    """The brief's deliberate omission: `parameter.set` already edits any parameter."""
    session.execute("mesh.configure", values={"cores": 1})
    session.execute("parameter.set", node="/mesh/settings", name="cores", value=6)
    assert _params(session)["cores"] == 6


def test_replay_reproduces_the_settings_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every command has a journal test (testing.md). Three configures run, then replay into a fresh
    session, and the two trees are compared."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    path = tmp_path / "journal.jsonl"
    first = Session(journal_path=path)
    first.execute("project.new", name="replay")
    first.execute("mesh.configure", mesher="blockmesh", values={"base_size": "2 [mm]"})
    first.execute("mesh.configure", values={"cores": 4})
    first.execute(
        "mesh.configure",
        values={
            "domain": "box",
            "box": ["0 [mm]", "0 [mm]", "0 [mm]", "1 [mm]", "2 [mm]", "3 [mm]"],
        },
    )
    before = _settings(first).to_json()

    second = Session()
    second.execute("project.new", name="replay")
    journal.replay(second, journal.Journal.read(path))
    after = _settings(second).to_json()
    assert before == after
    assert _params(second)["base_size"] == pytest.approx(0.002)


def test_list_reports_the_settings_a_new_project_already_has(session: Session) -> None:
    """**G2: every project has a `/mesh/settings` from `project.new`**, so `settings` is populated
    rather than null. It still reports null for a project whose node was removed — that path is kept
    alive by the removal test below — and "null rather than absent" remains the contract, so a
    client
    never needs a second call to distinguish the two.
    """
    reply = session.execute("mesh.list")
    assert reply["settings"] is not None, "G2: the node exists from project.new"
    # The shape is `node.summary()`: the parameters live under `params`.
    # **A new project starts on snappyHexMesh** (the owner's 1045 decision).
    assert reply["settings"]["params"]["mesher"] == "snappy"
    assert reply["results"] == []


def test_list_reports_null_when_the_node_was_removed(session: Session) -> None:
    """The null case still exists; it just needs the node gone first."""
    node = _settings(session)
    session.execute("node.remove", node=node.id)
    reply = session.execute("mesh.list")
    assert reply["settings"] is None
    assert reply["results"] == []


def test_list_reads_the_tree_and_reports_results(session: Session) -> None:
    """The results come from the tree, with a readable status. Nothing writes a result yet
    (T-145b), so
    this builds one the way that task will and checks the reading side."""
    session.execute("mesh.configure", values={"cores": 2})
    session.execute("node.add", parent="/mesh", type="group", name="results")
    session.execute(
        "node.add",
        parent="/mesh/results",
        type="mesh_result",
        name="blockmesh",
        params={"state": "interrupted", "cells": 4200, "ok": False, "mesher": "blockmesh"},
    )
    reply = session.execute("mesh.list")
    assert reply["settings"]["params"]["cores"] == 2
    assert len(reply["results"]) == 1
    result = reply["results"][0]
    assert result["state"] == "interrupted"
    assert result["cells"] == 4200
    assert "agent stopped reporting" in result["status"], "an interrupted job must explain itself"
    assert result["mesher"] == "blockmesh"


def test_the_commands_do_not_include_the_ones_the_briefs_exclude(session: Session) -> None:
    """`mesh.set`, `mesh.delete`, `mesh.add_refinement` and `mesh.add_layers` are
    deliberately absent:
    `parameter.set` and `node.remove` already exist, and the refinement nodes come with snappy.

    **The set is asserted exactly**, so adding a command is a deliberate act that updates this
    test —
    which is how T-207's `mesh.generate`/`cancel`/`status`/`log` announced themselves.
    """
    from stokes.api.registry import registry

    names = {name for name in registry.names() if name.startswith("mesh.")}
    assert names == {
        "mesh.meshers",
        "mesh.configure",
        "mesh.list",
        "mesh.generate",
        "mesh.cancel",
        "mesh.status",
        "mesh.log",
        # **The guided-meshing pair** (brief M-501, 2026-10-19): one read-only command that names
        # the nine tasks and what each needs, and one that runs the snapshot build dry and returns
        # every finding instead of the first refusal. Added to this exact set deliberately, which
        # is what the test is for.
        "mesh.tasks",
        "mesh.precheck",
        # The composite that says what each body is FOR, in one undo step (brief M-501).
        "mesh.body_role",
        # The cell estimate before anything runs (brief M-503): read-only, pure over the cached
        # snapshot, `values` tries sizes without saving them.
        "mesh.estimate",
        # The first-layer height from a target y+ (brief M-505): read-only, the region's material
        # evaluated at its reference temperature.
        "mesh.layer_height",
        # The quality record for the Quality page (brief M-506): read-only, one command.
        "mesh.quality",
        # Which kept mesh is in use (ADR-0034): one result per mesher, the user picks.
        "mesh.use",
    }
    for absent in ("mesh.set", "mesh.delete", "mesh.add_refinement", "mesh.add_layers"):
        assert absent not in names


@pytest.mark.parametrize("cores", [0, -2, 257, True, 1.5, "2"])
def test_invalid_core_count_does_not_change_other_mesh_settings(
    session: Session, cores: object
) -> None:
    before = _params(session)
    with pytest.raises(StokesError):
        session.execute("mesh.configure", values={"cores": cores, "max_cells": 2000})
    assert _params(session) == before
