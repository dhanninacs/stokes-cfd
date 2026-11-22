"""``implicit.*`` commands: shape derivatives of implicit parts (ADR-0035 decision 27, ADR-0037).

``implicit.normal_velocity`` says how fast each triangle of a part's surface moves along its normal
when one quantity changes: an operation's parameter, a vector's component, a sketch's dimension or
a project parameter they refer to. ``implicit.gradient`` sums it against a surface sensitivity s
(an adjoint's ``shape_sensitivity``, decision 26) to give dJ/dp, or, with no sensitivity, against
s = 1 to give the derivative of the part's volume. Both read; neither changes the model.

Contract: ``docs/engineering/implicit-schema.md`` (Shape derivatives).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError, ValidationError
from stokes.geometry.errors import MeshOnly, OperationInvalid
from stokes.log import fields, get_logger

log = get_logger(__name__)

MAX_SPEEDS = 2_000_000
"""Triangles whose speeds one call returns; a larger part is refused rather than truncated."""

PART = ParamDef("part", "string", description="The implicit part: node id or path.")


def _implicit_part(ctx: CommandContext, part: str) -> tuple[Any, Any, Any]:  # Any: model types
    from stokes.geometry.store import GeometryStore

    project = ctx.session.require_project()
    node = project.get(part)
    if node.type != "part" or node.params.get("family") != "implicit":
        raise MeshOnly(
            f"{node.path} is not an implicit part: shape derivatives need a part made with Create",
            details={"part": node.id},
        )
    mesh = GeometryStore.of(ctx.session).mesh(node.id)
    if mesh is None:
        raise OperationInvalid(
            f"{node.path} has no surface yet: run its operation first", details={"part": node.id}
        )
    return project, node, mesh


@command(
    "implicit.normal_velocity",
    params=(
        PART,
        ParamDef(
            "parameter",
            "string",
            description="The quantity, as an expression names it: 'R', "
            "'geometry.cyl.radius', 'geometry.cyl.position.x', 'geometry.sk.dimensions.W'.",
        ),
        ParamDef(
            "speeds",
            "boolean",
            required=False,
            default=True,
            description="Return the speed of every triangle (false: the summary only).",
        ),
    ),
    mutates=False,
)
def implicit_normal_velocity(
    ctx: CommandContext, part: str, parameter: str, speeds: bool = True
) -> dict[str, Any]:
    """How fast each triangle of an implicit part moves along its outward normal per unit change
    of a quantity: V_n = -(dphi/dp)/|grad phi|, metres per SI unit of the quantity."""
    import numpy as np

    from stokes.geometry.sensitivity import finite, normal_velocity

    project, node, mesh = _implicit_part(ctx, part)
    target, velocity, surface = normal_velocity(project, node, mesh, parameter)
    if surface.count > MAX_SPEEDS and speeds:
        raise ValidationError(
            f"the part has {surface.count} triangles; ask for the summary (speeds false)",
            details={"triangles": surface.count},
        )
    valid = velocity.valid
    area = float(surface.areas.sum())
    kept = float(surface.areas[valid].sum())
    moved = float(np.sum(velocity.speed * surface.areas)) / area if area > 0 else 0.0
    out: dict[str, Any] = {
        "part": node.id,
        "parameter": target.reference,
        "node": target.node_id,
        "name": target.param,
        "value": target.value,
        "step": target.step,
        "triangles": surface.count,
        "left_out": int(surface.count - int(np.count_nonzero(valid))),
        "covered": kept / area if area > 0 else 0.0,
        "mean": finite(moved),
        "min": finite(float(velocity.speed[valid].min())) if valid.any() else None,
        "max": finite(float(velocity.speed[valid].max())) if valid.any() else None,
    }
    if speeds:
        out["speeds"] = [round(float(v), 9) for v in velocity.speed]
    return out


@command(
    "implicit.gradient",
    params=(
        PART,
        ParamDef("parameters", "array", description="The quantities, as expression names."),
        ParamDef(
            "sensitivity",
            "array",
            required=False,
            description="s per triangle of the part's surface, in the objective's units per m³, "
            "against the triangles' outward normals; omitted: s = 1, so each result is the "
            "derivative of the part's volume (m³ per SI unit).",
        ),
    ),
    mutates=False,
)
def implicit_gradient(
    ctx: CommandContext, part: str, parameters: list[Any], sensitivity: list[Any] | None = None
) -> dict[str, Any]:
    """dJ/dp = sum of s * V_n * (n_phi . n) * A over the part's triangles, for each quantity."""
    from stokes.geometry.sensitivity import finite, gradient

    names = [str(p) for p in parameters]
    if not names:
        raise ValidationError("name at least one quantity")
    if sensitivity is not None and not all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in sensitivity
    ):
        raise ValidationError("the sensitivity is a list of numbers, one per triangle")
    project, node, mesh = _implicit_part(ctx, part)
    found = gradient(project, node, mesh, names, sensitivity)
    return {
        "part": node.id,
        "sensitivity": "given" if sensitivity is not None else "volume",
        "gradients": {
            name: {
                "value": finite(result.value),
                "covered": result.covered,
                "left_out": result.left_out,
            }
            for name, result in found.items()
        },
    }


