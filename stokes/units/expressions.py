"""Unit-checked expressions over named quantities (ADR-0012).

``evaluate("0.5 * rho * U_in**2", resolver, expect=dimension("pressure"))``
parses with the grammar in ``grammar.lark``, resolves names through
``resolver``, computes with pint quantities and checks the result's dimension.
No ``eval`` anywhere; the grammar is the only entry point.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from stokes.errors import ExpressionError, UnitMismatch, ValidationError
from stokes.units.dimensions import Dimension
from stokes.units.quantities import check_dimension, quantity
from stokes.units.registry import Quantity, ureg

Resolver = Callable[[str], Quantity]
"""Maps a dotted reference such as ``U_in`` or ``regions.fluid.inlet.velocity`` to a quantity."""

_MAX_LENGTH = 2000
_MAX_DEPTH = 64


def _dimensionless(q: Quantity, what: str) -> float:
    if not q.dimensionless:
        raise UnitMismatch(f"{what} needs a dimensionless argument, got {q.units}")
    return float(q.to("dimensionless").magnitude)


def _angle(q: Quantity, what: str) -> float:
    if q.dimensionless:
        return float(q.to("dimensionless").magnitude)
    try:
        return float(q.to("rad").magnitude)
    except Exception as exc:
        raise UnitMismatch(f"{what} needs an angle or a plain number, got {q.units}") from exc


def _same(args: Iterable[Quantity], what: str) -> list[Quantity]:
    items = list(args)
    if not items:
        raise ExpressionError(f"{what} needs at least one argument")
    first = items[0]
    for other in items[1:]:
        if other.dimensionality != first.dimensionality:
            raise UnitMismatch(
                f"{what} arguments differ in dimension: {first.units} vs {other.units}"
            )
    return items


def _fn_sqrt(x: Quantity) -> Quantity:
    return x**0.5


def _fn_abs(x: Quantity) -> Quantity:
    return abs(x)


def _fn_min(*xs: Quantity) -> Quantity:
    return min(_same(xs, "min"))


def _fn_max(*xs: Quantity) -> Quantity:
    return max(_same(xs, "max"))


def _fn_clamp(x: Quantity, lo: Quantity, hi: Quantity) -> Quantity:
    _same((x, lo, hi), "clamp")
    return max(lo, min(x, hi))


def _fn_sin(x: Quantity) -> Quantity:
    return ureg().Quantity(math.sin(_angle(x, "sin")))


def _fn_cos(x: Quantity) -> Quantity:
    return ureg().Quantity(math.cos(_angle(x, "cos")))


def _fn_tan(x: Quantity) -> Quantity:
    return ureg().Quantity(math.tan(_angle(x, "tan")))


def _fn_exp(x: Quantity) -> Quantity:
    return ureg().Quantity(math.exp(_dimensionless(x, "exp")))


def _fn_log(x: Quantity) -> Quantity:
    return ureg().Quantity(math.log(_dimensionless(x, "log")))


def _fn_log10(x: Quantity) -> Quantity:
    return ureg().Quantity(math.log10(_dimensionless(x, "log10")))


EXPRESSION_FUNCTIONS: dict[str, Callable[..., Quantity]] = {
    "sqrt": _fn_sqrt,
    "abs": _fn_abs,
    "min": _fn_min,
    "max": _fn_max,
    "clamp": _fn_clamp,
    "sin": _fn_sin,
    "cos": _fn_cos,
    "tan": _fn_tan,
    "exp": _fn_exp,
    "log": _fn_log,
    "log10": _fn_log10,
}

CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}


@cache
def _parser() -> Any:  # Any: lark.Lark
    from lark import Lark

    grammar = (Path(__file__).parent / "grammar.lark").read_text(encoding="utf-8")
    return Lark(grammar, parser="lalr", maybe_placeholders=True)


@lru_cache(maxsize=4096)
def _parse_cached(text: str) -> Any:  # Any: lark.Tree
    from lark.exceptions import UnexpectedInput

    try:
        return _parser().parse(text)
    except UnexpectedInput as exc:
        raise ExpressionError(
            f"cannot parse expression {text!r} at column {exc.column}: "
            "units belong in square brackets, e.g. 2 [m/s]",
            details={"text": text, "column": exc.column},
        ) from exc


def parse(text: str) -> Any:  # Any: lark.Tree
    """Parse to a tree (cached; trees are never mutated); typed error on failure."""
    if len(text) > _MAX_LENGTH:
        raise ExpressionError("expression too long", details={"length": len(text)})
    return _parse_cached(text)


_eval_class: type | None = None


def _evaluator_class() -> type:
    """Build the transformer class once; instances carry the resolver."""
    global _eval_class  # noqa: PLW0603 - module-level cache
    if _eval_class is not None:
        return _eval_class
    from lark import Token, Transformer, v_args

    registry = ureg()

    @v_args(inline=True)
    class Eval(Transformer):  # type: ignore[type-arg]  # lark ships no stubs
        def __init__(self, resolver: Resolver) -> None:
            super().__init__()
            self.resolver = resolver

        def number(self, token: Token) -> Quantity:
            return registry.Quantity(float(token))

        def quantity(self, number: Token, unit: Token) -> Quantity:
            try:
                q = quantity(float(number), str(unit).strip())
            except ValidationError as exc:
                raise ExpressionError(exc.message, details=exc.details) from exc
            # Base units at once: no offset-unit ambiguity (40 [degC] + 10 [K] is 323.15 K).
            return q.to_base_units()

        def path(self, *names: Token) -> str:
            return ".".join(str(n) for n in names)

        def ref(self, name: str) -> Quantity:
            if name in CONSTANTS:
                return registry.Quantity(CONSTANTS[name])
            try:
                value = self.resolver(name)
            except ExpressionError:
                raise
            except KeyError as exc:
                raise ExpressionError(f"unknown name {name!r}", details={"name": name}) from exc
            return value

        def args(self, *items: Quantity) -> tuple[Quantity, ...]:
            return items

        def call(self, name: Token, args: tuple[Quantity, ...] | None) -> Quantity:
            fn = EXPRESSION_FUNCTIONS.get(str(name))
            if fn is None:
                raise ExpressionError(
                    f"unknown function {name!s}(); known: {', '.join(sorted(EXPRESSION_FUNCTIONS))}"
                )
            try:
                return fn(*(args or ()))
            except TypeError as exc:
                raise ExpressionError(f"wrong number of arguments for {name!s}()") from exc

        def add(self, a: Quantity, b: Quantity) -> Quantity:
            return _combine(a, b, "+", lambda x, y: x + y)

        def sub(self, a: Quantity, b: Quantity) -> Quantity:
            return _combine(a, b, "-", lambda x, y: x - y)

        def mul(self, a: Quantity, b: Quantity) -> Quantity:
            return a * b

        def div(self, a: Quantity, b: Quantity) -> Quantity:
            if float(b.magnitude) == 0.0:
                raise ExpressionError("division by zero")
            return a / b

        def pow(self, a: Quantity, _op: Token, b: Quantity) -> Quantity:
            exponent = _dimensionless(b, "exponent")
            return a**exponent

        def neg(self, a: Quantity) -> Quantity:
            return -a

        def pos(self, a: Quantity) -> Quantity:
            return a

    _eval_class = Eval
    return Eval


def _evaluate_tree(tree: Any, resolver: Resolver) -> Quantity:  # Any: lark.Tree
    return _evaluator_class()(resolver).transform(tree)


def _combine(
    a: Quantity, b: Quantity, op: str, fn: Callable[[Quantity, Quantity], Quantity]
) -> Quantity:
    if a.dimensionality != b.dimensionality:
        raise UnitMismatch(
            f"cannot apply {op} to {a.units} and {b.units}",
            details={"left": str(a.units), "right": str(b.units)},
        )
    from pint.errors import OffsetUnitCalculusError

    try:
        return fn(a, b)
    except OffsetUnitCalculusError:
        # Resolved values may still carry offset units; combine on the absolute scale.
        return fn(a.to_base_units(), b.to_base_units())


def evaluate(text: str, resolver: Resolver, expect: Dimension | None = None) -> Quantity:
    """Evaluate ``text``; check the result against ``expect`` when given."""
    tree = parse(text)
    from lark.exceptions import VisitError

    try:
        result = _evaluate_tree(tree, resolver)
    except VisitError as exc:
        cause = exc.orig_exc
        if isinstance(cause, ValidationError):
            raise cause from None
        raise ExpressionError(f"cannot evaluate {text!r}: {cause}") from cause
    if expect is not None:
        check_dimension(result, expect)
    return result


def references(text: str) -> list[str]:
    """Dotted names referenced by ``text`` (constants excluded), in order, unique."""
    tree = parse(text)
    found: list[str] = []
    for node in tree.iter_subtrees():
        if node.data == "path":
            name = ".".join(str(t) for t in node.children)
            if name not in CONSTANTS and name not in found:
                found.append(name)
    return found


def validate(text: str) -> None:
    """Parse only; raise :class:`ExpressionError` for syntax errors."""
    parse(text)
