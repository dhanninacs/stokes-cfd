from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stokes.errors import (
    CycleError,
    ModelError,
    PersistenceError,
    ReferencedNode,
    UnitMismatch,
    UnknownNode,
    UnknownNodeType,
    ValidationError,
)
from stokes.model import (
    AddNode,
    ConvertGroup,
    Node,
    Project,
    SetParam,
    UndoStack,
    command_from_record,
    load_project,
    save_project,
)
from stokes.model.nodes import STATUSES
from stokes.model.persist import SCHEMA_VERSION
from stokes.model.project import SKELETON, referenced_error
from stokes.model.schema import (
    NODE_TYPE_ALIASES,
    NodeSpec,
    NodeTypeRegistry,
    ParamSpec,
    register_builtin_types,
    registry,
)

PARAMS = "/physics/parameters"


def make_project() -> Project:
    p = Project.new("demo")
    p.add(PARAMS, "parameter", "U_in", {"dimension": "velocity", "value": "2 [m/s]"})
    p.add(PARAMS, "parameter", "rho", {"dimension": "density", "value": "1.2 [kg/m**3]"})
    p.add(PARAMS, "parameter", "dp", {"dimension": "pressure", "value": "0.5 * rho * U_in**2"})
    return p


def test_a_fresh_registry_knows_the_built_in_types() -> None:
    """``register_builtin_types`` registers the study, material and geometry node types itself,
    so a registry knows ``tag`` without the API loading its commands (ADR-0020)."""
    fresh = NodeTypeRegistry()
    register_builtin_types(fresh)
    assert "tag" in fresh.types()
    assert fresh.get("tag").param("kind").choices == ("boundary", "region", "plain")
    assert {"study", "variable", "response", "objective", "constraint"} <= set(fresh.types())
    assert {"material", "property"} <= set(fresh.types())


def test_registering_the_built_in_types_twice_changes_nothing() -> None:
    before = registry.json_schema()
    register_builtin_types(registry)
    assert registry.json_schema() == before


def test_the_tag_members_are_a_json_parameter() -> None:
    """``members`` moved from a patterned string to the ``json`` kind (T-006), and its schema
    says so."""
    spec = registry.get("tag").param("members")
    assert spec.kind == "json"
    assert spec.json_schema()["x-kind"] == "json"
    assert spec.pattern is None


def tag_project() -> tuple[Project, str]:
    """A project with one tag under /geometry/tags."""
    p = Project.new("demo")
    tags = p.add("/geometry", "group", "tags")
    return p, p.add(tags.id, "tag", "inlet").id


@pytest.mark.parametrize(
    ("why", "value"),
    [
        ("not JSON text", "DROP TABLE"),
        ("not a list", '{"part": "n_0000000a", "faces": "all"}'),
        ("faces missing in the text", '[{"part": "n_0000000a"}]'),
        ("a part that is not a node id", '[{"part": "nope", "faces": "all"}]'),
        (
            "a repeated part",
            '[{"part": "n_0000000a", "faces": "all"}, {"part": "n_0000000a", "faces": "all"}]',
        ),
        ("an inverted range", '[{"part": "n_0000000a", "faces": [[3, 1]]}]'),
        ("a range past the face ids", '[{"part": "n_0000000a", "faces": [[2147483648, 2]]}]'),
        (
            "a basis with the wrong letters",
            '[{"part": "n_0000000a", "faces": [[0, 1]], "basis": "XYZ"}]',
        ),
        ("a basis on whole faces", '[{"part": "n_0000000a", "faces": "all", "basis": "abc"}]'),
        (
            "a key that is not part, faces or basis",
            '[{"part": "n_0000000a", "faces": "all", "colour": "red"}]',
        ),
        ("a face that is not an id or a range", '[{"part": "n_0000000a", "faces": [0, "x"]}]'),
        ("a boolean for a face id", '[{"part": "n_0000000a", "faces": [True]}]'),
        ("a number where the members live", 5),
        ("a null where the members live", None),
        ("a structured member without faces", [{"part": "n_0000000a"}]),
    ],
)
def test_tag_members_are_refused_when_written(why: str, value: object) -> None:
    """``members`` is checked when it is written (T-013): a value that does not parse to the
    members the tag commands produce is a ``validation`` error that names the problem, and
    the tag keeps the members it had."""
    p, tag_id = tag_project()
    with pytest.raises(ValidationError, match="members of /geometry/tags/inlet"):
        p.set_param(tag_id, "members", value)
    assert p.get(tag_id).params["members"] == "[]"


