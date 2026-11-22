"""The user's under-relaxation factors (ADR-0045): settings, snapshot, basis and every writer.

Each factor is optional: unset, the engine writes its own value and the case is byte for byte the
one written before the settings existed (the golden tests hold that); set, it replaces the
engine's value where that engine relaxes the equation.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from stokes.compile.coupled import simple as coupled
from stokes.compile.of14 import fluid
from stokes.compile.of14 import write as incompressible_write
from stokes.compile.snapshot import SolveSnapshot
from stokes.engines.model import RELAXATION_PARAMS
from tests.test_compile_coupled import duct
from tests.test_compile_of14_fluid import elbow

SETTINGS = "/solver/settings"


def flat(text: str) -> str:
    return " ".join(text.split())


def relaxation(text: str) -> str:
    """The `relaxationFactors` block of an fvSolution, whitespace-collapsed."""
    body = flat(text)
    start = body.index("relaxationFactors")
    depth, end = 0, start
    for i, ch in enumerate(body[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return body[start:end]


def incompressible(**over: Any) -> SolveSnapshot:
    return dataclasses.replace(duct(), engine="of14.incompressibleFluid", energy=False, **over)


# ------------------------------------------------------------------ the settings (API)


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from stokes.api import Session

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    s = Session()
    s.execute("project.new", name="demo")
    return s


def params(session: Any) -> dict[str, Any]:
    return dict(session.require_project().get(SETTINGS).params)


def test_the_factors_start_unset_and_set_through_configure(session: Any) -> None:
    assert all(params(session).get(name) is None for name in RELAXATION_PARAMS)
    session.execute("solve.configure", values={"relax_velocity": 0.6, "relax_pressure": 0.25})
    stored = params(session)
    assert (stored["relax_velocity"], stored["relax_pressure"]) == (0.6, 0.25)
    # null clears a factor back to the engine's own value, and undo restores it.
    session.execute("solve.configure", values={"relax_velocity": None})
    assert params(session).get("relax_velocity") is None
    session.execute("history.undo")
    assert params(session)["relax_velocity"] == 0.6


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_a_factor_outside_zero_to_one_is_refused(session: Any, bad: float) -> None:
    from stokes.errors import StokesError

    with pytest.raises(StokesError, match="greater than 0 and at most 1"):
        session.execute("solve.configure", values={"relax_energy": bad})
    assert params(session).get("relax_energy") is None


def test_the_schema_offers_an_empty_field(session: Any) -> None:
    """The JSON schema says null is allowed, so the form shows an empty Automatic field."""
    from stokes.model.schema import registry

    spec = registry.get("solver_settings").param("relax_velocity").json_schema()
    assert spec["type"] == ["number", "null"] and spec["x-optional"] is True
    assert "default" not in spec


def test_the_factors_survive_save_and_reopen(session: Any, tmp_path: Path) -> None:
    from stokes.api import Session

    session.execute("solve.configure", values={"relax_turbulence": 0.5})
    path = session.execute("project.info")["path"]
    session.execute("project.save")
    again = Session()
    again.execute("project.open", path=path)
    stored = dict(again.require_project().get(SETTINGS).params)
    assert stored["relax_turbulence"] == 0.5 and stored.get("relax_velocity") is None


# ------------------------------------------------------------------ the basis


def test_unset_factors_keep_the_basis_and_a_set_one_moves_it() -> None:
    from stokes.run.snapshot import solve_basis

    snap = incompressible()
    before = solve_basis(snap, "mb", "mj", "e")
    assert solve_basis(dataclasses.replace(snap, relax_velocity=None), "mb", "mj", "e") == before
    assert solve_basis(dataclasses.replace(snap, relax_velocity=0.7), "mb", "mj", "e") != before


# ------------------------------------------------------------------ the writers


def test_the_incompressible_writer() -> None:
    plain = relaxation(incompressible_write(incompressible())["system/fvSolution"])
    assert plain == 'relaxationFactors { equations { U 0.9; ".*" 0.9; } }'
    tuned = incompressible(relax_pressure=0.3, relax_velocity=0.7, relax_turbulence=0.5)
    text = relaxation(incompressible_write(tuned)["system/fvSolution"])
    assert text == 'relaxationFactors { fields { p 0.3; } equations { U 0.7; ".*" 0.5; } }'


def test_the_energy_writer() -> None:
    snap = elbow()
    own = relaxation(fluid.write(snap)["system/fvSolution"])
    tuned = dataclasses.replace(
        snap, relax_pressure=0.15, relax_velocity=0.4, relax_energy=0.6, relax_turbulence=0.45
    )
    text = relaxation(fluid.write(tuned)["system/fvSolution"])
    assert own != text
    for piece in ("p_rgh 0.15;", "U 0.4;", '".*" 0.45;'):
        assert piece in text, piece
    assert "h 0.6;" in text or "e 0.6;" in text


def test_the_coupled_writer_relaxes_temperature_only_when_asked() -> None:
    assert 'T 1; U 0.9; ".*" 0.9;' in relaxation(coupled.write(duct())["system/fvSolution"])
    tuned = relaxation(coupled.write(duct(relax_energy=0.8))["system/fvSolution"])
    assert "T 0.8;" in tuned


def test_a_transient_run_writes_no_relaxation() -> None:
    snap = incompressible(transient=True, delta_t=0.01, end_time=1.0, relax_velocity=0.5)
    assert "relaxationFactors" not in incompressible_write(snap)["system/fvSolution"]