@command(
    "geometry.measure",
    params=(ParamDef("part", "string", description="A part: node id or path."),),
    mutates=False,
)
def geometry_measure(ctx: CommandContext, part: str) -> dict[str, Any]:
    """What a part's current mesh measures (SI): volume, area, centroid, bounding box, extent, and
    whether the surface is closed. A study names the same values geometry.parts.<name>.volume."""
    from stokes.geometry.measure import measure_part
    from stokes.geometry.store import GeometryStore

    project = ctx.session.require_project()
    node = project.get(part)
    if node.type != "part":
        raise ValidationError(f"{node.path} is not a part", details={"node": node.id})
    mesh = GeometryStore.of(ctx.session).mesh(node.id)
    if mesh is None:
        raise OperationInvalid(f"{node.path} has no mesh yet", details={"part": node.id})
    return {"part": node.id, "path": node.path, **measure_part(mesh).to_json()}


# ---------------------------------------------------------------------- shape optimisation
OBJECTIVES = ("volume", "pressure_loss", "flow_split")
GRADIENTS = ("adjoint", "finite_difference")
"""Where a CFD objective's gradient comes from (ADR-0040): the adjoint wall sensitivity, or
forward differences through the whole loop (N + 1 ordinary solves per cycle)."""
TARGET_DEVIATION = 0.01
"""flow_split stops when every outlet's share is within this of 1/N (one percentage point)."""
NOISE = ParamDef(
    "noise_floor",
    "number",
    required=False,
    description="The relative change of J one remesh makes on its own (default 0.01): no "
    "improvement below it is reported as one, and a gradient check whose signal is under five of "
    "it is inconclusive.",
)


@command(
    "shape.optimise",
    params=(
        PART,
        ParamDef(
            "variables",
            "array",
            description="[{reference, lower, upper}]: the quantities moved, as expressions name "
            "them ('R', 'geometry.cyl.position.x', 'geometry.sk.dimensions.W'), bounds in SI.",
        ),
        ParamDef(
            "objective",
            "string",
            required=False,
            default="volume",
            description="volume: reach target_volume (exact gradient, no CFD). pressure_loss: "
            "the CFD cycle's total-pressure loss. flow_split: equal flow through the outlets, "
            "J = sum (Q_i/Q - 1/N)^2 (its adjoint needs adjointFoam on the host; see "
            "shape.gradients).",
        ),
        ParamDef("target_volume", "number", required=False, description="m³, for volume."),
        ParamDef(
            "gradient",
            "string",
            required=False,
            description="adjoint or finite_difference (N + 1 ordinary solves per cycle). "
            "Default: adjoint for pressure_loss, finite_difference for flow_split. The flow "
            "split's adjoint runs adjointFoam and is refused when the host has none; it takes "
            "every pressure outlet.",
        ),
        ParamDef(
            "fd_step",
            "array",
            required=False,
            description="finite_difference: the step per variable in SI; 10 % of each "
            "variable's span by default.",
        ),
        ParamDef(
            "target",
            "number",
            required=False,
            description="flow_split: stop when every outlet's share is within this of 1/N "
            f"(default {TARGET_DEVIATION}).",
        ),
        ParamDef(
            "outlets",
            "array",
            required=False,
            description="flow_split: the outlets to share the flow equally, by boundary name (the "
            "names of pressure-outlet tags; at least two). Default: every pressure outlet.",
        ),
        ParamDef(
            "method", "string", required=False, default="SLSQP", description="SLSQP or L-BFGS-B."
        ),
        ParamDef("max_cycles", "integer", required=False, default=20, description="At most 200."),
        NOISE,
        ParamDef(
            "wait",
            "boolean",
            required=False,
            default=False,
            description="Return the result (true), or start in the background and report with "
            "events (false: the server stays responsive through a long run).",
        ),
    ),
    mutates=False,
)
def shape_optimise(
    ctx: CommandContext,
    part: str,
    variables: list[Any],
    objective: str = "volume",
    target_volume: float | None = None,
    method: str = "SLSQP",
    max_cycles: int = 20,
    wait: bool = False,
    gradient: str | None = None,
    fd_step: list[Any] | None = None,
    target: float | None = None,
    noise_floor: float | None = None,
    outlets: list[Any] | None = None,
) -> dict[str, Any]:
    """Optimise an implicit part's parameters with gradients: each cycle is one
    evaluation of the objective and its gradient on a private copy of the project, streamed as a
    shape.cycle event. The live project does not change; apply the best point with
    parameter.set.

    The adjoint gradient is refused for a quantity that moves or resizes an inlet or outlet (its
    wall-only sensitivity cannot see that), and after a failed gradient check; an adjoint run stops
    at the second cycle as ``gradient_disagrees`` when J moved against the first gradient. The
    result says whether the best cycle improved on the first by more than the noise floor
    (ADR-0040)."""
    from stokes.api.shape_state import (
        adjoint_verdict,
        improvement,
        secant_disagrees,
        state_of,
    )
    from stokes.explore.optimise.gradient import optimise
    from stokes.explore.shape import VolumeObjective, current_values, variables_from

    source, deviation, noise = _settings(objective, max_cycles, gradient, target, noise_floor)
    project, node, _ = _implicit_part(ctx, part)
    chosen = variables_from(variables)
    state = state_of(ctx.session)
    evaluate: Any  # Any: a gradient evaluator (VolumeObjective or AdjointObjective)
    cycle_under_way: Any = None  # Any: the AdjointCycle, for shape.cancel(now)
    use_adjoint = objective != "volume" and source == "adjoint"
    split_outlets = _split_outlets(ctx, objective, outlets)
    if objective != "volume" and source == "finite_difference":
        evaluate, cycle_under_way = _fd_objective(
            ctx, project, node.id, chosen, objective, fd_step, split_outlets
        )
    elif use_adjoint:
        if objective == "flow_split":
            _require_split_adjoint(ctx, split_outlets)
        verdict = adjoint_verdict(
            state,
            node.id,
            [c.reference for c in chosen],
            objective,
            _gate_outlets(ctx, objective, split_outlets),
        )
        if verdict == "failed":
            raise ValidationError(
                "the last gradient check of these quantities failed the gate: the adjoint gradient "
                "is not used; use gradient finite_difference",
                details={"gate": "failed"},
            )
        evaluate, cycle_under_way = _adjoint_objective(
            ctx, project, node.id, chosen, verdict, objective, split_outlets
        )
    else:
        if target_volume is None or not target_volume > 0:
            raise ValidationError("the volume objective needs a positive target_volume (m³)")
        evaluate = VolumeObjective(project, node.id, chosen, float(target_volume))
    start = [
        min(max(v, c.lower), c.upper)
        for v, c in zip(current_values(project, chosen), chosen, strict=True)
    ]

    def reached(found: Any) -> bool:  # Any: gradient.Cycle
        split = found.detail.get("deviation") if objective == "flow_split" else None
        return isinstance(split, int | float) and split <= deviation

    def cycle(found: Any) -> bool:  # Any: gradient.Cycle
        row = found.to_json()
        state.append_cycle(node.id, row)
        ctx.session.emit("shape.cycle", part=node.id, **row)
        return state.is_cancelled(node.id)  # shape.cancel stops the run between cycles

    def work() -> dict[str, Any]:  # Any: JSON
        state.record(
            node.id, {"part": node.id, "objective": objective, "status": "running", "cycles": []}
        )
        if use_adjoint:
            _refuse_port_motion(project, node.id, cycle_under_way, chosen)
        result = optimise(
            evaluate,
            start,
            [c.lower for c in chosen],
            [c.upper for c in chosen],
            method=method,
            max_cycles=max_cycles,
            on_cycle=cycle,
            stop_when=reached if objective == "flow_split" else None,
            secant=(lambda a, b: secant_disagrees(a, b, noise)) if use_adjoint else None,
        )
        found = result.to_json()
        out = {
            "part": node.id,
            "objective": objective,
            **({"gradient": source} if objective != "volume" else {}),
            **({"target": deviation} if objective == "flow_split" else {}),
            **({"outlets": split_outlets} if split_outlets else {}),
            "variables": [c.to_json() for c in chosen],
            **found,
            **(improvement(found["cycles"], noise) if objective != "volume" else {}),
        }
        state.record(node.id, out)
        _prune(cycle_under_way, result)
        return out

    return _launch(ctx, node.id, "shape.finished", work, wait, active=cycle_under_way)


