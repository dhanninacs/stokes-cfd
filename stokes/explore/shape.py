"""Shape optimisation of implicit parts (ADR-0038 decision 5): the evaluators the gradient driver
(`stokes.explore.optimise.gradient`) calls once per cycle.

- :class:`VolumeObjective` drives a part's volume to a target, J = (V / V_target - 1)², with the
  exact gradient from the shape derivative (ADR-0035 decision 27 with s = 1). It needs no CFD, which
  makes it the loop's test and a useful tool of its own (size a part to a volume).
- :class:`AdjointObjective` is the CFD cycle: rebuild and mesh the part, solve the primal and the
  adjoint, read J and the wall sensitivity, and sum dJ/dp on the part's wall faces
  (`stokes.geometry.sensitivity.wall_gradient`). Its objective is the pressure loss or, with the
  open-source adjoint (ADR-0041), the flow split, whose J is computed here from the flows. The
  solve is a callable the caller provides (the run layer, or a test), so this module stays free of
  jobs and hosts.
- :class:`FiniteDifferenceObjective` (ADR-0040) needs no adjoint: N + 1 ordinary solves per
  cycle, forward differences (backward at an upper bound), rows labelled ``finite_difference``.
  Its objective is the pressure loss or the **flow split** of :func:`flow_split`,
  J = sum_i (Q_i / Q - 1/N)^2 over the outlets, from the per-port flows the agent returns.

Each evaluation sets the variables on a private copy of the project; the live project never
changes until the user applies a result.

Imports: the model and units at module level; geometry, implicit and numpy lazily.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stokes.explore.errors import InvalidInput
from stokes.explore.optimise.gradient import Evaluation
from stokes.model.project import Project


@dataclass(frozen=True)
class Variable:
    """A quantity the optimiser moves, as an expression names it, and its bounds (SI)."""

    reference: str
    lower: float
    upper: float

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {"reference": self.reference, "lower": self.lower, "upper": self.upper}


def variables_from(raw: Sequence[Any]) -> list[Variable]:  # Any: JSON
    """Variables from ``[{"reference", "lower", "upper"}, ...]``; refuses a bad entry."""
    out: list[Variable] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("reference"), str):
            raise InvalidInput("a variable is {reference, lower, upper}")
        try:
            lower, upper = float(item["lower"]), float(item["upper"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidInput(f"{item['reference']}: lower and upper must be numbers") from exc
        if not lower < upper:
            raise InvalidInput(f"{item['reference']}: lower must be below upper")
        out.append(Variable(item["reference"], lower, upper))
    if not out:
        raise InvalidInput("name at least one variable")
    return out


@dataclass(frozen=True)
class CycleResult:
    """What one CFD cycle returns (``solve(project)``): the solver's objective (the pressure loss),
    the wall faces' centres, normals, areas and s (empty without an adjoint), each port's flow in
    m³/s (outward positive), which ports are outlets, and what the run says of itself."""

    objective: float
    centres: Any = None  # Any: (n, 3) float array
    normals: Any = None  # Any: (n, 3) float array
    areas: Any = None  # Any: (n,) float array
    values: Any = None  # Any: (n,) float array, s
    flows: Mapping[str, float] = field(default_factory=dict)
    outlets: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)  # Any: JSON

    def wall(self) -> tuple[Any, Any, Any, Any]:  # Any: numpy arrays
        """The wall arrays, as ``wall_gradient`` takes them."""
        return self.centres, self.normals, self.areas, self.values


def as_cycle_result(found: Any) -> CycleResult:  # Any: a CycleResult or the older 5-tuple
    """A cycle's answer as a :class:`CycleResult`; the older ``(J, centres, normals, areas, s)``
    tuple is accepted, so a stand-in written for it keeps working."""
    if isinstance(found, CycleResult):
        return found
    objective, centres, normals, areas, values = found
    return CycleResult(float(objective), centres, normals, areas, values)


@dataclass(frozen=True)
class FlowSplit:
    """J = sum_i (Q_i / Q - 1/N)^2 over N outlets, and what it is made of."""

    objective: float
    fractions: dict[str, float]
    """Q_i / Q per outlet, in the outlets' order."""
    total: float
    """Q, the sum of the outlets' flows (m³/s)."""
    deviation: float
    """max_i |Q_i / Q - 1/N|: the stop criterion (0.01 is within one percentage point)."""

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "objective": self.objective,
            "fractions": dict(self.fractions),
            "total": self.total,
            "deviation": self.deviation,
        }


