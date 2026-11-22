"""Where the flow split's adjoint program comes from (ADR-0041): the probe record's default install
lists ``adjointFoam`` or not, a flow-split adjoint run is refused with a typed error when it does
not, and the cycle asks for the adjoint of the objective it was given. No host is asked: the probe
is a stand-in."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stokes.api import Session, shape_cycle
from stokes.api.commands.run import _require_adjoint_program
from stokes.run.errors import AdjointProgramMissing
from stokes.run.hosts import ADJOINT_PROGRAM, HostRegistry, install_utility


def _record(found: str | None) -> dict[str, Any]:  # Any: a probe record
    return {
        "default_install_id": "e_000000000001",
        "installs": [
            {"id": "e_000000000002", "utilities": {ADJOINT_PROGRAM: "C:/other/adjointFoam.exe"}},
            {"id": "e_000000000001", "utilities": {"foamRun": "x", ADJOINT_PROGRAM: found}},
        ],
    }


def test_the_default_install_decides() -> None:
    assert install_utility(_record("C:/of/adjointFoam.exe"), ADJOINT_PROGRAM) is not None
    # Another install having it does not count: the run goes to the default one.
    assert install_utility(_record(None), ADJOINT_PROGRAM) is None
    assert install_utility({"default_install_id": None, "installs": []}, ADJOINT_PROGRAM) is None
    assert install_utility(None, ADJOINT_PROGRAM) is None
    # An agent that predates the program does not list it at all.
    older = {"default_install_id": "e", "installs": [{"id": "e", "utilities": {"foamRun": "x"}}]}
    assert install_utility(older, ADJOINT_PROGRAM) is None


def _snap(engine: str, objective: str) -> Any:  # Any: a snapshot stand-in
    return SimpleNamespace(engine=engine, adjoint_objective=objective)


def test_run_start_refuses_a_split_adjoint_without_the_program() -> None:
    with pytest.raises(AdjointProgramMissing) as caught:
        _require_adjoint_program(_snap("of14.adjoint", "flow_split"), _record(None), "local")
    assert caught.value.code == "solve.adjoint_missing"
    assert "adjointFoam.exe" in caught.value.message and "of14-adjoint" in caught.value.message
    # The program found, the pressure loss or another engine: nothing to refuse.
    _require_adjoint_program(_snap("of14.adjoint", "flow_split"), _record("x.exe"), "local")
    _require_adjoint_program(_snap("of14.adjoint", "pressure_loss"), _record(None), "local")
    _require_adjoint_program(_snap("of14.incompressibleFluid", "flow_split"), _record(None), "l")


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:  # Any: a CommandContext stand-in
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="t")
    session.execute("solve.configure", values={"host": "local"})
    return SimpleNamespace(session=session)


def _probe(answer: dict[str, Any]) -> Any:  # Any: HostRegistry.probe's stand-in
    return lambda self, name, refresh=False: {"host": name, **answer}


def test_adjoint_program_reports_found_missing_and_unknown(
    ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        HostRegistry, "probe", _probe({"available": True, "record": _record("x.exe")})
    )
    found = shape_cycle.adjoint_program(ctx)
    assert found == {"host": "local", "program": ADJOINT_PROGRAM, "found": True, "why": ""}
    monkeypatch.setattr(HostRegistry, "probe", _probe({"available": True, "record": _record(None)}))
    missing = shape_cycle.adjoint_program(ctx)
    assert missing["found"] is False and "adjointFoam.exe" in missing["why"]
    monkeypatch.setattr(
        HostRegistry, "probe", _probe({"available": False, "why": "no agent", "record": None})
    )
    unknown = shape_cycle.adjoint_program(ctx)
    assert unknown["found"] is None and unknown["why"] == "no agent"


def test_the_cycle_configures_the_objective_it_was_given() -> None:
    cycle = object.__new__(shape_cycle.AdjointCycle)
    cycle._adjoint, cycle._objective, cycle._outlets = True, "flow_split", ["a", "b"]
    assert cycle._solver_values() == {
        "engine": "of14.adjoint",
        "turbulence": "laminar",
        # The adjoint writer takes one core only: the cycle sets it, not fails after the mesh.
        "cores": 1,
        "adjoint_objective": "flow_split",
        "adjoint_outlets": ["a", "b"],
    }
    cycle._objective = "pressure_loss"
    assert cycle._solver_values()["adjoint_outlets"] == []
    cycle._adjoint = False
    assert cycle._solver_values() == {"engine": "of14.incompressibleFluid", "turbulence": "laminar"}
