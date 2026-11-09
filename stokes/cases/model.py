"""The `foam_case` node type: an imported OpenFOAM 14 case (ADR-0029 decision 1, plan T-307).

A case lives at `/cases/<name>`; `case.import` creates the `/cases` group in its own undo step
when the project lacks it, as `tag.create` does for `/geometry/tags`. Its parameters:

* `source` - where it came from: `{"kind": "install", "tutorial": "<id>"}` or
  `{"kind": "folder", "path": "<absolute path>"}`.
* `snapshot` - the sha256 of the content-addressed copy under
  `<project>.stokes/cases/<node id>/source/` (`stokes.cases.store`).
* `dialect` - `of14`: an imported case is dialect-bound by definition (ADR-0029, amending ADR-0008).
* `solver` - derived: a module name, a `regionSolvers` map, or a legacy application.
* `recipe` - derived: the argv steps (`stokes.cases.recipe.Recipe.to_json`).
* `overrides` - the ordered list of recorded setting edits (`stokes.cases.overrides`, T-308).

`source`, `snapshot`, `solver` and `recipe` are written by `case.import`; the contract is
`docs/engineering/case-schema.md`.

Registration: `stokes.model.schema.register_builtin_types` calls `register_case_types`. Import
rule: the standard library at module level (the model imports this module while registering, and
`import stokes.api` has a 300 ms budget); the schema is imported inside the functions.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from stokes.errors import ValidationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.model.nodes import Node
    from stokes.model.schema import NodeSpec, NodeTypeRegistry

CASE_TYPE = "foam_case"
CASES_GROUP = "cases"
CASES_PATH = f"/{CASES_GROUP}"
DIALECTS: tuple[str, ...] = ("of14",)
SOURCE_KINDS: tuple[str, ...] = ("install", "folder")
SNAPSHOT_PATTERN = r"([0-9a-f]{64})?"
MAX_RECIPE_BYTES = 1024 * 1024
MAX_OVERRIDES_BYTES = 1024 * 1024
_MAX_TEXT = 4096
_SNAPSHOT_RE = re.compile(r"[0-9a-f]{64}")


def validate_source(node: Node, value: Any) -> Any:  # Any: JSON
    """`source` when written: `{kind: install, tutorial}` or `{kind: folder, path}`, or null."""
    if value is None:
        return value
    where = f"the source of {node.path}"
    if not isinstance(value, dict) or value.get("kind") not in SOURCE_KINDS:
        raise ValidationError(f"{where} must be an object with kind 'install' or 'folder'")
    key = "tutorial" if value["kind"] == "install" else "path"
    if set(value) != {"kind", key}:
        raise ValidationError(f"{where} of kind {value['kind']!r} holds kind and {key} only")
    text = value[key]
    if not isinstance(text, str) or not text or len(text) > _MAX_TEXT or "\x00" in text:
        raise ValidationError(f"{where} needs {key} as text")
    return value


def validate_overrides(node: Node, value: Any) -> Any:  # Any: JSON
    """`overrides` when written: a list of `{file, path, value}` (`stokes.cases.overrides`)."""
    from stokes.cases.overrides import parse_list
    from stokes.compile.of14.external import OverrideError

    if not isinstance(value, list):
        raise ValidationError(f"the overrides of {node.path} must be a list")
    try:
        parse_list(value)
    except OverrideError as exc:
        raise ValidationError(
            f"an override of {node.path} is not valid: {exc.message}",
            code=exc.code,
            details=exc.details,
        ) from exc
    return value


def validate_file_edits(node: Node, value: Any) -> Any:  # Any: JSON
    """`file_edits` when written: a list of `{file, text}` with case-relative `file` paths."""
    from stokes.cases.overrides import check_file
    from stokes.compile.of14.external import OverrideError

    if not isinstance(value, list) or len(value) > 64:
        raise ValidationError(f"the file edits of {node.path} must be a list of at most 64")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"file", "text"}:
            raise ValidationError(f"a file edit of {node.path} is {{file, text}}")
        try:
            check_file(item["file"])
        except OverrideError as exc:
            raise ValidationError(exc.message, code=exc.code, details=exc.details) from exc
        text = item["text"]
        if not isinstance(text, str) or "\x00" in text or len(text.encode()) > 256 * 1024:
            raise ValidationError(f"a file edit of {node.path} needs text under 256 KB")
    return value


def validate_recipe(node: Node, value: Any) -> Any:  # Any: JSON
    """`recipe` when written: an object with a `steps` list, or empty before an import."""
    if not isinstance(value, dict) or not isinstance(value.get("steps", []), list):
        raise ValidationError(f"the recipe of {node.path} must be an object with a steps list")
    return value


def validate_solver(node: Node, value: Any) -> Any:  # Any: JSON
    """`solver` when written: a name, a `{region: module}` map, or null."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        return value
    raise ValidationError(f"the solver of {node.path} must be a name or a region map")