def _settings(
    objective: str,
    max_cycles: int,
    gradient: str | None,
    target: float | None,
    noise_floor: float | None,
) -> tuple[str, float, float]:
    """The gradient source, the flow split's target and the noise floor, checked."""
    from stokes.api.shape_state import NOISE_FLOOR

    if objective not in OBJECTIVES:
        raise ValidationError(f"objective is one of {', '.join(OBJECTIVES)}")
    if not 1 <= max_cycles <= 200:
        raise ValidationError("max_cycles is 1 to 200")
    # The flow split's default stays finite differences (ADR-0041): its adjoint is opt-in until the
    # verification plan has passed, and it needs a program the host may not have.
    source = gradient or ("finite_difference" if objective == "flow_split" else "adjoint")
    if objective != "volume" and source not in GRADIENTS:
        raise ValidationError(f"gradient is one of {', '.join(GRADIENTS)}")
    deviation = TARGET_DEVIATION if target is None else float(target)
    if not deviation > 0:
        raise ValidationError("target must be positive")
    noise = NOISE_FLOOR if noise_floor is None else float(noise_floor)
    if not noise >= 0:
        raise ValidationError("noise_floor must not be negative")
    return source, deviation, noise


def _split_outlets(
    ctx: CommandContext, objective: str, outlets: list[Any] | None
) -> list[str] | None:
    """The flow split's chosen outlets, checked against the project's pressure outlets; None for
    every outlet (or another objective)."""
    if outlets is None:
        return None
    if objective != "flow_split":
        raise ValidationError("outlets applies to the flow_split objective only")
    names = [str(o) for o in outlets]
    if len(names) < 2 or len(set(names)) != len(names):
        raise ValidationError("name at least two different outlets")
    from stokes.api.shape_cycle import outlet_names

    known = outlet_names(ctx.session)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValidationError(
            f"not a pressure outlet: {', '.join(unknown)} (the outlets are {', '.join(known)})",
            details={"outlets": known},
        )
    return names


