"""``units.convert`` through ``Session.execute``: scaled and offset units, errors, the journal."""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Journal, Session, default_registry, replay
from stokes.api.commands.units import MAX_UNIT_TEXT
from stokes.errors import UnitConversionError, ValidationError


def convert(value: float, from_unit: str, to_unit: str) -> dict[str, float | str]:
    result: dict[str, float | str] = Session().execute(  # no project is needed
        "units.convert", value=value, from_unit=from_unit, to_unit=to_unit
    )
    return result


def test_the_command_is_registered_as_a_query() -> None:
    schema = {c["name"]: c for c in default_registry().json_schema()}["units.convert"]
    assert schema["mutates"] is False and schema["requires_project"] is False
    assert schema["input_schema"]["required"] == ["value", "from_unit", "to_unit"]


def test_a_scaled_unit_has_a_factor_and_no_offset() -> None:
    result = convert(2.5, "m", "mm")
    assert result == {"value": 2500.0, "from": "m", "to": "mm", "factor": 1000.0, "offset": 0.0}


def test_zero_in_a_scaled_unit_still_reports_the_factor() -> None:
    result = convert(0, "m", "mm")
    assert result["value"] == 0.0 and result["factor"] == 1000.0 and result["offset"] == 0.0


def test_a_temperature_has_an_offset() -> None:
    result = convert(25, "degC", "K")
    assert result["value"] == pytest.approx(298.15)
    assert result["factor"] == pytest.approx(1.0) and result["offset"] == pytest.approx(273.15)
    assert convert(100, "degF", "degC")["value"] == pytest.approx(37.7777777778)
    assert convert(-40, "degF", "degC")["value"] == pytest.approx(-40.0)


def test_a_tiny_value_keeps_its_factor_and_offset() -> None:
    """The factor used to come from (converted - offset) / value, which loses it at 1e-12."""
    result = convert(1e-12, "degC", "K")
    assert result["value"] == pytest.approx(273.15)
    assert result["factor"] == pytest.approx(1.0, abs=1e-12)
    assert result["offset"] == pytest.approx(273.15)


@pytest.mark.parametrize(
    ("from_unit", "to_unit"),
    [("bogus_unit", "m"), ("m", "bogus_unit"), ("m", "s"), ("m/", "m"), ("m**", "m")],
)
def test_a_conversion_that_cannot_be_made_has_one_error_code(from_unit: str, to_unit: str) -> None:
    with pytest.raises(UnitConversionError) as caught:
        convert(1.0, from_unit, to_unit)
    assert caught.value.code == "units.convert" and isinstance(caught.value, ValidationError)
    assert caught.value.details == {"from": from_unit, "to": to_unit}


def test_unit_text_is_capped() -> None:
    long_text = "m" * (MAX_UNIT_TEXT + 1)
    for from_unit, to_unit in ((long_text, "m"), ("m", long_text)):
        with pytest.raises(UnitConversionError) as caught:
            convert(1.0, from_unit, to_unit)
        assert str(MAX_UNIT_TEXT) in caught.value.message
        assert caught.value.details == {"length": len(long_text), "limit": MAX_UNIT_TEXT}
    assert MAX_UNIT_TEXT == 64


def test_a_conversion_is_not_journaled_and_replays_clean(tmp_path: Path) -> None:
    """A query leaves no record; the journal of the session around it replays to the same state,
    and a journal that does carry the command (written by hand) replays it harmlessly."""
    journal = tmp_path / "journal.jsonl"
    session = Session(journal_path=journal)
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    session.execute("parameter.define", name="L", dimension="length", value="2 [m]")
    answer = session.execute("units.convert", value=25, from_unit="degC", to_unit="K")
    records = Journal.read(journal)
    assert [r["command"] for r in records] == ["project.new", "parameter.define"]
    fresh = Session()
    fresh.execute("project.new", name="other")
    replay(fresh, records[1:])
    assert fresh.execute("node.tree")["children"] == session.execute("node.tree")["children"]
    assert fresh.execute("units.convert", value=25, from_unit="degC", to_unit="K") == answer
    by_hand = {
        "command": "units.convert",
        "params": {"value": 25, "from_unit": "degC", "to_unit": "K"},
    }
    assert replay(Session(), [by_hand]) == [answer]
