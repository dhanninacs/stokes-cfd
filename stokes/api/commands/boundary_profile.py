"""Boundary values that vary in time (ADR-0022 addendum 2026-11-24): ``boundary.set_profile`` and
``boundary.profile_preview``.

``boundary.set_profile`` gives one value of a boundary (a velocity inlet's speed, a mass-flow
inlet's mass flow, a pressure outlet's pressure, an inlet's temperature) a time profile, or takes
it away (``profile: null``, the constant again), in one undo step and one journal record. The
profile is stored in the boundary's ``profiles`` parameter in the form
:mod:`stokes.physics.conditions.profiles` writes: every number a quantity in SI with the
expression text it was given. The constant parameter (``velocity``) is left as it was, so taking
the profile away restores it.

``boundary.profile_preview`` checks a draft profile without changing anything and returns its SI
numbers, its time-mean, its cycle, its extremes, samples over the run's time and ``notes`` (what is
allowed but likely not meant: a pulse whose high is below its low), or the field that is wrong and
why: what the profile editor plots and puts under a field as the user types.
``boundary.set_profile`` reports the same ``notes``.

Events: ``node.changed {node_id, path, param: "profiles"}`` and ``boundary.changed``. Errors:
``boundary.invalid_profile`` (``details.parameter`` and ``details.field``), and
``boundary.invalid_value`` for a value the kind does not vary in time.

Import rule: the condition catalogue and profiles (standard library) and the set-up plan; units
inside the handlers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from stokes.api.commands.boundary import BOUNDARY, Lister, typed_boundary
from stokes.api.commands.setup_plan import Plan
from stokes.api.registry import CommandContext, ParamDef, command
from stokes.physics.conditions import InvalidValue
from stokes.physics.conditions.catalogue import BY_ID
from stokes.physics.conditions.profiles import (
    FORMS,
    PROFILED,
    VALUE_DIMENSIONS,
    Evaluate,
    InvalidProfile,
    TimeProfile,
    build,
    notes,
    resolve,
    stored_profiles,
)

if TYPE_CHECKING:
    from stokes.model import Node, Project

PARAMETER = ParamDef(
    "parameter",
    "string",
    description="The value that varies: velocity (velocity inlet, direction normal or vector), "
    "mass_flow (mass-flow inlet), pressure (pressure outlet) or temperature (inlets).",
)
PROFILE_HELP = (
    "The profile: {form: 'sine', mean, amplitude, frequency, phase}, {form: 'square', low, high, "
    "period, duty} or {form: 'table', rows: [{time, value} or [time, value], ...]} (two rows or "
    "more, times increasing). Each number is quantity text ('2 [m/s]', '5 [Hz]', '90 [deg]'), an "
    "SI number or an expression."
)
DEFAULT_POINTS = 200


def evaluator(project: Project) -> Evaluate:
    """Quantity text or an expression against the project's parameters, to SI; the expression
    text is kept so a profile follows the parameters it names when the case is written."""
    from stokes.units import dimension, evaluate, is_quantity_text, parse_quantity, to_si

    def run(text: str, dim_name: str) -> tuple[float, str | None]:
        dim = dimension(dim_name)
        if is_quantity_text(text):
            return float(to_si(parse_quantity(text, dim), dim)), None
        return float(to_si(evaluate(text, project.resolve, expect=dim), dim)), text

    return run


def check_use(node: Node, parameter: str) -> None:
    """``boundary.invalid_value`` unless the boundary's kind varies ``parameter`` in time and uses
    it with its present switches (a velocity inlet's speed with direction components is not in
    use)."""
    kind = str(node.params.get("kind"))
    spec = BY_ID.get(kind)
    allowed = PROFILED.get(kind, ())
    label = spec.label.lower() if spec is not None else kind
    if parameter not in allowed:
        what = ", ".join(allowed) if allowed else "no value"
        raise InvalidValue(
            f"a {label} cannot vary {parameter} in time; it varies {what}",
            details={"parameter": parameter, "kind": kind, "profiled": list(allowed)},
        )
    if spec is not None and parameter not in {use.name for use in spec.uses(node.params)}:
        raise InvalidValue(
            f"{node.name} does not use {parameter} with its present settings (a velocity inlet "
            "by components varies none of them: give it direction normal or vector)",
            details={"parameter": parameter, "kind": kind},
        )


@command(
    "boundary.set_profile",
    params=(
        BOUNDARY,
        PARAMETER,
        ParamDef(
            "profile", "object", required=False, description=PROFILE_HELP + " null: constant."
        ),
    ),
)
def boundary_set_profile(
    ctx: CommandContext,
    boundary: str,
    parameter: str,
    profile: dict[str, Any] | None = None,  # Any: JSON
) -> dict[str, Any]:  # Any: JSON
    """Make one value of a boundary vary in time, or constant again (profile null), in one undo
    step. The constant value is kept for when the profile goes."""
    plan = Plan(ctx)
    project = plan.project
    node = typed_boundary(project, boundary)
    if parameter not in VALUE_DIMENSIONS:
        raise InvalidValue(
            f"{parameter} cannot vary in time; these can: {', '.join(VALUE_DIMENSIONS)}",
            details={"parameter": parameter},
        )
    held = dict(stored_profiles(node.params))
    if profile is None:
        held.pop(parameter, None)
    else:
        check_use(node, parameter)
        held[parameter] = build(parameter, profile, evaluator(project))
    new = held or None
    if new != node.params.get("profiles"):
        plan.set(node.id, "profiles", new)
    plan.label = (
        f"make {node.name} {parameter} constant"
        if profile is None
        else f"vary {node.name} {parameter} in time"
    )
    plan.boundary_changed(node)
    changed = plan.push()
    resolved = resolve(parameter, held[parameter]) if parameter in held else None
    return {
        **Lister(ctx).entry(node),
        "changed": changed,
        "parameter": parameter,
        "profile": None if resolved is None else describe(resolved),
        "notes": [] if resolved is None else list(notes(resolved, parameter)),
    }


def describe(profile: TimeProfile) -> dict[str, Any]:  # Any: JSON
    """A resolved profile as the API reports it: its SI numbers and what the checks read."""
    low, high = profile.extremes()
    return {
        **profile.to_json(),
        "mean": profile.time_mean(),
        "cycle": profile.cycle(),
        "min": low,
        "max": high,
    }


def run_end_time(project: Project | None) -> float | None:
    """The run's end time in seconds when the run is transient, else None."""
    from stokes.engines.model import SETTINGS_PATH

    settings = None if project is None else project.find(SETTINGS_PATH)
    if settings is None or not settings.params.get("transient"):
        return None
    stored = settings.params.get("end_time")
    value = getattr(stored, "value", stored)
    return float(value) if isinstance(value, int | float) else None


