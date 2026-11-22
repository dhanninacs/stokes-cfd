"""The expression graph of implicit geometry (ADR-0035 decision 4).

An :class:`Expr` is a node of a graph over the coordinates x, y and z. A body's field is an
expression that is negative inside the body and positive outside. The set of operations is closed:
the graph is built by the feature builders only, never parsed from user text and never
``eval``-ed. Nodes are immutable and compared by identity, so a sub-graph shared by several parents
(a coordinate leaf, a repeated feature) is one object and is written once.

:func:`to_text` writes a graph in the mesher's text format (Fidget's: one ``name op args`` line per
node, dependencies first, the root last); :func:`check` applies the limits of decision 4.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

from stokes.implicit.errors import ImplicitInvalid, ImplicitTooLarge

Number = int | float

MAX_NODES = 100_000
"""Nodes in one graph (decision 4): labelling and gradients cost grows with it."""
MAX_CONSTANT = 1e9
"""Largest magnitude of a constant, in kernel units (mm)."""
TEXT_VERSION = "stokes-implicit-text/1"
"""The version of :func:`to_text`'s output; part of every cache key and of a part's basis."""

UNARY: tuple[str, ...] = ("neg", "abs", "square", "sqrt", "sin", "cos")
BINARY: tuple[str, ...] = ("add", "sub", "mul", "div", "min", "max", "mod", "atan2")
LEAVES: tuple[str, ...] = ("var-x", "var-y", "var-z", "const")
OPS: frozenset[str] = frozenset(UNARY + BINARY + LEAVES)

_TEXT_NAME = {op: op for op in OPS}
"""Our operation names to the mesher's: the same names, Fidget's text format (`Context::from_text`).
Checked against Fidget 0.5.1's parser: ``atan2`` takes y then x, and ``atan`` (one argument) is not
used."""


@dataclass(frozen=True, eq=False, repr=False)
class Expr:
    """One node: an operation, its arguments, and a value for ``const``."""

    op: str
    args: tuple[Expr, ...] = ()
    value: float = 0.0

    def __repr__(self) -> str:  # shallow: a graph may be deeper than the recursion limit
        if self.op == "const":
            return f"Expr(const {self.value!r})"
        return f"Expr({self.op}, {len(self.args)} argument(s))"

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ImplicitInvalid(f"unknown operation {self.op!r}", details={"op": self.op})
        arity = 0 if self.op in LEAVES else 1 if self.op in UNARY else 2
        if len(self.args) != arity:
            raise ImplicitInvalid(
                f"{self.op} takes {arity} argument(s), not {len(self.args)}",
                details={"op": self.op},
            )

    # arithmetic -------------------------------------------------------------------------------
    def __add__(self, other: Expr | Number) -> Expr:
        return Expr("add", (self, lift(other)))

    def __radd__(self, other: Number) -> Expr:
        return Expr("add", (lift(other), self))

    def __sub__(self, other: Expr | Number) -> Expr:
        return Expr("sub", (self, lift(other)))

    def __rsub__(self, other: Number) -> Expr:
        return Expr("sub", (lift(other), self))

    def __mul__(self, other: Expr | Number) -> Expr:
        return Expr("mul", (self, lift(other)))

    def __rmul__(self, other: Number) -> Expr:
        return Expr("mul", (lift(other), self))

    def __truediv__(self, other: Expr | Number) -> Expr:
        return Expr("div", (self, lift(other)))

    def __neg__(self) -> Expr:
        return Expr("neg", (self,))

    def abs(self) -> Expr:
        return Expr("abs", (self,))

    def square(self) -> Expr:
        return Expr("square", (self,))

    def sqrt(self) -> Expr:
        return Expr("sqrt", (self,))

    def sin(self) -> Expr:
        return Expr("sin", (self,))

    def cos(self) -> Expr:
        return Expr("cos", (self,))

    def mod(self, period: Expr | Number) -> Expr:
        """The Euclidean remainder: the result is in [0, period)."""
        return Expr("mod", (self, lift(period)))


def lift(value: Expr | Number) -> Expr:
    """A node for ``value``: the node itself, or a constant."""
    if isinstance(value, Expr):
        return value
    number = float(value)
    if not math.isfinite(number):
        raise ImplicitInvalid(
            "a constant of the graph is not finite", details={"value": repr(value)}
        )
    if abs(number) > MAX_CONSTANT:
        raise ImplicitInvalid(
            f"a constant of the graph is beyond ±{MAX_CONSTANT:g}", details={"value": number}
        )
    return Expr("const", value=number)


def minimum(a: Expr, b: Expr) -> Expr:
    return Expr("min", (a, b))


def maximum(a: Expr, b: Expr) -> Expr:
    return Expr("max", (a, b))


def atan2(y: Expr, x: Expr) -> Expr:
    return Expr("atan2", (y, x))


X = Expr("var-x")
Y = Expr("var-y")
Z = Expr("var-z")


def walk(root: Expr) -> Iterator[Expr]:
    """Every node of the graph once, each after its arguments (dependency order). Iterative:
    builders nest deeper than Python's recursion limit allows."""
    seen: set[int] = set()
    stack: list[tuple[Expr, bool]] = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        if id(node) in seen:
            continue
        if node.args and not expanded:
            stack.append((node, True))
            stack.extend((arg, False) for arg in reversed(node.args) if id(arg) not in seen)
            continue
        seen.add(id(node))
        yield node


def node_count(root: Expr) -> int:
    """The number of distinct nodes, constants included."""
    return sum(1 for _ in walk(root))


def check(root: Expr) -> int:
    """Apply the node limit of decision 4 and return the node count. Constants are checked when
    they are made (:func:`lift`)."""
    count = 0
    for _ in walk(root):
        count += 1
        if count > MAX_NODES:
            raise ImplicitTooLarge(
                f"the part's field has more than {MAX_NODES:,} nodes",
                details={"limit": MAX_NODES},
            )
    return count


def to_text(root: Expr) -> str:
    """The graph in the mesher's text format: nodes in dependency order, named ``_<n>``, equal
    constants written once, the root last. A pure function: the same graph gives the same text."""
    names: dict[int, str] = {}
    constants: dict[float, str] = {}
    lines: list[str] = []
    for node in walk(root):
        if node.op == "const":
            value = node.value + 0.0  # -0.0 and 0.0 are one constant
            name = constants.get(value)
            if name is None:
                name = f"_{len(lines)}"
                constants[value] = name
                lines.append(f"{name} const {value!r}")
            names[id(node)] = name
            continue
        name = f"_{len(lines)}"
        names[id(node)] = name
        lines.append(" ".join([name, _TEXT_NAME[node.op], *(names[id(a)] for a in node.args)]))
    # The root is the last node walked, so its line is the last; a shared constant cannot be the
    # root of a graph that has other nodes, because the root depends on everything else.
    return "\n".join(lines) + "\n"