def _require_split_adjoint(ctx: CommandContext, outlets: list[str] | None) -> None:
    """The flow split's adjoint (ADR-0041) only where it can run: the host has ``adjointFoam``
    (refused before any meshing, with how to get it) and every pressure outlet is in the split."""
    from stokes.api import shape_cycle
    from stokes.run.errors import AdjointProgramMissing

    found = shape_cycle.adjoint_program(ctx)
    if found.get("found") is not True:
        raise AdjointProgramMissing(
            str(found.get("why") or "the host cannot run the flow split's adjoint"),
            details={"host": found.get("host"), "program": found.get("program")},
        )
    if outlets is not None:
        left_out = [n for n in shape_cycle.outlet_names(ctx.session) if n not in outlets]
        if left_out:
            raise ValidationError(
                "the flow split's adjoint shares the flow among every pressure outlet; "
                f"{', '.join(left_out)} is left out: use gradient finite_difference for a subset",
                details={"left_out": left_out},
            )


def _gate_outlets(
    ctx: CommandContext, objective: str, outlets: list[str] | None
) -> list[str] | None:
    """The outlet set a gradient check and an optimisation are compared on (ADR-0040 decision 6):
    the chosen outlets, or every outlet; None for the pressure loss."""
    from stokes.api import shape_cycle

    if objective != "flow_split":
        return None
    return sorted(outlets) if outlets else sorted(shape_cycle.outlet_names(ctx.session))


def _refuse_port_motion(
    project: Any,  # Any: Project
    part: str,
    cycle: Any,  # Any: AdjointCycle
    chosen: list[Any],  # Any: shape.Variable
) -> None:
    """Refuse the adjoint for quantities that move or resize a port (ADR-0040)."""
    from stokes import workspace_root
    from stokes.api.shape_state import port_motion

    rules = getattr(cycle, "rules", None)
    if not rules:
        return
    moved = port_motion(
        project,
        part,
        rules,
        [c.reference for c in chosen],
        workspace_root.scratch_root() / "implicit-study",
    )
    if moved:
        # In the user's words (walkthrough 2026-11-18): the quantity as they named it and the
        # boundaries by name, not a dotted model path and node ids. The ids stay in ``details``.
        def quantity(ref: str) -> str:
            return ref.rsplit(".", 1)[-1].replace("_", " ")

        def port(ident: str) -> str:
            node = project.find(ident)
            return str(node.name) if node is not None else ident

        names = "; ".join(
            f"{quantity(ref)} moves {', '.join(port(t) for t in tags)}"
            for ref, tags in moved.items()
        )
        raise ValidationError(
            "the adjoint cannot follow a quantity that moves or resizes an inlet or outlet "
            f"({names}): choose Finite differences for it",
            details={"ports": moved},
        )


def _prune(cycle: Any, result: Any) -> None:  # Any: AdjointCycle, GradientResult
    """Remove the cycle folders of a finished run but the best and the last cycle's."""
    if cycle is None or not hasattr(cycle, "prune"):
        return
    keep: set[int] = set()
    for found in (result.best, result.cycles[-1] if result.cycles else None):
        number = found.detail.get("cycle") if found is not None else None
        if isinstance(number, int):
            keep.add(number)
    cycle.prune(keep)


def _launch(
    ctx: CommandContext,
    part: str,
    event: str,
    work: Callable[[], dict[str, Any]],
    wait: bool,
    active: Any = None,  # Any: the AdjointCycle shape.cancel(now) aborts, if any
) -> dict[str, Any]:  # Any: JSON
    """Run ``work`` now (``wait``), or on a background thread that reports with ``event``.

    **Every command runs on the server's event-loop thread**, so a CFD cycle of minutes run there
    would freeze the app and hold back its own progress events. In the background the work only
    reads what it was given (copies of the project taken before the thread starts) and emits
    events, which the session sends from any thread; each cycle runs in a private session."""
    from stokes.api.shape_state import state_of

    state = state_of(ctx.session)
    state.begin(part, active)  # refuses a part with work under way in this session
    if wait:
        try:
            out = work()
        finally:
            state.end(part)
        ctx.session.emit(event, part=part, status=str(out.get("status", "done")), result=out)
        return out

    def run() -> None:
        try:
            out = work()
            ctx.session.emit(event, part=part, status=str(out.get("status", "done")), result=out)
        except StokesError as exc:
            ctx.session.emit(event, part=part, status="failed", error=exc.message, code=exc.code)
        except Exception:
            # Not a typed error: the client gets a stable code and sentence, the log the trace.
            log.exception("shape work failed", extra=fields(part=part))
            ctx.session.emit(
                event,
                part=part,
                status="failed",
                error="the shape work failed unexpectedly; the log has the details",
                code="shape.internal",
            )
        finally:
            state.end(part)

    threading.Thread(target=run, name=f"stokes-shape-{part}", daemon=True).start()
    return {"part": part, "started": True}


