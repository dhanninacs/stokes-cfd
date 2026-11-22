"""The implicit operations (ADR-0035 decision 7, slice 1): node parameters in SI to
:mod:`stokes.implicit` bodies in millimetres.

An implicit operation does not run through ``run(ctx)`` like the B-rep operations: its inputs are
not shapes but other implicit parts, and those are rebuilt from their own operations. The session
sends a **recipe** for every implicit input (:func:`stokes.geometry.opplan.implicit_recipe`): the
operation type, its SI parameters and the recipes of its own inputs and tools. :func:`body_of`
turns a recipe into a :class:`~stokes.implicit.features.Body`, and the worker's implicit job
(``stokes.geometry.worker_main._implicit_job``) meshes it.

A recipe is untrusted JSON: types, sizes and depth are checked here, and every number goes through
the builders' own checks.

Import rule: the standard library, :mod:`stokes.geometry.errors` and :mod:`stokes.implicit`; no
OCP.
"""

from __future__ import annotations

import math
from typing import Any

from stokes.geometry.errors import OperationInvalid
from stokes.implicit import features, modify
from stokes.implicit import frame as frames
from stokes.implicit.errors import ImplicitError
from stokes.implicit.features import Body

MM_PER_M = 1000.0
MAX_RECIPE_DEPTH = 64
"""Operations in one chain from a primitive to the part."""
MAX_RECIPE_OPERATIONS = 10_000
"""Operations in one recipe (each primitive used through several chains counts every time)."""


# Every operation type this module builds; anything else in a recipe is refused before its inputs
# are counted, so a stray type says what it is rather than how many parts it takes.
_TYPES = frozenset(
    {
        "implicit_primitive",
        "implicit_sketch",
        "implicit_boolean",
        "implicit_transform",
        "implicit_split",
        "implicit_offset",
        "implicit_pattern",
        "implicit_lattice",
    }
)


def body_of(recipe: Any) -> Body:  # Any: JSON recipe
    """The body a recipe describes, in millimetres (the output its ``output`` index names, the
    first when it names none); :class:`OperationInvalid` when it cannot be built."""
    budget = [MAX_RECIPE_OPERATIONS]
    try:
        return _build(recipe, 0, budget)
    except ImplicitError as exc:
        raise OperationInvalid(exc.message, details={**exc.details, "code": exc.code}) from exc


def bodies_of(recipe: Any) -> list[tuple[str, Body]]:  # Any: JSON recipe
    """Every output of the operation a recipe describes, with its name: one body for most
    operations, two for a split (ADR-0036 decision 5)."""
    budget = [MAX_RECIPE_OPERATIONS]
    try:
        return _outputs(recipe, 0, budget)
    except ImplicitError as exc:
        raise OperationInvalid(exc.message, details={**exc.details, "code": exc.code}) from exc


def _build(recipe: Any, depth: int, budget: list[int]) -> Body:  # Any: JSON recipe
    outputs = _outputs(recipe, depth, budget)
    index = recipe.get("output", 0) if isinstance(recipe, dict) else 0
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(outputs):
        raise OperationInvalid("an implicit recipe names an output its operation does not have")
    return outputs[index][1]


def _outputs(  # noqa: PLR0911 - one return per operation type
    recipe: Any, depth: int, budget: list[int]
) -> list[tuple[str, Body]]:  # Any: JSON recipe
    if depth > MAX_RECIPE_DEPTH:
        raise OperationInvalid(
            f"an implicit part is built from more than {MAX_RECIPE_DEPTH} chained operations"
        )
    budget[0] -= 1
    if budget[0] < 0:
        raise OperationInvalid(
            f"an implicit part is built from more than {MAX_RECIPE_OPERATIONS} operations"
        )
    if not isinstance(recipe, dict):
        raise OperationInvalid("an implicit recipe is not an object")
    op_type = recipe.get("type")
    params = recipe.get("params")
    if not isinstance(op_type, str) or not isinstance(params, dict):
        raise OperationInvalid("an implicit recipe has no type or parameters")
    if op_type not in _TYPES:
        raise OperationInvalid(
            f"{op_type!r} is not an implicit operation", details={"type": op_type}
        )
    inputs = [_build(r, depth + 1, budget) for r in _list(recipe, "inputs")]
    tools = [_build(r, depth + 1, budget) for r in _list(recipe, "tools")]
    name = (
        _choice(params, "kind", "") or _choice(params, "op", "") or _choice(params, "profile", "")
    )
    if op_type == "implicit_primitive":
        return [(name, primitive(params))]
    if op_type == "implicit_sketch":
        return [(name, sketch(params))]
    if op_type == "implicit_boolean":
        return [(name, boolean(params, inputs, tools))]
    if len(inputs) != 1 or tools:
        raise OperationInvalid(
            f"{op_type} takes exactly one implicit part", details={"type": op_type}
        )
    if op_type == "implicit_transform":
        return [(name, transform(params, inputs[0]))]
    if op_type == "implicit_split":
        below, above = modify.split(
            inputs[0], _three(params, "position"), _choice(params, "normal", "+z")
        )
        return [("below", below), ("above", above)]
    if op_type == "implicit_offset":
        return [(name, offset(params, inputs[0]))]
    if op_type == "implicit_pattern":
        return [(name, pattern(params, inputs[0]))]
    if op_type == "implicit_lattice":
        return [(name, lattice(params, inputs[0]))]
    raise AssertionError(f"{op_type} is in _TYPES but has no builder")  # pragma: no cover