@pytest.mark.parametrize(
    "value",
    [
        # The canonical text the tag commands write, and the same data as a list, set
        # through parameter.set as structured data (T-006).
        '[{"part":"n_0000000a","faces":[[0,3],[7,7]],"basis":"1f3a9c2e5b7d"},'
        '{"part":"n_0000000b","faces":"all"}]',
        [
            {"part": "n_0000000a", "faces": [[0, 3], [7, 7]], "basis": "1f3a9c2e5b7d"},
            {"part": "n_0000000b", "faces": "all"},
        ],
        # A face id written alone stands for the one-face range.
        '[{"part":"n_0000000a","faces":[3]}]',
        # The empty tag, both ways.
        "[]",
        [],
    ],
)
def test_tag_members_accept_the_forms_the_commands_write(value: object) -> None:
    p, tag_id = tag_project()
    p.set_param(tag_id, "members", value)
    assert p.get(tag_id).params["members"] == value


def test_a_tag_created_with_bad_members_is_not_added() -> None:
    p = Project.new("demo")
    with pytest.raises(ValidationError, match="members of /geometry/inlet"):
        p.add("/geometry", "tag", "inlet", {"members": "DROP TABLE"})
    assert p.find("/geometry/inlet") is None


def test_null_members_are_refused_and_the_message_says_how_to_clear_a_tag() -> None:
    """``parameter.set`` takes null since ADR-0022 (it clears a reference); the members of a tag
    are not one, so null is refused in words a user can act on."""
    p, tag_id = tag_project()
    with pytest.raises(ValidationError, match=r"got null; an empty list clears the tag"):
        p.set_param(tag_id, "members", None)
    with pytest.raises(ValidationError, match=r"got a number$"):
        p.set_param(tag_id, "members", 5)
    p.set_param(tag_id, "members", [])  # the way to clear it
    assert p.get(tag_id).params["members"] == []


def test_skeleton() -> None:
    p = Project.new("demo")
    assert p.get("/").type == "project"
    assert p.get("/geometry").type == "group"
    assert p.get("physics.parameters").path == PARAMS
    assert p.get("/runs") is p.find("/runs")
    # **Derived from the skeleton**, so dropping a group does not need this edited by arithmetic:
    # `/monitors` was removed on 2026-10-18 because nothing created or read a node in it, and a
    # literal here failed with "11 == 12" - which says nothing about which group went.
    assert sum(1 for _ in p.nodes()) == 1 + len(SKELETON) + sum(len(subs) for _, subs in SKELETON)


def test_parameters_and_expressions() -> None:
    p = make_project()
    u = p.get(f"{PARAMS}/U_in")
    assert u.params["value"].value == pytest.approx(2.0)
    assert u.params["value"].unit == "m/s"
    dp = p.get(f"{PARAMS}/dp")
    assert dp.params["value"].value == pytest.approx(2.4)
    assert dp.params["value"].expr == "0.5 * rho * U_in**2"
    p.set_param(u.id, "value", "3 [m/s]")
    assert dp.params["value"].value == pytest.approx(5.4)
    assert dp.status == "ok"


def test_values_in_other_forms() -> None:
    p = make_project()
    u = p.get(f"{PARAMS}/U_in")
    p.set_param(u.id, "value", 4)
    assert u.params["value"].value == 4.0
    p.set_param(u.id, "value", {"value": 36, "unit": "km/h"})
    assert u.params["value"].value == pytest.approx(10.0)
    p.set_param(u.id, "value", {"value": 0, "unit": "m/s", "expr": "rho * 1 [m**4/(kg*s)]"})
    assert u.params["value"].value == pytest.approx(1.2)


