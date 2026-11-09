"""Background fits (ADR-0018 decision 6; contract "Events"): a slow stand-in provider shows the
event order (stale, progress, fitted, ok), the fit landing as its own undo step, the busy rule, a
result dropped because the material changed meanwhile, and the journal recording a replayable
call; a real CoolProp mixture shows cancellation through ``material.cancel_fit`` and the worker's
typed errors."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Journal, Session
from stokes.physics.materials import adding
from stokes.physics.materials.errors import Busy, Cancelled, FluidUnknown
from stokes.physics.materials.methods import R_UNIVERSAL
from stokes.physics.materials.providers.base import LibraryInfo, LibraryRecord, PropertyGrid
from stokes.physics.materials.service import MaterialsService
from tests.test_materials_support import (  # noqa: F401 - fixtures
    events,
    library_session,
    needs_coolprop,
    private_home,
    warm_worker,
)

FITTED = ["density", "viscosity", "specific_heat", "thermal_conductivity"]
M = 0.028


def laws(t: np.ndarray, p: float) -> dict[str, np.ndarray]:
    """A synthetic ideal gas: what each fit should find exactly."""
    return {
        "density": p * M / (R_UNIVERSAL * t),
        "viscosity": 1.4e-6 * t**1.5 / (t + 110.0),
        "specific_heat": 1000.0 + 0.2 * t,
        "thermal_conductivity": 0.004 + 7e-5 * t,
    }


class SlowProvider:
    """A library provider stand-in that evaluates in chunks with a pause between them."""

    name = "coolprop"

    def __init__(self, chunks: int = 6, pause: float = 0.05) -> None:
        self.chunks = chunks
        self.pause = pause
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def info(
        self,
        fluid: str,
        composition: str = "",
        basis: str = "",
        *,
        T_ref: float = 300.0,  # noqa: N803 - the protocol's name
        p_ref: float = 101325.0,
        phase: str = "auto",
    ) -> LibraryInfo:
        at = {k: float(v[0]) for k, v in laws(np.array([T_ref]), p_ref).items()}
        record = LibraryRecord(
            provider="coolprop",
            fluid=fluid,
            name="Slowgas",
            kind="fluid",
            molar_mass=M,
            properties=dict.fromkeys(FITTED, True),
        )
        return LibraryInfo(
            record=record,
            limits={"T_min": 100.0, "T_max": 2000.0, "T_sat": None, "phase_at_T_ref": "gas"},
            references={"eos": "synthetic"},
            detail={"values_at_ref": at, "missing": []},
        )

    def evaluate_chunked(
        self,
        fluid: str,
        composition: str,
        basis: str,
        phase: str,
        t: np.ndarray,
        p: float,
        *,
        on_progress: Any = None,  # Any: the progress callback
        cancel: threading.Event | None = None,
    ) -> PropertyGrid:
        self.started.set()
        self.release.wait(10.0)
        for chunk in range(self.chunks):
            if cancel is not None and cancel.is_set():
                raise Cancelled("cancelled between chunks")
            time.sleep(self.pause)
            if on_progress is not None:
                on_progress((chunk + 1) * t.size // self.chunks, t.size)
        return PropertyGrid(t=t, p=p, values=laws(t, p), missing=[], phases=["gas"])

    def evaluate(
        self, fluid: str, composition: str, basis: str, phase: str, t: np.ndarray, p: float
    ) -> PropertyGrid:
        return self.evaluate_chunked(fluid, composition, basis, phase, t, p)


@pytest.fixture
def slow(monkeypatch: pytest.MonkeyPatch) -> SlowProvider:
    provider = SlowProvider()
    monkeypatch.setattr(adding, "library_provider", lambda name: (provider, "9.9"))
    return provider


def statuses(session: Session, path: str) -> dict[str, str]:
    return {c.name: c.status for c in session.require_project().get(path).children}


def test_a_background_fit_reports_progress_and_lands_as_its_own_step(
    session: Session, slow: SlowProvider
) -> None:
    seen = events(
        session,
        "node.changed",
        "material.fit_progress",
        "material.fitted",
        "material.fit_failed",
    )
    added = session.execute(
        "material.add_from_library", provider="coolprop", fluid="Slowgas", wait=False
    )
    assert added["background"] is True and added["pending"] == FITTED and added["fit"] == {}
    path = added["path"]
    service = MaterialsService.of(session)
    assert service.wait(added["id"], timeout=30.0)
    assert statuses(session, path)["viscosity"] == "ok"
    project = session.require_project()
    assert project.get(f"{path}/density").params["method"] == "ideal_gas"
    assert project.get(f"{path}/viscosity").params["method"] == "sutherland"
    assert project.get(f"{path}/viscosity").meta["fit"]["provider_version"] == "9.9"
    names = [name for name, _ in seen]
    first_progress = names.index("material.fit_progress")
    first_fitted = names.index("material.fitted")
    stale = [d for n, d in seen[:first_progress] if d.get("param") == "status"]
    assert len(stale) == 4
    progress = [d for n, d in seen if n == "material.fit_progress"]
    assert progress[-1]["done"] == progress[-1]["total"] == 201
    assert first_progress < first_fitted
    fitted = [d for n, d in seen if n == "material.fitted"]
    assert {d["property"] for d in fitted} == set(FITTED) and all(d["ok"] for d in fitted)
    ok = [d for n, d in seen[first_fitted:] if d.get("param") == "status"]
    assert len(ok) == 4
    assert "material.fit_failed" not in names
    assert session.execute("history.state")["undo"] == "fit Slowgas from coolprop"
    session.execute("history.undo")
    assert project.get(f"{path}/viscosity").params["method"] == "constant"
    assert statuses(session, path)["viscosity"] == "stale"  # back to the stand-in, unfitted
    session.execute("history.redo")
    assert statuses(session, path)["viscosity"] == "ok"
    journal = Journal.read(session.journal.path) if session.journal.path else []
    record = next(r for r in journal if r["command"] == "material.add_from_library")
    assert record["params"]["wait"] is True and record["params"]["T_max"] == 750.0


def test_a_busy_material_refuses_another_fit(session: Session, slow: SlowProvider) -> None:
    slow.release.clear()
    added = session.execute(
        "material.add_from_library", provider="coolprop", fluid="Slowgas", wait=False
    )
    assert slow.started.wait(10.0)
    try:
        with pytest.raises(Busy):
            session.execute("material.refit", material=added["name"])
        with pytest.raises(Busy):
            session.execute("material.set_state", material=added["name"], T_max=600)
        codes = [
            f["code"] for f in session.execute("material.check", material=added["name"])["findings"]
        ]
        assert codes.count("materials.stale_fit") == 4
        assert session.execute("material.list")[0]["fitting"] is True
    finally:
        slow.release.set()
    assert MaterialsService.of(session).wait(added["id"], timeout=30.0)
    assert session.execute("material.check", material=added["name"])["ok"] is True


def test_a_fit_for_a_changed_material_is_dropped(session: Session, slow: SlowProvider) -> None:
    seen = events(session, "material.fit_failed")
    slow.release.clear()
    added = session.execute(
        "material.add_from_library", provider="coolprop", fluid="Slowgas", wait=False
    )
    assert slow.started.wait(10.0)
    session.execute("parameter.set", node=added["path"], name="T_max", value=600)
    slow.release.set()
    assert MaterialsService.of(session).wait(added["id"], timeout=30.0)
    assert {d["code"] for _, d in seen} == {"materials.stale_fit"}
    project = session.require_project()
    assert project.get(f"{added['path']}/viscosity").params["method"] == "constant"
    assert statuses(session, added["path"])["viscosity"] == "stale"
    session.execute("material.refit", material=added["name"], wait=True)
    assert statuses(session, added["path"])["viscosity"] == "ok"


@needs_coolprop
@pytest.mark.usefixtures("warm_worker", "private_home")
def test_a_background_fit_can_be_cancelled(tmp_path: Path) -> None:
    session = library_session(tmp_path)
    seen = events(session, "material.fit_progress", "material.fit_failed")
    added = session.execute(
        "material.add_from_library",
        provider="coolprop",
        fluid="Methane&Ethane",
        composition="0.5,0.5",
        T_min=250,
        T_max=400,
        wait=False,
    )
    deadline = time.monotonic() + 30.0
    while not any(n == "material.fit_progress" for n, _ in seen) and time.monotonic() < deadline:
        time.sleep(0.02)
    cancelled = session.execute("material.cancel_fit", material=added["name"])
    assert cancelled == {"cancelled": [added["id"]]}
    assert MaterialsService.of(session).wait(added["id"], timeout=30.0)
    failed = [d for n, d in seen if n == "material.fit_failed"]
    assert failed and {d["code"] for d in failed} == {"materials.cancelled"}
    assert statuses(session, added["path"])["density"] == "stale"
    assert session.execute("material.cancel_fit") == {"cancelled": []}


@needs_coolprop
@pytest.mark.usefixtures("warm_worker")
def test_the_worker_returns_typed_errors() -> None:
    from stokes.physics.materials import worker

    with pytest.raises(FluidUnknown) as unknown:
        worker.request(
            {
                "type": "evaluate",
                "provider": "coolprop",
                "fluid": "Unobtainium",
                "phase": "auto",
                "T": [300.0],
                "p": 101325.0,
            }
        )
    assert unknown.value.code == "materials.fluid_unknown"
    assert worker.running()


def test_opening_another_project_stops_the_fits_of_the_last(
    session: Session, slow: SlowProvider
) -> None:
    seen = events(session, "material.fit_failed")
    slow.release.clear()
    added = session.execute(
        "material.add_from_library", provider="coolprop", fluid="Slowgas", wait=False
    )
    assert slow.started.wait(10.0)
    service = MaterialsService.of(session)
    session.execute("project.new", name="other")
    slow.release.set()
    assert service.wait(added["id"], timeout=30.0)
    assert {d["code"] for _, d in seen} == {"materials.cancelled"}
    assert session.execute("material.list") == []