def _adjoint_objective(
    ctx: CommandContext,
    project: Any,  # Any: Project
    part: str,
    chosen: list[Any],  # Any: shape.Variable
    verdict: str = "unchecked",
    objective: str = "pressure_loss",
    outlets: list[str] | None = None,
) -> tuple[Any, Any]:  # Any: AdjointObjective, AdjointCycle
    """The CFD cycle's evaluator and its cycle: each cycle meshes and solves the adjoint of
    ``objective`` on a private copy."""
    import tempfile

    from stokes import workspace_root
    from stokes.api.shape_cycle import AdjointCycle
    from stokes.explore.shape import AdjointObjective

    workdir = Path(tempfile.mkdtemp(prefix="stokes-shape-", dir=workspace_root.scratch_root()))

    def step(name: str, info: dict[str, Any]) -> None:  # Any: JSON
        ctx.session.emit("shape.step", part=part, step=name, run="optimise", **info)

    cycle = AdjointCycle(
        ctx.session, part, workdir, on_step=step, objective=objective, outlets=outlets or ()
    )
    found = AdjointObjective(project, part, chosen, cycle, verdict, objective, outlets)
    return found, cycle


def _fd_objective(
    ctx: CommandContext,
    project: Any,  # Any: Project
    part: str,
    chosen: list[Any],  # Any: shape.Variable
    objective: str,
    fd_step: list[Any] | None,
    outlets: list[str] | None = None,
) -> tuple[Any, Any]:  # Any: FiniteDifferenceObjective, AdjointCycle
    """The finite-difference evaluator (ADR-0040): every solve is an ordinary primal cycle on a
    private copy; each is reported as a ``shape.probe`` event."""
    import tempfile

    from stokes import workspace_root
    from stokes.api.shape_cycle import AdjointCycle
    from stokes.explore.shape import FiniteDifferenceObjective

    if fd_step is not None and (
        len(fd_step) != len(chosen)
        or not all(
            isinstance(v, int | float) and not isinstance(v, bool) and v > 0 for v in fd_step
        )
    ):
        raise ValidationError("fd_step is one positive number per variable (SI)")
    workdir = Path(tempfile.mkdtemp(prefix="stokes-shape-", dir=workspace_root.scratch_root()))

    def step(name: str, info: dict[str, Any]) -> None:  # Any: JSON
        ctx.session.emit("shape.step", part=part, step=name, run="optimise", **info)

    def probe(found: dict[str, Any]) -> None:  # Any: JSON
        ctx.session.emit("shape.probe", part=part, **found)

    cycle = AdjointCycle(
        ctx.session, part, workdir, on_step=step, adjoint=False, solves_per_cycle=len(chosen) + 1
    )
    try:
        evaluate = FiniteDifferenceObjective(
            project,
            chosen,
            cycle,
            objective,
            [float(v) for v in fd_step] if fd_step is not None else None,
            on_probe=probe,
            outlets=outlets,
        )
    except StokesError as exc:
        raise ValidationError(exc.message) from exc
    return evaluate, cycle


@command("shape.gradients", params=(), mutates=False)
def shape_gradients(ctx: CommandContext) -> dict[str, Any]:
    """The gradient sources each shape objective may use on the host a cycle would run on: the
    flow split's adjoint only where the host has adjointFoam (ADR-0041; ``adjoint_program`` says
    what was found and, when it was not, why). Reads the cached host probe."""
    from stokes.api import shape_cycle

    ctx.session.require_project()
    program = shape_cycle.adjoint_program(ctx)
    split = ["finite_difference", "adjoint"] if program.get("found") is True else []
    return {
        "objectives": {
            "volume": [],
            "pressure_loss": ["adjoint", "finite_difference"],
            "flow_split": split or ["finite_difference"],
        },
        "defaults": {"pressure_loss": "adjoint", "flow_split": "finite_difference"},
        "adjoint_program": program,
    }


CHECK_RUNS = ("base", "plus", "minus")
"""The three evaluations of a gradient check, in the order they run."""
CHECK_OBJECTIVES = ("pressure_loss", "flow_split")


@command(
    "shape.cancel",
    params=(
        PART,
        ParamDef(
            "now",
            "boolean",
            required=False,
            default=False,
            description="Also stop the CFD cycle under way: its mesh or run is cancelled and the "
            "cycle is not recorded (false: stop when the cycle under way ends).",
        ),
    ),
    mutates=False,
)
def shape_cancel(ctx: CommandContext, part: str, now: bool = False) -> dict[str, Any]:
    """Stop a part's optimisation or gradient check: after the cycle under way (``now`` false: its
    run finishes and is recorded), or at once (``now`` true: the cycle's mesh or run is cancelled).
    """
    from stokes.api.shape_state import state_of

    node = ctx.session.require_project().get(part)
    cycle = state_of(ctx.session).cancel(node.id)
    aborted = False
    if now:
        aborted = bool(cycle.abort()) if cycle is not None else False
    return {"part": node.id, "cancelling": True, "now": now, "aborted": aborted}


