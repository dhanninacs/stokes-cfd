"""The ``json`` parameter kind (T-006): structured data stored as is, deep-copied, compared by
value and set through ``parameter.set``; quantities and expressions do not apply to it."""

from __future__ import annotations

import pytest

from stokes.errors import ValidationError
from stokes.model import Project
from stokes.model.commands import SetParam, UndoStack
from stokes.model.schema import (
    MAX_JSON_BYTES,
    NodeSpec,
    NodeTypeRegistry,
    ParamSpec,
    register_builtin_types,
)

HOLDER = "/geometry/holder"


def make_project() -> tuple[Project, NodeTypeRegistry]:
    registry = NodeTypeRegistry()
    register_builtin_types(registry)
    registry.register(
        NodeSpec(
            "json_holder",
            "JSON holder",
            params=(
                ParamSpec("data", "json", default={"a": [1, 2]}, max_bytes=256),
                ParamSpec("small", "json", default=None),
            ),
            doc="Test node: two json parameters.",
        ),
        replace=True,
    )
    project = Project.new("demo", node_registry=registry)
    project.add("/geometry", "json_holder", "holder")
    return project, registry


def test_the_kind_is_registered_and_its_schema_says_so() -> None:
    _, registry = make_project()
    spec = registry.get("json_holder").param("data")
    assert spec.kind == "json"
    schema = spec.json_schema()
    assert schema["x-kind"] == "json" and schema["x-max-bytes"] == 256
    assert "type" not in schema  # a json parameter holds any JSON-compatible value


def test_max_bytes_applies_only_to_json() -> None:
    with pytest.raises(ValidationError):
        ParamSpec("x", "string", max_bytes=10)
    with pytest.raises(ValidationError):
        ParamSpec("x", "json", max_bytes=0)


def test_set_and_get_roundtrip_every_json_shape() -> None:
    project, _ = make_project()
    holder = project.get(HOLDER)
    value = {"list": [1, 2.5, True, None], "text": "hi", "nested": {"x": 0}}
    project.set_param(holder.id, "data", value)
    assert holder.params["data"] == value


def test_the_stored_value_is_a_deep_copy() -> None:
    project, _ = make_project()
    holder = project.get(HOLDER)
    given = {"list": [1, 2]}
    project.set_param(holder.id, "data", given)
    given["list"].append(3)
    assert holder.params["data"] == {"list": [1, 2]}  # the model kept its own copy


def test_undo_restores_the_previous_value_by_copy() -> None:
    project, _ = make_project()
    holder = project.get(HOLDER)
    stack = UndoStack()
    first = {"a": [1]}
    stack.push(SetParam(holder.id, "data", first), project)
    first["a"].append(99)  # the command must have snapshotted the old value
    stack.push(SetParam(holder.id, "data", {"b": 2}), project)
    assert holder.params["data"] == {"b": 2}
    stack.undo(project)
    assert holder.params["data"] == {"a": [1]}
    stack.undo(project)
    assert holder.params["data"] == {"a": [1, 2]}


def test_values_are_compared_by_value() -> None:
    project, _ = make_project()
    holder = project.get(HOLDER)
    project.set_param(holder.id, "data", {"a": [1, 2]})
    old = project.set_param(holder.id, "data", {"a": [1, 2]})
    assert old == {"a": [1, 2]}


def test_max_bytes_is_enforced() -> None:
    project, _ = make_project()
    holder = project.get(HOLDER)
    project.set_param(holder.id, "data", "x" * 100)
    with pytest.raises(ValidationError):
        project.set_param(holder.id, "data", "x" * 1000)


def test_only_json_compatible_data_is_accepted() -> None:
    project, _ = make_project()
    holder = project.get(HOLDER)
    with pytest.raises(ValidationError):
        project.set_param(holder.id, "data", {"set": {1, 2}})
    with pytest.raises(ValidationError):
        project.set_param(holder.id, "data", {"f": float("inf")})
    with pytest.raises(ValidationError):
        project.set_param(holder.id, "data", {1: "a"})


def test_the_default_limit_is_64_kb() -> None:
    assert MAX_JSON_BYTES == 64 * 1024
    project, _ = make_project()
    holder = project.get(HOLDER)
    project.set_param(holder.id, "small", "x" * 1000)  # no max_bytes on the spec: the default
    assert len(holder.params["small"]) == 1000
    with pytest.raises(ValidationError):
        project.set_param(holder.id, "small", "y" * (MAX_JSON_BYTES + 1))


def test_a_validate_hook_checks_the_value_as_stored() -> None:
    """``ParamSpec.validate`` (T-013) runs after the kind's own checks, on the value as it is
    stored: it returns the value to keep, or it raises a ``ValidationError`` that names the
    problem. The check is skipped only with ``validate=False`` (the undo of a set)."""

    def even(node, value):  # node: stokes.model.Node, value: the stored JSON value
        if value not in (0, 2, 4):
            raise ValidationError(f"{node.path}: the data holds {value!r}, not one of 0, 2, 4")
        return value

    registry = NodeTypeRegistry()
    register_builtin_types(registry)
    registry.register(
        NodeSpec(
            "hooked",
            "Hooked",
            params=(ParamSpec("data", "json", default=0, validate=even),),
            doc="Test node: one json parameter with a structural check.",
        ),
        replace=True,
    )
    project = Project.new("demo", node_registry=registry)
    holder = project.add("/geometry", "hooked", "holder")
    project.set_param(holder.id, "data", 2)
    assert holder.params["data"] == 2
    with pytest.raises(ValidationError, match="not one of 0, 2, 4"):
        project.set_param(holder.id, "data", 3)
    assert holder.params["data"] == 2
    project.set_param(holder.id, "data", 1, validate=False)  # the undo of a set skips the check
    assert holder.params["data"] == 1