def flow_split(flows: Mapping[str, float], outlets: Sequence[str] = ()) -> FlowSplit:
    """The flow-split objective of ``flows`` (m³/s per port, outward positive) over ``outlets``,
    or over the ports with outflow when none are named. Refused when there is no outflow."""
    names = list(outlets) or [name for name, q in flows.items() if q > 0]
    missing = [name for name in names if name not in flows]
    if missing:
        raise InvalidInput(f"no flow for the outlets {', '.join(missing)}")
    if not names:
        raise InvalidInput("no port has outflow: the flow split is undefined")
    total = float(sum(flows[name] for name in names))
    if not total > 0:
        raise InvalidInput("the outlets carry no net outflow: the flow split is undefined")
    target = 1.0 / len(names)
    fractions = {name: float(flows[name]) / total for name in names}
    objective = float(sum((f - target) ** 2 for f in fractions.values()))
    deviation = float(max(abs(f - target) for f in fractions.values()))
    return FlowSplit(objective, fractions, total, deviation)


def flow_split_derivative(
    flows: Mapping[str, float], outlets: Sequence[str] = ()
) -> dict[str, float]:
    """dJ/dQ_i of the flow split, c_i = (2/Q)(e_i - sum_k e_k f_k) with e_i = f_i - 1/N: the
    Python mirror of the outlet source the open-source adjoint applies (ADR-0041), for tests. J is
    homogeneous of degree zero in the Q_i, so sum_i c_i Q_i = 0."""
    split = flow_split(flows, outlets)
    target = 1.0 / len(split.fractions)
    errors = {name: f - target for name, f in split.fractions.items()}
    mean = sum(errors[name] * f for name, f in split.fractions.items())
    return {name: 2.0 / split.total * (e - mean) for name, e in errors.items()}


class _Copies:
    """Private copies of a project with the variables set."""

    def __init__(self, project: Project, variables: Sequence[Variable]) -> None:
        self._data = project.to_json()
        self._registry = project.registry
        self.variables = list(variables)

    def at(self, x: Any) -> Project:  # Any: array of SI values
        copy_ = Project.from_json(copy.deepcopy(self._data), node_registry=self._registry)
        for variable, value in zip(self.variables, list(x), strict=True):
            node, param = copy_.resolve_param(variable.reference)
            copy_.set_param(node.id, param, float(value))
        return copy_


def current_values(project: Project, variables: Sequence[Variable]) -> list[float]:
    """Each variable's value in the live project (SI), the optimiser's start."""
    out = []
    for variable in variables:
        node, param = project.resolve_param(variable.reference)
        out.append(float(project.value_of(node.id, param).to_base_units().magnitude))
    return out


