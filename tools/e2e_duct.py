"""The duct, end to end, headless: model -> mesh -> solve -> the four checks (T-214c, checkpoint B).

    PYTHONPATH=.:agent STOKES_OPENFOAM_BASHRC=<the env script> python3 tools/e2e_duct.py <folder>

**This is the gate the owner's target depends on.** It builds the duct, meshes it, solves it on real
OpenFOAM, and then asks four questions whose answers are physics rather than plumbing: did the
residuals
fall, does mass balance, is the pressure drop the one a developed duct flow has, and (later)
does the wall
pressure rise monotonically. Each prints `PASS`, `FAIL` or `SKIPPED`, and the process exits 1
if any FAIL.

## Why the numbers are what they are

`Re = U*Dh/nu = 100` with `Dh = 0.02 m` and `nu` **read from the material**, so the tool
cannot disagree
with the solver about the fluid. The expected pressure drop is the **developed** value,
`dp/rho = (fRe)*nu*U*L/(2*Dh^2)` with `fRe = 56.9` for a square duct — and the band is
`[0.95, 1.20] x` that, because an entrance region and a 500 mm length mean the honest
expectation is about
1.06 rather than 1.00 (correction 6).

**`p` in the solver is KINEMATIC** (m^2/s^2, `p/rho`), which is why the drop is stated as
`dp/rho` and why the
check never multiplies by a density.
"""

from __future__ import annotations

import json
import sys
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from stokes.api import Session
from tools.e2e_support import duct_project

#: A 20 mm square duct, so the hydraulic diameter is its side.
DH_M = 0.02
#: The duct's length in `duct_project`.
LENGTH_M = 0.5
#: The Darcy friction factor times Reynolds for fully developed laminar flow in a square duct.
FRE = 56.9
#: The check's band, from correction 6: the honest expectation is ~1.06, not 1.00.
BAND = (0.95, 1.20)
#: How far the residuals must fall. Three decades is the brief's bar.
DECADES = 3.0
#: How closely the inlet and outlet flow rates must agree.
MASS_TOLERANCE = 0.01
#: How many bins along x the wall pressure profile is averaged into (check 4).
BINS = 20


def viscosity(session: Session, part: str) -> float:
    """`nu = mu/rho` at the material's reference temperature, through the same path the
    snapshot uses."""
    from stokes.mesh.service import MeshService
    from stokes.run.snapshot import build_solve_snapshot

    project = session.require_project()
    settings = project.find("/solver/settings")
    if settings is None:
        raise SystemExit("no solver settings; solve.configure has not run")
    status = MeshService.of(session).status()
    status["part"] = part
    return float(build_solve_snapshot(session, settings, status).nu)


def _residual_fall(history: dict[str, Any], field: str) -> tuple[float | None, float | None]:
    """The first and last values of one field's initial residuals."""
    values = [v for v in (history.get("fields", {}).get(field) or []) if v is not None]
    if len(values) < 2:
        return (None, None)
    return (float(values[0]), float(values[-1]))


def check_residuals(status: dict[str, Any], history: dict[str, Any]) -> tuple[str, str]:
    """1. Did both `p` and `Ux` fall by three decades, and did the solver say it converged?"""
    lines: list[str] = []
    ok = True
    for field in ("p", "Ux"):
        first, last = _residual_fall(history, field)
        if first is None or last is None or first <= 0 or last <= 0:
            lines.append(f"{field}: not enough data (first={first}, last={last})")
            ok = False
            continue
        fell = first / last
        decades = 0.0 if fell <= 0 else __import__("math").log10(fell)
        lines.append(f"{field}: {first:.3e} -> {last:.3e}  ({decades:.1f} decades)")
        if decades < DECADES:
            ok = False
    converged = bool(status.get("converged"))
    lines.append(f"converged: {converged}")
    if not converged:
        ok = False
    return ("PASS" if ok else "FAIL", "; ".join(lines))