@command(
    "boundary.profile_preview",
    params=(
        PARAMETER,
        ParamDef("profile", "object", description=PROFILE_HELP),
        ParamDef(
            "end_time",
            "number",
            required=False,
            description="Seconds to sample over; default: the run's end time when it is "
            "transient, else two cycles of the profile.",
        ),
        ParamDef(
            "points",
            "integer",
            required=False,
            default=DEFAULT_POINTS,
            description="How many samples (2 to 2000).",
        ),
    ),
    mutates=False,
    requires_project=False,
)
def boundary_profile_preview(
    ctx: CommandContext,
    parameter: str,
    profile: dict[str, Any],  # Any: JSON
    end_time: float | None = None,
    points: int = DEFAULT_POINTS,
) -> dict[str, Any]:  # Any: JSON
    """Check a draft profile and sample it, changing nothing: ``ok`` with the SI profile, its
    mean, cycle, extremes and samples, or ``ok: false`` with the field that is wrong and why."""
    project = ctx.session.project
    if parameter not in VALUE_DIMENSIONS:
        return {"ok": False, "field": "parameter", "message": f"{parameter} cannot vary in time"}
    try:
        if project is None:
            from stokes.model import Project

            project = Project.new("preview")
        stored = build(parameter, profile, evaluator(project))
        resolved = resolve(parameter, stored)
    except InvalidProfile as exc:
        return {
            "ok": False,
            "field": str(exc.details.get("field") or "form"),
            "message": exc.message,
        }
    run_end = run_end_time(ctx.session.project)
    span = end_time if end_time is not None and end_time > 0 else run_end or 0.0
    times, values = resolved.samples(span, points)
    return {
        "ok": True,
        "profile": describe(resolved),
        "stored": stored,
        "end_time": times[-1],
        "run_end_time": run_end,
        "times": times,
        "values": values,
        "forms": list(FORMS),
        "notes": list(notes(resolved, parameter)),
    }


def profiles_of(project: Project, node: Node) -> Mapping[str, TimeProfile]:
    """The boundary's profiles in use, resolved with their expressions evaluated again; a
    profile that no longer resolves (a parameter it named is gone) is left out here and reported
    by the set-up check."""
    from stokes.physics.conditions.profiles import varying_in

    found: dict[str, TimeProfile] = {}
    held = stored_profiles(node.params)
    run = evaluator(project)
    for name in varying_in(project, node):
        try:
            found[name] = resolve(name, held[name], run)
        except InvalidProfile:
            continue
    return found
