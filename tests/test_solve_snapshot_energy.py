"""The energy fields of the solve snapshot (ADR-0027 step 2).

Synthetic cubes and a hand-written mesh status, so no OpenFOAM and no OCP: the snapshot reads only
the tree and the mesh result's patch names. The engine-backed duct tests live in
``test_solve_snapshot.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session
from stokes.compile.snapshot import (
    ENERGY_FIELDS,
    THERMAL_FIELDS,
    TURBULENCE_FIELDS,
    PatchCondition,
    SolveSnapshot,
)
from stokes.run.errors import SolveInvalid
from stokes.run.snapshot import _rounded, build_solve_snapshot, solve_basis
from tests.selection_support import add_part, cube, new_session, part

SETTINGS = "/solver/settings"
BOUNDARIES = "/regions/fluid/boundaries"
#: The mesh result a finished mesh would report: one patch per boundary the set-up names.
MESH: dict[str, Any] = {
    "result": "n_mesh",
    "state": "done",
    "patch_types": {"inlet": "patch", "outlet": "patch", "wall": "wall"},
    "patch_tags": {},
}


def set_up(tmp_path: Path) -> Session:
    """A water-filled cube with a 2 m/s inlet, an outlet and walls over the rest."""
    session = new_session(tmp_path)
    body = add_part(session, "a", part(cube(0.01, (0, 0, 0), name="a")))
    session.execute("material.create", name="water")
    session.execute("region.create", name="fluid", bodies=[body], material="water")

    def pick(*faces: int) -> list[dict[str, Any]]:
        return [{"part": body, "faces": list(faces)}]

    session.execute(
        "boundary.assign",
        target=pick(0),
        kind="velocity_inlet",
        region="fluid",
        values={"velocity": "2 [m/s]", "temperature": "320 [K]"},
    )
    session.execute("boundary.assign", target=pick(1), kind="pressure_outlet", region="fluid")
    session.execute("boundary.assign", target=pick(2, 3, 4, 5), kind="wall", region="fluid")
    return session


def energy_on(session: Session, **values: Any) -> None:
    session.execute("solve.configure", engine="of14.fluid", values={"energy": True, **values})


def snapshot(session: Session) -> SolveSnapshot:
    return build_solve_snapshot(session, session.require_project().get(SETTINGS), dict(MESH))


def set_wall(session: Session, name: str, value: Any) -> None:
    wall = session.require_project().get(f"{BOUNDARIES}/wall")
    session.execute("parameter.set", node=wall.id, name=name, value=value)


def by_name(snap: SolveSnapshot) -> dict[str, PatchCondition]:
    return {patch.name: patch for patch in snap.patches}


def legacy_basis(snap: SolveSnapshot) -> str:
    """`solve_basis` as it was before the energy fields existed, for a serial case."""
    from stokes.compile.of14 import WRITER_VERSION

    body = asdict(snap)
    body.pop("cores")
    for name in ENERGY_FIELDS:
        body.pop(name)
    # Added with the transient controls; not in the basis while they are off.
    for name in ("max_courant", "write_time", "average", "average_start"):
        body.pop(name)
    body.pop("non_orthogonal")  # added with the limited schemes; not in the basis while off
    for patch in body["patches"]:
        for name in (*THERMAL_FIELDS, *TURBULENCE_FIELDS):
            patch.pop(name)
        patch.pop("mass_flow")  # added with the mass-flow inlet; dropped from the basis when None
    doc: dict[str, Any] = {
        "writer": WRITER_VERSION,
        "engine": "e",
        "mesh_basis": "mb",
        "mesh_job": "mj",
        "snapshot": body,
    }
    text = json.dumps(_rounded(doc), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def test_energy_off_keeps_every_default_and_the_old_basis(tmp_path: Path) -> None:
    """**A flow-only project is unchanged**: the new fields are the dataclass defaults, the
    patches carry no thermal condition, and the basis hashes exactly as before step 2."""
    snap = snapshot(set_up(tmp_path))
    defaults = {item.name: item.default for item in fields(SolveSnapshot)}
    for name in ENERGY_FIELDS:
        assert getattr(snap, name) == defaults[name], name
    for patch in snap.patches:
        assert (patch.thermal, patch.temperature, patch.heat_flux) == (None, None, None)
    assert solve_basis(snap, "mb", "mj", "e") == legacy_basis(snap)


def test_energy_on_fills_the_material_and_the_settings(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    energy_on(session, gravity=[0.0, 0.0, -9.81], operating_pressure="1 [bar]")
    snap = snapshot(session)
    assert snap.energy is True
    assert snap.cp == pytest.approx(4180.64, rel=1e-4)
    assert snap.kappa == pytest.approx(0.60869, rel=1e-4)
    assert snap.mu == pytest.approx(snap.nu * snap.rho, rel=1e-9)
    assert snap.T_ref == pytest.approx(300.0)
    assert snap.gravity == pytest.approx((0.0, 0.0, -9.81))
    assert snap.p_ref == pytest.approx(1.0e5)


def test_energy_on_moves_the_basis(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    off = solve_basis(snapshot(session), "mb", "mj", "e")
    energy_on(session)
    assert solve_basis(snapshot(session), "mb", "mj", "e") != off


def test_the_energy_writer_version_moves_an_energy_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `of14.fluid` case is written by the energy writer, so its version marks runs stale."""
    from stokes.compile.of14 import fluid

    session = set_up(tmp_path)
    energy_on(session)
    snap = snapshot(session)
    assert snap.engine == fluid.ENGINE
    before = solve_basis(snap, "mb", "mj", "e")
    monkeypatch.setattr(fluid, "WRITER_VERSION", "of14-fluid/test")
    assert solve_basis(snap, "mb", "mj", "e") != before