def _list(recipe: dict[str, Any], key: str) -> list[Any]:  # Any: JSON recipes
    value = recipe.get(key, [])
    if not isinstance(value, list):
        raise OperationInvalid(f"the {key} of an implicit recipe are not a list")
    return value


# ------------------------------------------------------------------------------- values
def _mm(value: Any, name: str) -> float:  # Any: JSON number
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OperationInvalid(f"{name} is not a number", details={"parameter": name})
    number = float(value) * MM_PER_M
    if not math.isfinite(number):
        raise OperationInvalid(f"{name} is not finite", details={"parameter": name})
    return number


def _three(params: dict[str, Any], name: str) -> tuple[float, float, float]:  # Any: JSON
    raw = params.get(name)
    if not isinstance(raw, list | tuple) or len(raw) != 3:
        raise OperationInvalid(f"{name} must be three lengths", details={"parameter": name})
    a, b, c = (_mm(v, name) for v in raw)
    return a, b, c


def _number(params: dict[str, Any], name: str, default: float) -> float:  # Any: JSON
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise OperationInvalid(f"{name} is not a finite number", details={"parameter": name})
    return float(value)


def _whole(params: dict[str, Any], name: str, default: int) -> int:  # Any: JSON
    """A count: a whole number, stored as a dimensionless quantity (ADR-0037) or, in an older
    recipe, as an integer."""
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise OperationInvalid(f"{name} is not a whole number", details={"parameter": name})
    if not math.isfinite(value) or abs(value - round(value)) > 1e-9:
        raise OperationInvalid(f"{name} is not a whole number", details={"parameter": name})
    return round(value)


def _choice(params: dict[str, Any], name: str, default: str) -> str:  # Any: JSON
    value = params.get(name, default)
    if not isinstance(value, str):
        raise OperationInvalid(f"{name} is not text", details={"parameter": name})
    return value


# ------------------------------------------------------------------------------- operations
def primitive(params: dict[str, Any]) -> Body:  # noqa: PLR0911 - one return per kind  # Any: JSON
    """An ``implicit_primitive``: the kind's body at its position."""
    kind = _choice(params, "kind", "box")
    position = _three(params, "position")
    axis = _choice(params, "axis", "z")

    def mm(name: str, default: float) -> float:
        return _mm(params.get(name, default), name)

    if kind == "box":
        return features.box(_three(params, "size"), position)
    if kind == "rounded_box":
        return features.rounded_box(_three(params, "size"), position, mm("corner_radius", 0.001))
    if kind == "cylinder":
        return features.cylinder(mm("radius", 0.005), mm("length", 0.01), position, axis)
    if kind == "cone":
        return features.cone(
            mm("radius", 0.005), mm("length", 0.01), mm("top_radius", 0.0), position, axis
        )
    if kind == "sphere":
        return features.sphere(mm("radius", 0.005), position)
    if kind == "torus":
        return features.torus(mm("radius", 0.005), mm("tube_radius", 0.002), position, axis)
    if kind == "capsule":
        return features.capsule(mm("radius", 0.005), mm("length", 0.01), position, axis)
    if kind == "half_space":
        return features.half_space_block(
            position, _choice(params, "normal", "+z"), _three(params, "size")
        )
    raise OperationInvalid(f"unknown implicit primitive {kind!r}", details={"kind": kind})


def sketch(params: dict[str, Any]) -> Body:  # Any: JSON
    """An ``implicit_sketch``: a profile on a sketch plane, pulled or revolved."""
    from stokes.implicit import sketch as sketches

    def mm(name: str, default: float) -> float:
        return _mm(params.get(name, default), name)

    kind = _choice(params, "profile", "rectangle")
    if kind == "custom":
        from stokes.implicit.profile2d import custom_profile

        entities = params.get("entities", [])
        shape = custom_profile(entities, MM_PER_M)
    else:
        sides = _whole(params, "sides", 6)
        shape = sketches.named(
            kind,
            width=mm("width", 0.01),
            height=mm("height", 0.01),
            radius=mm("radius", 0.005),
            corner_radius=mm("corner_radius", 0.001),
            slot_length=mm("slot_length", 0.01),
            sides=sides,
        )
    plane = _choice(params, "plane", "xy")
    origin = _three(params, "origin")
    method = _choice(params, "method", "extrude")
    if method == "extrude":
        symmetric = params.get("symmetric", False)
        return sketches.extrude(shape, plane, origin, mm("depth", 0.01), symmetric is True)
    if method == "revolve":
        if kind != "custom":  # a named profile is centred: place it its distance from the axis
            shape = sketches.shifted(shape, mm("axis_distance", 0.02))
        return sketches.revolve(shape, plane, origin, _number(params, "angle", sketches.FULL_TURN))
    raise OperationInvalid(f"unknown sketch method {method!r}", details={"method": method})


