"""Time profiles in the solve snapshot (ADR-0022 addendum 2026-11-24): a transient run carries the
profile, a steady run the time-mean, and the steady-only coupled engine refuses. Synthetic cube
and a hand-written mesh status, as ``test_solve_snapshot_energy.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.run.errors import SolveInvalid
from tests.test_solve_snapshot_energy import BOUNDARIES, by_name, energy_on, set_up, snapshot

SINE = {"form": "sine", "mean": "3 [m/s]", "amplitude": "1 [m/s]", "frequency": "10 [Hz]"}
PULSE = {"form": "square", "low": "0 [Pa]", "high": "200 [Pa]", "period": "0.1 [s]", "duty": 0.5}


def varied(tmp_path: Path) -> Session:
    session = set_up(tmp_path)
    session.execute(
        "boundary.set_profile", boundary=f"{BOUNDARIES}/inlet", parameter="velocity", profile=SINE
    )
    session.execute(
        "boundary.set_profile", boundary=f"{BOUNDARIES}/outlet", parameter="pressure", profile=PULSE
    )
    return session


def transient(session: Session) -> None:
    session.execute(
        "solve.configure", values={"transient": True, "end_time": "0.2 [s]", "delta_t": "1 [ms]"}
    )


def test_a_transient_run_carries_the_profiles_and_their_means(tmp_path: Path) -> None:
    session = varied(tmp_path)
    transient(session)
    patches = by_name(snapshot(session))
    inlet, outlet = patches["inlet"], patches["outlet"]
    assert inlet.velocity_profile is not None and inlet.velocity_profile.form == "sine"
    speed = sum(c * c for c in inlet.velocity or ()) ** 0.5
    assert speed == pytest.approx(3.0)  # direction times the time-mean
    assert outlet.pressure_profile is not None and outlet.pressure == pytest.approx(100.0)
    assert patches["wall"].velocity_profile is None


def test_a_steady_run_takes_the_time_mean_and_no_profile(tmp_path: Path) -> None:
    patches = by_name(snapshot(varied(tmp_path)))
    assert patches["inlet"].velocity_profile is None
    assert sum(c * c for c in patches["inlet"].velocity or ()) ** 0.5 == pytest.approx(3.0)
    assert patches["outlet"].pressure_profile is None
    assert patches["outlet"].pressure == pytest.approx(100.0)


def test_a_temperature_varies_with_energy_on(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    energy_on(session)
    transient(session)
    warming = {"form": "table", "rows": [["0 [s]", "300 [K]"], ["0.2 [s]", "340 [K]"]]}
    session.execute(
        "boundary.set_profile",
        boundary=f"{BOUNDARIES}/inlet",
        parameter="temperature",
        profile=warming,
    )
    inlet = by_name(snapshot(session))["inlet"]
    assert inlet.temperature == pytest.approx(320.0)
    assert inlet.temperature_profile is not None and inlet.temperature_profile.rows[-1][1] == 340.0


def test_the_coupled_engine_refuses_a_value_that_varies(tmp_path: Path) -> None:
    session = varied(tmp_path)
    session.execute("solve.configure", engine="coupled.simple")
    with pytest.raises(SolveInvalid) as caught:
        snapshot(session)
    assert caught.value.code == "solve.profile_steady_only"
    assert "inlet" in caught.value.message and "steady" in caught.value.message


def test_a_profile_whose_parameter_is_gone_is_refused(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    made = session.execute("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    session.execute(
        "boundary.set_profile",
        boundary=f"{BOUNDARIES}/inlet",
        parameter="velocity",
        profile={"form": "table", "rows": [[0, "U_in"], [1, "U_in * 2"]]},
    )
    session.require_project().get(made["path"]).name = "renamed"  # as a hand edit would
    session.require_project()._reindex()
    with pytest.raises(SolveInvalid) as caught:
        snapshot(session)
    assert caught.value.code == "solve.profile_invalid"


def test_a_mass_flow_profile_carries_its_mean_and_its_profile(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    session.execute(
        "boundary.set",
        boundary=f"{BOUNDARIES}/inlet",
        kind="mass_flow_inlet",
        values={"mass_flow": "0.002 [kg/s]"},
    )
    flow = {"form": "table", "rows": [["0 [s]", "0.001 [kg/s]"], ["0.2 [s]", "0.003 [kg/s]"]]}
    session.execute(
        "boundary.set_profile",
        boundary=f"{BOUNDARIES}/inlet",
        parameter="mass_flow",
        profile=flow,
    )
    steady = by_name(snapshot(session))["inlet"]
    assert steady.mass_flow == pytest.approx(0.002) and steady.mass_flow_profile is None
    transient(session)
    inlet = by_name(snapshot(session))["inlet"]
    assert inlet.mass_flow == pytest.approx(0.002)
    assert inlet.mass_flow_profile is not None and inlet.mass_flow_profile.form == "table"


def test_a_temperature_profile_with_energy_off_is_not_acted_on(tmp_path: Path) -> None:
    """The profile is kept, but nothing acts on it while the energy equation is off: the coupled
    engine does not refuse, the set-up check is quiet and boundary.list does not list it."""
    session = set_up(tmp_path)
    energy_on(session)
    session.execute(
        "boundary.set_profile",
        boundary=f"{BOUNDARIES}/inlet",
        parameter="temperature",
        profile={"form": "table", "rows": [["0 [s]", "300 [K]"], ["1 [s]", "340 [K]"]]},
    )
    session.execute("solve.configure", engine="coupled.simple", values={"energy": False})
    assert by_name(snapshot(session))["inlet"].temperature_profile is None
    codes = {f["code"] for f in session.execute("setup.check")["findings"]}
    assert not {c for c in codes if c.startswith("setup.profile")}
    listed = {b["name"]: b for b in session.execute("boundary.list")}
    assert listed["inlet"]["varies"] == []


def test_fast_mode_holds_each_varying_value_at_its_mean(tmp_path: Path) -> None:
    """Fast mode's steady preview (ADR-0047) reads the time-means and lists the boundaries."""
    from stokes.api.fast_inputs import _means, _time_means

    session = varied(tmp_path)
    project = session.require_project()
    assert sorted(_time_means(project)) == ["inlet", "outlet"]
    inlet = project.get(f"{BOUNDARIES}/inlet")
    outlet = project.get(f"{BOUNDARIES}/outlet")
    assert _means(project, inlet) == {"velocity": pytest.approx(3.0)}
    assert _means(project, outlet) == {"pressure": pytest.approx(100.0)}
    session.execute("boundary.set_profile", boundary=inlet.id, parameter="velocity", profile=None)
    assert _time_means(project) == ("outlet",)
