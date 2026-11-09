"""Resolve plain-arithmetic `#calc` in an imported case's copy, so it can be staged (ADR-0033).

## Why

`#calc "expr"` makes OpenFOAM compile and run C++ while it reads a dictionary, which is why the
`external` lint refuses it (ADR-0029). Most tutorials use it for nothing more than arithmetic on
numbers defined a few lines above - the ballValve mesh's `rBend #calc "sqrt(scalar($rBall*$rBall -
$rPipe*$rPipe))";` - and refusing those cases outright left them unloadable.

## What it does

In the project's own copy of a case (never the source), every `#calc "expr"` whose expression is
arithmetic over numbers and over earlier plain-number entries of the same file is replaced by its
value, computed by Stokes' own expression grammar (`stokes.units.expressions`: no `eval`, no code).
Anything else - a cross-file reference (`$blockMeshDict.x!y`), a vector function (`cmptProduct`),
a name the file does not define as a number - is left as it was, and the lint then refuses the file
exactly as before. So this only ever removes a `#calc` it has fully evaluated.

`$name`, `${name}` and `$<scalar>name` / `$<label>name` are references; `scalar(x)` and `label(x)`
are casts and read as `(x)`; `pow(a, b)` is `(a)**(b)`. A result that is a whole number is written
as one, because blockMesh reads cell counts as labels.

Import rules: standard library and `stokes.units`. Headless.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

#: A `#calc` with its quoted expression.
_CALC_RE = re.compile(r'#calc\s+"(?P<expr>[^"\n]*)"')
#: A plain-number entry, `name  1.5e-3;`, anywhere in the file (first word on its line).
_NUMBER_ENTRY_RE = re.compile(
    r"^[ \t]*(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]+"
    r"(?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)[ \t]*;",
    re.MULTILINE,
)
#: The name a `#calc` entry defines: the word before `#calc` on its line.
_ENTRY_NAME_RE = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]+$")
#: `$name`, `${name}`, `$<type>name`, `$<type>{name}`; a name with `.`/`!`/`/` is not matched whole.
_REF_RE = re.compile(
    r"\$(?:<[a-z]+>)?(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))"
)
_CAST_RE = re.compile(r"\b(?:scalar|label)\(")
_SAFE_RE = re.compile(r"^[A-Za-z0-9_+\-*/().,\s]*$")


@dataclass
class Resolved:
    """The rewritten text and what happened to each `#calc`."""

    text: str
    resolved: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)


def _pow_to_power(expr: str) -> str | None:
    """`pow(a, b)` -> `((a)**(b))`, innermost first; None when the brackets do not balance."""
    while (at := expr.find("pow(")) >= 0:
        depth, comma, end = 0, -1, -1
        for index in range(at + 3, len(expr)):
            char = expr[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
            elif char == "," and depth == 1 and comma < 0:
                comma = index
        if end < 0 or comma < 0:
            return None
        base, exponent = expr[at + 4 : comma], expr[comma + 1 : end]
        expr = f"{expr[:at]}(({base})**({exponent})){expr[end + 1 :]}"
    return expr


def _translate(expr: str, numbers: dict[str, float]) -> str | None:
    """The expression in Stokes' grammar, or None when it is not arithmetic over ``numbers``."""
    missing = False

    def ref(match: re.Match[str]) -> str:
        nonlocal missing
        name = match.group("braced") or match.group("bare")
        after = expr[match.end() : match.end() + 1]
        # `.` and `!` reach into another dictionary (`$blockMeshDict.cylinder!x0b`); a `/` after a
        # name is division here, never a path.
        if name not in numbers or after in (".", "!", "{"):
            missing = True
            return name
        return f"_v_{name}"

    out = _REF_RE.sub(ref, expr)
    if missing or "$" in out:
        return None
    out = _CAST_RE.sub("(", out)
    powered = _pow_to_power(out)
    if powered is None or not _SAFE_RE.match(powered):
        return None
    return powered


def _evaluate(expr: str, numbers: dict[str, float]) -> float | None:
    from stokes.errors import StokesError
    from stokes.units.expressions import evaluate
    from stokes.units.registry import ureg

    def resolver(name: str) -> Any:  # Any: a pint Quantity, as the grammar's Resolver returns
        if not name.startswith("_v_") or name[3:] not in numbers:
            raise KeyError(name)
        return ureg().Quantity(numbers[name[3:]])

    # Typed failures only (a name it does not know, a dimension, a domain error): each leaves the
    # #calc in place for the lint to refuse, which is the safe outcome.
    try:
        quantity = evaluate(expr, resolver)
        value = float(quantity.to_base_units().magnitude)
        if not quantity.dimensionless:
            return None
    except (StokesError, KeyError, ArithmeticError, ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _number_text(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def resolve_calc(text: str) -> Resolved:
    """Replace each fully evaluable `#calc` in ``text`` by its value; leave every other one."""
    out = Resolved(text=text)
    if "#calc" not in text:
        return out
    pieces: list[str] = []
    numbers: dict[str, float] = {}
    cursor = 0
    for match in _CALC_RE.finditer(text):
        before = text[cursor : match.start()]
        for entry in _NUMBER_ENTRY_RE.finditer(before):
            numbers[entry.group("name")] = float(entry.group("value"))
        expr = match.group("expr")
        translated = _translate(expr, numbers)
        value = None if translated is None else _evaluate(translated, numbers)
        if value is None:
            out.left.append(expr)
            pieces.append(text[cursor : match.end()])
        else:
            out.resolved.append(expr)
            pieces.append(before + _number_text(value))
            line_start = before.rfind("\n") + 1
            named = _ENTRY_NAME_RE.search(before[line_start:])
            if named is not None:
                numbers[named.group("name")] = value
        cursor = match.end()
    pieces.append(text[cursor:])
    out.text = "".join(pieces)
    return out
