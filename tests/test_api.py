from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.api import (
    CommandRegistry,
    Journal,
    ParamDef,
    Session,
    command,
    default_registry,
    replay,
)
from stokes.errors import (
    LicenceFeatureMissing,
    NoProject,
    UnknownCommand,
    UnknownNode,
    ValidationError,
)
from stokes.license import Features
from stokes.model.project import SKELETON

PARAMS = "/physics/parameters"


def test_project_new_and_info(session: Session) -> None:
    info = session.execute("project.info")
    assert info["name"] == "demo"
    # **14, not 12**: G2 added `/mesh/settings` and `/solver/settings` to the skeleton, so a new
    # project carries both stages' settings from the start rather than after the first configure.
    # **Derived, not remembered.** The skeleton plus the settings nodes a project now ensures
    # (`/mesh/settings` and `/solver/settings`). `/monitors` was removed on 2026-10-18 because
    # nothing created or read a node in it, and a literal here failed with "13 == 14" - which
    # says nothing about which node went.
    assert info["nodes"] == 1 + len(SKELETON) + sum(len(subs) for _, subs in SKELETON) + 2
    assert info["path"] and info["path"].endswith("demo.stokes")


def test_define_set_get(velocity_parameter: Session) -> None:
    s = velocity_parameter
    got = s.execute("parameter.get", node=f"{PARAMS}/U_in", name="value")
    assert got["value"] == pytest.approx(2.0) and got["unit"] == "m/s"
    assert got["display"] == "2 m/s"
    got = s.execute("parameter.get", node=f"{PARAMS}/U_in", name="value", unit="km/h")
    assert got["display"] == "7.2 km/h"
    s.execute("parameter.define", name="dp", dimension="pressure", value="0.5 * rho * U_in**2")
    assert s.execute("parameter.get", node=f"{PARAMS}/dp", name="value")["value"] == pytest.approx(
        2.4
    )
    s.execute("parameter.set", node=f"{PARAMS}/U_in", name="value", value="3 [m/s]")
    assert s.execute("parameter.get", node=f"{PARAMS}/dp", name="value")["value"] == pytest.approx(
        5.4
    )
    listed = s.execute("parameter.list")
    assert [p["name"] for p in listed] == ["U_in", "rho", "dp"]
    assert listed[2]["expr"] == "0.5 * rho * U_in**2"


def test_display_units_command(velocity_parameter: Session) -> None:
    s = velocity_parameter
    s.execute("project.set_units", system="mm")
    assert s.execute("project.info")["display_units"] == "mm"
    got = s.execute("parameter.get", node=f"{PARAMS}/rho", name="value")
    assert got["display"].endswith("kg/m³") or "kg" in got["display"]
    s.execute("history.undo")
    assert s.execute("project.info")["display_units"] == "si"


def test_tree_and_nodes(session: Session) -> None:
    tree = session.execute("node.tree", depth=1)
    assert [c["name"] for c in tree["children"]] == [
        "geometry",
        "mesh",
        "physics",
        "regions",
        "solver",
        # `monitors` was here until 2026-10-18; nothing created or read a node in it.
        "scenes",
        "runs",
        "studies",
    ]
    added = session.execute("node.add", parent=PARAMS, type="parameter", name="x")
    assert added["path"] == f"{PARAMS}/x"
    session.execute("node.rename", node=added["id"], name="y")
    assert session.execute("node.get", node=f"{PARAMS}/y")["id"] == added["id"]
    session.execute("node.remove", node=added["id"])
    with pytest.raises(UnknownNode):
        session.execute("node.get", node=added["id"])
    assert "parameter" in session.execute("node.types")


def test_undo_redo(velocity_parameter: Session) -> None:
    s = velocity_parameter
    s.execute("parameter.set", node=f"{PARAMS}/U_in", name="value", value="9 [m/s]")
    assert s.execute("history.state")["undo"] == "set value"
    s.execute("history.undo")
    assert s.execute("parameter.get", node=f"{PARAMS}/U_in", name="value")["value"] == 2.0
    s.execute("history.redo")
    assert s.execute("parameter.get", node=f"{PARAMS}/U_in", name="value")["value"] == 9.0


def test_validation_errors(session: Session) -> None:
    with pytest.raises(UnknownCommand):
        session.execute("nope.nothing")
    with pytest.raises(ValidationError):
        session.execute("node.get", nodes="/")
    with pytest.raises(ValidationError):
        session.execute("node.tree", depth="deep")
    with pytest.raises(ValidationError):
        session.execute("parameter.define", name="q", dimension="spin", value="1")


