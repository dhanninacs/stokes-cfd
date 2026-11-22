"""What a session knows of its shape optimisations and gradient checks, and the rules that decide
whether a CFD gradient may be trusted (ADR-0040).

- :class:`ShapeState` is per session (``session.extensions``), behind one lock: the parts with work
  under way, the parts asked to stop, the cycle each runs (for ``shape.cancel now``), the last
  optimisation and the last gradient check of each part. Two sessions on copies of one project
  share node ids and must not block or cancel each other, and the work runs on its own thread.
- :func:`gate` and :func:`consistency` turn a gradient check's rows into a verdict: ``passed``,
  ``failed``, ``inconclusive`` (the finite difference itself is noise) or ``no_adjoint``.
- :func:`adjoint_verdict` is what an adjoint optimisation may call its gradient: ``verified`` only
  after a passing check of the same part, quantities, objective and (flow split) outlets; a failed
  check blocks it.
- :func:`secant_disagrees` is the free check at the second cycle: does J move the way the first
  gradient said it would?
- :func:`port_motion` finds the quantities that move or resize an inlet or outlet, which the
  wall-only adjoint sensitivity cannot see.

Headless: the model and numpy; geometry and the implicit mesher lazily (port motion only).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stokes.api.session import Session

GATE_RATIO = (0.8, 1.25)
"""The band every adjoint / finite-difference ratio must lie in for the gate to pass."""
GATE_COSINE = 0.95
"""The least cosine between the adjoint and the finite-difference gradients (two or more)."""
CONSISTENCY = 0.3
"""Forward and backward differences may differ by this share of the central one; beyond it the
finite difference is curvature or noise, and the check is inconclusive."""
SIGNAL = 5.0
"""|J+ - J-| must be at least this many noise floors for a check to conclude."""
NOISE_FLOOR = 0.01
"""The relative change of J one remesh makes on its own, until measured for the case (about 0.5 to
1 % on a 22 k-cell snappy mesh, 2026-11-14 review): no improvement below it is reported as one."""
PORT_CHANGE = 0.002
"""A port whose area changes by more than this share (or that moves off its plane) under a
quantity's step is moved by it: the adjoint wall sensitivity cannot see that."""

_CREATE = threading.Lock()
"""Guards creating a session's state; each state has its own lock afterwards."""


@dataclass
class ShapeState:
    """One session's shape work. Every read and write goes through :attr:`lock`."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    running: set[str] = field(default_factory=set)
    cancelled: set[str] = field(default_factory=set)
    active: dict[str, Any] = field(default_factory=dict)  # Any: the AdjointCycle under way
    history: dict[str, dict[str, Any]] = field(default_factory=dict)  # Any: JSON
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)  # Any: JSON

    def is_cancelled(self, part: str) -> bool:
        with self.lock:
            return part in self.cancelled

    def cancel(self, part: str) -> Any:  # Any: the active cycle, if any
        with self.lock:
            self.cancelled.add(part)
            return self.active.get(part)

    def begin(self, part: str, active: Any = None) -> None:  # Any: AdjointCycle
        from stokes.errors import ValidationError

        with self.lock:
            if part in self.running:
                raise ValidationError("this part is already being optimised or checked")
            self.running.add(part)
            self.cancelled.discard(part)
            if active is not None:
                self.active[part] = active

    def end(self, part: str) -> None:
        with self.lock:
            self.running.discard(part)
            self.active.pop(part, None)

    def record(self, part: str, entry: dict[str, Any]) -> None:  # Any: JSON
        with self.lock:
            self.history[part] = entry

    def append_cycle(self, part: str, row: dict[str, Any]) -> None:  # Any: JSON
        with self.lock:
            self.history.setdefault(part, {"part": part, "cycles": []})["cycles"].append(row)

    def record_check(self, part: str, entry: dict[str, Any]) -> None:  # Any: JSON
        with self.lock:
            self.checks[part] = entry

    def read(self, part: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Copies of the part's last optimisation and last check."""
        with self.lock:
            found = self.history.get(part)
            check = self.checks.get(part)
            return (
                {**found, "cycles": list(found.get("cycles", []))} if found else None,
                dict(check) if check else None,
            )