def is_snapshot(text: object) -> bool:
    """Whether ``text`` is a snapshot hash (64 lowercase hex characters)."""
    return isinstance(text, str) and _SNAPSHOT_RE.fullmatch(text) is not None


def case_specs() -> tuple[NodeSpec, ...]:
    """The `foam_case` type, built on call like the other optional types."""
    from stokes.model.schema import NodeSpec, ParamSpec

    return (
        NodeSpec(
            CASE_TYPE,
            "OpenFOAM case",
            params=(
                ParamSpec(
                    "source",
                    "json",
                    label="Source",
                    validate=validate_source,
                    doc='Where the case came from: {"kind": "install", "tutorial": <id>} or '
                    '{"kind": "folder", "path": <absolute path>}. Written by case.import.',
                ),
                ParamSpec(
                    "snapshot",
                    "string",
                    label="Snapshot",
                    default="",
                    pattern=SNAPSHOT_PATTERN,
                    doc="The sha256 of the case's read-only copy in the project.",
                ),
                ParamSpec(
                    "dialect",
                    "enum",
                    label="Dialect",
                    choices=DIALECTS,
                    default=DIALECTS[0],
                    doc="The OpenFOAM release whose files the case holds.",
                ),
                ParamSpec(
                    "solver",
                    "json",
                    label="Solver",
                    validate=validate_solver,
                    doc="Derived from system/controlDict: a solver module, a regionSolvers map "
                    "or a legacy application.",
                ),
                ParamSpec(
                    "recipe",
                    "json",
                    label="Recipe",
                    default={},
                    max_bytes=MAX_RECIPE_BYTES,
                    validate=validate_recipe,
                    hidden=True,
                    doc="Derived: the argv steps that run the case, from its Allrun or the "
                    "default [blockMesh, application]; lines not understood are listed.",
                ),
                ParamSpec(
                    "file_edits",
                    "json",
                    label="File edits",
                    default=[],
                    max_bytes=MAX_OVERRIDES_BYTES,
                    validate=validate_file_edits,
                    hidden=True,
                    doc="Whole-file text edits, [{file, text}] (case.file_write); each was "
                    "linted by the external profile when it was saved.",
                ),
                ParamSpec(
                    "overrides",
                    "json",
                    label="Overrides",
                    default=[],
                    max_bytes=MAX_OVERRIDES_BYTES,
                    validate=validate_overrides,
                    hidden=True,
                    doc="Recorded changes to the case's settings, in order.",
                ),
            ),
            doc="An OpenFOAM 14 case imported from a tutorial or a folder: an expert case type "
            "beside the engineer-facing model. It lives at /cases/<name>.",
        ),
    )


def register_case_types(target: NodeTypeRegistry) -> None:
    """Register the case node type (idempotent, like the other built-in types)."""
    for spec in case_specs():
        target.register(spec, replace=True)
