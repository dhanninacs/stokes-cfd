"""Boundary values that vary in time (ADR-0022 addendum 2026-11-24): the profile forms and their
rules, the stored form (quantities in SI with their expression text), ``boundary.set_profile``
through ``Session.execute`` with undo and journal replay, and ``boundary.profile_preview``."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Journal, Session, replay
from stokes.physics.conditions import InvalidValue
from stokes.physics.conditions.profiles import (
    InvalidProfile,
    TimeProfile,
    build,
    check_stored,
    from_stored,
    varying,
)
from tests.selection_support import add_part, cube, new_session, part

FLUID = "/regions/fluid"
INLET = f"{FLUID}/boundaries/inlet"


def _si(text: str, dim: str) -> tuple[float, str | None]:
    """A stand-in evaluator: '<number> [unit]' with the units these tests use, to SI."""
    number, _, unit = text.partition(" ")
    factor = {"": 1.0, "[m/s]": 1.0, "[Hz]": 1.0, "[s]": 1.0, "[ms]": 1e-3, "[deg]": math.pi / 180}
    return float(number) * factor[unit], None


SINE = {"form": "sine", "mean": "2 [m/s]", "amplitude": "0.5 [m/s]", "frequency": "4 [Hz]"}


# ---------------------------------------------------------------- the forms


def test_sine_square_and_table_values_means_and_cycles() -> None:
    sine = TimeProfile("sine", mean=2.0, amplitude=0.5, frequency=4.0, phase=math.pi / 2)
    assert sine.value_at(0.0) == pytest.approx(2.5)
    assert sine.time_mean() == 2.0 and sine.cycle() == 0.25 and sine.extremes() == (1.5, 2.5)
    square = TimeProfile("square", low=1.0, high=3.0, period=0.5, duty=0.25)
    assert square.value_at(0.1) == 3.0 and square.value_at(0.2) == 1.0
    assert square.value_at(0.6) == 3.0  # the next period
    assert square.time_mean() == pytest.approx(1.5) and square.cycle() == 0.5
    table = TimeProfile("table", rows=((0.0, 0.0), (0.2, 2.0), (1.0, 2.0)))
    assert table.value_at(0.1) == pytest.approx(1.0) and table.value_at(5.0) == 2.0
    assert table.value_at(-1.0) == 0.0  # held at the ends, as OpenFOAM's clamp
    assert table.time_mean() == pytest.approx((0.2 * 1.0 + 0.8 * 2.0) / 1.0)
    assert table.cycle() == 1.0


def test_affine_scales_and_offsets_every_form() -> None:
    sine = TimeProfile("sine", mean=100.0, amplitude=10.0, frequency=1.0).affine(0.001, 5.0)
    assert sine.mean == pytest.approx(5.1) and sine.amplitude == pytest.approx(0.01)
    table = TimeProfile("table", rows=((0.0, 1.0), (1.0, 3.0))).affine(2.0)
    assert table.rows == ((0.0, 2.0), (1.0, 6.0))


def test_samples_cover_the_span_and_include_table_corners() -> None:
    table = TimeProfile("table", rows=((0.0, 0.0), (0.33, 1.0), (1.0, 1.0)))
    times, values = table.samples(1.0, 5)
    assert times[0] == 0.0 and times[-1] == 1.0 and 0.33 in times
    assert values[times.index(0.33)] == 1.0
    times, _ = TimeProfile("sine", mean=1, amplitude=1, frequency=2).samples(0.0, 3)
    assert times[-1] == pytest.approx(1.0)  # no run end: two cycles


# ---------------------------------------------------------------- built and stored


def test_build_stores_quantities_in_si_and_reads_back() -> None:
    stored = build("velocity", {**SINE, "phase": "90 [deg]"}, _si)
    assert stored["frequency"] == {"value": 4.0, "unit": "Hz"}
    assert stored["phase"]["unit"] == "rad"
    assert stored["phase"]["value"] == pytest.approx(math.pi / 2)
    profile = from_stored("velocity", stored)
    assert profile.value_at(0.0) == pytest.approx(2.5)
    table = build(
        "velocity", {"form": "table", "rows": [[0, 1], {"time": "1 [s]", "value": 2}]}, _si
    )
    assert [r["time"]["value"] for r in table["rows"]] == [0.0, 1.0]
    assert check_stored(None, {"velocity": table}) == {"velocity": table}
    assert check_stored(None, None) is None


@pytest.mark.parametrize(
    ("parameter", "raw", "field"),
    [
        (
            "velocity",
            {"form": "sine", "mean": 1, "amplitude": 1, "frequency": 0, "phase": 0},
            "frequency",
        ),
        (
            "velocity",
            {"form": "sine", "mean": 1, "amplitude": -1, "frequency": 1, "phase": 0},
            "amplitude",
        ),
        (
            "velocity",
            {"form": "sine", "mean": 0, "amplitude": 1, "frequency": 1, "phase": 0},
            "value",
        ),
        ("velocity", {"form": "square", "low": 1, "high": 2, "period": 1, "duty": 1}, "duty"),
        ("velocity", {"form": "square", "low": 1, "high": 2, "period": 0, "duty": 0.5}, "period"),
        ("velocity", {"form": "table", "rows": [[0, 1]]}, "rows"),
        ("velocity", {"form": "table", "rows": [[0, 1], [0, 2]]}, "rows.1.time"),
        ("velocity", {"form": "table", "rows": [[0, 1], "x"]}, "rows.1"),
        (
            "temperature",
            {"form": "sine", "mean": 300, "amplitude": 400, "frequency": 1, "phase": 0},
            "amplitude",
        ),
        ("velocity", {"form": "ramp"}, "form"),
        (
            "velocity",
            {"form": "sine", "mean": "fast", "amplitude": 1, "frequency": 1, "phase": 0},
            "mean",
        ),
        ("velocity", {"form": "sine", "amplitude": 1, "frequency": 1, "phase": 0}, "mean"),
        ("density", {"form": "sine"}, "parameter"),
    ],
)
def test_build_refuses_with_the_field_that_is_wrong(
    parameter: str, raw: dict[str, Any], field: str
) -> None:
    def evaluate(text: str, dim: str) -> tuple[float, str | None]:
        if text == "fast":
            raise InvalidValue("not a quantity")
        return _si(text, dim)

    with pytest.raises(InvalidProfile) as caught:
        build(parameter, raw, evaluate)
    assert caught.value.code == "boundary.invalid_profile"
    assert caught.value.details["field"] == field


def test_a_pressure_may_go_negative_and_a_flow_may_reverse_for_part_of_a_cycle() -> None:
    raw = {"form": "sine", "mean": -100, "amplitude": 500, "frequency": 10, "phase": 0}
    assert from_stored("pressure", build("pressure", raw, _si)).extremes() == (-600.0, 400.0)
    flow = {"form": "sine", "mean": 1, "amplitude": 2, "frequency": 10, "phase": 0}
    assert from_stored("velocity", build("velocity", flow, _si)).extremes()[0] == -1.0


def test_stored_form_is_checked_for_units() -> None:
    stored = build("velocity", SINE, _si)
    stored["mean"] = {"value": 2.0, "unit": "km/h"}
    with pytest.raises(InvalidProfile, match="stored in"):
        check_stored(None, {"velocity": stored})


def test_varying_lists_profiles_in_use_only() -> None:
    held = {"velocity": {}, "pressure": {}}
    assert varying("velocity_inlet", {"profiles": held}) == ("velocity",)
    assert varying("velocity_inlet", {"profiles": held, "direction": "components"}) == ()
    assert varying("wall", {"profiles": held}) == ()


# ---------------------------------------------------------------- the commands


@pytest.fixture
def inlet(tmp_path: Path) -> Session:
    session = new_session(tmp_path, "first")
    a = add_part(session, "a", part(cube(0.01, name="a")))
    session.execute(
        "boundary.assign",
        target=[{"part": a, "faces": [0]}],
        kind="velocity_inlet",
        values={"velocity": "2 [m/s]"},
    )
    session.execute(
        "boundary.assign",
        target=[{"part": a, "faces": [1]}],
        kind="pressure_outlet",
        values={"pressure": "0 [Pa]"},
    )
    return session


def test_set_profile_stores_it_keeps_the_constant_and_undoes(inlet: Session) -> None:
    session = inlet
    project = session.require_project()
    done = session.execute(
        "boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE
    )
    node = project.get(INLET)
    assert node.params["profiles"]["velocity"]["mean"] == {"value": 2.0, "unit": "m/s"}
    assert node.params["velocity"].value == 2.0  # the constant stays for when the profile goes
    assert done["profile"]["form"] == "sine" and done["profile"]["cycle"] == 0.25
    assert done["profile"]["mean"] == 2.0 and done["profile"]["min"] == 1.5
    assert session.execute("history.state")["undo"] == "vary inlet velocity in time"
    session.execute("history.undo")
    assert node.params.get("profiles") is None
    session.execute("history.redo")
    assert "velocity" in node.params["profiles"]
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=None)
    assert node.params.get("profiles") is None


def test_set_profile_takes_expressions_and_keeps_their_text(inlet: Session) -> None:
    session = inlet
    session.execute("parameter.define", name="U_in", dimension="velocity", value="3 [m/s]")
    profile = {"form": "table", "rows": [["0 [s]", "U_in"], ["0.1 [s]", "U_in * 2"]]}
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=profile)
    rows = session.require_project().get(INLET).params["profiles"]["velocity"]["rows"]
    assert rows[1]["value"] == {"value": 6.0, "unit": "m/s", "expr": "U_in * 2"}


def test_set_profile_refuses_what_the_kind_does_not_vary(inlet: Session) -> None:
    session = inlet
    outlet = f"{FLUID}/boundaries/outlet"
    with pytest.raises(InvalidValue, match="cannot vary velocity"):
        session.execute("boundary.set_profile", boundary=outlet, parameter="velocity", profile=SINE)
    session.execute("boundary.set", boundary=INLET, values={"direction": "components"})
    with pytest.raises(InvalidValue, match="does not use velocity"):
        session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE)
    with pytest.raises(InvalidProfile) as caught:
        session.execute(
            "boundary.set_profile",
            boundary=outlet,
            parameter="pressure",
            profile={"form": "table", "rows": [[0, 0]]},
        )
    assert caught.value.details == {"parameter": "pressure", "field": "rows"}


def test_set_profile_is_one_journal_record_that_replays(inlet: Session, tmp_path: Path) -> None:
    session = inlet
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE)
    square = {
        "form": "square",
        "low": "0 [Pa]",
        "high": "500 [Pa]",
        "period": "20 [ms]",
        "duty": 0.3,
    }
    session.execute(
        "boundary.set_profile",
        boundary=f"{FLUID}/boundaries/outlet",
        parameter="pressure",
        profile=square,
    )
    records = Journal.read(tmp_path / "first.jsonl")
    assert [r["command"] for r in records[-2:]] == ["boundary.set_profile"] * 2
    second = new_session(tmp_path, "second")
    replay(second, records[1:])
    for path in (INLET, f"{FLUID}/boundaries/outlet"):
        mine = session.require_project().get(path)
        theirs = second.require_project().get(path)
        assert (theirs.id, theirs.params["profiles"]) == (mine.id, mine.params["profiles"])


def test_preview_samples_a_draft_or_names_the_wrong_field(inlet: Session) -> None:
    session = inlet
    reply = session.execute(
        "boundary.profile_preview", parameter="velocity", profile=SINE, points=5
    )
    assert reply["ok"] is True and reply["profile"]["mean"] == 2.0
    assert len(reply["times"]) == 5 and reply["end_time"] == pytest.approx(0.5)
    assert reply["run_end_time"] is None
    bad = session.execute(
        "boundary.profile_preview",
        parameter="velocity",
        profile={**SINE, "frequency": "-1 [Hz]"},
    )
    assert bad == {"ok": False, "field": "frequency", "message": "the frequency must be above zero"}
    wrong_unit = session.execute(
        "boundary.profile_preview", parameter="velocity", profile={**SINE, "mean": "2 [Pa]"}
    )
    assert wrong_unit["ok"] is False and wrong_unit["field"] == "mean"


def test_preview_runs_without_a_project() -> None:
    session = Session()
    reply = session.execute(
        "boundary.profile_preview",
        parameter="pressure",
        profile={"form": "table", "rows": [[0, 0], [1, 100]]},
        end_time=2.0,
        points=3,
    )
    assert reply["ok"] is True and reply["values"][-1] == 100.0


def test_kinds_say_which_values_vary(inlet: Session) -> None:
    kinds = {k["id"]: k for k in inlet.execute("boundary.kinds")}
    assert kinds["velocity_inlet"]["profiled"] == ["velocity", "temperature"]
    assert kinds["pressure_outlet"]["profiled"] == ["pressure"]
    assert kinds["wall"]["profiled"] == []


def test_a_change_of_kind_drops_the_profiles_the_new_kind_cannot_vary(inlet: Session) -> None:
    """Checked by review: a velocity profile left hidden under a mass-flow inlet came back live
    when the kind was changed back. The kind change now takes it away in the same step."""
    session = inlet
    node = session.require_project().get(INLET)
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE)
    done = session.execute(
        "boundary.set",
        boundary=INLET,
        kind="mass_flow_inlet",
        values={"mass_flow": "0.001 [kg/s]"},
    )
    assert done["profiles_dropped"] == ["velocity"] and node.params.get("profiles") is None
    session.execute("boundary.set", boundary=INLET, kind="velocity_inlet")
    assert node.params.get("profiles") is None
    listed = {b["name"]: b for b in session.execute("boundary.list")}
    assert listed["inlet"]["varies"] == []
    session.execute("history.undo")
    session.execute("history.undo")  # the kind change and its drop are one step
    assert "velocity" in node.params["profiles"]


def test_the_node_summary_says_what_varies(inlet: Session) -> None:
    """The tree's marker and the Results notes read ``varies`` from the summary, the server's rule
    (kind and switches), not the raw stored profiles."""
    session = inlet
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE)
    assert session.execute("node.get", node=INLET)["varies"] == ["velocity"]
    session.execute("parameter.set", node=INLET, name="velocity_x", value="1 [m/s]")
    session.execute("parameter.set", node=INLET, name="velocity_y", value="0 [m/s]")
    session.execute("parameter.set", node=INLET, name="velocity_z", value="0 [m/s]")
    done = session.execute("boundary.set", boundary=INLET, values={"direction": "components"})
    assert done["profiles_dropped"] == ["velocity"]  # not in use: taken away, not kept hidden
    assert "varies" not in session.execute("node.get", node=INLET)
    outlet = session.execute("node.get", node=f"{FLUID}/boundaries/outlet")
    assert "varies" not in outlet


def test_a_refusal_names_the_row_as_the_editor_numbers_it() -> None:
    with pytest.raises(InvalidProfile) as caught:
        build("velocity", {"form": "table", "rows": [[0, 1], ["", 2]]}, _si)
    assert caught.value.details["field"] == "rows.1.time"
    assert caught.value.message == "row 2 time needs a value"


# ---------------------------------------------------------------- a temperature swing


def _preview(session: Session, parameter: str, profile: dict[str, Any]) -> dict[str, Any]:
    reply: dict[str, Any] = session.execute(
        "boundary.profile_preview", parameter=parameter, profile=profile, points=3
    )
    return reply


@pytest.mark.parametrize(
    ("amplitude", "kelvin"),
    [("5 [degC]", 5.0), ("9 [degF]", 5.0), ("5 [K]", 5.0), ("5 [delta_degC]", 5.0), (5, 5.0)],
)
def test_a_temperature_amplitude_is_a_difference(amplitude: Any, kelvin: float) -> None:
    """Checked by review: '5 [degC]' as an amplitude became 278.15 K (a swing of 556 K) and the
    preview said ok. An amplitude is a swing, so an offset unit is the size of its degree."""
    sine = {"form": "sine", "mean": "20 [degC]", "amplitude": amplitude, "frequency": "1 [Hz]"}
    reply = _preview(Session(), "temperature", sine)
    assert reply["ok"] is True
    assert reply["profile"]["amplitude"] == pytest.approx(kelvin)
    assert reply["profile"]["mean"] == pytest.approx(293.15)
    assert reply["profile"]["min"] == pytest.approx(293.15 - kelvin)
    assert reply["stored"]["amplitude"]["unit"] == "K"


@pytest.mark.parametrize("amplitude", ["swing", "5 [degC] + 1 [K]"])
def test_a_temperature_amplitude_read_as_a_level_gets_a_note(
    inlet: Session, amplitude: str
) -> None:
    """Checked by review: a parameter holding '5 [degC]', or '5 [degC] + 1 [K]', is a level inside
    an expression (278.15 K, 279.15 K) and the preview said ok with nothing to say."""
    session = inlet
    session.execute("parameter.define", name="swing", dimension="temperature", value="5 [degC]")
    sine = {"form": "sine", "mean": "20 [degC]", "amplitude": amplitude, "frequency": "1 [Hz]"}
    reply = _preview(session, "temperature", sine)
    assert reply["ok"] is True and reply["profile"]["amplitude"] > 270
    assert [n["field"] for n in reply["notes"]] == ["amplitude"]
    assert "level" in reply["notes"][0]["message"] and "278.15 K" in reply["notes"][0]["message"]
    done = session.execute(
        "boundary.set_profile", boundary=INLET, parameter="temperature", profile=sine
    )
    assert [n["field"] for n in done["notes"]] == ["amplitude"]
    plain = _preview(session, "temperature", {**sine, "amplitude": "5 [degC]"})
    assert plain["notes"] == []


@pytest.mark.parametrize("amplitude", ["2 * 5 [degC]", "2.5 [degC]*2"])
def test_an_amplitude_past_the_mean_is_refused_on_the_amplitude(amplitude: str) -> None:
    """Checked by review: the refusal named the field 'value' (the editor's form line) and said
    only that the temperature must stay above 0 K, when the amplitude was read as 556 K."""
    sine = {"form": "sine", "mean": "20 [degC]", "amplitude": amplitude, "frequency": "1 [Hz]"}
    reply = _preview(Session(), "temperature", sine)
    assert reply["ok"] is False and reply["field"] == "amplitude"
    assert "is not below the mean" in reply["message"] and "level" in reply["message"]


@pytest.mark.parametrize(
    ("profile", "field"),
    [
        ({"form": "sine", "mean": -1, "amplitude": 0, "frequency": 1}, "mean"),
        ({"form": "square", "low": 0, "high": 300, "period": 1, "duty": 0.5}, "low"),
        ({"form": "square", "low": 300, "high": -1, "period": 1, "duty": 0.5}, "high"),
        ({"form": "table", "rows": [[0, 300], [1, 0]]}, "rows.1.value"),
    ],
)
def test_a_temperature_at_zero_kelvin_is_refused_where_it_is(
    profile: dict[str, Any], field: str
) -> None:
    reply = _preview(Session(), "temperature", profile)
    assert reply["ok"] is False and reply["field"] == field
    assert "above 0 K" in reply["message"]


def test_pulse_levels_and_table_values_stay_absolute_temperatures() -> None:
    session = Session()
    pulse = {"form": "square", "low": "20 [degC]", "high": "68 [degF]", "period": 1, "duty": 0.5}
    reply = _preview(session, "temperature", pulse)
    assert reply["profile"]["low"] == pytest.approx(293.15)
    assert reply["profile"]["high"] == pytest.approx(293.15)
    table = {"form": "table", "rows": [["0 [s]", "20 [degC]"], ["1 [s]", "30 [degC]"]]}
    rows = _preview(session, "temperature", table)["profile"]["rows"]
    assert [v for _t, v in rows] == [pytest.approx(293.15), pytest.approx(303.15)]


def test_a_pulse_with_high_below_low_is_accepted_with_a_note() -> None:
    session = Session()
    pulse = {"form": "square", "low": "0.5 [m/s]", "high": "0.2 [m/s]", "period": 1, "duty": 0.5}
    reply = _preview(session, "velocity", pulse)
    assert reply["ok"] is True
    assert [n["field"] for n in reply["notes"]] == ["high"]
    assert "below low" in reply["notes"][0]["message"]
    upright = _preview(session, "velocity", {**pulse, "low": "0.2 [m/s]", "high": "0.5 [m/s]"})
    assert upright["notes"] == []


# ---------------------------------------------------------------- every path that changes the kind


def test_parameter_set_on_the_kind_drops_profiles_in_the_same_step(inlet: Session) -> None:
    """Checked by review: only boundary.set dropped them; parameter.set kept a hidden speed
    profile that came back live when the kind was changed back."""
    session = inlet
    node = session.require_project().get(INLET)
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE)
    done = session.execute("parameter.set", node=INLET, name="kind", value="mass_flow_inlet")
    assert done["profiles_dropped"] == ["velocity"]
    assert node.params["kind"] == "mass_flow_inlet" and node.params.get("profiles") is None
    session.execute("history.undo")  # one step: the kind and the profile come back together
    assert node.params["kind"] == "velocity_inlet" and "velocity" in node.params["profiles"]
    session.execute("history.redo")
    assert node.params.get("profiles") is None


def test_parameter_set_on_the_direction_drops_the_speed_profile(inlet: Session) -> None:
    session = inlet
    node = session.require_project().get(INLET)
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE)
    session.execute("parameter.set", node=INLET, name="direction", value="vector")
    assert "velocity" in node.params["profiles"]  # a vector inlet still varies its speed
    done = session.execute("parameter.set", node=INLET, name="direction", value="components")
    assert done["profiles_dropped"] == ["velocity"] and node.params.get("profiles") is None
    session.execute("parameter.set", node=INLET, name="direction", value="normal")
    assert node.params.get("profiles") is None
    session.execute("history.undo")
    session.execute("history.undo")
    assert node.params["direction"] == "vector" and "velocity" in node.params["profiles"]


def test_parameter_set_replays_the_drop(inlet: Session, tmp_path: Path) -> None:
    session = inlet
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE)
    session.execute("parameter.set", node=INLET, name="kind", value="mass_flow_inlet")
    second = new_session(tmp_path, "second")
    replay(second, Journal.read(tmp_path / "first.jsonl")[1:])
    theirs = second.require_project().get(INLET)
    assert theirs.params["kind"] == "mass_flow_inlet" and theirs.params.get("profiles") is None


def test_boundary_assign_reports_the_profiles_it_drops(inlet: Session) -> None:
    """The Full mode Condition picker changes the kind with boundary.assign: it says what it took
    away, as boundary.set does."""
    session = inlet
    session.execute("boundary.set_profile", boundary=INLET, parameter="velocity", profile=SINE)
    done = session.execute(
        "boundary.assign",
        target=INLET,
        kind="mass_flow_inlet",
        values={"mass_flow": "0.001 [kg/s]"},
    )
    assert done["profiles_dropped"] == ["velocity"]
    again = session.execute("boundary.assign", target=INLET, kind="mass_flow_inlet")
    assert again["profiles_dropped"] == []


def test_the_summary_follows_the_energy_switch_and_is_sent_again(inlet: Session) -> None:
    """``varies`` is ``varying_in`` (the server's one rule): a temperature profile counts while
    energy is solved, and a change of the solve settings re-sends the boundary's summary."""
    from stokes.api.summaries import node_event

    session = inlet
    project = session.require_project()
    profile = {"form": "sine", "mean": "300 [K]", "amplitude": "5 [K]", "frequency": "1 [Hz]"}
    session.execute(
        "boundary.set_profile", boundary=INLET, parameter="temperature", profile=profile
    )
    assert session.execute("node.get", node=INLET)["varies"] == []
    session.execute("solve.configure", engine="of14.fluid", values={"energy": True})
    assert session.execute("node.get", node=INLET)["varies"] == ["temperature"]
    settings = project.get("/solver/settings")
    sent = node_event(project, "node.changed", {"node_id": settings.id})
    also = {s["path"]: s for s in sent.get("also", [])}
    assert also[INLET]["varies"] == ["temperature"]
