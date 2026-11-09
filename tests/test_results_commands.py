"""The `results.*` commands through `Session.execute`: reports, histogram, XY on a surface, contour
lines, the calculator and saved field functions (undo, journal replay, save and reopen).

The surfaces are synthetic (a unit plate standing in for a patch, another for the slice) so the
numbers are known by hand; the loader is the one seam replaced. The real join of the run's field
cache and the mesh's surfaces is checked on a solved case in `test_results_engine.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Session, replay
from stokes.api.commands import results as results_commands
from stokes.api.journal import Journal
from stokes.errors import StokesError
from tests.test_post_integrals import plate


def _loaded(ref: str) -> Any:
    surface = plate(10)
    surface = type(surface)(ref, surface.positions, surface.triangles)
    n = surface.positions.shape[0]
    x = surface.positions[:, 0]
    velocity = np.tile([0.0, 0.0, 2.0], (n, 1))
    fields = {"U": velocity, "U_mag": np.full(n, 2.0), "p": 1.0 + x}
    units = {"U": "m/s", "U_mag": "m/s", "p": "m2/s2"}
    kind = "slice" if ref == "slice" else "patch"
    return results_commands._Loaded(
        surface, fields, units, kind, node_id="" if kind == "slice" else "n_00000001"
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, session: Session) -> Session:
    known = {"inlet", "outlet", "slice"}

    def load(_ctx: Any, _run: Any, ref: str, _plane: Any) -> Any:
        if ref not in known:
            raise StokesError(f"no surface {ref}", code="results.no_surface")
        return _loaded(ref)

    monkeypatch.setattr(results_commands, "_load", load)
    monkeypatch.setattr(results_commands, "_run_fields", lambda _c, _r: ("r_test", b"", {}))
    monkeypatch.setattr(results_commands, "_density", lambda _c, given: given or 1000.0)
    return session


def test_a_surface_report_has_a_row_per_surface_and_a_net_row(fake: Session) -> None:
    out = fake.execute("results.surface_integrals", surfaces=["inlet", "outlet"], field="p")
    assert out["run"] == "r_test" and out["field_units"] == "m2/s2"
    assert [r["surface"] for r in out["rows"]] == ["inlet", "outlet"]
    row = out["rows"][0]
    assert row["area"] == pytest.approx(1.0)
    assert row["area_avg"] == pytest.approx(1.5)
    assert row["flow_rate"] == pytest.approx(2.0)
    assert row["mass_flow"] == pytest.approx(2000.0)
    assert row["mass_avg"] == pytest.approx(1.5)
    assert out["net"]["flow_rate"] == pytest.approx(4.0)
    assert out["units"]["integral"] == "m2/s2 m2" and out["units"]["flow_rate"] == "m3/s"


def test_a_report_without_a_field_gives_area_and_flow(fake: Session) -> None:
    out = fake.execute("results.surface_integrals", surfaces=["slice"])
    assert out["rows"][0]["area"] == pytest.approx(1.0)
    assert out["rows"][0]["area_avg"] is None and out["rows"][0]["flow_rate"] == pytest.approx(2.0)


def test_a_report_of_an_expression_and_of_a_component(fake: Session) -> None:
    total = fake.execute("results.surface_integrals", surfaces=["inlet"], field="p + 0.5*U_mag^2")
    assert total["rows"][0]["area_avg"] == pytest.approx(3.5)
    assert total["field_units"] == "m2/s2"
    w = fake.execute("results.surface_integrals", surfaces=["inlet"], field="U_z")
    assert w["rows"][0]["area_avg"] == pytest.approx(2.0)


def test_refusals_carry_codes(fake: Session) -> None:
    with pytest.raises(StokesError) as caught:
        fake.execute("results.surface_integrals", surfaces=["inlet"], field="U")
    assert caught.value.code == "results.vector_field"
    with pytest.raises(StokesError) as caught:
        fake.execute("results.surface_integrals", surfaces=["inlet"], field="U_mag + p")
    assert caught.value.code == "units.mismatch"
    with pytest.raises(StokesError) as caught:
        fake.execute("results.surface_integrals", surfaces=[], field="p")
    assert caught.value.code == "results.no_surface"
    with pytest.raises(StokesError):
        fake.execute("results.surface_integrals", surfaces=["nowhere"], field="p")


def test_histogram_plot_contours_and_evaluate(fake: Session) -> None:
    hist = fake.execute("results.histogram", surfaces=["inlet"], field="p", bins=4)
    assert sum(hist["fraction"]) == pytest.approx(1.0) and hist["units"] == "m2/s2"
    plot = fake.execute("results.surface_plot", surfaces=["inlet", "slice"], field="p", axis="x")
    assert [s["surface"] for s in plot["series"]] == ["inlet", "slice"]
    assert plot["series"][0]["y"] == pytest.approx([1 + x for x in plot["series"][0]["x"]])
    lines = fake.execute("results.contour_lines", surface="slice", field="p", levels=3)
    assert [round(level["level"], 6) for level in lines["levels"]] == [1.25, 1.5, 1.75]
    assert all(len(level["segments"]) > 0 for level in lines["levels"])
    chosen = fake.execute("results.contour_lines", surface="slice", field="p", levels=[1.5])
    assert len(chosen["levels"]) == 1
    checked = fake.execute("results.evaluate", surface="inlet", field="rho * p")
    assert checked["units"] == "Pa" and checked["max"] == pytest.approx(2000.0)


def test_a_saved_function_is_usable_by_name(fake: Session) -> None:
    saved = fake.execute("results.function_save", name="p_total", definition="p + 0.5*U_mag^2")
    assert saved["replaced"] is False
    out = fake.execute("results.surface_integrals", surfaces=["inlet"], field="p_total")
    assert out["rows"][0]["area_avg"] == pytest.approx(3.5)
    # A function may use another, by name inside an expression.
    fake.execute("results.function_save", name="twice", definition="2 * p_total")
    out = fake.execute("results.surface_integrals", surfaces=["inlet"], field="twice")
    assert out["rows"][0]["area_avg"] == pytest.approx(7.0)


def test_function_save_list_replace_remove_and_undo(session: Session) -> None:
    assert session.execute("results.function_list") == {"functions": []}
    first = session.execute("results.function_save", name="q", definition="0.5 * U_mag^2")
    node = session.require_project().get(first["id"])
    assert node.type == "field_function" and node.path.startswith("/field_functions/")
    again = session.execute("results.function_save", name="q", definition="U_mag^2")
    assert again["replaced"] is True and again["id"] == first["id"]
    listed = session.execute("results.function_list")["functions"]
    assert listed == [{"id": first["id"], "name": "q", "definition": "U_mag^2"}]
    session.execute("history.undo")
    assert session.execute("results.function_list")["functions"][0]["definition"] == "0.5 * U_mag^2"
    session.execute("results.function_remove", function="q")
    assert session.execute("results.function_list") == {"functions": []}


@pytest.mark.parametrize(
    ("name", "definition", "code"),
    [
        ("2bad", "p", "field_function.name_invalid"),
        ("x", "p", "field_function.name_invalid"),
        ("q", "p +", "units.expression"),
        ("q", "q + 1", "units.expression"),
    ],
)
def test_a_bad_function_is_refused(session: Session, name: str, definition: str, code: str) -> None:
    with pytest.raises(StokesError) as caught:
        session.execute("results.function_save", name=name, definition=definition)
    assert caught.value.code == code


def test_functions_replay_from_the_journal_and_survive_save(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    first = Session(journal_path=journal)
    first.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    saved = first.execute("results.function_save", name="q", definition="0.5 * U_mag^2")
    first.execute("results.function_save", name="r", definition="p")
    first.execute("results.function_remove", function="r")
    first.execute("project.save")

    second = Session()
    second.execute("project.new", name="demo", path=str(tmp_path / "again"))
    replay(
        second, [r for r in Journal.read(journal) if r["command"].startswith("results.function")]
    )
    assert second.execute("results.function_list")["functions"] == [
        {"id": saved["id"], "name": "q", "definition": "0.5 * U_mag^2"}
    ]

    reopened = Session()
    reopened.execute("project.open", path=str(tmp_path / "demo.stokes"))
    assert [f["name"] for f in reopened.execute("results.function_list")["functions"]] == ["q"]


def test_a_project_without_results_says_so(session: Session) -> None:
    with pytest.raises(StokesError):
        session.execute("results.surfaces")


def test_a_radius_plot_measures_from_the_surface_s_own_axis(fake: Session) -> None:
    plot = fake.execute("results.surface_plot", surfaces=["outlet"], field="p", axis="radius")
    xs = plot["series"][0]["x"]
    assert min(xs) == pytest.approx(0.0, abs=1e-9)
    assert max(xs) == pytest.approx(2**0.5 / 2)


def test_a_fitted_range(fake: Session) -> None:
    out = fake.execute("results.range", surfaces=["slice"], field="p")
    assert out["min"] == pytest.approx(1.0) and out["max"] == pytest.approx(2.0)
    assert 1.0 <= out["low"] < out["high"] <= 2.0
    assert out["units"] == "m2/s2"