@command(
    "shape.check",
    params=(
        PART,
        ParamDef(
            "parameter",
            "string",
            required=False,
            description="One quantity, as an expression names it (or give parameters).",
        ),
        ParamDef(
            "parameters",
            "array",
            required=False,
            description="Several quantities: one base solve, then plus and minus each (2N + 1).",
        ),
        ParamDef(
            "objective",
            "string",
            required=False,
            default="pressure_loss",
            description="pressure_loss (adjoint against central differences) or flow_split "
            "(adjoint against central differences when the host has adjointFoam, else the "
            "central differences alone).",
        ),
        ParamDef(
            "outlets",
            "array",
            required=False,
            description="flow_split: the outlets to share the flow equally, by boundary name "
            "(at least two). Default: every pressure outlet.",
        ),
        ParamDef(
            "step",
            "number",
            required=False,
            description="The finite-difference step in SI. Default: 5 % of each value, and for "
            "a length at least four base cells of the mesh (the geometry must change by more "
            "than the mesh does on its own); a quantity at zero takes 5 % of its own scale.",
        ),
        NOISE,
        ParamDef(
            "wait",
            "boolean",
            required=False,
            default=False,
            description="Return the result (true), or start in the background and report with "
            "events (false: the server stays responsive through a long run).",
        ),
    ),
    mutates=False,
)
def shape_check(
    ctx: CommandContext,
    part: str,
    parameter: str | None = None,
    parameters: list[Any] | None = None,
    objective: str = "pressure_loss",
    step: float | None = None,
    wait: bool = False,
    noise_floor: float | None = None,
    outlets: list[Any] | None = None,
) -> dict[str, Any]:
    """Check a CFD objective's gradient against central finite differences at the current point:
    one base cycle, then each quantity plus and minus its step (2N + 1 cycles). Each ratio
    adjoint / finite difference should be near 1, and the cosine between the two gradients near 1:
    ``passed`` is the gate (ADR-0038 decision 4, ADR-0040). The flow split's adjoint is checked
    when the host has adjointFoam and the split takes every outlet (ADR-0041); otherwise its check
    gives the finite-difference gradient alone. Each row's forward
    and backward differences must agree and its signal stand above the noise floor, or the check
    is ``inconclusive`` (``passed`` null) with a larger step suggested."""
    import tempfile

    from stokes import workspace_root
    from stokes.api.shape_state import NOISE_FLOOR
    from stokes.explore.shape import Variable as ShapeVariable
    from stokes.explore.shape import current_values

    names = [str(p) for p in parameters] if parameters else ([parameter] if parameter else [])
    if not names:
        raise ValidationError("name the quantity to check (parameter or parameters)")
    if len(set(names)) != len(names):
        raise ValidationError("each quantity is checked once")
    if objective not in CHECK_OBJECTIVES:
        raise ValidationError(f"objective is one of {', '.join(CHECK_OBJECTIVES)}")
    project, node, _ = _implicit_part(ctx, part)
    split_outlets = _split_outlets(ctx, objective, outlets)
    values = current_values(project, [ShapeVariable(n, -1e300, 1e300) for n in names])
    steps = [
        float(step) if step else _default_step(project, n, v)
        for n, v in zip(names, values, strict=True)
    ]
    if not all(h > 0 for h in steps):
        raise ValidationError("the step must be positive")
    noise = NOISE_FLOOR if noise_floor is None else float(noise_floor)
    workdir = Path(
        tempfile.mkdtemp(prefix="stokes-shape-check-", dir=workspace_root.scratch_root())
    )
    check = _GradientCheck(
        ctx,
        project,
        node.id,
        names,
        values,
        steps,
        objective,
        workdir,
        split_outlets,
        _check_with_adjoint(ctx, objective, split_outlets),
    )
    check.noise = noise
    return _launch(ctx, node.id, "shape.checked", check.work, wait, active=check.cycle)


def _check_with_adjoint(ctx: CommandContext, objective: str, outlets: list[str] | None) -> bool:
    """Whether a check compares an adjoint: always for the pressure loss; for the flow split when
    its adjoint can run here (ADR-0041), else the check is the finite differences alone."""
    if objective != "flow_split":
        return True
    try:
        _require_split_adjoint(ctx, outlets)
    except StokesError:
        return False
    return True


def _default_step(project: Any, reference: str, value: float) -> float:  # Any: Project
    """5 % of the value; a length takes at least four base cells of the mesh settings but at most
    half its own value, and a quantity at zero 5 % of the body's scale (a length) or 0.05 of its
    unit, never a step far below what the mesh resolves."""
    owner, param = project.resolve_param(reference)
    quantity = project.value_of(owner.id, param).to_base_units()
    step = abs(value) * 0.05
    # **By dimension, not by the unit's spelling.** The test was `str(units) == "meter"`, and the
    # units print as "m": no length ever took this branch, so every length stepped by the 0.05 of
    # the branch below, which is 50 mm. A 3 mm corner cut was checked at 53 mm and at -47 mm
    # (measured 2026-11-19: "a loop of the profile crosses itself" on the minus point).
    if str(quantity.dimensionality) == "[length]":
        settings = project.find("/mesh/settings")
        raw = settings.params.get("base_size") if settings is not None else None
        base = float(getattr(raw, "value", raw) or 0.0) if raw is not None else 0.0
        wanted = max(step, 4.0 * base, 1e-4)
        # **Never more than half the value.** Four base cells of a 1 mm mesh is 4 mm, and a 3 mm
        # corner cut minus 4 mm is a negative length: the check's "minus" point then failed to
        # build ("a loop of the profile crosses itself") and the whole check with it. Half the
        # value keeps both points on the same side of zero; a quantity at zero keeps the rule above.
        return min(wanted, 0.5 * abs(value)) if value != 0.0 else wanted
    return max(step, 0.05)