def state_of(session: Session) -> ShapeState:
    with _CREATE:
        found = session.extensions.get("shape_state")
        if not isinstance(found, ShapeState):
            found = ShapeState()
            session.extensions["shape_state"] = found
        return found


# ---------------------------------------------------------------------------- the gate
def consistency(row: dict[str, Any], base: float, noise: float) -> dict[str, Any]:  # Any: JSON
    """The noise test of one check row (base J, J+, J- at +-h): forward and backward differences
    agree within :data:`CONSISTENCY` of the central one, and |J+ - J-| is at least :data:`SIGNAL`
    noise floors (``noise`` is relative to |J|)."""
    h = float(row["step"])
    plus, minus = float(row["plus"]), float(row["minus"])
    forward, backward = (plus - base) / h, (base - minus) / h
    central = (plus - minus) / (2 * h)
    floor = noise * abs(base)
    signal = abs(plus - minus)
    agree = abs(forward - backward) <= CONSISTENCY * abs(central) if central else False
    strong = signal >= SIGNAL * floor
    # Why it failed decides the next step: a weak signal wants a larger step; forward and backward
    # differences that disagree under a strong signal are curvature (measured on the manifold:
    # -0.107 against -0.179 per metre at 2,700 noise floors), which a smaller step resolves.
    cause = None if agree and strong else ("noise" if not strong else "curvature")
    return {
        "forward": forward,
        "backward": backward,
        "signal_to_noise": signal / floor if floor > 0 else None,
        "consistent": bool(agree and strong),
        **(
            {"cause": cause, "suggested_step": h * (2.0 if cause == "noise" else 0.5)}
            if cause
            else {}
        ),
    }


def gate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:  # Any: JSON
    """The verdict of a check's rows. ``inconclusive`` when any finite difference failed its noise
    test (``passed`` is then None: the reference itself is noise, try a larger step); otherwise
    ``passed`` when every ratio adjoint / finite difference lies in :data:`GATE_RATIO` and, with two
    or more quantities, the cosine is at least :data:`GATE_COSINE`; ``failed`` otherwise, with
    ``wrong_sign`` when a ratio is negative."""
    if not rows or "adjoint" not in rows[0]:
        clean = all(r.get("consistent", True) for r in rows)
        return {"verdict": "no_adjoint" if clean else "inconclusive", "passed": None}
    a = [float(r["adjoint"]) for r in rows]
    f = [float(r["finite_difference"]) for r in rows]
    norm = math.sqrt(sum(v * v for v in a)) * math.sqrt(sum(v * v for v in f))
    cosine = sum(x * y for x, y in zip(a, f, strict=True)) / norm if norm else None
    ratios = [r.get("ratio") for r in rows]
    out: dict[str, Any] = {"cosine": cosine}  # Any: JSON
    if not all(r.get("consistent", True) for r in rows):
        return {**out, "verdict": "inconclusive", "passed": None}
    wrong = any(r is not None and r < 0 for r in ratios)
    passed = all(r is not None and GATE_RATIO[0] <= r <= GATE_RATIO[1] for r in ratios) and (
        len(rows) < 2 or (cosine is not None and cosine >= GATE_COSINE)
    )
    return {
        **out,
        "verdict": "passed" if passed else "failed",
        "passed": bool(passed),
        **({"wrong_sign": True} if wrong else {}),
    }


def adjoint_verdict(
    state: ShapeState,
    part: str,
    references: Sequence[str],
    objective: str = "pressure_loss",
    outlets: Sequence[str] | None = None,
) -> str:
    """``verified`` when the part's last check is a passing adjoint check of the same quantities,
    the same objective and, for the flow split, the same outlets (ADR-0041); ``failed`` when that
    check failed; ``unchecked`` otherwise (no check, a check without an adjoint, another objective,
    other quantities or outlets, or an inconclusive one)."""
    _, check = state.read(part)
    same = (
        check is not None
        and check.get("objective_name") == objective
        and check.get("source", "adjoint") == "adjoint"
        and sorted(check.get("parameters") or []) == sorted(references)
        and (outlets is None or sorted(check.get("outlets") or []) == sorted(outlets))
    )
    if not same or check is None:
        return "unchecked"
    return {"passed": "verified", "failed": "failed"}.get(str(check.get("verdict")), "unchecked")


