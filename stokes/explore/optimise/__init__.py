"""Optimisation drivers (ADR-0016): the ``Evaluator`` protocol, single-objective drivers on scipy
(differential evolution, Nelder-Mead, COBYLA), multi-objective NSGA-II and NSGA-III through
pymoo, and a surrogate-assisted loop, all behind one generation-callback protocol.

``study.optimise`` wraps the run queue in an ``Evaluator``, picks a driver, passes a callback that
turns every ``Generation`` into a ``study.generation`` event (returning ``True`` from it cancels
the run) and stores the ``OptimiseResult``: the evaluated points, the Pareto set, the convergence
and hypervolume histories and the final population.

Import rule: numpy only at module level; scipy and pymoo are imported by the drivers that use them.
"""

from __future__ import annotations

from stokes.explore.optimise.assisted import propose_candidates, surrogate_assisted
from stokes.explore.optimise.multi import nsga2, nsga3, reference_directions
from stokes.explore.optimise.single import cobyla, differential_evolution, nelder_mead
from stokes.explore.optimise.types import (
    CallableEvaluator,
    Evaluator,
    Generation,
    GenerationCallback,
    OptimiseResult,
)

__all__ = [
    "CallableEvaluator",
    "Evaluator",
    "Generation",
    "GenerationCallback",
    "OptimiseResult",
    "cobyla",
    "differential_evolution",
    "nelder_mead",
    "nsga2",
    "nsga3",
    "propose_candidates",
    "reference_directions",
    "surrogate_assisted",
]
