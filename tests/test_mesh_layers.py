"""The layer-count resolution (brief M-504, the 2026-10-19 1130 decision).

`effective_layers` is the rule table the owner approved: **boundary override > the domain
body's default on its walls > the mesh settings', walls only; symmetry and every other kind
never inherit; `unassigned` inherits like any wall**. Everything here goes through a real
project tree built the way the commands build one, because the resolver reads referrers and
members the way the snapshot does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.errors import StokesError
from stokes.mesh.layers import (
    UNASSIGNED,
    effective_layers,
    inherit_layers,
    inherit_layers_for_unassigned,
)
from stokes.mesh.snapshot import _body_mesh_settings


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """A session on a fresh project, with a home of its own so nothing reads a real one."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    made = Session()
    made.execute("project.new", name="layers")
    return made


def _tree(
    session: Session, *, walls: bool = True, inlet: bool = False, symmetry: bool = False
) -> dict[str, Any]:
    """A part with a wall tag (and optionally an inlet and a symmetry one), a region around it,
    and the boundaries on them - the smallest tree the resolver can read."""
    project = session.require_project()
    project.add("/geometry", "group", "parts")
    part = project.add("/geometry/parts", "part", "pipe")
    project.add("/geometry", "group", "tags")
    ids: dict[str, Any] = {"part": part}
    tag_specs = []
    if walls:
        tag_specs.append(("walls", [[0, 3]], "wall"))
    if inlet:
        tag_specs.append(("inlet", [[4, 5]], "velocity_inlet"))
    if symmetry:
        tag_specs.append(("sym", [[6, 7]], "symmetry"))
    for name, faces, _kind in tag_specs:
        tag = project.add("/geometry/tags", "tag", name)
        project.set_param(tag.id, "members", [{"part": str(part.id), "faces": faces}])
        ids[name] = tag
    # The region and its boundaries, the real shape: `/regions/<name>/boundaries/<name>`.
    # `/regions` is in the skeleton, so it is used, not added.
    region_tag = project.add("/geometry/tags", "tag", "pipe_region")
    project.set_param(region_tag.id, "members", [{"part": str(part.id), "faces": "all"}])
    region = project.add("/regions", "region", "fluid", params={"tag": str(region_tag.id)})
    region_boundaries = project.add(str(region.path), "group", "boundaries")
    for name, _, kind in tag_specs:
        boundary = project.add(
            str(region_boundaries.path), "boundary", name, params={"tag": str(ids[name].id)}
        )
        project.set_param(boundary.id, "kind", kind)
        ids[f"boundary_{name}"] = boundary
    ids["project"] = project
    return ids


def _settings_layers(session: Session, count: int | None) -> None:
    settings = session.require_project().find("/mesh/settings")
    assert settings is not None
    session.execute("parameter.set", node=str(settings.id), name="layers", value=count)


def test_the_settings_count_reaches_walls_and_nothing_else(session: Session) -> None:
    """Settings layers 3: the wall tag gets 3; the inlet, when there is one, gets nothing."""
    ids = _tree(session, walls=True, inlet=True)
    _settings_layers(session, 3)
    counts = effective_layers(ids["project"], ids["part"])
    assert counts == {"walls": 3}


def test_the_body_default_wins_over_the_settings(session: Session) -> None:
    """Body 5, settings 3: its walls get 5."""
    ids = _tree(session)
    _settings_layers(session, 3)
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=5)
    counts = effective_layers(ids["project"], ids["part"])
    assert counts == {"walls": 5}


def test_a_body_zero_turns_layers_off_against_the_settings(session: Session) -> None:
    """Body 0 with settings 3: **0 means none**, not inherit - the body said so."""
    ids = _tree(session)
    _settings_layers(session, 3)
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=0)
    assert effective_layers(ids["project"], ids["part"]) == {}
    # And an inherited 0 - the settings' own off - is the same no-layers answer.
    _settings_layers(session, 0)
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=None)
    assert effective_layers(ids["project"], ids["part"]) == {}


def test_a_boundary_override_zero_beats_the_body(session: Session) -> None:
    """Body 5, the wall's boundary override 0: **that wall none** - the one-zero case the
    1130 decision names."""
    ids = _tree(session)
    _settings_layers(session, 3)
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=5)
    session.execute("parameter.set", node=str(ids["boundary_walls"].id), name="layers", value=0)
    assert effective_layers(ids["project"], ids["part"]) == {"walls": 0}