def test_cycles_are_rejected() -> None:
    p = make_project()
    a = p.add(PARAMS, "parameter", "a", {"dimension": "length", "value": "1 [m]"})
    b = p.add(PARAMS, "parameter", "b", {"dimension": "length", "value": "a * 2"})
    with pytest.raises(CycleError):
        p.set_param(a.id, "value", "b / 2")
    assert a.params["value"].value == 1.0
    with pytest.raises(CycleError):
        p.set_param(b.id, "value", "b * 2")
    c = p.add(PARAMS, "parameter", "c", {"dimension": "length", "value": "b + 1 [m]"})
    with pytest.raises(CycleError):
        p.set_param(a.id, "value", "c")
    assert c.params["value"].value == pytest.approx(3.0)


def test_unit_mismatch_in_expression() -> None:
    p = make_project()
    with pytest.raises(UnitMismatch):
        p.set_param(f"{PARAMS}/dp", "value", "U_in")


def test_removing_a_referenced_parameter_marks_dependents_failed() -> None:
    p = make_project()
    p.remove(f"{PARAMS}/U_in")
    dp = p.get(f"{PARAMS}/dp")
    assert dp.status == "failed"
    assert "error" in dp.meta
    p.add(PARAMS, "parameter", "U_in", {"dimension": "velocity", "value": "1 [m/s]"})
    assert dp.status == "ok"
    assert dp.params["value"].value == pytest.approx(0.6)


def test_rename_re_evaluates_dependents() -> None:
    p = make_project()
    p.rename(f"{PARAMS}/U_in", "U")
    dp = p.get(f"{PARAMS}/dp")
    assert dp.status == "failed"
    p.set_param(dp.id, "value", "0.5 * rho * U**2")
    assert dp.status == "ok" and dp.params["value"].value == pytest.approx(2.4)


def test_dimension_change_resets_value() -> None:
    p = make_project()
    u = p.get(f"{PARAMS}/U_in")
    p.set_param(u.id, "dimension", "pressure")
    assert u.params["value"].value == 0.0
    assert u.params["value"].unit == "Pa"
    assert p.get(f"{PARAMS}/dp").status == "failed"


def renamed_types() -> NodeTypeRegistry:
    """The built-in types plus ``gizmo``, whose old name is ``widget``."""
    fresh = NodeTypeRegistry()
    register_builtin_types(fresh)
    fresh.register(NodeSpec("gizmo", "Gizmo", params=(ParamSpec("size", "number", default=1.0),)))
    fresh.alias("widget", "gizmo")
    return fresh


def test_an_old_node_type_resolves_to_the_current_type() -> None:
    fresh = renamed_types()
    assert fresh.get("widget") is fresh.get("gizmo")
    assert fresh.canonical("widget") == "gizmo"
    assert fresh.canonical("gizmo") == "gizmo"
    assert "gizmo" in fresh.types() and "widget" not in fresh.types()
    assert fresh.aliases()["widget"] == "gizmo"
    fresh.alias("doohickey", "widget")  # renamed again: the oldest name follows the chain
    assert fresh.get("doohickey").type == "gizmo"
    fresh.alias("widget", "gizmo")  # the same pair again changes nothing
    with pytest.raises(ValidationError):  # a type registered under an old name
        fresh.register(NodeSpec("widget", "Widget"))
    with pytest.raises(ValidationError):  # a registered type made an alias
        fresh.alias("gizmo", "group")
    with pytest.raises(ValidationError):  # an old name pointed somewhere else
        fresh.alias("widget", "group")
    with pytest.raises(ValidationError):  # a cycle
        fresh.alias("gizmo_x", "gizmo_x")
    with pytest.raises(ValidationError):
        fresh.alias("not a type", "gizmo")
    fresh.alias("gone", "missing")
    with pytest.raises(UnknownNodeType):
        fresh.get("gone")


