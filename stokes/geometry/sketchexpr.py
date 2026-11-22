"""A drawn sketch's coordinates as expressions of its dimensions (ADR-0037).

A coordinate or a radius in an ``implicit_sketch``'s ``entities`` is a number (metres) or an
expression text in the grammar of :mod:`stokes.units.expressions` (``"W / 2"``,
``"-(H + 5 [mm])"``). Its names are the sketch's own ``dimensions``. A dimension may itself be an
expression of the project's parameters, so a sketch follows a parameter through its dimension,
and the dependency graph sees the whole chain: a parameter → the dimension's quantity → the
operation, which re-runs.

The texts are evaluated when the operation's recipe is built (:mod:`stokes.geometry.opplan`), so
the worker and the mesher receive numbers only. Headless: no Qt, VTK or OCP.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from stokes.errors import StokesError
from stokes.geometry.errors import OperationInvalid
from stokes.units.dimensions import dimension
from stokes.units.expressions import evaluate, references
from stokes.units.quantities import to_si
from stokes.units.registry import Quantity

POINT_KEYS = ("a", "b", "m", "c")
LENGTH = dimension("length")


def entity_names(entities: Any) -> set[str]:  # Any: JSON
    """Every name the expression texts of ``entities`` refer to."""
    found: set[str] = set()
    for text in _texts(entities):
        try:
            found.update(references(text))
        except StokesError:
            continue
    return found


def resolve_entities(
    entities: Any,  # Any: JSON entities
    dimensions: Mapping[str, Quantity],
) -> list[Any]:  # Any: JSON entities with numbers only
    """``entities`` with every expression text replaced by its value in metres.

    :class:`OperationInvalid` names the entity and the text of an expression that does not
    evaluate: an unknown name (the sketch has no such dimension), a quantity that is not a
    length, or a grammar error.
    """
    if not isinstance(entities, list):
        return entities  # type: ignore[no-any-return]  # the builder refuses it with its own words

    def resolver(ref: str) -> Quantity:
        if ref in dimensions:
            return dimensions[ref]
        raise OperationInvalid(
            f"the sketch has no dimension {ref!r}: add it to the sketch's dimensions",
            details={"name": ref},
        )

    out: list[Any] = []
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            out.append(entity)
            continue
        what = f"entity {index + 1}"
        out.append(_entity(entity, what, resolver))
    return out


def _entity(
    entity: dict[str, Any], what: str, resolver: Callable[[str], Quantity]
) -> dict[str, Any]:  # Any: JSON
    copy = dict(entity)
    for key in POINT_KEYS:
        if key in copy:
            copy[key] = _point(copy[key], f"{what}'s {key}", resolver)
    if "r" in copy:
        copy["r"] = _value(copy["r"], f"{what}'s radius", resolver)
    if isinstance(copy.get("points"), list):
        copy["points"] = [
            _point(p, f"{what}'s point {n + 1}", resolver) for n, p in enumerate(copy["points"])
        ]
    return copy


def _point(raw: Any, what: str, resolver: Callable[[str], Quantity]) -> Any:  # Any: JSON
    if isinstance(raw, list | tuple) and len(raw) == 2:
        return [_value(raw[0], f"{what} (u)", resolver), _value(raw[1], f"{what} (v)", resolver)]
    return raw


def _value(raw: Any, what: str, resolver: Callable[[str], Quantity]) -> Any:  # Any: JSON
    if not isinstance(raw, str):
        return raw
    try:
        return to_si(evaluate(raw, resolver, expect=LENGTH), LENGTH)
    except OperationInvalid as exc:
        raise OperationInvalid(f"{what}: {exc.message}", details=exc.details) from exc
    except StokesError as exc:
        raise OperationInvalid(
            f"{what}: {raw!r} is not a length ({exc.message})", details={"text": raw}
        ) from exc


def _texts(entities: Any) -> list[str]:  # Any: JSON
    texts: list[str] = []
    if not isinstance(entities, list):
        return texts
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        values: list[Any] = [entity.get(k) for k in POINT_KEYS] + [entity.get("r")]
        values += list(entity.get("points") or [])
        for value in values:
            items = value if isinstance(value, list | tuple) else [value]
            texts.extend(item for item in items if isinstance(item, str))
    return texts