def test_the_inlet_is_a_fixed_temperature_and_the_outlet_its_backflow(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    energy_on(session)
    named = by_name(snapshot(session))
    assert (named["inlet"].thermal, named["inlet"].temperature) == ("fixedValue", 320.0)
    assert (named["outlet"].thermal, named["outlet"].temperature) == ("inletOutlet", 300.0)
    assert named["wall"].thermal == "zeroGradient", "a wall is adiabatic by default"


def test_the_outlet_carries_its_centroid_with_energy_on(tmp_path: Path) -> None:
    """The `fluid` writer needs the outlet's height for `p_rgh` (ADR-0027 step 5)."""
    session = set_up(tmp_path)
    assert by_name(snapshot(session))["outlet"].centroid is None, "energy off: not asked"
    energy_on(session, gravity=[0.0, -9.81, 0.0])
    named = by_name(snapshot(session))
    centroid = named["outlet"].centroid
    assert centroid is not None and len(centroid) == 3
    assert all(0.0 <= value <= 0.01 for value in centroid), "on the 10 mm cube"
    assert named["inlet"].centroid is None and named["wall"].centroid is None


def test_a_temperature_wall_is_a_fixed_value(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    set_wall(session, "thermal", "temperature")
    set_wall(session, "temperature", "350 [K]")
    energy_on(session)
    wall = by_name(snapshot(session))["wall"]
    assert (wall.thermal, wall.temperature) == ("fixedValue", 350.0)


def test_a_heat_flux_wall_carries_its_flux(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    set_wall(session, "thermal", "heat_flux")
    set_wall(session, "heat_flux", "500 [W/m**2]")
    energy_on(session)
    wall = by_name(snapshot(session))["wall"]
    assert (wall.thermal, wall.heat_flux, wall.temperature) == ("heatFlux", 500.0, None)


def test_a_convection_wall_is_refused_with_its_code(tmp_path: Path) -> None:
    session = set_up(tmp_path)
    set_wall(session, "thermal", "convection")
    snapshot(session)  # energy off: the switch is not read
    energy_on(session)
    with pytest.raises(SolveInvalid) as caught:
        snapshot(session)
    assert caught.value.code == "solve.thermal_unsupported"
    assert "convection" in str(caught.value) and "wall" in str(caught.value)


def test_the_old_keyword_construction_still_works() -> None:
    """Every existing caller builds the records without the new fields."""
    snap = SolveSnapshot(
        settings_id="s",
        engine="of14.incompressibleFluid",
        turbulence="laminar",
        iterations=10,
        write_interval=5,
        residual_target=1e-4,
        nu=1e-6,
        rho=1000.0,
        patches=(
            PatchCondition("inlet", "patch", "velocity_inlet", velocity=(1.0, 0.0, 0.0)),
            PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
        ),
        monitor_patches=("inlet",),
        cores=1,
    )
    assert snap.energy is False and snap.gravity == (0.0, 0.0, 0.0) and snap.p_ref == 101325.0
    assert snap.patch("inlet").thermal is None