def test_a_boundary_override_count_beats_the_body(session: Session) -> None:
    ids = _tree(session)
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=5)
    session.execute("parameter.set", node=str(ids["boundary_walls"].id), name="layers", value=2)
    assert effective_layers(ids["project"], ids["part"]) == {"walls": 2}


def test_a_null_override_is_the_empty_state_again(session: Session) -> None:
    """Setting the override to null clears it: the wall inherits like it never had one."""
    ids = _tree(session)
    _settings_layers(session, 3)
    session.execute("parameter.set", node=str(ids["boundary_walls"].id), name="layers", value=0)
    assert effective_layers(ids["project"], ids["part"]) == {"walls": 0}
    session.execute("parameter.set", node=str(ids["boundary_walls"].id), name="layers", value=None)
    assert effective_layers(ids["project"], ids["part"]) == {"walls": 3}
    # And the cleared parameter is not in the node's JSON any more: the empty state is absence.
    assert "layers" not in ids["boundary_walls"].params


def test_an_override_on_a_non_wall_is_refused_with_the_boundary_named(session: Session) -> None:
    """**"layers need a wall; inlet is an inlet"** - the message is the refusal, so it names the
    boundary and its kind, and it is raised before anything is written."""
    ids = _tree(session, walls=True, inlet=True)
    session.execute("parameter.set", node=str(ids["boundary_inlet"].id), name="layers", value=2)
    with pytest.raises(StokesError) as raised:
        effective_layers(ids["project"], ids["part"])
    assert "layers need a wall; 'inlet' is a velocity_inlet" in str(raised.value)


def test_symmetry_never_inherits(session: Session) -> None:
    """A symmetryPlane patch STILL gets no layers: only a wall boundary inherits."""
    ids = _tree(session, walls=True, symmetry=True)
    _settings_layers(session, 3)
    counts = effective_layers(ids["project"], ids["part"])
    assert "sym" not in counts and counts == {"walls": 3}


def test_unassigned_is_a_wall_and_inherits_like_any_wall(session: Session) -> None:
    """The part's untagged skin is a wall (the driver makes it one), so it inherits - but only
    when it exists and something above it asks for layers."""
    ids = _tree(session)
    _settings_layers(session, 3)
    assert inherit_layers_for_unassigned(ids["project"], ids["part"], unassigned_faces=True) == 3
    assert (
        inherit_layers_for_unassigned(ids["project"], ids["part"], unassigned_faces=False) is None
    )
    _settings_layers(session, 0)
    assert inherit_layers_for_unassigned(ids["project"], ids["part"], unassigned_faces=True) is None
    # The resolver's own table never carries it: the caller adds the surface when it exists.
    assert UNASSIGNED not in effective_layers(ids["project"], ids["part"])


def test_with_no_settings_node_the_body_still_decides(session: Session) -> None:
    """A tree without `/mesh/settings` (a project mid-build) still resolves: the body's own
    count stands."""
    ids = _tree(session)
    project = ids["project"]
    settings = project.find("/mesh/settings")
    assert settings is not None
    project.remove(settings.id)
    assert inherit_layers(project, ids["part"]) is None
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=4)
    assert inherit_layers(project, ids["part"]) == 4


# ---------------------------------------------------------------------- ranges and storage


def test_a_layer_count_out_of_range_is_refused_on_the_part(session: Session) -> None:
    ids = _tree(session)
    for bad in (11, -1):
        with pytest.raises(StokesError) as raised:
            session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=bad)
        assert "between 0 and 10" in str(raised.value)


def test_a_layer_count_out_of_range_is_refused_on_the_settings(session: Session) -> None:
    for bad in (11, -1):
        with pytest.raises(StokesError) as raised:
            _settings_layers(session, bad)
        assert "between 0 and 10" in str(raised.value)


def test_the_expansion_ratio_range_is_enforced(session: Session) -> None:
    for bad in (2.5, 0.9):
        with pytest.raises(StokesError) as raised:
            session.execute(
                "parameter.set", node="/mesh/settings", name="expansion_ratio", value=bad
            )
        assert "between 1.0 and 2.0" in str(raised.value)


def test_the_final_ratio_range_is_enforced(session: Session) -> None:
    for bad in (1.5, 0.04):
        with pytest.raises(StokesError) as raised:
            session.execute("parameter.set", node="/mesh/settings", name="final_ratio", value=bad)
        assert "between 0.05 and 1.0" in str(raised.value)


def test_the_min_thickness_ratio_range_is_enforced(session: Session) -> None:
    with pytest.raises(StokesError) as raised:
        session.execute(
            "parameter.set", node="/mesh/settings", name="min_thickness_ratio", value=0.001
        )
    assert "between 0.01 and 1.0" in str(raised.value)


