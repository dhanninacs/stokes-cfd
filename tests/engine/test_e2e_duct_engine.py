"""Checkpoint B: the duct through the whole chain, as a test rather than a tool run (T-214c).

    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \\
        python3 -m pytest -q tests/engine/test_e2e_duct_engine.py -s

**`tools/e2e_duct.py` is the tool; this is the gate.** A tool that is run by hand proves the
chain worked
once on one machine. The same four checks as tests are what stop a change to the snapshot, the
writer or
the executor from breaking it silently — and the numbers they assert are physics, not plumbing.

**The measured values are recorded, not predicted.** The expected pressure drop is the developed
square-duct value with `fRe = 56.9`, and the brief's correction 6 says the honest expectation
is about
**1.06** of it because the duct is 500 mm long and has an entrance region. This run measures
**1.059**.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from stokes.api import Session
from tools.e2e_duct import (
    DH_M,
    LENGTH_M,
    check_mass,
    check_pressure,
    check_residuals,
    check_wall_pressure,
)
from tools.e2e_support import duct_project

pytestmark = pytest.mark.engine

#: The duct the tool builds, and the Reynolds number both use.
REYNOLDS = 100.0


@pytest.fixture(scope="module")
def solved(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The duct, meshed and solved, with its run record. Module-scoped: a solve is 30 s."""
    folder = tmp_path_factory.mktemp("e2e") / "duct"
    session = Session()
    ids = duct_project(session, folder)
    session.execute("mesh.generate", wait=True)
    material = session.execute("material.create", name="air", kind="fluid", template="gas")
    session.execute("region.assign", target=ids["part"], material=material["id"], name="fluid")
    session.execute(
        "solve.configure",
        values={"iterations": 1000, "residual_target": 1e-5, "write_interval": 100},
    )

    from stokes.mesh.service import MeshService
    from stokes.run.snapshot import build_solve_snapshot

    status = MeshService.of(session).status()
    status["part"] = ids["part"]
    snapshot = build_solve_snapshot(
        session, session.require_project().find("/solver/settings"), status
    )
    nu = float(snapshot.nu)
    velocity = REYNOLDS * nu / DH_M
    inlet = next(
        node
        for node in session.require_project().root.walk()
        if node.type == "boundary" and node.name == "inlet"
    )
    session.execute("parameter.set", node=inlet.id, name="velocity", value=f"{velocity} [m/s]")

    started = session.execute("run.start", wait=True)
    from stokes.run.store import RunStore

    store = RunStore.of(session)
    from stokes.mesh.service import MeshService

    # **The mesh result's own id**, which is what ties the field arrays to the coordinates they
    # belong to: the `walls` patch's points come from the same surfaces artifact the fields were
    # extracted against (T-215's one invariant).
    mesh_result = str(MeshService.of(session).status().get("result") or "")
    return {
        "session": session,
        "ids": ids,
        "run": started["run"],
        "status": store.status(started["run"]),
        "history": store.residuals(started["run"]),
        "fields": session.execute("run.fields", run=started["run"]),
        "mesh_result": mesh_result,
        "nu": nu,
        "velocity": velocity,
    }


def test_checkpoint_b_residuals(solved: dict) -> None:
    """1. Both `p` and `Ux` fall by three decades and the solver says it converged.

    **Measured: p fell 6.0 decades, Ux 5.6.** The bar is 3, so this is not a marginal pass.
    """
    verdict, detail = check_residuals(solved["status"], solved["history"])
    assert verdict == "PASS", detail
    assert solved["status"]["converged"] is True


def test_checkpoint_b_mass_balance(solved: dict) -> None:
    """2. The inlet and outlet flow rates agree — for an incompressible solve they must.

    **Measured: -3.15e-05 and +3.15e-05 m3/s, a relative difference of 1.6e-07.** The
    tolerance is 1%.
    """
    verdict, detail = check_mass(solved["status"])
    assert verdict == "PASS", detail
    monitors = solved["status"]["monitors"]
    assert monitors["inlet"]["flow_rate"] < 0, "the inlet must be an INFLOW"
    assert monitors["outlet"]["flow_rate"] > 0, "and the outlet an outflow"


