"""CoolProp through the materials worker (ADR-0018 slice 2; contract "Tests"): reference states
against the NIST WebBook (IAPWS-95 and its transport formulations; nitrogen) and dry air through
CoolProp's Lemmon formulation, fits of water and air, an INCOMP glycol, a fluid with no
transport models, the phase rules, mixtures, transport coverage in searches, and the session's
evaluation cache. Skipped without CoolProp (the 'materials' extra)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.errors import ValidationError
from stokes.physics.materials.errors import (
    FluidUnknown,
    OutOfRange,
    PropertyUnavailable,
)
from stokes.physics.materials.providers import get_provider
from tests.test_materials_support import (  # noqa: F401 - fixtures
    fit_methods,
    library_session,
    needs_coolprop,
    private_home,
    warm_worker,
)

pytestmark = [needs_coolprop, pytest.mark.usefixtures("warm_worker", "private_home")]

# fluid, T (K), (rho kg/m3, mu Pa s, kappa W/(m K), c_p J/(kg K)) at 101325 Pa: the contract's
# reference states (NIST WebBook; air from CoolProp's Lemmon formulation, as REFPROP gives it).
REFERENCE = [
    ("Water", 300.0, "liquid", (996.557, 853.742e-6, 0.609500, 4180.64)),
    ("Water", 350.0, "liquid", (973.728, 368.470e-6, 0.664874, 4194.47)),
    ("Water", 500.0, "gas", (0.440930, 17.2987e-6, 0.0360352, 1981.61)),
    ("Nitrogen", 300.0, "auto", (1.13816, 17.8901e-6, 0.0259687, 1041.36)),
    ("Air", 300.0, "auto", (1.17700, 18.5373e-6, 0.0263845, 1006.37)),
]
ORDER = ("density", "viscosity", "thermal_conductivity", "specific_heat")
TOLERANCE = {
    "density": 1e-4,
    "specific_heat": 1e-4,
    "viscosity": 1e-3,
    "thermal_conductivity": 1e-3,
}


@pytest.mark.parametrize(("fluid", "t", "phase", "expected"), REFERENCE)
def test_reference_states(
    fluid: str, t: float, phase: str, expected: tuple[float, float, float, float]
) -> None:
    grid = get_provider("coolprop").evaluate(fluid, "", "", phase, np.array([t]), 101325.0)
    assert grid.missing == []
    for prop, value in zip(ORDER, expected, strict=True):
        assert grid.values[prop][0] == pytest.approx(value, rel=TOLERANCE[prop]), (fluid, t, prop)


def test_water_fits_within_one_percent_over_280_to_370_k(tmp_path: Path) -> None:
    session = library_session(tmp_path)
    added = session.execute(
        "material.add_from_library", provider="coolprop", fluid="Water", T_min=280, T_max=370
    )
    assert added["background"] is False and added["missing"] == []
    # c_p of liquid water varies by less than 1 % over this range, so the constant (the value
    # at T_ref, the first candidate) wins, as it did for the built-in water_liquid record.
    assert fit_methods(added) == {
        "density": "rational_polynomial",
        "viscosity": "log_polynomial",
        "specific_heat": "constant",
        "thermal_conductivity": "polynomial",
    }
    assert all(fit["max_rel_err"] <= 0.01 for fit in added["fit"].values())
    params = added["params"]
    assert params["source"] == "library" and params["provider"] == "coolprop"
    assert params["phase"] == "auto" and params["fluid"] == "Water"
    project = session.require_project()
    meta = project.get(f"{added['path']}/viscosity").meta
    fit = meta["fit"]
    assert fit["provider"] == "coolprop" and fit["provider_version"].startswith("8.")
    assert fit["points"] == 101 and fit["T_min"] == 280.0 and fit["T_max"] == 370.0
    assert fit["reference"]["viscosity"] == "Huber-JPCRD-2009"
    assert fit["T_sat"] == pytest.approx(373.124, abs=1e-3)
    assert meta["signature"]["params"] == ["coefficients", "valid_max", "valid_min"]
    molar = project.get(f"{added['path']}/molar_mass").params["value"].value
    assert molar == pytest.approx(0.018015268)
    check = session.execute("material.check", material=added["name"])
    assert check["ok"] is True, check["findings"]
    both = session.execute("material.evaluate", material=added["name"], count=11, source="both")
    for prop in ORDER:
        assert both["properties"][prop]["max_rel_err"] <= 0.01


def test_air_takes_the_ideal_gas_law_and_the_default_range(tmp_path: Path) -> None:
    session = library_session(tmp_path)
    added = session.execute("material.add_from_library", provider="coolprop", fluid="Air")
    assert fit_methods(added)["density"] == "ideal_gas"
    assert added["params"]["T_min"]["value"] == pytest.approx(250.0)  # T_ref - 50 K
    assert added["params"]["T_max"]["value"] == pytest.approx(750.0)  # T_ref + 450 K
    assert added["fit"]["density"]["max_rel_err"] < 2e-3


def test_an_incompressible_glycol_by_mass(tmp_path: Path) -> None:
    session = library_session(tmp_path)
    info = session.execute(
        "material.library_info", provider="coolprop_incomp", fluid="MEG", composition="0.5"
    )
    assert info["limits"]["T_freeze"] == pytest.approx(237.16, abs=0.01)
    assert info["limits"]["fraction_max"] == pytest.approx(0.6)
    assert info["outputs"]["speed_of_sound"] is False
    assert info["basis"] == "mass" and info["molar_mass"] is None
    added = session.execute(
        "material.add_from_library", provider="coolprop_incomp", fluid="MEG", composition="0.5"
    )
    assert added["params"]["basis"] == "mass"
    assert added["params"]["T_min"]["value"] == pytest.approx(
        240.0
    )  # max(T_freeze + 1, T_ref - 60)
    assert fit_methods(added)["density"] == "rational_polynomial"
    with pytest.raises(ValidationError):
        session.execute(
            "material.add_from_library",
            provider="coolprop_incomp",
            fluid="MEG",
            composition="0.5",
            basis="mole",
        )
    with pytest.raises(FluidUnknown):
        session.execute(
            "material.add_from_library", provider="coolprop_incomp", fluid="MEG", composition="0.9"
        )


def test_a_fluid_without_transport_models(tmp_path: Path) -> None:
    session = library_session(tmp_path)
    added = session.execute("material.add_from_library", provider="coolprop", fluid="Neon")
    assert added["missing"] == ["thermal_conductivity", "viscosity"]
    assert set(added["fit"]) == {"density", "specific_heat"}
    found = session.execute("material.evaluate", material="Neon", count=3, source="library")
    assert found["properties"]["viscosity"]["missing"] is True
    assert found["properties"]["viscosity"]["library"] is None
    codes = [f["code"] for f in session.execute("material.check", material="Neon")["findings"]]
    assert codes.count("materials.property_unavailable") == 2
    with pytest.raises(PropertyUnavailable):
        session.execute("material.refit", material="Neon", properties=["viscosity"])
    with pytest.raises(PropertyUnavailable):
        session.execute(
            "material.add_from_library",
            provider="coolprop",
            fluid="Neon",
            methods={"viscosity": "polynomial"},
        )
    session.execute(
        "material.set_method",
        material="Neon",
        property="viscosity",
        method="constant",
        params={"value": "31.8 [uPa*s]"},
    )
    codes = [f["code"] for f in session.execute("material.check", material="Neon")["findings"]]
    assert codes.count("materials.property_unavailable") == 1


def test_phase_auto_refuses_a_range_across_saturation(tmp_path: Path) -> None:
    session = library_session(tmp_path)
    with pytest.raises(OutOfRange) as crossed:
        session.execute(
            "material.add_from_library", provider="coolprop", fluid="Water", T_min=350, T_max=400
        )
    assert "phase" in crossed.value.message
    forced = session.execute(
        "material.add_from_library",
        provider="coolprop",
        fluid="Water",
        phase="liquid",
        T_min=350,
        T_max=400,
    )
    findings = session.execute("material.check", material=forced["name"])["findings"]
    forced_rows = [f for f in findings if f["code"] == "materials.phase_forced"]
    assert len(forced_rows) == 1 and forced_rows[0]["severity"] == "info"
    assert "373.1" in forced_rows[0]["message"]


def test_mixtures_by_mole_fraction() -> None:
    provider = get_provider("coolprop")
    info = provider.info("Nitrogen&Oxygen", "0.79,0.21", "mole").to_json()
    assert info["molar_mass"] == pytest.approx(0.79 * 0.0280134 + 0.21 * 0.0319988, rel=1e-4)
    assert info["properties"]["viscosity"] is True
    assert info["values_at_ref"]["density"] == pytest.approx(1.1723, rel=1e-3)
    with pytest.raises(ValidationError):
        provider.info("Nitrogen&Oxygen", "0.7,0.2", "mole")  # does not add up to 1
    with pytest.raises(ValidationError):
        provider.info("Nitrogen&Oxygen", "0.79,0.21", "mass")
    with pytest.raises(FluidUnknown):
        provider.info("Unobtainium")
    predefined = provider.search("R410A.mix")[0]
    assert predefined.fluid == "R410A.mix" and predefined.basis == "mole"
    assert predefined.composition is not None and len(predefined.composition.split(",")) == 2


def test_searches_report_transport_coverage_and_rank_names_first() -> None:
    records = get_provider("coolprop").search("", "fluid")
    pure = [r for r in records if r.composition is None]
    assert len(pure) == 136
    viscosity = sum(r.properties["viscosity"] for r in pure)
    conductivity = sum(r.properties["thermal_conductivity"] for r in pure)
    neither = sum(
        not r.properties["viscosity"] and not r.properties["thermal_conductivity"] for r in pure
    )
    assert (viscosity, conductivity, neither) == (66, 63, 70)  # CoolProp 8.0.0, research note
    found = get_provider("coolprop").search("air")
    assert [r.fluid for r in found[:2]] == ["Air", "Air.mix"]  # the pseudo-pure fluid first
    assert get_provider("coolprop").search("7732-18-5")[0].fluid == "Water"
    assert get_provider("coolprop").search("", "solid") == []


def test_the_library_search_spans_providers(tmp_path: Path) -> None:
    # REFPROP's index (loading each of its fluids, about 5 s once) is left to its own tests.
    session = library_session(tmp_path)
    names = ["builtin", "coolprop", "coolprop_incomp"]
    found = session.execute("material.library_search", query="nitrogen", providers=names)
    providers = [r["provider"] for r in found["records"]]
    assert providers[:2] == ["builtin", "coolprop"]
    assert found["records"][1]["properties"]["viscosity"] is True
    glycols = session.execute("material.library_search", query="ethylene glycol", providers=names)
    assert {"builtin", "coolprop_incomp"} <= {r["provider"] for r in glycols["records"]}


def test_library_values_are_cached_per_session(tmp_path: Path) -> None:
    from stokes.physics.materials.service import MaterialsService

    session = library_session(tmp_path)
    session.execute("material.add_from_library", provider="coolprop", fluid="Water")
    service = MaterialsService.of(session)
    before = service.cache_size()
    first = session.execute("material.evaluate", material="Water", count=21, source="library")
    second = session.execute("material.evaluate", material="Water", count=21, source="library")
    assert first["properties"] == second["properties"]
    assert service.cache_size() == before + 1
    session.execute("material.evaluate", material="Water", count=31, source="library")
    assert service.cache_size() == before + 2


def test_set_state_with_refit_is_one_undo_step(tmp_path: Path) -> None:
    session = library_session(tmp_path)
    added = session.execute(
        "material.add_from_library", provider="coolprop", fluid="Water", T_min=280, T_max=370
    )
    viscosity = session.require_project().get(f"{added['path']}/viscosity")
    before = list(viscosity.params["coefficients"])
    changed = session.execute("material.set_state", material="Water", T_max=330)
    assert changed["params"]["T_max"]["value"] == 330.0 and changed["fit"]
    assert viscosity.params["coefficients"] != before
    assert viscosity.meta["fit"]["T_max"] == 330.0 and viscosity.status == "ok"
    assert session.execute("history.state")["undo"] == "set state of Water"
    session.execute("history.undo")
    assert viscosity.params["coefficients"] == before
    assert viscosity.meta["fit"]["T_max"] == 370.0
    stale = session.execute("material.set_state", material="Water", T_min=290, refit=False)
    assert stale["fit"] == {}
    assert viscosity.status == "stale"
    codes = [f["code"] for f in session.execute("material.check", material="Water")["findings"]]
    assert "materials.stale_fit" in codes
    refit = session.execute("material.refit", material="Water")
    assert set(refit["fit"]) == {"density", "viscosity", "specific_heat", "thermal_conductivity"}
    assert viscosity.status == "ok" and viscosity.meta["fit"]["T_min"] == 290.0


def test_a_journal_replays_library_materials_exactly(tmp_path: Path) -> None:
    from stokes.api import Journal, Session, replay

    journal = tmp_path / "journal.jsonl"
    first = Session(journal_path=journal)
    first.execute("project.new", name="demo")
    first.execute(
        "material.add_from_library",
        provider="coolprop",
        fluid="Water",
        T_min="7 degC",
        T_max=370,
        wait=False,  # recorded as wait: true, so the replay is deterministic
    )
    from stokes.physics.materials.service import MaterialsService

    assert MaterialsService.of(first).wait(timeout=60.0)
    first.execute(
        "parameter.set", node="/physics/materials/Water/viscosity", name="valid_max", value=360
    )
    first.execute("material.set_state", material="Water", T_max=350)
    records = Journal.read(journal)
    assert [r["command"] for r in records] == [
        "project.new",
        "material.add_from_library",
        "parameter.set",
        "material.set_state",
    ]
    second = Session()
    second.execute("project.new", name="other")
    replay(second, records[1:])
    root = "/physics/materials"
    left = first.execute("node.tree", root=root)
    right = second.execute("node.tree", root=root)
    assert left["children"][0]["params"] == right["children"][0]["params"]
    for mine, theirs in zip(
        left["children"][0]["children"], right["children"][0]["children"], strict=True
    ):
        assert mine["params"] == theirs["params"], mine["name"]
        assert mine.get("meta", {}).get("detached") == theirs.get("meta", {}).get("detached")


def test_providers_without_waiting_answer_at_once_and_announce_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from stokes.api import Session
    from stokes.physics.materials.providers import remote

    monkeypatch.setattr(remote._known, "report", None)  # as in a fresh process
    session = Session()
    announced = threading.Event()
    found: dict[str, Any] = {}
    session.events.subscribe(
        "material.providers_changed", lambda e, d: (found.update(d), announced.set())
    )
    entries = {p["name"]: p for p in session.execute("material.providers", wait=False)}
    assert entries["coolprop"]["available"] is True and entries["coolprop"]["fluids"] is None
    assert entries["refprop"]["available"] is None  # not detected yet
    assert announced.wait(60.0)
    later = {p["name"]: p for p in found["providers"]}
    assert later["coolprop"]["fluids"] == 136
    assert later["refprop"]["available"] in (True, False)
