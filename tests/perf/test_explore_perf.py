"""Budget for the design-exploration numerics (docs/engineering/performance.md, ADR-0016).

A 10-variable, 1000-point Latin hypercube followed by a polynomial fit must stay interactive: a
study panel refits its surrogates as the table fills. The quadratic fit (66 terms) is the usual
response surface, the cubic one (286 terms) the largest the package builds for ten variables.
The best of three runs is compared with the target scaled by the machine factor, because a busy
CPU (linear algebra libraries spin when cores are shared) adds noise to any single run; the
targets are several times what a quiet reference machine needs, so they catch an algorithmic
regression, not scheduler noise.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest

from stokes.explore.domain import Range
from stokes.explore.sampling import latin_hypercube
from stokes.explore.space import Variable
from stokes.explore.surrogate import PolynomialSurrogate
from tests.perf.harness import HEADROOM, RESULTS, machine_factor


def response(points: np.ndarray) -> np.ndarray:
    """A smooth synthetic response with interactions, cheap to evaluate."""
    return np.sin(points).sum(axis=1) + points[:, 0] * points[:, 1] - 0.5 * points[:, 2] ** 2


@pytest.mark.perf
@pytest.mark.parametrize(("degree", "target_ms"), [(2, 80.0), (3, 800.0)])
def test_lhs_and_polynomial_fit_budget(degree: int, target_ms: float) -> None:
    variables = [Variable(f"x{i + 1}", Range(0.0, 1.0)) for i in range(10)]
    # Warm up: the first call imports scipy.stats.qmc and wakes the linear algebra libraries.
    warm = latin_hypercube(variables, 50, seed=0)
    PolynomialSurrogate(degree, ridge=1e-6).fit(warm.points, response(warm.points))

    timings: list[float] = []
    r2 = 0.0
    for repeat in range(3):
        start = time.perf_counter()
        table = latin_hypercube(variables, 1000, seed=repeat)
        model = PolynomialSurrogate(degree).fit(
            table.points, response(table.points), bounds=table.space.bounds
        )
        timings.append((time.perf_counter() - start) * 1000)
        r2 = float(model.diagnostics["r2_train"])  # type: ignore[arg-type]
    elapsed_ms = min(timings)
    limit_ms = target_ms * machine_factor() * HEADROOM
    with RESULTS.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "name": f"explore.lhs_1000x10_poly{degree}_fit",
                    "ms": round(elapsed_ms, 3),
                    "limit_ms": round(limit_ms, 3),
                }
            )
            + "\n"
        )
    assert r2 > 0.98, "the fit must be a real one"
    assert elapsed_ms <= limit_ms, (
        f"LHS 1000x10 plus degree-{degree} fit took {elapsed_ms:.0f} ms (best of 3), "
        f"budget {limit_ms:.0f} ms (target {target_ms:.0f} ms, "
        f"machine factor {machine_factor():.2f})"
    )
