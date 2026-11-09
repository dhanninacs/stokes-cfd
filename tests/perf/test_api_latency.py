from __future__ import annotations

import pytest

from stokes.api import Session
from tests.perf.harness import budget

PARAMS = "/physics/parameters"


@pytest.mark.perf
def test_parameter_set_latency(velocity_parameter: Session) -> None:
    s = velocity_parameter
    s.execute("parameter.define", name="dp", dimension="pressure", value="0.5 * rho * U_in**2")
    s.execute("parameter.set", node=f"{PARAMS}/U_in", name="value", value="2.5 [m/s]")  # warm
    with budget("api.parameter_set_x20", target_ms=100.0):
        for i in range(20):
            s.execute(
                "parameter.set", node=f"{PARAMS}/U_in", name="value", value=f"{2 + i * 0.1} [m/s]"
            )


@pytest.mark.perf
def test_expression_evaluation_latency(velocity_parameter: Session) -> None:
    project = velocity_parameter.require_project()
    project.evaluate("0.5 * rho * U_in**2")  # warm the parser and registry
    with budget("units.evaluate_x100", target_ms=100.0):
        for _ in range(100):
            project.evaluate("0.5 * rho * U_in**2 + sqrt(U_in**2) * rho * U_in")


@pytest.mark.perf
def test_save_load_latency(velocity_parameter: Session) -> None:
    s = velocity_parameter
    for i in range(300):
        s.execute("parameter.define", name=f"p{i}", dimension="length", value=f"{i} [mm]")
    with budget("model.save_300_params", target_ms=250.0):
        s.execute("project.save")
    with budget("model.load_300_params", target_ms=250.0):
        s.execute("project.open", path=str(s.project_path))