def test_add_node_stores_the_current_type() -> None:
    p = Project.new("demo", node_registry=renamed_types())
    stack = UndoStack()
    cmd = AddNode("/geometry", "widget", "w", {"size": 2.0})
    assert stack.push(cmd, p)["type"] == "gizmo"
    assert p.get("/geometry/w").type == "gizmo"
    assert cmd.to_record()["type"] == "gizmo"
    assert stack.undo_text == "add gizmo w"
    stack.undo(p)
    stack.redo(p)
    assert p.get("/geometry/w").type == "gizmo"
    assert p.get("/geometry/w").params["size"] == 2.0


def test_the_built_in_registry_takes_the_renamed_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """``register_builtin_types`` makes every entry of the alias table an alias."""
    monkeypatch.setitem(NODE_TYPE_ALIASES, "folder", "group")
    fresh = NodeTypeRegistry()
    register_builtin_types(fresh)
    assert fresh.get("folder").type == "group"
    assert "folder" not in fresh.types()


def test_a_save_survives_a_transient_lock_on_the_project_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scanner holding `project.json` open for a moment must not fail the save (T-094).

    On Windows the final rename fails with `PermissionError` while anything has the target open, and
    antivirus, the indexer and backup agents all do that. The suite hit it about one run in six,
    always inside a full run. This drives it deterministically: the first two renames are refused,
    the third is allowed, and the saved file must be the whole project.

    The real flake was invisible for a whole round because the test asserted a bare
    `assert code == 0`; the message that named `cannot write …\\project.json` only appeared once the
    assertion was changed to print what the CLI answered.
    """
    p = make_project()
    folder = tmp_path / "locked"
    real_replace = Path.replace
    refusals = {"left": 2}

    def flaky_replace(self: Path, target: Path) -> Path:
        if refusals["left"] > 0 and self.name.startswith(".project-"):
            refusals["left"] -= 1
            raise PermissionError(13, "the file is in use by another process")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    saved = save_project(p, folder)
    assert refusals["left"] == 0, "the test did not actually exercise the retry"
    # The atomic promise still holds: what landed is a whole project, not a partial file.
    loaded = load_project(saved)
    assert [n.id for n in loaded.nodes()] == [n.id for n in p.nodes()]


def test_a_save_that_cannot_rename_says_so_with_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock that never clears is still reported, with the file it could not write."""
    p = make_project()

    def always_locked(self: Path, target: Path) -> Path:
        raise PermissionError(13, "the file is in use by another process")

    monkeypatch.setattr(Path, "replace", always_locked)
    with pytest.raises(PersistenceError, match=r"cannot write .*project\.json"):
        save_project(p, tmp_path / "locked")


def test_persistence_roundtrip(tmp_path: Path) -> None:
    p = make_project()
    folder = save_project(p, tmp_path / "demo")
    assert folder.name == "demo.stokes"
    assert (folder / "project.json").is_file()
    stored = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == SCHEMA_VERSION == 3
    loaded = load_project(folder)
    assert [n.id for n in loaded.nodes()] == [n.id for n in p.nodes()]
    dp = loaded.get(f"{PARAMS}/dp")
    assert dp.params["value"].expr == "0.5 * rho * U_in**2"
    assert dp.params["value"].value == pytest.approx(2.4)
    loaded.set_param(f"{PARAMS}/U_in", "value", "4 [m/s]")
    assert dp.params["value"].value == pytest.approx(9.6)


def test_validation() -> None:
    p = Project.new("demo")
    with pytest.raises(ValidationError):
        p.add(PARAMS, "parameter", "bad name")
    p.add(PARAMS, "parameter", "x")
    with pytest.raises(ModelError):
        p.add(PARAMS, "parameter", "x")
    with pytest.raises(ModelError):
        p.add("/", "parameter", "y")
    with pytest.raises(UnknownNode):
        p.get("/nowhere")
    with pytest.raises(ValidationError):
        p.set_display_units("martian")
    with pytest.raises(ValidationError):
        p.set_param(f"{PARAMS}/x", "dimension", "spin")
    with pytest.raises(ValidationError):
        p.set_param(f"{PARAMS}/x", "value", True)
    with pytest.raises(ModelError):
        p.remove("/")


