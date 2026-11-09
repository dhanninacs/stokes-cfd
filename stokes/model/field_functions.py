"""The `field_function` node type: a named custom field (Fluent's *custom field functions*).

A field function is a name and an expression over a run's fields, e.g. ``q = 0.5 * U_mag^2`` or
``p_total = p + 0.5 * U_mag^2``. It is **model state**, saved with the project, so a report or a
picture that uses it comes back the same when the project is reopened - which is why it is a node
and not a setting of the front end. The expression is evaluated on demand at the points of a
surface by `stokes.post.calculator`, through the one expression grammar (ADR-0012).

Functions live at `/field_functions/<id-name>` in a group the first `results.function_save` makes in
its own undo step, as `/views` is made. The name is the parameter `name` (an identifier, so another
expression can read it); the tree name comes from the id.

Registration: `stokes.model.schema.register_builtin_types` calls `register_field_function_types`.
Import rule: the standard library at module level.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from stokes.errors import ValidationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.model.schema import NodeSpec, NodeTypeRegistry

FIELD_FUNCTION_TYPE = "field_function"
FIELD_FUNCTIONS_GROUP = "field_functions"
FIELD_FUNCTIONS_PATH = f"/{FIELD_FUNCTIONS_GROUP}"

#: A function's name is an identifier, so an expression can use it like a field.
NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_]{0,39}"
#: The longest definition: the expression grammar's own limit is 2000 characters.
MAX_DEFINITION = 2000

_NAME = re.compile(rf"^{NAME_PATTERN}$")

#: Names a function may not take: the coordinates, the density and the constants are given.
RESERVED: frozenset[str] = frozenset({"x", "y", "z", "rho", "pi", "e"})


def clean_name(name: str) -> str:
    """A function's name as stored: trimmed, an identifier, not a reserved name."""
    text = name.strip()
    if not _NAME.match(text):
        raise ValidationError(
            "a field function's name is a letter or _ followed by letters, digits or _ "
            "(at most 40), e.g. p_total",
            code="field_function.name_invalid",
            details={"name": name},
        )
    if text in RESERVED:
        raise ValidationError(
            f"{text!r} is a name the calculator already gives (x, y, z, rho, pi, e)",
            code="field_function.name_invalid",
            details={"name": text},
        )
    return text


def field_function_specs() -> tuple[NodeSpec, ...]:
    """The `field_function` type, built on call like the other optional types."""
    from stokes.model.schema import NodeSpec, ParamSpec

    return (
        NodeSpec(
            FIELD_FUNCTION_TYPE,
            "Field function",
            params=(
                ParamSpec(
                    "name",
                    "string",
                    label="Name",
                    default="f",
                    pattern=NAME_PATTERN,
                    doc="What the function is called; reports, plots and other functions use "
                    "this name.",
                ),
                ParamSpec(
                    "definition",
                    "string",
                    label="Definition",
                    default="U_mag",
                    doc="The expression, over the run's fields (U_mag, p, U_x...), the "
                    "coordinates x, y, z, rho and constants with units in brackets.",
                ),
            ),
            doc="A custom field: a name for an expression over a run's fields. It lives at "
            "/field_functions/<id>.",
        ),
    )


def register_field_function_types(target: NodeTypeRegistry) -> None:
    """Register the field function node type (idempotent)."""
    for spec in field_function_specs():
        target.register(spec, replace=True)
