"""`results.streams`: openings grouped by region; each stream's flow, pressure drop and heat."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from stokes.api.commands import results_streams


class _Node(SimpleNamespace):
    def walk(self) -> Any:
        yield self
        for child in getattr(self, "children", []):
            yield from child.walk()


def _project() -> Any:
    tags = {
        f"t_{n}": _Node(type="tag", name=n, params={}, children=[])
        for n in ("hot_in", "hot_out", "cold_in", "cold_out")
    }

    def boundary(name: str, kind: str) -> _Node:
        return _Node(
            type="boundary", name=name, params={"kind": kind, "tag": f"t_{name}"}, children=[]
        )

    def region(name: str, kind: str, material: str, *children: _Node) -> _Node:
        return _Node(
            type="region",
            name=name,
            params={"kind": kind, "material": material},
            children=list(children),
        )

    root = _Node(
        type="group",
        name="",
        params={},
        children=[
            region(
                "hot",
                "fluid",
                "water",
                boundary("hot_in", "velocity_inlet"),
                boundary("hot_out", "pressure_outlet"),
            ),
            region(
                "cold",
                "fluid",
                "water",
                boundary("cold_in", "velocity_inlet"),
                boundary("cold_out", "pressure_outlet"),
            ),
            region("wall", "solid", "aluminium"),
            *tags.values(),
        ],
    )
    return SimpleNamespace(root=root, find=tags.get)


def test_the_openings_are_grouped_by_their_region_and_solids_are_left_out() -> None:
    found = results_streams._openings(_project())
    assert found == {
        "hot": {"inlets": ["hot_in"], "outlets": ["hot_out"], "material": "water"},
        "cold": {"inlets": ["cold_in"], "outlets": ["cold_out"], "material": "water"},
    }


def test_a_stream_s_pressure_drop_and_heat_come_from_its_monitors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(results_streams, "_specific_heat", lambda ctx, material, t: 4180.0)
    monitors = {
        "hot_in": {"flow_rate": -0.002, "p_mean": 101400.0, "T_area": 351.0, "T_bulk": 350.0},
        "hot_out": {"flow_rate": 0.002, "p_mean": 101325.0, "T_area": 334.0, "T_bulk": 330.0},
    }
    stream = results_streams._stream
    found = stream(None, "hot", "water", ["hot_in"], ["hot_out"], monitors, True)  # type: ignore[arg-type]
    assert found["pressure_drop"] == pytest.approx(75.0)
    assert found["mass_flow"] == pytest.approx(0.002)
    assert found["heat"] == pytest.approx(0.002 * 4180.0 * (330.0 - 350.0))


def test_a_run_without_the_bulk_temperature_has_no_heat(monkeypatch: pytest.MonkeyPatch) -> None:
    """**The area average is never a duty** (2026-11-24): a run recorded before the bulk monitor
    existed has only the area-averaged `T`, and its heat is not given rather than given low."""
    monkeypatch.setattr(results_streams, "_specific_heat", lambda ctx, material, t: 4180.0)
    monitors = {
        "hot_in": {"flow_rate": -0.002, "p_mean": 101400.0, "T_area": 350.0},
        "hot_out": {"flow_rate": 0.002, "p_mean": 101325.0, "T_area": 330.0},
    }
    stream = results_streams._stream
    found = stream(None, "hot", "water", ["hot_in"], ["hot_out"], monitors, True)  # type: ignore[arg-type]
    assert found["heat"] is None and found["T_out"] is None
    assert found["pressure_drop"] == pytest.approx(75.0)


def test_a_flow_only_run_has_no_heat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(results_streams, "_specific_heat", lambda ctx, material, t: 4180.0)
    monitors = {
        "in": {"flow_rate": -1e-5, "p_mean": 0.3},
        "out": {"flow_rate": 1e-5, "p_mean": 0.0},
    }
    stream = results_streams._stream
    found = stream(None, "fluid", "air", ["in"], ["out"], monitors, False)  # type: ignore[arg-type]
    assert found["pressure_drop"] == pytest.approx(0.3)
    assert found["heat"] is None and found["T_in"] is None