def test_requires_project() -> None:
    s = Session()
    with pytest.raises(NoProject):
        s.execute("node.get", node="/")
    assert "stokes" in s.execute("schema.version")


def test_events(session: Session) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    session.events.subscribe("node.added", lambda e, d: seen.append((e, d)))
    session.execute("parameter.define", name="U", dimension="velocity", value="1 [m/s]")
    assert seen and seen[0][1]["path"] == f"{PARAMS}/U"


def test_journal_replays_into_identical_tree(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    s = Session(journal_path=journal)
    s.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    s.execute("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    s.execute(
        "parameter.define", name="dp", dimension="pressure", value="0.5 * 1.2 [kg/m**3] * U_in**2"
    )
    s.execute("parameter.set", node=f"{PARAMS}/U_in", name="value", value="3 [m/s]")
    s.execute("node.add", parent="/geometry", type="group", name="parts")
    expected = s.execute("node.tree")
    records = Journal.read(journal)
    assert [r["command"] for r in records] == [
        "project.new",
        "parameter.define",
        "parameter.define",
        "parameter.set",
        "node.add",
    ]
    assert records[1]["params"]["node_id"]
    assert records[3]["params"]["node"] == f"{PARAMS}/U_in"
    fresh = Session()
    fresh.execute("project.new", name="other")
    replay(fresh, records[1:])
    assert fresh.execute("node.tree")["children"] == expected["children"]
    # The same records replay through the command as well (into a third session).
    third = Session()
    third.execute("project.new", name="third")
    partial = tmp_path / "partial.jsonl"
    partial.write_text("\n".join(json.dumps(r) for r in records[1:]) + "\n", encoding="utf-8")
    assert third.execute("journal.replay", path=str(partial)) == {"replayed": 4}
    assert third.execute("parameter.get", node=f"{PARAMS}/dp", name="value")[
        "value"
    ] == pytest.approx(5.4)


def test_parameter_set_takes_null_to_clear_an_optional_reference(tmp_path: Path) -> None:
    """ADR-0022 decision 2.8: a nullable command parameter passes an explicit null to the
    handler; parameter.set clears an optional reference with it, journals it and replays it."""
    journal = tmp_path / "journal.jsonl"
    s = Session(journal_path=journal)
    s.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    s.execute("material.create", name="water")
    region = s.execute("region.create", name="fluid", material="water")
    cleared = s.execute("parameter.set", node=region["path"], name="material", value=None)
    assert cleared["params"]["material"] is None
    with pytest.raises(ValidationError, match="required reference"):
        s.execute("parameter.set", node=region["path"], name="tag", value=None)
    with pytest.raises(ValidationError, match="needs parameter 'value'"):
        s.execute("parameter.set", node=region["path"], name="material")
    records = Journal.read(journal)
    assert records[-1]["command"] == "parameter.set" and records[-1]["params"]["value"] is None
    fresh = Session()
    fresh.execute("project.new", name="other")
    replay(fresh, records[1:])
    assert fresh.execute("node.get", node=region["path"])["params"]["material"] is None
    assert ParamDef("x", "string", nullable=True).json_schema()["type"] == ["string", "null"]
    assert "type" not in ParamDef("x", "any", nullable=True).json_schema()
    assert ParamDef("x", "string").json_schema()["type"] == "string"


def test_the_set_up_commands_are_registered() -> None:
    schema = {c["name"]: c for c in default_registry().json_schema()}
    for name in ("boundary.assign", "boundary.set", "boundary.delete"):
        assert schema[name]["mutates"] is True, name
    for name in ("region.create", "region.assign", "region.set", "region.delete"):
        assert schema[name]["mutates"] is True, name
    for name in (
        "boundary.list",
        "region.list",
        "boundary.kinds",
        "boundary.overlap",
        "boundary.uncovered",
    ):
        assert schema[name]["mutates"] is False, name
    assign = schema["boundary.assign"]["input_schema"]
    assert assign["required"] == ["target", "kind"]
    assert "type" not in assign["properties"]["target"]  # a reference or members, checked by hand
    assert {"node_id", "tag_node_id", "region_node_id"} <= set(assign["properties"])
    assert schema["tag.delete"]["input_schema"]["properties"]["force"]["default"] is False


def test_feature_gate() -> None:
    reg = CommandRegistry()

    @command("remote.ping", feature="remote", requires_project=False, mutates=False, into=reg)
    def remote_ping(ctx: Any) -> dict[str, bool]:
        return {"pong": True}

    with pytest.raises(LicenceFeatureMissing):
        Session(reg).execute("remote.ping")
    assert Session(reg, features=Features({"remote"})).execute("remote.ping") == {"pong": True}


def test_schema_generation() -> None:
    registry = default_registry()
    schema = {c["name"]: c for c in registry.json_schema()}
    assert "parameter.set" in schema
    props = schema["parameter.set"]["input_schema"]["properties"]
    assert set(props) == {"node", "name", "value"}
    assert schema["parameter.set"]["input_schema"]["required"] == ["node", "name", "value"]
    assert schema["node.types"]["requires_project"] is False
    assert ParamDef("x", "integer").check(3) == 3
    # Every command lists the old names that resolve to it, and only current names are entries.
    assert list(schema) == registry.names()
    listed = {old: name for name, c in schema.items() for old in c["aliases"]}
    assert listed == registry.aliases()


def renamed_registry() -> CommandRegistry:
    """A registry where ``demo.stale`` is the old name of ``demo.fresh``."""
    reg = CommandRegistry()

    @command("demo.fresh", params=(ParamDef("label", "string"),), requires_project=False, into=reg)
    def demo_fresh(ctx: Any, label: str) -> dict[str, str]:
        return {"label": label}

    reg.alias("demo.stale", "demo.fresh")
    return reg


def test_an_old_command_name_resolves_to_the_current_command(tmp_path: Path) -> None:
    reg = renamed_registry()
    assert reg.get("demo.stale") is reg.get("demo.fresh")
    assert reg.canonical("demo.stale") == "demo.fresh"
    assert reg.canonical("demo.fresh") == "demo.fresh"
    assert reg.names() == ["demo.fresh"]
    assert reg.aliases() == {"demo.stale": "demo.fresh"}
    schema = reg.json_schema()
    assert [(c["name"], c["aliases"]) for c in schema] == [("demo.fresh", ["demo.stale"])]
    # The session runs the command under its old name and journals the current one.
    journal = tmp_path / "journal.jsonl"
    session = Session(reg, journal_path=journal)
    assert session.execute("demo.stale", label="a") == {"label": "a"}
    assert [(r["command"], r["params"]) for r in Journal.read(journal)] == [
        ("demo.fresh", {"label": "a"})
    ]
    # A renamed command renamed again: the oldest name follows the chain.
    reg.alias("demo.oldest", "demo.stale")
    assert reg.get("demo.oldest").name == "demo.fresh"
    assert reg.aliases() == {"demo.oldest": "demo.fresh", "demo.stale": "demo.fresh"}
    assert reg.json_schema()[0]["aliases"] == ["demo.oldest", "demo.stale"]


def test_a_name_is_a_command_or_an_alias_not_both() -> None:
    reg = renamed_registry()
    with pytest.raises(ValidationError):  # a command registered under an old name
        command("demo.stale", requires_project=False, into=reg)(lambda ctx: None)
    with pytest.raises(ValidationError):  # a registered command made an alias
        reg.alias("demo.fresh", "demo.other")
    with pytest.raises(ValidationError):  # an old name pointed somewhere else
        reg.alias("demo.stale", "demo.other")
    reg.alias("demo.stale", "demo.fresh")  # the same pair again changes nothing
    reg.alias("demo.one", "demo.two")
    with pytest.raises(ValidationError):  # a cycle
        reg.alias("demo.two", "demo.one")
    with pytest.raises(ValidationError):
        reg.alias("demo.same", "demo.same")
    with pytest.raises(ValidationError):
        reg.alias("Not a name", "demo.fresh")
    with pytest.raises(UnknownCommand) as caught:  # an alias whose command is not registered
        reg.get("demo.one")
    assert caught.value.details["alias_of"] == "demo.two"


def test_both_hosts_register_the_sample_builder() -> None:
    """`project.sample_open` finds its builder only if the host imported `stokes.app.demo`.

    The start screen's Flanged pipe example stopped opening when a change removed that import from
    both hosts, and nothing noticed: the error read as a missing optional dependency.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "stokes"
    for host in ("app/__main__.py", "server/__main__.py"):
        assert "import stokes.app.demo" in (root / host).read_text(encoding="utf-8"), host
