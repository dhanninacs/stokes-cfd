"""The `view` node type: a saved view of a run's results (plan RESULTS-OVERHAUL §3.5, brief R4).

A saved view is the figure's settings under a name: the camera, the field, the range, the colour
map, the bands and the settings of each look (slice, iso, streamlines, arrows) and of the legend.
**It is the record the image export writes beside every picture** (`web/src/panels/exportModel.ts`
`buildSidecar`), so a view and a sidecar `.json` are the same thing and either re-applies the other.
The server does not read the record: it keeps it as it comes, caps its size and hands it back, so
the front end owns the shape and a later version of the shape never needs a migration here.

Views live at `/views/<id-name>`, one node each, in a `/views` group that the first `view.save`
creates in its own undo step (as `case.import` makes `/cases`), so a project that never saves a view
has no group and an undone first save leaves none behind. A node's tree name is generated from its
id (`v_3fa9c2d1`), because tree names are identifiers and a view's name is free text: the text is
the `name` parameter, and `view.save` replaces by it.

Registration: `stokes.model.schema.register_builtin_types` calls `register_view_types`. Import rule:
the standard library at module level (the model imports this module while registering, and
`import stokes.api` has a 300 ms budget); the schema is imported inside the functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stokes.errors import ValidationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.model.nodes import Node
    from stokes.model.schema import NodeSpec, NodeTypeRegistry

VIEW_TYPE = "view"
VIEWS_GROUP = "views"
VIEWS_PATH = f"/{VIEWS_GROUP}"

#: The longest view name. A name is a file name in "Export all" and a row in a menu.
MAX_NAME = 80
#: What a record may weigh when serialised. A figure with several slices is a few kilobytes.
MAX_RECORD_BYTES = 256 * 1024
#: A name has no control characters (a newline in a menu row or a file name is a bug waiting).
NAME_PATTERN = r"[^\x00-\x1f\x7f]{1,80}"
#: A timestamp as `Project`'s own: ISO 8601, or empty before the first save.
CREATED_PATTERN = r"[0-9T:+\-Z.]{0,40}"


def clean_name(name: str) -> str:
    """A view's name as stored: trimmed; refused when empty, too long or with control characters."""
    text = name.strip()
    if not text:
        raise ValidationError("a view needs a name", code="view.name_invalid")
    if len(text) > MAX_NAME:
        raise ValidationError(
            f"a view name is at most {MAX_NAME} characters",
            code="view.name_invalid",
            details={"max": MAX_NAME},
        )
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValidationError("a view name has no control characters", code="view.name_invalid")
    return text


def validate_record(node: Node, value: Any) -> Any:  # Any: JSON
    """`record` when written: a JSON object (the sidecar), kept as it is."""
    if not isinstance(value, dict):
        raise ValidationError(f"the record of {node.path} must be an object")
    return value


def view_specs() -> tuple[NodeSpec, ...]:
    """The `view` type, built on call like the other optional types."""
    from stokes.model.schema import NodeSpec, ParamSpec

    return (
        NodeSpec(
            VIEW_TYPE,
            "Saved view",
            params=(
                ParamSpec(
                    "name",
                    "string",
                    label="Name",
                    default="View",
                    pattern=NAME_PATTERN,
                    doc="What the view is called; saving a view with the same name replaces it.",
                ),
                ParamSpec(
                    "record",
                    "json",
                    label="Record",
                    default={},
                    max_bytes=MAX_RECORD_BYTES,
                    validate=validate_record,
                    hidden=True,
                    doc="The figure's settings: the sidecar object the image export writes "
                    "(camera, field, range, map, bands, looks, legend). Kept as written.",
                ),
                ParamSpec(
                    "created",
                    "string",
                    label="Saved",
                    default="",
                    pattern=CREATED_PATTERN,
                    doc="When the view was last saved (ISO 8601, UTC).",
                ),
            ),
            doc="A named, saved look at a run's results: camera, field, range, colour map and the "
            "settings of every look. It lives at /views/<id>.",
        ),
    )


def register_view_types(target: NodeTypeRegistry) -> None:
    """Register the view node type (idempotent, like the other built-in types)."""
    for spec in view_specs():
        target.register(spec, replace=True)