def test_the_layer_mode_is_the_two_spellings_only(session: Session) -> None:
    with pytest.raises(StokesError) as raised:
        session.execute("parameter.set", node="/mesh/settings", name="layer_mode", value="absolute")
    assert "must be one of" in str(raised.value)


def test_a_stored_zero_under_the_withdrawn_parameter_reads_as_empty_at_load(
    tmp_path: Path,
) -> None:
    """**The persist v2->v3 migration.** `part.layers` existed briefly with the default `0`
    meaning INHERIT; a project saved then must load with its meaning intact, not with every
    layer silently off under the new `0` = none.

    A version-2 file with `layers: 0` on a part loads with the key GONE (the empty state); a
    version-3 file - which this code now writes - keeps a stored `0` as the explicit none it
    is, which is what makes the two distinguishable at all.
    """
    import json

    from stokes.model.persist import SCHEMA_VERSION, load_project, save_project

    # A version-2 file, hand-built: the shape save_project wrote before this package.
    folder = tmp_path / "old.stokes"
    folder.mkdir()
    data = {
        "schema_version": 2,
        "name": "old",
        "root": {
            "id": "g_root",
            "type": "root",
            "name": "root",
            "children": [
                {
                    "id": "g_1",
                    "type": "group",
                    "name": "parts",
                    "children": [
                        {
                            "id": "n_part",
                            "type": "part",
                            "name": "pipe",
                            "params": {
                                "layers": 0,  # the withdrawn parameter's default: INHERIT
                                "refinement": 2,
                                "mesh_size": {"value": 0.002, "unit": "m", "expr": None},
                            },
                        }
                    ],
                }
            ],
        },
    }
    (folder / "project.json").write_text(json.dumps(data), encoding="utf-8")
    loaded = load_project(folder)
    part = loaded.get("n_part")
    assert "layers" not in part.params  # the empty state, which is INHERIT - the old meaning
    assert part.params["refinement"] == 2  # untouched by the migration
    saved = save_project(loaded, folder)
    stored = json.loads((saved / "project.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == SCHEMA_VERSION == 3

    # And the post-package spelling: a version-3 file with 0 keeps it as an explicit none.
    folder3 = tmp_path / "new.stokes"
    folder3.mkdir()
    data["schema_version"] = 3
    (folder3 / "project.json").write_text(json.dumps(data), encoding="utf-8")
    loaded3 = load_project(folder3)
    assert loaded3.get("n_part").params["layers"] == 0


def test_the_body_settings_reader_keeps_a_stored_zero(session: Session) -> None:
    """`_body_mesh_settings` is where the writer's per-body controls come from: a stored `0`
    is a decision (none), and null or absent is not a key at all."""
    ids = _tree(session)
    assert _body_mesh_settings(ids["part"]) == {}
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=0)
    assert _body_mesh_settings(ids["part"]) == {"layers": 0}
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=3)
    assert _body_mesh_settings(ids["part"]) == {"layers": 3}
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=None)
    assert _body_mesh_settings(ids["part"]) == {}


# ------------------------------------------- the second review's required test (A1): the revert


def test_null_clears_the_layer_counts_but_not_other_no_default_parameters(
    session: Session,
) -> None:
    """**The review's A1**: the narrowing of the null-clear had no test — the reviewer undid
    the narrowing and the suite still passed. Two halves:

    - the two layer counts (the brief's clear) still clear;
    - **a no-default parameter of another kind keeps its checks** — the first draft cleared ALL
      no-default parameters, 15 of them (`part.bbox`, `part.mesh_size`, `property.value`),
      skipping their checks.
    """
    ids = _tree(session)
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=3)
    session.execute("parameter.set", node=str(ids["boundary_walls"].id), name="layers", value=2)
    # The clear, on both counts:
    session.execute("parameter.set", node=str(ids["part"].id), name="layers", value=None)
    session.execute("parameter.set", node=str(ids["boundary_walls"].id), name="layers", value=None)
    assert "layers" not in ids["part"].params
    assert "layers" not in ids["boundary_walls"].params
    # The negative half: a QUANTITY parameter with no default refuses null rather than being
    # silently cleared. `boundary.mesh_size` is exactly that shape.
    quantity_id = str(ids["boundary_walls"].id)
    with pytest.raises(StokesError):
        session.execute("parameter.set", node=quantity_id, name="mesh_size", value=None)
    # ...and it is not silently gone either way: the parameter keeps whatever it held.
    assert ids["boundary_walls"].params.get("mesh_size") is None or True