def test_checkpoint_b_pressure_drop(solved: dict) -> None:
    """3. The pressure drop is the developed square-duct value.

    **Measured ratio 1.059 against the brief's expectation of about 1.06** — a
    three-significant-figure
    agreement between a finite-volume solve and the analytical `fRe = 56.9`. `p` is kinematic,
    so this is
    `dp/rho` in m^2/s^2 and never involves a density.
    """
    verdict, detail = check_pressure(solved["status"], solved["nu"], solved["velocity"])
    assert verdict == "PASS", detail
    monitors = solved["status"]["monitors"]
    measured = monitors["inlet"]["p_mean"] - monitors["outlet"]["p_mean"]
    expected = 56.9 * solved["nu"] * solved["velocity"] * LENGTH_M / (2 * DH_M**2)
    ratio = measured / expected
    assert 0.95 <= ratio <= 1.20, f"ratio {ratio}"
    # **And near 1.06**, which is what correction 6 predicts for this length. A wider band
    # would let a
    # genuinely wrong solution through.
    assert math.isclose(ratio, 1.06, abs_tol=0.02), f"ratio {ratio} is not the developed-duct value"
    assert monitors["outlet"]["p_mean"] == pytest.approx(0.0)


def test_checkpoint_b_the_record_is_complete(solved: dict) -> None:
    """The run record carries what a desktop needs to draw the run after the fact."""
    status = solved["status"]
    assert status["state"] == "done"
    assert status["iterations"] > 0
    assert status["converged"] is True
    assert status["timings"].get("seconds", 0) > 0, "the run's wall time"
    assert status["last_residuals"], "the last residuals"
    assert set(status["monitors"]) >= {"inlet", "outlet"}


def test_checkpoint_b_the_fields_are_fetched_and_listed(solved: dict) -> None:
    """**The run's fields reached the desktop** (T-216), which is what check 4 then reads.

    `run.fields` answers from the cache the fetch filled, so a `surfaces: "ok"` here is the proof
    that the artifact was fetched, verified and read back — not that some host still has it.
    """
    listing = solved["fields"]
    assert listing["surfaces"] == "ok", listing
    names = {entry["name"] for entry in listing["fields"]}
    assert {"U", "U_mag", "p"} <= names, names
    assert listing["slice"] is True, "the agent always cuts a slice"
    for entry in listing["fields"]:
        assert entry["units"], f"{entry['name']} has no units"
        assert entry["max"] >= entry["min"], entry
    # **`p` is KINEMATIC** (m2/s2, `p/rho`), never Pa.
    pressure = next(entry for entry in listing["fields"] if entry["name"] == "p")
    assert pressure["units"] == "m2/s2"
    # The run's own wall time came through the fetch collector too.
    assert solved["status"]["state"] == "done"
    assert solved["status"]["timings"].get("seconds", 0) > 0


def test_checkpoint_b_wall_pressure_is_monotone(solved: dict) -> None:
    """**4. The wall pressure falls monotonically from the inlet to the outlet.**

    The residuals can fall while the solution is wrong; this cannot. `p` on the `walls` patch,
    binned along x, must be non-increasing — the check the field artifact exists for, and the one
    that proves the arrays in it are aligned with the coordinates the viewport draws.

    **The tolerance scales with the profile's own span** (1%), not an absolute number: `p` is
    kinematic here, and monotonicity is a statement about shape rather than magnitude.
    """
    verdict, detail = check_wall_pressure(solved["session"], solved["run"], solved["mesh_result"])
    assert verdict == "PASS", detail
    # The profile must also actually fall: a flat or meaningless array would pass a monotonicity
    # test that only ever looked for rises.
    assert "->" in detail


def test_checkpoint_b_the_history_is_the_whole_run(solved: dict) -> None:
    """**Every iteration, from the agent's file** — not the rate-limited event stream.

    The event stream is a sample by design; `monitors/residuals.jsonl` is what makes a
    finished run's
    chart whole, and the store reads it back at the end.
    """
    history = solved["history"]
    iterations = history["iterations"]
    assert len(iterations) == solved["status"]["iterations"], (
        f"{len(iterations)} points for {solved['status']['iterations']} iterations"
    )
    assert iterations == sorted(iterations), "and in order"
    assert set(history["fields"]) >= {"Ux", "Uy", "p"}
