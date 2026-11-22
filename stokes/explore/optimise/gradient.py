"""The gradient driver of shape optimisation (ADR-0038 decision 5): SLSQP or L-BFGS-B in the
variables' bounds, one evaluation (objective and gradient) per cycle.

A cycle of an adjoint optimisation is a primal and an adjoint solve, minutes each, so the driver
counts cycles rather than iterations, records every one (the point, J, dJ/dx and where the gradient
came from: ``adjoint``, ``finite_difference`` or ``exact``), and can be stopped between cycles. The
variables are scaled to the unit box, so a radius in metres and an angle in radians move in
comparable steps; the gradient is scaled with them.

Import rule: numpy at module level; scipy lazily.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from stokes.explore._imports import scipy_module
from stokes.explore.arrays import Floats
from stokes.explore.errors import Cancelled, InvalidInput

METHODS = ("SLSQP", "L-BFGS-B")


@dataclass(frozen=True)
class Evaluation:
    """What one cycle returns: J, dJ/dx (in the variables' own units) and its source."""

    objective: float
    gradient: Floats
    source: str
    detail: dict[str, Any] = field(default_factory=dict)  # Any: JSON, e.g. coverage, unverified


class GradientEvaluator(Protocol):
    def __call__(self, x: Floats) -> Evaluation: ...


@dataclass(frozen=True)
class Cycle:
    index: int
    x: tuple[float, ...]
    objective: float
    gradient: tuple[float, ...]
    source: str
    seconds: float
    detail: dict[str, Any]  # Any: JSON

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "index": self.index,
            "x": list(self.x),
            "objective": self.objective,
            "gradient": list(self.gradient),
            "gradient_norm": math.sqrt(sum(g * g for g in self.gradient)),
            "source": self.source,
            "seconds": self.seconds,
            **({"detail": self.detail} if self.detail else {}),
        }


@dataclass
class GradientResult:
    status: str
    """``converged``, ``target_reached``, ``gradient_disagrees``, ``max_cycles``, ``cancelled``
    or ``failed``."""
    message: str
    cycles: list[Cycle]

    @property
    def best(self) -> Cycle | None:
        return min(self.cycles, key=lambda c: c.objective) if self.cycles else None

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        best = self.best
        return {
            "status": self.status,
            "message": self.message,
            "cycles": [c.to_json() for c in self.cycles],
            "best": best.to_json() if best is not None else None,
        }


def optimise(
    evaluate: GradientEvaluator,
    x0: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
    *,
    method: str = "SLSQP",
    max_cycles: int = 20,
    tolerance: float = 1e-6,
    on_cycle: Callable[[Cycle], bool | None] | None = None,
    stop_when: Callable[[Cycle], bool] | None = None,
    secant: Callable[[Cycle, Cycle], bool] | None = None,
) -> GradientResult:
    """Minimise J from ``x0`` inside ``[lower, upper]``. ``on_cycle`` sees each cycle; returning
    True (or an evaluator raising :class:`Cancelled`) stops the run with what it has.
    ``stop_when`` ends the run as ``target_reached`` at the first cycle it accepts (the flow
    split's max |Q_i/Q - 1/N|, ADR-0040: a least-squares J flattens near its optimum, and a line
    search there chases noise). ``secant(first, second)`` is asked at the second cycle whether J
    moved against the first gradient; True ends the run as ``gradient_disagrees`` instead of
    spending the rest of the budget on a line search that a wrong gradient sends the wrong way."""
    if method not in METHODS:
        raise InvalidInput(f"method is one of {', '.join(METHODS)}; not {method!r}")
    lo = np.asarray(lower, dtype=np.float64)
    hi = np.asarray(upper, dtype=np.float64)
    start = np.asarray(x0, dtype=np.float64)
    if not (lo.shape == hi.shape == start.shape) or lo.ndim != 1 or not len(lo):
        raise InvalidInput("x0, lower and upper need one value per variable")
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and np.all(hi > lo)):
        raise InvalidInput("every variable needs finite bounds with lower < upper")
    if max_cycles < 1:
        raise InvalidInput("max_cycles must be at least 1")
    span = hi - lo
    cycles: list[Cycle] = []
    # **The objective is scaled by its first value.** SLSQP's ftol is absolute: a pressure loss of
    # 1e-7 m^5/s^3 looked converged after one cycle (measured, ADR-0038). Dividing J and its
    # gradient by |J| at the start gives every problem an objective of order one.
    scale: list[float] = []

    class _Stop(Exception):
        pass

    class _Reached(Exception):
        pass

    class _Disagrees(Exception):
        pass

    def fun(u: Floats) -> tuple[float, Floats]:
        if len(cycles) >= max_cycles:
            raise _Stop("max_cycles")
        x = lo + np.clip(u, 0.0, 1.0) * span
        began = time.monotonic()
        found = evaluate(x)
        gradient = np.asarray(found.gradient, dtype=np.float64).reshape(-1)
        if gradient.shape != x.shape or not math.isfinite(found.objective):
            raise InvalidInput("the evaluator returned no finite objective or a wrong gradient")
        cycle = Cycle(
            len(cycles),
            tuple(float(v) for v in x),
            float(found.objective),
            tuple(float(g) for g in gradient),
            found.source,
            time.monotonic() - began,
            dict(found.detail),
        )
        cycles.append(cycle)
        if on_cycle is not None and on_cycle(cycle):
            raise Cancelled("stopped")
        if stop_when is not None and stop_when(cycle):
            raise _Reached()
        if secant is not None and len(cycles) == 2 and secant(cycles[0], cycles[1]):
            raise _Disagrees()
        if not scale:
            scale.append(abs(float(found.objective)) or 1.0)
        return float(found.objective) / scale[0], gradient * span / scale[0]

    minimize = scipy_module("scipy.optimize").minimize
    u0 = np.clip((start - lo) / span, 0.0, 1.0)
    try:
        res = minimize(
            fun,
            u0,
            jac=True,
            method=method,
            bounds=[(0.0, 1.0)] * len(u0),
            options={"maxiter": max_cycles, "ftol": tolerance}
            if method == "SLSQP"
            else {"maxiter": max_cycles, "ftol": tolerance, "maxfun": max_cycles},
        )
    except _Stop:
        return GradientResult("max_cycles", f"stopped after {max_cycles} cycles", cycles)
    except _Reached:
        return GradientResult("target_reached", "the target was reached", cycles)
    except _Disagrees:
        return GradientResult(
            "gradient_disagrees",
            "J moved against the gradient between the first two cycles: the gradient is wrong "
            "for this case (check it with shape.check, or use finite differences)",
            cycles,
        )
    except Cancelled as exc:
        return GradientResult("cancelled", str(exc) or "stopped", cycles)
    status = "converged" if bool(res.success) else "failed"
    return GradientResult(status, str(res.message), cycles)