def secant_disagrees(first: Any, second: Any, noise: float) -> bool:  # Any: gradient.Cycle
    """True when J moved against the first cycle's gradient between the first two cycles by more
    than the noise floor: the gradient predicted a fall (g . dx < 0) and J rose, or the reverse."""
    dx = [b - a for a, b in zip(first.x, second.x, strict=True)]
    predicted = float(sum(g * d for g, d in zip(first.gradient, dx, strict=True)))
    actual = float(second.objective) - float(first.objective)
    if abs(actual) <= noise * abs(first.objective) or predicted == 0:
        return False
    return (predicted < 0) != (actual < 0)


def improvement(cycles: Sequence[dict[str, Any]], noise: float) -> dict[str, Any]:  # Any: JSON
    """How much the best cycle lowered J from the first, and whether that is above the noise floor
    (a smaller change is not reported as an improvement)."""
    if not cycles:
        return {"improvement": None, "significant": False}
    first = float(cycles[0]["objective"])
    best = min(float(c["objective"]) for c in cycles)
    change = (first - best) / abs(first) if first else 0.0
    return {"improvement": change, "significant": change > noise, "noise_floor": noise}


# ---------------------------------------------------------------------------- port motion
def port_motion(
    project: Any,  # Any: Project
    part: str,
    rules: Sequence[Any],  # Any: retag.Rule
    references: Sequence[str],
    cache: Path,
) -> dict[str, list[str]]:
    """The ports (plane-rule tags: inlets, outlets, symmetry planes) each quantity moves or
    resizes, found by rebuilding the part's surface at the quantity plus a small step and finding
    the ports again: an area change over :data:`PORT_CHANGE` or a port that left its plane counts.
    Only quantities that move a port are returned."""
    import copy

    import numpy as np

    from stokes.geometry.errors import OperationInvalid
    from stokes.geometry.meshfile import split_faces
    from stokes.geometry.operations.implicit import body_of
    from stokes.geometry.opplan import implicit_recipe
    from stokes.geometry.retag import apply_rules, faces_of
    from stokes.implicit.bodymesh import mesh_body
    from stokes.model.project import Project

    planes = [r for r in rules if r.kind == "plane"]
    if not planes:
        return {}

    def surface(data: Any) -> Any:  # Any: Project -> a PartMesh stand-in
        made = mesh_body(body_of(implicit_recipe(data, data.get(part))), cache)
        solid = _Solid(
            made.positions.astype(np.float32),
            made.triangles,
            split_faces(made.positions, made.triangles),
        )
        return _Mesh((solid,))

    def areas(mesh: Any) -> dict[str, float]:  # Any: PartMesh stand-in
        faces, _ = faces_of(mesh)
        found = apply_rules(mesh, planes)
        return {tag: sum(faces[i].area for i in ids) for tag, ids in found.items()}

    snapshot = project.to_json()
    base = areas(surface(project))
    out: dict[str, list[str]] = {}
    for reference in references:
        data = Project.from_json(copy.deepcopy(snapshot), node_registry=project.registry)
        owner, param = data.resolve_param(reference)
        value = float(data.value_of(owner.id, param).to_base_units().magnitude)
        data.set_param(owner.id, param, value + max(abs(value) * 0.05, 1e-4))
        try:
            moved = areas(surface(data))
        except OperationInvalid:
            out[reference] = [r.tag for r in planes]  # a port left its plane
            continue
        changed = [
            tag
            for tag, area in base.items()
            if area > 0 and abs(moved.get(tag, 0.0) - area) / area > PORT_CHANGE
        ]
        if changed:
            out[reference] = changed
    return out


@dataclass(frozen=True)
class _Solid:
    positions: Any  # Any: float32 (n, 3)
    indices: Any  # Any: uint32 (m, 3)
    face_ids: Any  # Any: uint32 (m,)


@dataclass(frozen=True)
class _Mesh:
    solids: tuple[_Solid, ...]