def test_attach_restores_dependencies() -> None:
    p = make_project()
    node, parent_id, index = p.remove(f"{PARAMS}/U_in")
    assert p.get(f"{PARAMS}/dp").status == "failed"
    p.attach(parent_id, node, index)
    dp = p.get(f"{PARAMS}/dp")
    assert dp.status == "ok" and dp.params["value"].value == pytest.approx(2.4)
    p.set_param(node.id, "value", "1 [m/s]")
    assert dp.params["value"].value == pytest.approx(0.6)


def test_warning_is_a_node_status(tmp_path: Path) -> None:
    """`warning` sits beside the other statuses: the model accepts it and keeps it on disk."""
    assert "warning" in STATUSES
    p = make_project()
    p.set_status(f"{PARAMS}/U_in", "warning")
    assert p.get(f"{PARAMS}/U_in").status == "warning"
    folder = save_project(p, tmp_path / "warn")
    assert load_project(folder).get(f"{PARAMS}/U_in").status == "warning"
    with pytest.raises(ValidationError):
        p.set_status(f"{PARAMS}/U_in", "sideways")


def json_holder_project() -> tuple[Project, NodeTypeRegistry]:
    """A project with one node holding a ``json`` parameter besides a quantity one."""
    fresh = NodeTypeRegistry()
    register_builtin_types(fresh)
    fresh.register(
        NodeSpec(
            "json_holder",
            "JSON holder",
            params=(
                ParamSpec("data", "json", default={"a": [1, 2]}),
                ParamSpec("value", "quantity", dimension="dimensionless", default=1.0),
            ),
        ),
        replace=True,
    )
    p = Project.new("demo", node_registry=fresh)
    p.add("/geometry", "json_holder", "holder")
    return p, fresh


def test_a_json_parameter_survives_a_save_and_load(tmp_path: Path) -> None:
    """The value is stored as is, and a JSON object with a ``value`` key is not read as a
    quantity."""
    p, node_registry = json_holder_project()
    holder = p.get("/geometry/holder")
    p.set_param(holder.id, "data", {"value": "not a quantity", "list": [1, 2]})
    loaded = load_project(save_project(p, tmp_path / "json"), node_registry=node_registry)
    assert loaded.get("/geometry/holder").params["data"] == {
        "value": "not a quantity",
        "list": [1, 2],
    }
    assert loaded.get("/geometry/holder").params["value"].value == pytest.approx(1.0)


def test_an_old_project_with_members_as_a_string_still_loads(tmp_path: Path) -> None:
    """A project saved before the ``json`` kind holds ``members`` as JSON text; it reads back."""
    p = Project.new("demo")
    tags = p.add("/geometry", "group", "tags")
    node = p.add(tags.id, "tag", "inlet")
    p.set_param(node.id, "members", '[{"part":"n_00000000","faces":"all"}]')
    loaded = load_project(save_project(p, tmp_path / "old"))
    assert loaded.get(f"{tags.path}/inlet").params["members"] == (
        '[{"part":"n_00000000","faces":"all"}]'
    )


# ---------------------------------------------------------------- references (ADR-0022 2.8)


def setup_project() -> tuple[Project, dict[str, Node]]:
    """A region ``fluid`` with its tag, and two cyclic boundaries on one tag, partnered one way:
    ``right`` names ``left``. Built with ``Project.add``, as the commands' steps add them."""
    p = Project.new("demo")
    tags = p.add("/geometry", "group", "tags")
    tag = p.add(tags.id, "tag", "inlet")
    body = p.add(tags.id, "tag", "fluid", {"kind": "region"})
    region = p.add("/regions", "region", "fluid", {"tag": body.id})
    group = p.add(region.id, "group", "boundaries")
    left = p.add(group.id, "boundary", "left", {"kind": "cyclic", "tag": tag.id})
    right = p.add(group.id, "boundary", "right", {"kind": "cyclic", "tag": tag.id})
    p.set_param(right.id, "partner", left.id)
    nodes = {
        "tags": tags,
        "tag": tag,
        "body": body,
        "region": region,
        "group": group,
        "left": left,
        "right": right,
    }
    return p, nodes


