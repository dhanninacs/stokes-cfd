"""Boundary values that vary in time, as the solve snapshot reads them (ADR-0022 addendum
2026-11-24).

On a **transient** run a patch whose boundary varies a value carries the resolved profile
(``velocity_profile``, ``mass_flow_profile``, ``pressure_profile``, ``temperature_profile``) and the
case writers turn it into a function of time. The constant fields of the patch hold the profile's
time-mean (a velocity inlet's ``velocity`` is its direction times the mean speed), so whatever
reads them (an initial field, a turbulence estimate) reads a representative value.

On a **steady** run the solver's "time" counts iterations, so a profile in seconds means nothing:
the patch gets the time-mean as its constant value and no profile, and ``setup.check`` says so
(``setup.profile_steady``). The steady-only coupled engine refuses a case that varies a value
(``solve.profile_steady_only``) rather than solve something else than what was asked.

Expressions in a profile are evaluated again here, against the parameters as they are now.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from stokes.run.errors import SolveInvalid

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.physics.conditions.profiles import TimeProfile

#: The engine that solves steady flow only (ADR-0044): it cannot follow a value in time.
STEADY_ONLY_ENGINES: tuple[str, ...] = ("coupled.simple",)


def boundary_profiles(project: Any, node: Any) -> dict[str, TimeProfile]:  # Any: model
    """The boundary's profiles in use, resolved; ``solve.profile_invalid`` for one that no longer
    resolves (an expression naming a parameter that is gone)."""
    from stokes.api.commands.boundary_profile import evaluator
    from stokes.physics.conditions.profiles import (
        InvalidProfile,
        resolve,
        stored_profiles,
        varying_in,
    )

    held = stored_profiles(node.params)
    found: dict[str, TimeProfile] = {}
    for name in varying_in(project, node):
        try:
            found[name] = resolve(name, held[name], evaluator(project))
        except InvalidProfile as exc:
            raise SolveInvalid(
                f"the time profile of {node.name}'s {name.replace('_', ' ')} does not resolve: "
                f"{exc.message}",
                code="solve.profile_invalid",
                details={"boundary": node.id, "parameter": name},
            ) from exc
    return found


def refuse_steady_only(project: Any, engine: str) -> None:  # Any: Project
    """``solve.profile_steady_only`` when a steady-only engine is asked to solve a case whose
    boundaries vary a value in time."""
    if engine not in STEADY_ONLY_ENGINES:
        return
    from stokes.physics.conditions.profiles import varying_in

    names = sorted(
        node.name
        for node in project.root.walk()
        if getattr(node, "type", "") == "boundary" and varying_in(project, node)
    )
    if names:
        raise SolveInvalid(
            f"{', '.join(names)} {'varies' if len(names) == 1 else 'vary'} in time, and the "
            "coupled solver solves steady flow only: choose the OpenFOAM solver and a transient "
            "run, or make the values constant",
            code="solve.profile_steady_only",
            details={"boundaries": names, "engine": engine},
        )


def apply_profiles(
    session: Session,
    project: Any,  # Any: Project
    node: Any,  # Any: the boundary Node
    values: dict[str, Any],  # Any: the patch's snapshot values, changed in place
    *,
    transient: bool,
) -> None:
    """The patch's values with its profiles: the time-mean as the constant, and on a transient
    run the profile itself."""
    from stokes.run.snapshot import _along_normal, _unit

    for name, profile in boundary_profiles(project, node).items():
        mean = profile.time_mean()
        if name == "velocity":
            if str(node.params.get("direction") or "normal") == "vector":
                direction = _unit(node.params.get("direction_vector"))
            else:
                direction = _along_normal(session, node, 1.0)
            if math.hypot(*direction) == 0.0:
                continue  # no direction to vary along: the snapshot's own checks refuse it
            values["velocity"] = tuple(c * mean for c in direction)
        elif name == "temperature":
            if values.get("thermal") != "fixedValue":
                continue  # energy off: the temperature is not written
            values["temperature"] = mean
        else:
            values[name] = mean
        if transient:
            values[f"{name}_profile"] = profile
