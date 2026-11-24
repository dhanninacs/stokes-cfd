"""The set-up check's findings about values that vary in time (ADR-0022 addendum 2026-11-24).

One function, :func:`profile_findings`, called by ``checks._boundary`` for each boundary. The
codes (appended to ``checks.CODES``):

- ``setup.profile_steady`` (warning): a value varies in time and the run is steady; the run uses
  the time-mean. The fix switches the run to transient (``solve.configure``). A conjugate
  (multi-region, by the mesh's rule: snappyHexMesh, a part domain and more than one body) case
  is written steady only for now (``compile.unsupported`` for a transient one), so there the
  finding has no fix while the run is steady, and while it is transient its fix
  switches the run back to steady.
- ``setup.profile_steady_only`` (error): the solver is the coupled one, which solves steady flow
  only and refuses the case (``solve.profile_steady_only``).
- ``setup.profile_short`` (info): the run's end time is shorter than one period of a sine or a
  square, or than the table's last time: the run sees only part of the profile.
- ``setup.profile_reverses`` (warning): an inflow (a speed or a mass flow) goes to zero or below
  for part of the profile: the inlet then takes fluid out, which is allowed and rarely meant.
- ``setup.profile_invalid`` (error): a stored profile no longer resolves (an expression that
  names a parameter that is gone, or a hand-edited project).

Import rule: the standard library, the profiles module and, inside the functions, the engine
settings path, the multi-region rule of ``stokes.mesh.conjugate`` (``is_multi_region``, the rule the
mesh snapshot uses) and the units' dimension table; nothing heavy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stokes.physics.conditions.profiles import (
    VALUE_DIMENSIONS,
    InvalidProfile,
    TimeProfile,
    resolve,
    stored_profiles,
    varying_in,
)

if TYPE_CHECKING:
    from stokes.model import Node

CODES: tuple[str, ...] = (
    "setup.profile_steady",
    "setup.profile_steady_only",
    "setup.profile_short",
    "setup.profile_reverses",
    "setup.profile_invalid",
)
STEADY_ONLY_ENGINES: tuple[str, ...] = ("coupled.simple",)
INFLOWS: tuple[str, ...] = ("velocity", "mass_flow")
WORDS = {"velocity": "speed", "mass_flow": "mass flow", "pressure": "pressure"}


def _word(name: str) -> str:
    return WORDS.get(name, name.replace("_", " "))


def _settings(project: Any) -> Any:  # Any: Project and Node
    from stokes.engines.model import SETTINGS_PATH

    return project.find(SETTINGS_PATH)


def _seconds(stored: Any) -> float | None:  # Any: a stored quantity
    value = getattr(stored, "value", stored)
    return float(value) if isinstance(value, int | float) else None


def _conjugate(project: Any) -> bool:  # Any: Project
    """A multi-region case by the mesh's own rule (snappyHexMesh, a part domain, more than one
    body): two bodies meshed one other way are one region and run transient like any other."""
    from stokes.mesh.conjugate import is_multi_region

    return is_multi_region(project)


def _with_unit(name: str, value: float) -> str:
    """``value`` (SI) with the SI unit of the parameter: ``-0.5 m/s``."""
    from stokes.units import dimension

    unit = dimension(VALUE_DIMENSIONS[name]).si_unit
    return f"{value:g} {unit}".strip()


def _conjugate_finding(report: Any, node: Node, listed: str, *, transient: bool) -> None:  # Any
    """A conjugate case is written steady only for now (ADR-0042): it runs on the time-mean."""
    if transient:
        report.add(
            "setup.profile_steady",
            "warning",
            f"{node.name}'s {listed} varies in time, but a conjugate case is solved steady only "
            "for now: switch the run to steady, which uses the time-mean",
            suggestion="switch the run to steady",
            nodes=(node.id,),
            fix={"command": "solve.configure", "params": {"values": {"transient": False}}},
        )
        return
    report.add(
        "setup.profile_steady",
        "warning",
        f"{node.name}'s {listed} varies in time, but a conjugate case is solved steady only for "
        "now: the run uses the time-mean",
        suggestion="nothing to switch yet; make the value constant to silence this",
        nodes=(node.id,),
        fix=_edit(node.id),
    )


def _edit(node_id: str) -> dict[str, Any]:  # Any: JSON
    return {"action": "condition.edit", "node": node_id}


def profile_findings(report: Any, node: Node) -> None:  # Any: checks._Report
    """Every finding about the boundary's values that vary in time."""
    names = varying_in(report.project, node)
    if not names:
        return
    project = report.project
    held = stored_profiles(node.params)
    from stokes.api.commands.boundary_profile import evaluator

    profiles: dict[str, TimeProfile] = {}
    for name in names:
        try:
            profiles[name] = resolve(name, held[name], evaluator(project))
        except InvalidProfile as exc:
            report.add(
                "setup.profile_invalid",
                "error",
                f"the time profile of {node.name}'s {_word(name)} does not resolve: {exc.message}",
                suggestion="open the boundary and set its profile again",
                nodes=(node.id,),
                fix=_edit(node.id),
            )
    if not profiles:
        return
    listed = " and ".join(_word(name) for name in profiles)
    settings = _settings(project)
    engine = str(settings.params.get("engine") or "") if settings is not None else ""
    transient = bool(settings.params.get("transient")) if settings is not None else False
    conjugate = engine not in STEADY_ONLY_ENGINES and _conjugate(project)
    if engine in STEADY_ONLY_ENGINES:
        report.add(
            "setup.profile_steady_only",
            "error",
            f"{node.name}'s {listed} varies in time, and the coupled solver solves steady flow "
            "only",
            suggestion="choose the OpenFOAM solver and a transient run, or make the value constant",
            nodes=(node.id,),
            fix=_edit(node.id),
        )
    elif conjugate:
        _conjugate_finding(report, node, listed, transient=transient)
    elif not transient:
        report.add(
            "setup.profile_steady",
            "warning",
            f"{node.name}'s {listed} varies in time, but the run is steady: it uses the time-mean",
            suggestion="switch the run to transient",
            nodes=(node.id,),
            fix={"command": "solve.configure", "params": {"values": {"transient": True}}},
        )
    end = _seconds(settings.params.get("end_time")) if settings is not None else None
    for name, profile in profiles.items():
        if transient and not conjugate and end is not None and end < profile.cycle():
            what = "the table's last time" if profile.form == "table" else "one period"
            report.add(
                "setup.profile_short",
                "info",
                f"the run ends at {end:g} s, before {what} of {node.name}'s {_word(name)} "
                f"({profile.cycle():g} s)",
                suggestion=f"run to {profile.cycle():g} s or more to see the whole profile",
                nodes=(node.id,),
            )
        if name in INFLOWS and profile.extremes()[0] <= 0:
            report.add(
                "setup.profile_reverses",
                "warning",
                f"{node.name}'s {_word(name)} goes to {_with_unit(name, profile.extremes()[0])} "
                "for part of its "
                "profile: the inlet then takes fluid out",
                suggestion="keep the lowest value above zero unless backflow is meant",
                nodes=(node.id,),
                fix=_edit(node.id),
            )