def test_referrers_name_every_reference_into_a_node_or_its_subtree() -> None:
    p, n = setup_project()
    assert p.referrers(n["tag"].id) == [(n["left"], "tag"), (n["right"], "tag")]
    assert p.referrers(n["left"].path) == [(n["right"], "partner")]  # an id or a path
    assert p.referrers(n["right"].id) == []
    assert p.referrers(n["region"].id) == []
    # The subtree's own references go with it; those from outside are what removal leaves.
    assert p.referrers(n["region"].id, subtree=True) == []
    assert p.referrers(n["tags"].id, subtree=True) == [
        (n["region"], "tag"),
        (n["left"], "tag"),
        (n["right"], "tag"),
    ]
    assert p.referrers(n["left"].id, subtree=True) == [(n["right"], "partner")]
    assert p.referrers("/", subtree=True) == []
    with pytest.raises(UnknownNode):
        p.referrers("/nowhere")


def test_the_model_removes_unguarded_and_the_error_names_the_referrers() -> None:
    """``Project.remove`` stays the plain detach that undo uses; ``node.remove`` guards it."""
    p, n = setup_project()
    found = p.referrers(n["tags"].id, subtree=True)
    error = referenced_error(n["tags"], found)
    assert isinstance(error, ReferencedNode) and isinstance(error, ModelError)
    assert error.code == "model.referenced"
    assert error.message.startswith("/geometry/tags is referenced by /regions/fluid (tag), ")
    assert error.details["count"] == 3
    assert [r["param"] for r in error.details["referrers"]] == ["tag", "tag", "tag"]
    many = referenced_error(n["tag"], [(n["left"], "tag")] * 7)
    assert many.message.count("(tag)") == 5 and "and 2 more" in many.message
    p.remove(n["tag"].id)  # the model does not refuse: a project edited by hand can dangle
    assert p.find(n["tag"].id) is None and n["left"].params["tag"] == n["tag"].id


def test_null_clears_an_optional_reference_and_a_required_one_refuses_it() -> None:
    p, n = setup_project()
    assert p.set_param(n["right"].id, "partner", None) == n["left"].id
    assert n["right"].params["partner"] is None
    assert p.set_param(n["region"].id, "material", None) is None  # null stays null
    with pytest.raises(ValidationError, match="required reference") as caught:
        p.set_param(n["left"].id, "tag", None)
    assert caught.value.details["parameter"] == "tag"
    with pytest.raises(ValidationError, match="required reference"):
        p.set_param(n["region"].id, "tag", None)
    assert n["left"].params["tag"] == n["tag"].id
    with pytest.raises(ValidationError):
        p.set_param(n["right"].id, "partner", 7)
    stack = UndoStack()  # undo puts a cleared reference back
    stack.push(SetParam(n["left"].id, "partner", n["right"].id), p)
    stack.push(SetParam(n["left"].id, "partner", None), p)
    stack.undo(p)
    assert n["left"].params["partner"] == n["right"].id
    stack.undo(p)
    assert n["left"].params["partner"] is None


def test_a_node_is_added_only_with_its_required_references() -> None:
    p, n = setup_project()
    with pytest.raises(ValidationError, match="required reference") as caught:
        p.add(n["group"].id, "boundary", "lone", {"kind": "wall"})
    assert caught.value.details == {"parameter": "tag", "type": "boundary"}
    with pytest.raises(ValidationError, match="required reference"):
        p.add("/regions", "region", "solid")
    assert p.find(f"{n['group'].path}/lone") is None and p.find("/regions/solid") is None
    # A refused node leaves nothing behind: an expression it held does not count.
    p.add(PARAMS, "parameter", "U", {"dimension": "velocity", "value": "2 [m/s]"})
    with pytest.raises(ValidationError):
        p.add(n["group"].id, "boundary", "lone", {"velocity": "U * 2", "kind": "velocity_inlet"})
    p.remove(f"{PARAMS}/U")
    assert all(node.status == "ok" for node in p.nodes())


def test_only_a_reference_can_be_required_and_the_schema_says_which() -> None:
    with pytest.raises(ValidationError):
        ParamSpec("name", "string", required=True)
    boundary, region = registry.get("boundary"), registry.get("region")
    assert boundary.param("tag").required and region.param("tag").required
    assert not boundary.param("partner").required and not region.param("material").required
    assert region.param("tag").json_schema()["x-required"] is True
    assert "x-required" not in region.param("material").json_schema()


