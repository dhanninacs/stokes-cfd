"""No node type may show a raw JSON field on its page (Claude's package-two review item 5).

**The bug**: the tag page rendered `members`, so a user saw

    [{"part":"n_1364…","faces":[[2,2]],"basis":"2437…"}]

where a sentence belongs. It is worse than ugly - `[[2,2]]` is an **inclusive face-id range**, and
"3 of 6 faces" is what a person reads.

**The rule, and why it is about the TYPE rather than the text**: a field whose type is `json`
renders
through the generic field renderer, which prints the value verbatim. Strings and numbers are
editable
by a person; a blob is not. So every `json` parameter must either be **hidden** (the page shows
its own card, as the tag card does) or **have its own control**.

**A gate rather than a page fix, because the page was not the mistake**: `members` was declared
`json` and visible, and the next `json` parameter added would have reached a user the same way. This
is
the same shape as the user-text gate: the check is on the declaration, not on one rendering of it.
"""

from __future__ import annotations

import pytest

from stokes.model import registry


def json_param_names() -> list[tuple[str, str]]:
    """Every `(node type, parameter)` whose type is `json`, across the whole registry."""
    found: list[tuple[str, str]] = []
    for name in registry.types():
        spec = registry.get(name)
        schema = spec.json_schema().get("properties", {})
        for param, declared in schema.items():
            if declared.get("type") == "object" and "x-hidden" not in declared:
                # A `json` parameter's schema is `object` or `array` unless it declares one; either
                # way an unhidden one is what the renderer prints verbatim.
                found.append((name, param))
    return found


@pytest.mark.parametrize("name", registry.types())
def test_no_visible_json_parameter_on_a_node_type(name: str) -> None:
    """**The check the tag's `members` would have failed.**

    A `json` parameter that is not hidden reaches the page as a dump. It must either be hidden (the
    page shows a card that reads it properly) or declare a `hidden` flag - and the failure names the
    node type and the parameter, because that pair IS the fix.
    """
    spec = registry.get(name)
    schema = spec.json_schema().get("properties", {})
    offenders = [
        param
        for param, declared in schema.items()
        if declared.get("type") in ("object", "array")
        and "x-hidden" not in declared
        and _is_a_dump(declared)
    ]
    assert offenders == [], f"{name} shows a raw JSON field: {offenders}"


#: How many numbers a plain vector may hold before it stops being one. A direction, a bounding box
# and
#: a colour have three or six; a coefficient list or a table has as many as the fit produced.
VECTOR_LIMIT = 6


def _is_a_dump(declared: dict) -> bool:
    """True for a parameter the generic renderer would print as a BLOB.

    **The rule has to tell a vector from a dump, and my first version did not** - it flagged
    `solver_settings.slice_normal`, which is three floats and perfectly readable as `[0, 0, 1]`.
    That is the failure mode to look for BEFORE proposing a rule (the four over-broad `$`
    attempts here): a gate that refuses good code gets relaxed by the next person, and then it
    protects nothing.

    So: an object is always a dump; an array of numbers is a dump only when it is **longer than a
    vector**, which is what a coefficient list or a table is.
    """
    kind = declared.get("type")
    if kind == "object":
        return True
    if kind != "array":
        return False
    items = declared.get("items")
    if not isinstance(items, dict):
        # An array with no item schema: an object list, which is a dump whatever its length.
        return True
    if items.get("type") != "number":
        return True
    # **A short DEFAULT is what tells a vector from a fit.** Two versions of this rule were wrong
    # first: `maxItems` flagged `direction_vector` (three floats, no declared bound), and
    # `x-dimension` flagged `slice_normal` (three floats, no dimension marker - it is a direction,
    # so
    # it has none). What the two share is the thing the rule should ask: **the schema already shows
    # the value's shape in its default**, and a vector's default is a handful of numbers.
    #
    # A fit's coefficients have no default at all - they come from the data - which is exactly the
    # case that must be a dump.
    # **Two signals, and the corpus needed both.** Each of these three versions refused a real
    # vector, one after another, because the schemas do not spell "three numbers" the same way:
    #
    #   `slice_normal`      default [0, 0, 1], no dimension marker
    #   `direction_vector`  default (1, 0, 0) as a TUPLE, with a dimension marker
    #   `box`               default [] (empty until a mesh is configured), marker "length"
    #
    # So a dump is what NEITHER of them describes: no short default and no dimension. A fit's
    # coefficients have neither - they come from the data - which is the case that must be refused.
    if declared.get("x-dimension") is not None:
        return False
    default = declared.get("default")
    if isinstance(default, (list, tuple)) and 0 < len(default) <= VECTOR_LIMIT:
        return False
    limit = declared.get("maxItems")
    # SIM103's advice, taken: the last two checks are one expression, and writing them as a
    # negated condition says the rule in one place rather than as a chain of early returns.
    return not (limit is not None and int(limit) <= VECTOR_LIMIT)


def test_the_scan_finds_node_types_so_it_cannot_pass_vacuously() -> None:
    """**A guard that cannot fail is not a guard.**

    An empty registry would make the check above pass for the wrong reason, so this pins that
    the scan really looked at types and at their parameters.
    """
    names = list(registry.types())
    assert len(names) > 20, names
    with_params = [n for n in names if registry.get(n).json_schema().get("properties")]
    assert len(with_params) > 10, with_params