class VolumeObjective:
    """J = (V / target - 1)² for one implicit part, with its exact gradient."""

    def __init__(
        self,
        project: Project,
        part: str,
        variables: Sequence[Variable],
        target: float,
        cache: Path | None = None,
    ) -> None:
        if not target > 0:
            raise InvalidInput("the target volume must be positive")
        self._copies = _Copies(project, variables)
        self._part = project.get(part).id
        self._target = target
        if cache is None:
            from stokes import workspace_root

            cache = workspace_root.scratch_root() / "implicit-study"
        self._cache = cache

    def __call__(self, x: Any) -> Evaluation:  # Any: array of SI values
        import numpy as np

        from stokes.geometry.measure import measure
        from stokes.geometry.operations.implicit import body_of
        from stokes.geometry.opplan import implicit_recipe
        from stokes.geometry.sensitivity import body_size, field_builder, target_of
        from stokes.implicit.bodymesh import mesh_body
        from stokes.implicit.sensitivity import normal_velocity, surface_gradient

        project = self._copies.at(x)
        part = project.get(self._part)
        made = mesh_body(body_of(implicit_recipe(project, part)), self._cache)
        volume = measure([(made.positions, made.triangles)]).volume
        p = made.positions
        t = made.triangles.astype(np.int64)
        a, b, c = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
        cross = np.cross(b - a, c - a)
        twice = np.linalg.norm(cross, axis=1)
        normals = cross / np.where(twice > 0, twice, 1.0)[:, None]
        centres_mm = (a + b + c) / 3.0 * 1000.0
        size = body_size(project, part)
        dv = []
        for variable in self._copies.variables:
            target = target_of(project, variable.reference)
            v = normal_velocity(
                field_builder(project, part, target), target.value, target.step, centres_mm, size
            )
            v = type(v)(v.speed / 1000.0, v.normal, v.valid)
            dv.append(surface_gradient(v, np.ones(len(twice)), normals, twice / 2.0).value)
        ratio = volume / self._target - 1.0
        gradient = np.array([2.0 * ratio / self._target * g for g in dv])
        return Evaluation(ratio * ratio, gradient, "exact", {"volume": volume})


class AdjointObjective:
    """The CFD cycle: ``solve(project)`` meshes and runs the adjoint case of the part's flow and
    returns (J, wall faces' centres, normals, areas, s); the gradient is summed on the part."""

    def __init__(
        self,
        project: Project,
        part: str,
        variables: Sequence[Variable],
        solve: Callable[[Project], Any],  # Any: a CycleResult (or the older 5-tuple)
        verdict: str = "unchecked",
        objective: str = "pressure_loss",
        outlets: Sequence[str] | None = None,
    ) -> None:
        """``verdict`` is the gradient gate's word on this part and these quantities
        (``verified``, or ``unchecked``; a ``failed`` gate is refused before this is made).
        ``objective`` is the one the adjoint case was written for; a flow split's J is computed
        from the cycle's flows over ``outlets`` (default every outlet), as the solver's is."""
        if objective not in OBJECTIVES:
            raise InvalidInput(f"objective is one of {', '.join(OBJECTIVES)}; not {objective!r}")
        self._copies = _Copies(project, variables)
        self._part = project.get(part).id
        self._solve = solve
        self._verdict = verdict
        self._objective = objective
        self._outlets = list(outlets) if outlets else None

    def __call__(self, x: Any) -> Evaluation:  # Any: array of SI values
        import numpy as np

        from stokes.geometry.sensitivity import wall_gradient

        project = self._copies.at(x)
        solved = as_cycle_result(self._solve(project))
        objective, made = objective_of(solved, self._objective, self._outlets)
        centres, normals, areas, s = solved.wall()
        found = wall_gradient(
            project,
            project.get(self._part),
            centres,
            normals,
            areas,
            s,
            [v.reference for v in self._copies.variables],
        )
        gradient = np.array([found[v.reference].value for v in self._copies.variables])
        covered = min((r.covered for r in found.values()), default=0.0)
        # ADR-0038 decision 4, ADR-0040: unverified until a check of the same part and quantities
        # has passed the gate; the row says which.
        detail: dict[str, Any] = {  # Any: JSON
            "covered": covered,
            "gate": self._verdict,
            "unverified": self._verdict != "verified",
        }
        # ADR-0041 decision 8: whether s is the solver's own or Stokes' reconstruction.
        if solved.detail.get("sensitivity_source") is not None:
            detail["sensitivity_source"] = solved.detail["sensitivity_source"]
        if self._objective == "flow_split":
            # The split's fractions and deviation, as a finite-difference row carries them (the
            # stop rule and the panel read them), and the run's own facts.
            detail.update({**made, **dict(solved.detail), "flows": dict(solved.flows)})
            detail["pressure_loss"] = float(solved.objective)
        return Evaluation(objective, gradient, "adjoint", detail)


OBJECTIVES = ("pressure_loss", "flow_split")
"""What :class:`FiniteDifferenceObjective` measures from one solve."""