def test_a_plain_group_converts_in_place_and_back() -> None:
    """ConvertGroup (ADR-0022 decision 2.9): the same id, name, place and children."""
    p, n = setup_project()
    legacy = p.add(n["group"].id, "group", "inlet", index=0)
    child = p.add("/regions", "group", "old")
    p.add(child.id, "group", "boundaries")
    stack = UndoStack()
    params = {"kind": "velocity_inlet", "tag": n["tag"].id, "velocity": 2}
    stack.push(ConvertGroup(legacy.path, "boundary", params), p)
    assert legacy.type == "boundary" and n["group"].children[0] is legacy
    assert legacy.params["velocity"].value == 2.0 and legacy.params["partner"] is None
    expr = "regions.fluid.boundaries.inlet.velocity * 2"
    q = p.add(PARAMS, "parameter", "q", {"dimension": "velocity", "value": expr})
    assert q.params["value"].value == pytest.approx(4.0)
    stack.undo(p)  # a plain group again: what read its parameters fails
    assert legacy.type == "group" and legacy.params == {} and q.status == "failed"
    stack.redo(p)
    assert legacy.type == "boundary" and q.status == "ok"
    stack.push(ConvertGroup(child.id, "region", {"tag": n["body"].id}), p)
    assert child.type == "region" and [c.name for c in child.children] == ["boundaries"]
    record = ConvertGroup(child.id, "region", {"tag": n["body"].id}).to_record()
    rebuilt = command_from_record(record)
    assert isinstance(rebuilt, ConvertGroup) and rebuilt.to_record() == record
    refusals: list[tuple[str, str, dict[str, Any]]] = [
        (legacy.id, "boundary", {"tag": n["tag"].id}),  # typed already
        ("/regions", "region", {"tag": n["body"].id}),  # the skeleton
        (child.path + "/boundaries", "boundary", {"tag": n["tag"].id}),  # a region holds groups
    ]
    for ref, node_type, values in refusals:
        with pytest.raises(ModelError):
            p.convert_group(ref, node_type, values)
    grown = p.add(n["group"].id, "group", "grown")
    p.add(grown.id, "group", "inside")
    with pytest.raises(ModelError, match="inside"):  # a boundary has no children
        p.convert_group(grown.id, "boundary", {"tag": n["tag"].id})
    with pytest.raises(ValidationError, match="required reference"):
        p.convert_group(grown.id, "region")
    bare = p.add(n["group"].id, "group", "bare")
    with pytest.raises(ValidationError):  # a refused value leaves the plain group as it was
        p.convert_group(bare.id, "boundary", {"tag": n["tag"].id, "kind": "fan"})
    assert (grown.type, grown.params, bare.type, bare.params) == ("group", {}, "group", {})


def test_members_written_as_text_like_the_old_projects() -> None:
    """The old form still writes: ``members`` as the JSON text of the list the commands
    produce passes the write check, as it does on the read path."""
    p, tag_id = tag_project()
    text = (
        '[{"part":"n_0000000a","faces":[[0,2]],"basis":"ab12"},{"part":"n_0000000b","faces":"all"}]'
    )
    p.set_param(tag_id, "members", text)
    assert p.get(tag_id).params["members"] == text


def test_undo_restores_members_that_no_longer_pass_the_check() -> None:
    """Undo restores the previous value as it was (``validate=False``): a value written
    before the check began may no longer pass it, and it still comes back; the tag commands
    refuse it when they read it, until the tag is set again."""
    p, tag_id = tag_project()
    node = p.get(tag_id)
    node.params["members"] = "DROP TABLE"  # as an old project file can hold it
    stack = UndoStack()
    stack.push(SetParam(tag_id, "members", '[{"part":"n_0000000a","faces":"all"}]'), p)
    assert node.params["members"] == '[{"part":"n_0000000a","faces":"all"}]'
    stack.undo(p)
    assert node.params["members"] == "DROP TABLE"