def check_mass(status: dict[str, Any]) -> tuple[str, str]:
    """2. Do the inlet and outlet flow rates agree? For an incompressible solve they must."""
    monitors = status.get("monitors") or {}
    inlet = (monitors.get("inlet") or {}).get("flow_rate")
    outlet = (monitors.get("outlet") or {}).get("flow_rate")
    if inlet is None or outlet is None:
        return ("SKIPPED", f"no flow rate (inlet={inlet}, outlet={outlet})")
    if inlet == 0:
        return ("FAIL", f"inlet flow rate is zero (outlet={outlet})")
    relative = abs(abs(inlet) - abs(outlet)) / abs(inlet)
    verdict = "PASS" if relative <= MASS_TOLERANCE else "FAIL"
    return (verdict, f"inlet={inlet:.6e} outlet={outlet:.6e} m3/s  |diff|/|inlet|={relative:.2e}")


def check_pressure(status: dict[str, Any], nu: float, velocity: float) -> tuple[str, str]:
    """3. Is the pressure drop the one a developed square-duct flow has?

    **`p` is kinematic**, so this compares `dp/rho` in m^2/s^2 with `(fRe)*nu*U*L/(2·Dh^2)`.

    **SKIPPED, not FAIL, when the monitor is missing**: a run whose `patchAverage` wrote
    nothing has not
    disproved the pressure drop, it has failed to measure it — and calling that a failure
    would hide the
    difference. The brief says to mark it skipped and say so.
    """
    monitors = status.get("monitors") or {}
    inlet = (monitors.get("inlet") or {}).get("p_mean")
    outlet = (monitors.get("outlet") or {}).get("p_mean")
    if inlet is None or outlet is None:
        return (
            "SKIPPED",
            "no patch-average p (the fields task T-215 would compute it); "
            f"inlet={inlet} outlet={outlet}",
        )
    measured = float(inlet) - float(outlet)
    expected = FRE * nu * velocity * LENGTH_M / (2 * DH_M**2)
    ratio = measured / expected if expected else 0.0
    verdict = "PASS" if BAND[0] <= ratio <= BAND[1] else "FAIL"
    return (
        verdict,
        f"dp/rho measured={measured:.6e} expected={expected:.6e} m2/s2  "
        f"ratio={ratio:.3f} (band {BAND[0]}-{BAND[1]})",
    )


def check_wall_pressure(session: Session, run_id: str, mesh_result: str) -> tuple[str, str]:
    """4. Does the wall pressure rise monotonically from the inlet to the outlet?

    **The physics check the artifact exists for.** In a developing duct flow the pressure falls
    along the flow, so `p` on the `walls` patch, binned by `x` from the inlet to the outlet, must be
    **monotonically non-increasing**. The residuals can fall while the solution is wrong; this
    cannot.

    Binned, not per-vertex: a wall patch's vertices are not sorted along x, and a single pair of
    neighbouring vertices carries mesh noise. Twenty bins is a tenth of the 200-cell duct in each
    direction — enough to see the gradient and too few to see one cell's noise. A bin with no
    vertices is skipped rather than counted, which is why this reads the counts as well as the
    means.

    **SKIPPED, not FAIL, when there is no artifact**: the run's fields are the thing being checked,
    and a missing cache file has not disproved the pressure rise. The brief is explicit that a
    check which cannot run must say so rather than pass.
    """
    from stokes.geometry.errors import MeshFormatError
    from stokes.run.fields import field_array, read_header
    from stokes.run.store import RunStore

    store = RunStore.of(session)
    try:
        path = store.fields_path(run_id)
        data = memoryview(path.read_bytes())
        header = read_header(data)
    except (OSError, MeshFormatError) as exc:
        return ("SKIPPED", f"no field artifact for {run_id}: {type(exc).__name__}: {exc}")

    walls = next(
        (
            position
            for position, patch in enumerate(header["patches"])
            if str(patch.get("name")) == "walls" and "p" in (patch.get("fields") or {})
        ),
        None,
    )
    if walls is None:
        return ("SKIPPED", "the field artifact has no p on the walls patch")
    positions = wall_positions(session, mesh_result, int(header["patches"][walls]["points"]))
    if positions is None:
        return ("SKIPPED", "no cached surfaces for the mesh result, so no wall coordinates")
    values = np.asarray(field_array(data, header, walls, "p"), dtype="float64").reshape(-1)
    if values.size != positions.shape[0]:
        return (
            "FAIL",
            f"the walls patch has {positions.shape[0]} surface points and "
            f"{values.size} field values; they must be the same vertices",
        )

    x = positions[:, 0]
    edges = np.linspace(float(x.min()), float(x.max()), BINS + 1)
    which = np.clip(np.digitize(x, edges) - 1, 0, BINS - 1)
    means: list[float] = []
    counts: list[int] = []
    for index in range(BINS):
        here = values[which == index]
        if here.size:
            means.append(float(here.mean()))
            counts.append(int(here.size))
    if len(means) < 2:
        return ("SKIPPED", f"only {len(means)} non-empty bins along x; too few to judge")
    rises = [later - earlier for earlier, later in pairwise(means) if later > earlier]
    span = max(means) - min(means)
    # **A tolerance scaled by the profile's own span**, not an absolute one: a `p` in m^2/s^2 and a
    # `p` in Pa are different magnitudes, and "monotone" is a statement about shape. A rise under
    # 1% of the span is rounding in a mean of a few float32 values, not a pressure rise.
    tolerance = 0.01 * span if span > 0 else 0.0
    worst = max(rises, default=0.0)
    verdict = "PASS" if worst <= tolerance else "FAIL"
    detail = (
        f"{len(means)} bins over x=[{x.min():.4f}, {x.max():.4f}] m, "
        f"{sum(counts)} wall points; p {means[0]:.4e} -> {means[-1]:.4e} m2/s2 "
        f"(span {span:.3e}); worst rise {worst:.3e} vs tolerance {tolerance:.3e}"
    )
    return (verdict, detail)