class _GradientCheck:
    """One ``shape.check``: the base cycle, then plus and minus each quantity, on private copies
    of a snapshot of the project taken before the work starts (it may run on another thread)."""

    def __init__(
        self,
        ctx: CommandContext,
        project: Any,  # Any: Project
        part: str,
        names: list[str],
        values: list[float],
        steps: list[float],
        objective: str,
        workdir: Path,
        outlets: list[str] | None = None,
        with_adjoint: bool = True,
    ) -> None:
        from stokes.api.shape_cycle import AdjointCycle

        self.ctx = ctx
        self.part = part
        self.names, self.values, self.steps = names, values, steps
        self.objective = objective
        self.outlets = outlets
        self.gate_outlets = _gate_outlets(ctx, objective, outlets)
        self.with_adjoint = with_adjoint
        self.noise = 0.01
        self.runs = [("base", "")] + [(kind, n) for n in names for kind in ("plus", "minus")]
        # Which of the evaluations is running (1-based), for the step events.
        self.under_way = {"check": 0, "finished": 0}
        self.snapshot = project.to_json()
        self.registry = project.registry
        self.cycle = AdjointCycle(
            ctx.session,
            part,
            workdir,
            on_step=self.report,
            adjoint=self.with_adjoint,
            objective=objective,
            outlets=outlets or (),
        )

    def report(self, name: str, info: dict[str, Any]) -> None:  # Any: JSON
        index = self.under_way["check"]
        kind, which = self.runs[index - 1] if 1 <= index <= len(self.runs) else ("", "")
        self.ctx.session.emit(
            "shape.step",
            part=self.part,
            step=name,
            run="check",
            check=index,
            checks=len(self.runs),
            check_run=kind,
            **({"parameter": which} if which else {}),
            **info,
        )

    def at(self, point: list[float]) -> Any:  # Any: Project
        import copy

        from stokes.model.project import Project

        data = Project.from_json(copy.deepcopy(self.snapshot), node_registry=self.registry)
        for name, v in zip(self.names, point, strict=True):
            owner, param = data.resolve_param(name)
            data.set_param(owner.id, param, v)
        return data

    def evaluate(self, index: int, point: list[float]) -> Any:  # Any: CycleResult
        from stokes.api.shape_state import state_of
        from stokes.explore.errors import Cancelled
        from stokes.explore.shape import as_cycle_result

        if state_of(self.ctx.session).is_cancelled(self.part):
            raise Cancelled("stopped")
        self.under_way["check"] = index
        found = as_cycle_result(self.cycle(self.at(point)))
        self.under_way["finished"] = index
        return found

    def work(self) -> dict[str, Any]:  # Any: JSON
        from stokes.api.shape_state import consistency, gate, state_of
        from stokes.explore.errors import Cancelled
        from stokes.explore.shape import objective_of
        from stokes.geometry.sensitivity import wall_gradient

        rows: list[dict[str, Any]] = []  # Any: JSON
        try:
            base = self.evaluate(1, self.values)
            j0, made = objective_of(base, self.objective, self.outlets)
            adjoint: dict[str, Any] = {}  # Any: wall_gradient results
            if self.with_adjoint:
                here = self.at(self.values)
                centres, normals, areas, s = base.wall()
                adjoint = wall_gradient(
                    here, here.get(self.part), centres, normals, areas, s, self.names
                )
            for i, name in enumerate(self.names):
                row = self._row(i, name, adjoint.get(name))
                rows.append({**row, **consistency(row, j0, self.noise)})
        except Cancelled:
            return {
                "part": self.part,
                "parameter": self.names[0],
                "parameters": self.names,
                "status": "cancelled",
                "completed": self.under_way["finished"],
                "checks": len(self.runs),
            }
        out: dict[str, Any] = {  # Any: JSON
            "status": "done",
            "part": self.part,
            "objective_name": self.objective,
            "parameters": self.names,
            "objective": j0,
            "gradients": rows,
            "cells": base.detail.get("cells"),
            "source": "adjoint" if self.with_adjoint else "finite_difference",
            **(
                {"sensitivity_source": base.detail["sensitivity_source"]}
                if self.with_adjoint and base.detail.get("sensitivity_source") is not None
                else {}
            ),
            **({"split": made} if made else {}),
            **({"outlets": self.gate_outlets} if self.gate_outlets is not None else {}),
            "noise_floor": self.noise,
            **gate(rows),
        }
        if out["verdict"] == "inconclusive":
            out["suggested_step"] = [
                float(row.get("suggested_step", h)) for row, h in zip(rows, self.steps, strict=True)
            ]
        if len(rows) == 1:
            # The single-quantity answer, as before (the panel's Check gradient reads it).
            row = rows[0]
            keys = (
                "parameter",
                "value",
                "step",
                "finite_difference",
                "adjoint",
                "ratio",
                "covered",
            )
            out.update({k: row[k] for k in keys if k in row})
        state_of(self.ctx.session).record_check(self.part, out)
        return out

    def _row(self, i: int, name: str, adjoint: Any) -> dict[str, Any]:  # Any: GradientResult
        from stokes.explore.shape import objective_of

        h = self.steps[i]
        found = []
        for k, sign in enumerate((1.0, -1.0)):
            point = list(self.values)
            point[i] += sign * h
            index = 2 + 2 * i + k
            found.append(objective_of(self.evaluate(index, point), self.objective, self.outlets)[0])
        fd = (found[0] - found[1]) / (2 * h)
        row: dict[str, Any] = {  # Any: JSON
            "parameter": name,
            "value": self.values[i],
            "step": h,
            "plus": found[0],
            "minus": found[1],
            "finite_difference": fd,
        }
        if adjoint is not None:
            row["adjoint"] = adjoint.value
            row["ratio"] = adjoint.value / fd if fd else None
            row["covered"] = adjoint.covered
        return row


