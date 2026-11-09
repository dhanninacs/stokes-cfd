"""Shared helpers for the study tests (no tests of its own): the pipe parameters of the demo and a
three-point inlet-velocity sweep over them. Synthetic values only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from stokes.api import Session

PARAMS = "/physics/parameters"
SWEEP = "/studies/sweep"
RHO = 998.0
AREA = 3.141592653589793 * 0.03**2  # D = 60 mm


def define_pipe(session: Session) -> None:
    """U_in, rho, D and the derived dp_dyn and Q, as in the demo project."""
    run = session.execute
    run("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    run("parameter.define", name="rho", dimension="density", value="998 [kg/m**3]")
    run("parameter.define", name="D", dimension="length", value="60 [mm]")
    run("parameter.define", name="dp_dyn", dimension="pressure", value="0.5 * rho * U_in**2")
    run("parameter.define", name="Q", dimension="volume_flow", value="U_in * pi * (D/2)**2")


def make_sweep(session: Session, name: str = "sweep") -> str:
    """A sweep over U_in = 1, 2, 3 m/s with the responses dp_dyn and Q; returns its path."""
    run = session.execute
    run("study.create", name=name, description="inlet velocity sweep")
    path = f"/studies/{name}"
    run("study.add_variable", study=path, parameter="U_in", values=["1 [m/s]", 2, "3 [m/s]"])
    run("study.add_response", study=path, expression="dp_dyn")
    run("study.add_response", study=path, expression="Q")
    return path


def dp(u: float) -> float:
    return 0.5 * RHO * u * u


def recorder(session: Session, event: str = "*") -> list[tuple[str, dict[str, Any]]]:
    """Every event (or one kind) the session emits from now on, in order."""
    seen: list[tuple[str, dict[str, Any]]] = []
    listener: Callable[[str, dict[str, Any]], None] = lambda e, d: seen.append((e, d))  # noqa: E731
    session.events.subscribe(event, listener)
    return seen