def wall_positions(session: Session, mesh_result: str, points: int) -> Any:
    """The `walls` patch's own surface points, from the mesh result's cached `.stkmsh`.

    **The surfaces artifact is the authority on the point order** (T-215): the field artifact's
    `walls` array is indexed the same way, so reading the coordinates from anywhere else would align
    pressure with the wrong vertex. None when the cache file is gone — the check then skips.
    """
    from stokes.mesh.service import MeshService
    from stokes.mesh.surfaces import patch_arrays

    service = MeshService.of(session)
    from stokes.geometry.errors import MeshFormatError
    from stokes.mesh.errors import MeshInvalid

    try:
        # **The same reader the mesh routes use**, so this cannot disagree with what the viewport
        # was shown: `_load` is the service's own cache lookup, with its LRU and its validation.
        data, header = service._load(mesh_result)
    except (OSError, MeshFormatError, MeshInvalid):
        return None
    position = next(
        (
            index
            for index, patch in enumerate(header["patches"])
            if str(patch.get("name")) == "walls"
        ),
        None,
    )
    if position is None:
        return None
    positions, _triangles, _edges = patch_arrays(data, header, position)
    if int(positions.shape[0]) != points:
        return None
    return positions


def main(argv: list[str]) -> int:
    """Run the duct end to end.

    ``e2e_duct.py <folder> [--order mesh-first|region-first]``

    **`region-first` is the GUI's own order** (load a CAD file, define the boundaries and the
    region,
    then press Mesh) and it is the one the owner meets first. It used to fail with
    ``the xmax side holds faces of fluid and outlet``, because a region's whole-part tag
    competed with
    `outlet` for the `xmax` side; a region tag is cell membership, not a boundary patch.
    `mesh-first` is
    the order this tool has always used. Both must pass, so the check runs either way rather than
    trusting that the newer order still works.
    """
    # **`--order X` consumes its value.** A first version split argv into "flags" and
    # "non-flags" and
    # then dropped the value, so `--order region-first` silently ran mesh-first - the flag
    # appeared to
    # work and tested the wrong thing, which is worse than rejecting it.
    rest = list(argv[1:])
    order = "mesh-first"
    host = "local"
    args: list[str] = []
    while rest:
        item = rest.pop(0)
        if item == "--order":
            if not rest:
                print("--order needs a value", file=sys.stderr)
                return 2
            order = rest.pop(0)
        elif item.startswith("--order="):
            order = item.split("=", 1)[1]
        elif item == "--host":
            if not rest:
                print("--host needs a value", file=sys.stderr)
                return 2
            host = rest.pop(0)
        elif item.startswith("--host="):
            host = item.split("=", 1)[1]
        else:
            args.append(item)
    if len(args) != 1:
        print(
            "usage: e2e_duct.py <folder> [--order mesh-first|region-first] [--host NAME]",
            file=sys.stderr,
        )
        return 2
    if order not in ("mesh-first", "region-first"):
        print(f"order must be mesh-first or region-first; got {order!r}", file=sys.stderr)
        return 2
    folder = Path(args[0])
    session = Session()
    ids = duct_project(session, folder)
    # **The host is a project setting**, the same one the Mesh and Solve panels' Execution
    # section writes. Configure it before the first generate so both the mesher and the solver
    # run where the caller asked; `run.start` resolves the same setting itself.
    session.execute("mesh.configure", values={"host": host})
    print(f"host: {host}", flush=True)
    if order == "region-first":
        # **The GUI's order**: the fluid region exists before the first press of Mesh.
        print("order: region-first (the GUI's order)", flush=True)

    # **The velocity that makes Re = 100**, from the material's own nu — computed before the
    # solve, so
    # the tool cannot disagree with the solver about the fluid.
    if order == "region-first":
        material = session.execute("material.create", name="air", kind="fluid", template="gas")
        session.execute("region.assign", target=ids["part"], material=material["id"], name="fluid")
        mesh_done = session.execute("mesh.generate", wait=True)
        print(f"mesh: {mesh_done['state']}", flush=True)
    else:
        print("order: mesh-first", flush=True)
        mesh_done = session.execute("mesh.generate", wait=True)
        print(f"mesh: {mesh_done['state']}", flush=True)
        material = session.execute("material.create", name="air", kind="fluid", template="gas")
        session.execute("region.assign", target=ids["part"], material=material["id"], name="fluid")
    if mesh_done["state"] != "done":
        print(f"FAIL mesh: {mesh_done['state']}", flush=True)
        return 1
    session.execute(
        "solve.configure",
        values={
            "iterations": 1000,
            "residual_target": 1e-5,
            "write_interval": 100,
            "host": host,
        },
    )
    nu = viscosity(session, ids["part"])
    velocity = 100.0 * nu / DH_M
    print(f"nu={nu:.6e} m2/s  U={velocity:.6f} m/s  (Re=100, Dh={DH_M} m)", flush=True)
    from stokes.engines.model import SETTINGS_PATH

    inlet = next(
        node
        for node in session.require_project().root.walk()
        if node.type == "boundary" and node.name == "inlet"
    )
    session.execute("parameter.set", node=inlet.id, name="velocity", value=f"{velocity} [m/s]")
    del SETTINGS_PATH

    # **No remesh.** The first version re-meshed after assigning the region, on the theory
    # that the case
    # and the record should agree — and `blockMesh` refused: `the xmax side holds faces of fluid and
    # outlet; blockMesh gives a side one patch`. The region's tag shares a side with the
    # outlet tag. The
    # mesh never needed the material anyway (`stokes/mesh/snapshot.py` does not read one), so the
    # re-mesh was both unnecessary and wrong.

    started = session.execute("run.start", wait=True)
    print(f"run.start: {json.dumps(started)}", flush=True)
    # **What `run.fields` reports**, which is what a desktop reads: the names, their ranges and
    # whether a slice was cut. Printed because check 4 is only meaningful if it is non-empty.
    print(
        f"run.fields: {json.dumps(session.execute('run.fields', run=started['run']), default=str)}",
        flush=True,
    )
    status = session.execute("run.status")
    history = session.execute("run.residuals")
    session.execute("project.save")
    print(json.dumps({"run.status": status}, indent=1, default=str), flush=True)

    results = [
        ("1. residuals", check_residuals(status, history)),
        ("2. mass balance", check_mass(status)),
        ("3. pressure drop", check_pressure(status, nu, velocity)),
        (
            "4. wall pressure",
            check_wall_pressure(session, started["run"], str(mesh_done.get("result") or "")),
        ),
    ]
    print()
    failed = False
    for name, (verdict, detail) in results:
        print(f"{verdict:8} {name}: {detail}")
        if verdict == "FAIL":
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