def transform(params: dict[str, Any], body: Body) -> Body:  # Any: JSON
    """An ``implicit_transform`` of one body."""
    kind = _choice(params, "kind", "translate")
    axis_name = _choice(params, "axis", "z")
    axis = frames.AXIS_VECTORS.get(axis_name)
    if axis is None:
        raise OperationInvalid(f"unknown axis {axis_name!r}", details={"axis": axis_name})
    if kind == "translate":
        motion = frames.translation(_three(params, "offset"))
    elif kind == "rotate":
        motion = frames.rotation(axis, _number(params, "angle", 0.0), _three(params, "centre"))
    elif kind == "mirror":
        motion = frames.mirror(axis, _three(params, "centre"))
    elif kind == "scale":
        motion = frames.uniform_scale(_number(params, "factor", 1.0), _three(params, "centre"))
    else:
        raise OperationInvalid(f"unknown implicit transform {kind!r}", details={"kind": kind})
    return features.transformed(body, motion)


def boolean(params: dict[str, Any], inputs: list[Body], tools: list[Body]) -> Body:  # Any: JSON
    """An ``implicit_boolean`` of the inputs and the tools: sharp, or with every seam rounded by
    ``blend_radius``."""
    op = _choice(params, "op", "union")
    blend = _mm(params.get("blend_radius", 0.0), "blend_radius")
    if blend < 0.0:
        raise OperationInvalid("a blend radius must not be negative")
    if not inputs:
        raise OperationInvalid("a boolean needs at least one implicit input part")
    if blend > 0.0:
        if op == "union":
            return modify.blended_union([*inputs, *tools], blend)
        if op == "intersection":
            return modify.blended_intersection([*inputs, *tools], blend)
        if op == "difference":
            if not tools:
                raise OperationInvalid("a difference needs at least one tool part")
            return modify.blended_difference(features.union(inputs), tools, blend)
    if op == "union":
        return features.union([*inputs, *tools])
    if op == "intersection":
        return features.intersection([*inputs, *tools])
    if op == "difference":
        if not tools:
            raise OperationInvalid("a difference needs at least one tool part")
        return features.difference(features.union(inputs), tools)
    raise OperationInvalid(f"unknown implicit boolean {op!r}", details={"op": op})


def offset(params: dict[str, Any], body: Body) -> Body:  # Any: JSON
    """An ``implicit_offset``: grow or shrink by a distance, or a shell of that thickness."""
    distance = _mm(params.get("distance", 0.001), "distance")
    if _choice(params, "kind", "offset") == "shell":
        return modify.shell(body, distance, params.get("outward") is True)
    if _choice(params, "kind", "offset") != "offset":
        raise OperationInvalid("an offset's kind is offset or shell")
    return modify.offset(body, distance)


def pattern(params: dict[str, Any], body: Body) -> Body:  # Any: JSON
    """An ``implicit_pattern``: copies along an axis, or about one."""
    count = _whole(params, "count", 3)
    axis = _choice(params, "axis", "x")
    kind = _choice(params, "kind", "linear")
    if kind == "linear":
        return modify.linear_pattern(body, axis, count, _mm(params.get("pitch", 0.01), "pitch"))
    if kind == "polar":
        angle = _number(params, "angle", 2 * math.pi)
        return modify.polar_pattern(body, axis, count, _three(params, "centre"), angle)
    raise OperationInvalid(f"unknown pattern {kind!r}", details={"kind": kind})


def lattice(params: dict[str, Any], body: Body) -> Body:  # Any: JSON
    """An ``implicit_lattice``: the body filled with a triply periodic minimal surface."""
    return modify.lattice(
        body,
        _choice(params, "kind", "gyroid"),
        _choice(params, "form", "sheet"),
        _mm(params.get("cell", 0.005), "cell"),
        _mm(params.get("thickness", 0.0005), "thickness"),
        _number(params, "bias", 0.0),
        _three(params, "origin"),
    )


def run(ctx: Any) -> Any:  # Any: OpContext in, OpResult out; never returns
    """The B-rep contract's entry point, which an implicit operation does not use: the worker
    sends implicit operations to its implicit job before it looks for ``run``."""
    raise OperationInvalid("an implicit operation runs through the worker's implicit job")
