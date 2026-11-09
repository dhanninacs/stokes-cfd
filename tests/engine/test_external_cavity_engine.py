"""An imported tutorial runs end to end on the local install (plan T-309, `engine` marker).

The install's own `incompressibleFluid/cavity` is imported, its `endTime` is overridden with a
quantity, and `run.start case=` runs it under the agent's `external` profile. **Nothing from the
tutorial is in the repository** (ADR-0029 decision 2): the case is read from the install at run
time, and the test skips when the install has no tutorial folder.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from stokes.api import Session

pytestmark = pytest.mark.engine

CASE = "incompressibleFluid/cavity"


def test_the_install_cavity_runs_with_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    roots = session.execute("tutorial.roots")
    if not roots["active"] or not (Path(roots["active"]) / CASE).is_dir():
        pytest.skip("the local install has no tutorial folder with the cavity")
    session.execute("project.new", name="cavity", path=str(tmp_path / "cavity"))
    imported = session.execute("case.import", tutorial=CASE)
    assert imported["status"] == "ready", imported
    session.execute(
        "case.set",
        node="/cases/cavity",
        file="system/controlDict",
        path=["endTime"],
        value="0.1 [s]",
    )
    reply = session.execute("run.start", case="/cases/cavity")
    assert reply["steps"] and reply["job"], reply
    deadline = time.monotonic() + 600
    status = session.execute("run.status", run=reply["run"])
    while status["state"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(1)
        status = session.execute("run.status", run=reply["run"])
    assert status["state"] in ("done", "done_unconverged"), status
    residuals = session.execute("run.residuals", run=reply["run"])
    assert residuals["time"][-1] == pytest.approx(0.1), "the endTime override was staged"
    assert {"Ux", "p"} <= set(residuals["fields"])


def test_the_cavity_shows_every_field_at_each_written_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-310: discovered fields with units from the files, and a second written time."""
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    roots = session.execute("tutorial.roots")
    if not roots["active"] or not (Path(roots["active"]) / CASE).is_dir():
        pytest.skip("the local install has no tutorial folder with the cavity")
    session.execute("project.new", name="cavity", path=str(tmp_path / "cavity"))
    session.execute("case.import", tutorial=CASE)
    for key, value in (("endTime", "0.5 [s]"), ("writeInterval", 50)):
        session.execute(
            "case.set", node="/cases/cavity", file="system/controlDict", path=[key], value=value
        )
    run = session.execute("run.start", case="/cases/cavity")["run"]
    deadline = time.monotonic() + 600
    while session.execute("run.status", run=run)["state"] in ("queued", "running"):
        assert time.monotonic() < deadline, "the run did not finish"
        time.sleep(1)
    latest = session.execute("run.fields", run=run)
    assert latest["time"] == pytest.approx(0.5)
    assert latest["times"] == pytest.approx([0.0, 0.25, 0.5])
    units = {entry["name"]: entry["units"] for entry in latest["fields"]}
    assert units["U"] == "m/s" and units["p"] == "m2/s2"
    assert {"k", "epsilon"} <= set(units), "the case's own fields are found, not just U and p"

    session.execute("run.fields", run=run)  # map the file, as a client would, before switching
    session.execute("run.time", run=run, time=0.25)
    deadline = time.monotonic() + 120
    while session.execute("run.fields", run=run)["time"] != pytest.approx(0.25):
        assert time.monotonic() < deadline, "run.time did not change the time"
        time.sleep(0.5)
    # T-315: the case's own mesh is the project's mesh result, and each patch can be coloured.
    from stokes.run.store import RunStore

    deadline = time.monotonic() + 60
    while True:
        results = session.require_project().find("/mesh/results")
        patches = [
            patch
            for result in (results.children if results is not None else [])
            for patch in result.children
            if patch.type == "mesh_patch"
        ]
        if patches or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    assert {patch.name for patch in patches} >= {"movingWall", "fixedWalls"}
    lid = next(patch for patch in patches if patch.name == "movingWall")
    payload = RunStore.of(session).field_payload_for(run, lid.id, "U_mag")
    assert payload, "the lid's speed is served for the viewport"
