"""`build_solve_snapshot` against a real meshed duct (T-214a).

**These are engine-backed on purpose.** Every one of the four bugs this module had was
invisible to a
hand-built node and visible the moment a real project ran through it:

* `tag.params["members"]` is a **JSON string**, not a list — `.get` on it raised `AttributeError`;
* `material.list` returns a **plain list** with **no `T_ref`** — reading it as a dict raised;
* the patches live in **`node.meta`**, not in `MeshService.status()`, so reading them from
`status` gave
  an empty patch list — a case with no boundary conditions, silently;
* `tag.anchor` puts the tag's **PATH** in `tag`, not its id, so matching on the id found
nothing and the
  inlet got a **zero velocity**.

Four silent wrong answers, each caught only by running against a real duct. The tests below
pin them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from stokes.api import Session
from stokes.mesh.service import MeshService
from stokes.run.errors import SolveInvalid
from stokes.run.snapshot import build_solve_snapshot, solve_basis
from tools.e2e_support import duct_project

pytestmark = pytest.mark.engine

#: T-212's duct: a 500x20x20 mm channel, solved at 0.078615 m/s. Its engine test measured
#: `sum(phi) = -3.0e-05 m3/s` at the inlet, which is a CORRECT inward velocity.
VELOCITY = 0.078615


@pytest.fixture(scope="module")
def duct(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """A meshed duct with a region, a material and a velocity inlet."""
    folder = tmp_path_factory.mktemp("snapshot") / "duct"
    os.environ.setdefault("STOKES_HOME", str(tmp_path_factory.mktemp("home")))
    session = Session()
    ids = duct_project(session, folder)
    for name, value, param in (
        ("inlet", f"{VELOCITY} [m/s]", "velocity"),
        ("outlet", "0 [Pa]", "pressure"),
    ):
        node = next(
            n for n in session.project.root.walk() if n.type == "boundary" and n.name == name
        )
        session.execute("parameter.set", node=node.id, name=param, value=value)
    session.execute("mesh.generate", wait=True)
    material = session.execute("material.create", name="air", kind="fluid", template="gas")
    session.execute("region.assign", target=ids["part"], material=material["id"], name="fluid")
    session.execute("solve.configure", values={"iterations": 200})
    return {"session": session, "ids": ids}


def snapshot(duct: dict) -> object:
    session = duct["session"]
    settings = session.project.find("/solver/settings")
    return build_solve_snapshot(session, settings, MeshService.of(session).status())


def test_nu_and_rho_are_the_brief_s_numbers(duct: dict) -> None:
    """`nu = mu/rho` at the material's reference temperature, from `material.evaluate`.

    The brief's expected values: `rho = 1.17663`, `mu = 1.850e-05`, `nu = 1.5723e-05`. All
    three were
    checked **by hand** during the pause and match to five figures.
    """
    snap = snapshot(duct)
    assert snap.rho == pytest.approx(1.17663, rel=1e-4)
    assert snap.nu == pytest.approx(1.5723e-05, rel=1e-3)


def test_every_mesh_patch_becomes_a_condition(duct: dict) -> None:
    """**The patches come from the MESH** (correction 2), so all three of the duct's appear —
    including
    the walls, which a boundaries-driven walk would have had to guess at."""
    snap = snapshot(duct)
    named = {patch.name: patch for patch in snap.patches}
    assert set(named) == {"inlet", "outlet", "walls"}
    assert named["inlet"].kind == "velocity_inlet"
    assert named["outlet"].kind == "pressure_outlet"
    assert named["walls"].kind == "wall"


def test_the_patch_types_come_from_the_driver(duct: dict) -> None:
    """`patch_types` is what `blockMesh` will write: the walls are a `wall`, the ends are
    `patch`."""
    snap = snapshot(duct)
    named = {patch.name: patch for patch in snap.patches}
    assert named["walls"].patch_type == "wall"
    assert named["inlet"].patch_type == "patch"


def test_the_inlet_velocity_is_the_magnitude_along_the_inward_normal(duct: dict) -> None:
    """**The bug this catches had a zero velocity**, which runs and does nothing.

    The tag's normal is `(-1, 0, 0)` — outward from the part — and an inlet is INFLOW, so the
    velocity
    points the other way. In this duct the tag's normal already points into the fluid, so the
    sign does
    not flip and the velocity is `+0.078615` on x. **The magnitude is what T-212 measured**:
    its engine
    test saw `sum(phi) = -3.0e-05 m3/s`, which a correct inward velocity produces.
    """
    snap = snapshot(duct)
    inlet = next(patch for patch in snap.patches if patch.name == "inlet")
    assert inlet.velocity is not None
    magnitude = sum(v * v for v in inlet.velocity) ** 0.5
    assert magnitude == pytest.approx(VELOCITY, rel=1e-6)
    assert inlet.velocity[0] > 0, f"the inlet must blow INTO the duct, got {inlet.velocity}"


def test_the_outlet_pressure_is_read(duct: dict) -> None:
    snap = snapshot(duct)
    outlet = next(patch for patch in snap.patches if patch.name == "outlet")
    assert outlet.pressure == pytest.approx(0.0)


def test_monitor_patches_are_the_inlet_and_the_outlet(duct: dict) -> None:
    """The two patches a residual plot watches: where the flow enters and where it leaves."""
    snap = snapshot(duct)
    assert set(snap.monitor_patches) == {"inlet", "outlet"}


def test_no_mesh_result_is_refused_under_its_own_code(duct: dict) -> None:
    """**The code is a KEYWORD on the error, not a key in `details`.**

    `SolveInvalid.code` is the class default `"solve.invalid"`; putting the specific reason in
    `details={"code": …}` left every refusal reporting the generic one, so a client could not
    branch on
    it. Four refusals now carry their own code, and this pins the mechanism for one of them.
    """
    session = duct["session"]
    del session
    fresh = Session()
    fresh.execute("project.new", name="bare")
    with pytest.raises(SolveInvalid) as caught:
        build_solve_snapshot(fresh, None, {"result": None})
    assert caught.value.code == "solve.no_mesh"


def test_a_region_without_a_material_is_solve_invalid(duct: dict) -> None:
    """The commonest real set-up mistake: a mesh, but nothing saying what the fluid is."""
    session = Session()
    session.execute("project.new", name="nomaterial")
    # A finished mesh whose patches are known, but no region holding the domain part.
    with pytest.raises(SolveInvalid) as caught:
        build_solve_snapshot(
            session,
            session.project.find("/solver/settings"),
            {"result": "n_x", "state": "done", "patch_types": {}, "patch_tags": {}},
        )
    assert caught.value.code == "solve.no_material"


def test_a_stale_mesh_is_refused(duct: dict) -> None:
    session = duct["session"]
    settings = session.project.find("/solver/settings")
    status = MeshService.of(session).status()
    status["stale"] = True
    with pytest.raises(SolveInvalid) as caught:
        build_solve_snapshot(session, settings, status)
    assert caught.value.code == "solve.mesh_stale"


def test_a_failed_mesh_is_refused_under_its_own_code(duct: dict) -> None:
    session = duct["session"]
    settings = session.project.find("/solver/settings")
    with pytest.raises(SolveInvalid) as caught:
        build_solve_snapshot(session, settings, {"result": "n_x", "state": "failed"})
    assert caught.value.code == "solve.mesh_failed"


def test_the_basis_is_stable_and_excludes_host_but_moves_with_cores(duct: dict) -> None:
    """**`host` is deliberately out**, for the reason `mesh_basis` excludes it: the same inputs
    elsewhere produce the same case, and a basis that moves for no reason is one users ignore.

    **`cores` above one is in** (ADR-0025): the parallel case carries a decomposition dictionary,
    and reuse must not hand back the old serial run when the user asks for more ranks.
    """
    snap = snapshot(duct)
    first = solve_basis(snap, "mb", "mj", "org-14")
    assert first == solve_basis(snap, "mb", "mj", "org-14"), (
        "the same inputs must give the same basis"
    )
    assert first != solve_basis(snap, "mb", "mj", "org-15"), "a different engine must move it"
    assert first != solve_basis(snap, "mb", "other", "org-14"), "a different mesh job must move it"
    assert len(first) == 40, "sha1 hex"
    from dataclasses import replace

    assert solve_basis(replace(snap, cores=1), "mb", "mj", "org-14") == first
    two = solve_basis(replace(snap, cores=2), "mb", "mj", "org-14")
    assert two != first, "a parallel case is not the serial case"
    assert two != solve_basis(replace(snap, cores=4), "mb", "mj", "org-14")


def test_the_basis_moves_when_the_velocity_does(duct: dict) -> None:
    """A real change must move it — otherwise the basis is not doing its job."""
    import dataclasses

    snap = snapshot(duct)
    before = solve_basis(snap, "mb", "mj", "org-14")
    # **`dataclasses.replace`, not attribute assignment**: both records are FROZEN, which is
    # what makes
    # the basis a function of an immutable input rather than of whatever the caller did to it.
    changed = [
        dataclasses.replace(patch, velocity=(0.5, 0.0, 0.0)) if patch.name == "inlet" else patch
        for patch in snap.patches
    ]
    moved = dataclasses.replace(snap, patches=tuple(changed))
    assert solve_basis(moved, "mb", "mj", "org-14") != before


def test_solve_check_is_ready_for_the_duct(duct: dict) -> None:
    """The end of T-214a: a real project passes the check and names `run.start` as the next step."""
    reply = duct["session"].execute("solve.check")
    assert reply["state"] == "ready", reply["findings"]
    assert reply["next"] == "run.start"


def test_snapshot_carries_selected_solver_cores(duct: dict) -> None:
    duct["session"].execute("solve.configure", values={"cores": 4})
    assert snapshot(duct).cores == 4
