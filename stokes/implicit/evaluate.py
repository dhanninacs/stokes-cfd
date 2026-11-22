"""The numpy evaluator of an implicit graph (ADR-0035 decisions 11 and 27).

It evaluates a graph at many points in float64. It is far slower than the mesher, and it is the
reference: tests compare the mesher with it, checks (wall thickness, leaks, a point inside) use it
without meshing, and the shape derivatives of decision 27 are central differences of it.

Semantics match the mesher's: ``mod`` is the Euclidean remainder (the result has the sign of the
period), ``sqrt`` of a negative argument is 0, ``atan2(y, x)`` is the two-argument arctangent.

Import rule: numpy inside the functions (the package's rule).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stokes.implicit.graph import Expr, walk

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    F64 = npt.NDArray[np.float64]

CHUNK = 200_000
"""Points evaluated at a time: bounds the memory of one node's values (1.6 MB per node)."""


def evaluate(root: Expr, points: Any, chunk: int = CHUNK) -> F64:  # Any: array-like (n, 3)
    """The field at each of ``points`` (an (n, 3) array of x, y, z in kernel units)."""
    import numpy as np

    order = list(walk(root))
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    out = np.empty(len(pts), dtype=np.float64)
    binary: dict[str, Callable[..., Any]] = {
        "add": np.add,
        "sub": np.subtract,
        "mul": np.multiply,
        "div": np.divide,
        "min": np.minimum,
        "max": np.maximum,
        "mod": np.mod,  # numpy's mod is the remainder with the sign of the divisor
        "atan2": np.arctan2,
    }
    unary: dict[str, Callable[..., Any]] = {
        "neg": np.negative,
        "abs": np.abs,
        "square": np.square,
        "sqrt": lambda a: np.sqrt(np.maximum(a, 0.0)),
        "sin": np.sin,
        "cos": np.cos,
    }
    with np.errstate(divide="ignore", invalid="ignore"):
        for start in range(0, len(pts), max(1, int(chunk))):
            part = pts[start : start + chunk]
            values: dict[int, Any] = {}  # Any: float64 array or float
            for node in order:
                result: Any  # Any: float64 array or float
                if node.op == "var-x":
                    result = part[:, 0]
                elif node.op == "var-y":
                    result = part[:, 1]
                elif node.op == "var-z":
                    result = part[:, 2]
                elif node.op == "const":
                    result = node.value
                elif node.op in binary:
                    result = binary[node.op](values[id(node.args[0])], values[id(node.args[1])])
                else:
                    result = unary[node.op](values[id(node.args[0])])
                values[id(node)] = result
            out[start : start + len(part)] = np.broadcast_to(values[id(root)], (len(part),))
    return out


def gradient(root: Expr, points: Any, step: float = 1e-4) -> F64:  # Any: array-like (n, 3)
    """The field's gradient at ``points`` by central differences of ``step`` (kernel units), as an
    (n, 3) array. Used by checks and by the shape derivatives of decision 27."""
    import numpy as np

    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    grad = np.empty_like(pts)
    for axis in range(3):
        offset = np.zeros(3)
        offset[axis] = step
        grad[:, axis] = (evaluate(root, pts + offset) - evaluate(root, pts - offset)) / (2 * step)
    return grad
