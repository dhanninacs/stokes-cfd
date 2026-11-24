"""The set-up check's findings about values that vary in time (ADR-0022 addendum 2026-11-24)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.physics.conditions.checks import CODES
from tests.test_solve_snapshot_energy import BOUNDARIES, set_up

SINE = {"form": "sine", "mean": "3 [m/s]", "amplitude": "1 [m/s]", "frequency": "2 [Hz]"}


def findings(session: Session) -> dict[str, dict[str, Any]]:
    return {f["code"]: f for f in session.execute("setup.check")["findings"]}


def vary(session: Session, profile: dict[str, Any] = SINE) -> None:
    session.execute(
        "boundary.set_profile",
        boundary=f"{BOUNDARIES}/inlet",
        parameter="velocity",
        profile=profile,
    )


def test_the_codes_are_listed() -> None:
    for code in (
        "setup.profile_steady",
        "setup.profile_steady_only",
        "setup.profile_short",
        "setup.profile_reverses",
        "setup.profile_invalid",
    ):
        assert code in CODES


def test_a_steady_run_is_told_to_switch_and_the_fix_does_it(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    assert not {c for c in findings(session) if c.startswith("setup.profile")}
    vary(session)
    found = findings(session)["setup.profile_steady"]
    assert found["severity"] == "warning" and "time-mean" in found["message"]
    fix = found["fix"]
    assert fix == {"command": "solve.configure", "params": {"values": {"transient": True}}}
    session.execute(fix["command"], **fix["params"])
    assert "setup.profile_steady" not in findings(session)


def test_a_short_run_is_information(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    vary(session)
    session.execute("solve.configure", values={"transient": True, "end_time": "0.2 [s]"})
    short = findings(session)["setup.profile_short"]
    assert short["severity"] == "info" and "0.5 s" in short["message"]
    session.execute("solve.configure", values={"end_time": "1 [s]"})
    assert "setup.profile_short" not in findings(session)


def test_the_coupled_engine_is_an_error(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    vary(session)
    session.execute("solve.configure", engine="coupled.simple")
    found = findings(session)
    assert found["setup.profile_steady_only"]["severity"] == "error"
    assert "setup.profile_steady" not in found


def test_an_inflow_that_reverses_is_a_warning(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    vary(session, {**SINE, "amplitude": "4 [m/s]"})
    assert findings(session)["setup.profile_reverses"]["severity"] == "warning"


def test_a_profile_that_no_longer_resolves_is_an_error(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    made = session.execute("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    vary(session, {"form": "table", "rows": [[0, "U_in"], [1, "U_in * 2"]]})
    assert "setup.profile_invalid" not in findings(session)
    session.require_project().get(made["path"]).name = "renamed"  # as a hand edit would
    session.require_project()._reindex()
    found = findings(session)["setup.profile_invalid"]
    assert found["severity"] == "error" and "U_in" in found["message"]


def test_a_conjugate_case_runs_on_the_mean_and_is_not_sent_to_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The multi-region writer is steady only (compile.unsupported for a transient case): the
    finding offers no switch to transient, and on a transient run it switches back to steady."""
    import stokes.mesh.conjugate as conjugate

    session = set_up(tmp_path)
    monkeypatch.setattr(conjugate, "is_conjugate", lambda project: True)
    vary(session)
    steady = findings(session)["setup.profile_steady"]
    assert "conjugate" in steady["message"] and "time-mean" in steady["message"]
    assert steady["fix"].get("command") is None
    session.execute("solve.configure", values={"transient": True, "end_time": "0.1 [s]"})
    found = findings(session)
    back = found["setup.profile_steady"]["fix"]
    assert back == {"command": "solve.configure", "params": {"values": {"transient": False}}}
    assert "setup.profile_short" not in found
    session.execute(back["command"], **back["params"])
    assert findings(session)["setup.profile_steady"]["fix"].get("command") is None


# ---------------------------------------------------------------- one rule for a conjugate case


def two_bodies(tmp_path: Path) -> Session:
    """The water cube of ``set_up`` and an aluminium block beside it, a solid region: two bodies."""
    from tests.selection_support import add_part, cube, part

    session = set_up(tmp_path)
    block = add_part(session, "b", part(cube(0.01, (0.01, 0, 0), name="b")))
    session.execute("material.add_from_library", fluid="aluminium", name="aluminium")
    session.execute(
        "region.create", name="block", kind="solid", bodies=[block], material="aluminium"
    )
    return session


def test_two_bodies_are_conjugate_only_where_the_mesh_is_multi_region(tmp_path: Path) -> None:
    """Checked by review: the check called any two-body project conjugate, but the mesh is
    multi-region only with snappyHexMesh and a part domain (``snapshot_for``). Meshed by cfMesh
    the case is one region, runs transient, and the finding offers the switch."""
    from stokes.mesh.conjugate import is_multi_region

    session = two_bodies(tmp_path)
    project = session.require_project()
    vary(session)
    assert is_multi_region(project)
    assert findings(session)["setup.profile_steady"]["fix"].get("command") is None
    session.execute("mesh.configure", mesher="cfmesh")
    assert not is_multi_region(project)
    fix = findings(session)["setup.profile_steady"]["fix"]
    assert fix == {"command": "solve.configure", "params": {"values": {"transient": True}}}
    session.execute("solve.configure", values={"transient": True, "end_time": "0.1 [s]"})
    assert "setup.profile_short" in findings(session)  # a transient one-region run, checked
    session.execute("mesh.configure", mesher="snappy", values={"domain": "box"})
    assert not is_multi_region(project)


def test_an_unused_temperature_profile_does_not_vary_on_a_one_region_case(tmp_path: Path) -> None:
    """Energy off and two bodies meshed as one region: the energy equation is not solved, so the
    inlet's temperature profile is not in use anywhere (``boundary.list``, the summary, the
    check); on the multi-region mesh, which always solves energy, it is."""
    session = two_bodies(tmp_path)
    inlet = f"{BOUNDARIES}/inlet"
    session.execute(
        "boundary.set_profile",
        boundary=inlet,
        parameter="temperature",
        profile={**SINE, "mean": "300 [K]", "amplitude": "5 [K]"},
    )
    listed = {b["name"]: b for b in session.execute("boundary.list")}
    assert listed["inlet"]["varies"] == ["temperature"]
    assert session.execute("node.get", node=inlet)["varies"] == ["temperature"]
    session.execute("mesh.configure", mesher="cfmesh")
    listed = {b["name"]: b for b in session.execute("boundary.list")}
    assert listed["inlet"]["varies"] == []
    assert session.execute("node.get", node=inlet)["varies"] == []
    assert not {c for c in findings(session) if c.startswith("setup.profile")}


def test_a_reversing_inflow_says_its_unit(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    vary(session, {**SINE, "amplitude": "3.5 [m/s]"})
    message = findings(session)["setup.profile_reverses"]["message"]
    assert "goes to -0.5 m/s " in message