def objective_of(
    found: CycleResult, objective: str, outlets: Sequence[str] | None = None
) -> tuple[float, dict[str, Any]]:  # Any: JSON
    """J of one solve, by name, and what it is made of (the fractions of a flow split). ``outlets``
    narrows a flow split to the outlets the user chose; by default it is every outlet."""
    if objective == "pressure_loss":
        return float(found.objective), {}
    if objective == "flow_split":
        split = flow_split(found.flows, list(outlets) if outlets else found.outlets)
        return split.objective, split.to_json()
    raise InvalidInput(f"objective is one of {', '.join(OBJECTIVES)}; not {objective!r}")


class FiniteDifferenceObjective:
    """J and dJ/dx by forward differences through the whole CFD loop (ADR-0040): one solve at x
    and one per variable at x + h_i e_i, or x - h_i e_i where the forward step would leave the upper
    bound. Every solve is an ordinary primal run, so the rows say ``finite_difference`` and need no
    gate. ``on_probe`` sees each solve (the point, which variable moved, J and its detail)."""

    def __init__(
        self,
        project: Project,
        variables: Sequence[Variable],
        solve: Callable[[Project], Any],  # Any: a CycleResult
        objective: str = "flow_split",
        steps: Sequence[float] | None = None,
        on_probe: Callable[[dict[str, Any]], None] | None = None,  # Any: JSON
        outlets: Sequence[str] | None = None,
    ) -> None:
        """``outlets``: the flow split's outlets by patch name (default: every outlet)."""
        self._outlets = list(outlets) if outlets else None
        if objective not in OBJECTIVES:
            raise InvalidInput(f"objective is one of {', '.join(OBJECTIVES)}; not {objective!r}")
        self._copies = _Copies(project, variables)
        self._solve = solve
        self._objective = objective
        spans = [v.upper - v.lower for v in variables]
        chosen = list(steps) if steps is not None else [0.1 * s for s in spans]
        if len(chosen) != len(spans):
            raise InvalidInput("give one finite-difference step per variable")
        for step, span, variable in zip(chosen, spans, variables, strict=True):
            if not 0 < step < span:
                raise InvalidInput(
                    f"{variable.reference}: the step must be positive and inside the bounds' span"
                )
        self.steps = [float(s) for s in chosen]
        self._on_probe = on_probe or (lambda probe: None)

    def measure(self, x: Any) -> tuple[float, dict[str, Any]]:  # Any: array; JSON
        """J at ``x`` from one solve, and its detail (fractions, flows, the run's own facts)."""
        found = as_cycle_result(self._solve(self._copies.at(x)))
        value, made = objective_of(found, self._objective, self._outlets)
        detail: dict[str, Any] = {**made, **dict(found.detail)}  # Any: JSON
        if found.flows:
            detail["flows"] = dict(found.flows)
        if self._objective != "pressure_loss":
            detail["pressure_loss"] = float(found.objective)
        return value, detail

    def __call__(self, x: Any) -> Evaluation:  # Any: array of SI values
        import numpy as np

        point = np.asarray(x, dtype=np.float64).reshape(-1)
        base, detail = self.measure(point)
        self._on_probe({"x": point.tolist(), "moved": None, "objective": base, "detail": detail})
        gradient = np.zeros(len(point))
        probes: list[dict[str, Any]] = []  # Any: JSON
        for i, (variable, step) in enumerate(zip(self._copies.variables, self.steps, strict=True)):
            h = step if point[i] + step <= variable.upper else -step
            moved = point.copy()
            moved[i] += h
            value, more = self.measure(moved)
            gradient[i] = (value - base) / h
            self._on_probe(
                {
                    "x": moved.tolist(),
                    "moved": variable.reference,
                    "step": h,
                    "objective": value,
                    "detail": more,
                }
            )
            probes.append(
                {
                    "variable": variable.reference,
                    "step": h,
                    "objective": value,
                    **({"fractions": more["fractions"]} if "fractions" in more else {}),
                }
            )
        detail["probes"] = probes
        detail["solves"] = 1 + len(point)
        return Evaluation(base, gradient, "finite_difference", detail)