@command("shape.history", params=(PART,), mutates=False)
def shape_history(ctx: CommandContext, part: str) -> dict[str, Any]:
    """The last shape optimisation of a part in this session: its cycles and best point, and its
    last gradient check (``check``) when there is one."""
    from stokes.api.shape_state import state_of

    project = ctx.session.require_project()
    node = project.get(part)
    found, check = state_of(ctx.session).read(node.id)
    if found is None and check is None:
        raise ValidationError(f"{node.path} has not been optimised in this session")
    out = dict(found or {"part": node.id, "cycles": []})
    if check is not None:
        out["check"] = check
    return out


def _body_name(project: Any, op: Any) -> str:  # Any: Project, Node
    """What the user calls an operation: the name of the body it made ("sphere"), else its own."""
    for ident in op.params.get("outputs") or ():
        made = project.find(str(ident))
        if made is not None:
            return str(made.name)
    return str(op.name)


@command("shape.variables", params=(PART,), mutates=False)
def shape_variables(ctx: CommandContext, part: str) -> dict[str, Any]:
    """The continuous quantities that shape an implicit part, as an optimiser names them: every
    length, angle and factor of the operations that make it (through its inputs), and the project
    parameters their expressions read; each with its value and bounds of half to one and a half
    times it (SI). Counts are left out: they have no gradient. ``label`` is ``body`` and
    ``quantity`` joined ("sphere Radius"); ``unit`` is the SI unit the value and bounds are in."""
    from stokes.model.nodes import QuantityValue
    from stokes.units.expressions import references

    project = ctx.session.require_project()
    node = project.get(part)
    found: list[dict[str, Any]] = []
    seen_ops: set[str] = set()
    seen_refs: set[str] = set()
    names_read: set[str] = set()

    def ref_of(owner: Any, name: str) -> str:  # Any: Node
        return f"{owner.path.strip('/').replace('/', '.')}.{name}"

    def add(
        reference: str,
        body: str,
        quantity: str,
        value: float,
        unit: str,
        owner: Any,
        name: str,
    ) -> None:  # Any: Node
        if reference in seen_refs:
            return
        seen_refs.add(reference)
        low, high = sorted((0.5 * value, 1.5 * value)) if value != 0 else (-0.01, 0.01)
        found.append(
            {
                "reference": reference,
                "label": f"{body} {quantity}",
                # The label's two halves, so a client can show "Sketch 1 · pull distance".
                "body": body,
                "quantity": quantity,
                "value": value,
                "unit": unit,
                "lower": low,
                "upper": high,
                # The node and parameter, so a client can leave out what the kind does not read.
                "node": owner.id,
                "name": name,
            }
        )

    def walk(part_node: Any) -> None:  # Any: Node
        op = project.find(str(part_node.params.get("derived") or ""))
        if op is None or op.id in seen_ops:
            return
        seen_ops.add(op.id)
        spec = project.spec_of(op)
        for key, value in op.params.items():
            if not isinstance(value, QuantityValue):
                continue
            try:
                pspec = spec.param(key)
            except StokesError:
                continue
            if pspec.integer or pspec.hidden:
                continue
            # A sketch dimension is an entry of the `dimensions` map ("dimensions.H"): its own name
            # is what the user typed and reads, so the label is "dimension H", not the map's label.
            map_name, _, entry = key.partition(".")
            add(
                ref_of(op, key),
                _body_name(project, op),
                f"dimension {entry}"
                if entry and map_name == "dimensions"
                else (pspec.label or key),
                value.value,
                value.unit,
                op,
                key,
            )
            if value.expr:
                names_read.update(references(value.expr))
        for ident in [*(op.params.get("inputs") or []), *(op.params.get("tools") or [])]:
            child = project.find(str(ident))
            if child is not None:
                walk(child)

    walk(node)
    for name in sorted(names_read):
        try:
            owner, param = project.resolve_param(name)
        except StokesError:
            continue
        stored = owner.params.get(param)
        if isinstance(stored, QuantityValue) and not project.spec_of(owner).param(param).integer:
            add(name, "parameter", name, stored.value, stored.unit, owner, param)
    return {"part": node.id, "variables": found}
