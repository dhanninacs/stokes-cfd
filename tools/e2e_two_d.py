"""The 2D mode end to end (ADR-0031): two cases built, meshed and solved through the API only.

    STOKES_FOAMWIN_ENV=<of14-env.ps1> python tools/e2e_two_d.py <scratch folder> [channel|step]

* **channel** - a planar 2D channel, 200 x 10 mm in xy, 1 mm thick, on blockMesh. Laminar at
  Re = 50 (on the height); the pressure drop is checked against plane Poiseuille,
  `dp = 12 nu U L / h^2`, with an allowance for the entrance region.
* **step** - a backward-facing step, the same 2D on snappyHexMesh: a slab whose inlet channel is
  half the height of the main one, cut from a box, extruded to one cell by `extrudeMesh`. Checked
  for a converged run and a mass balance.

Everything goes through `session.execute`, so the run is what a client would do. Prints one line per
check and exits non-zero when one fails.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from stokes.api import Session
from tools.e2e_support import _output_part, _ranges

RE = 50.0


def _faces(session: Session, part_id: str) -> dict[str, list[int]]:
    """CAD faces by where their centroid lies on the part's box: x low/high, z low/high, other."""
    import numpy as np

    from stokes.geometry.store import GeometryStore

    mesh = GeometryStore.of(session).mesh(part_id)
    assert mesh is not None
    pos, idx, fid, off = [], [], [], 0
    for solid in mesh.solids:
        p = np.asarray(solid.positions, dtype="float64")
        pos.append(p)
        idx.append(np.asarray(solid.indices) + off)
        fid.append(np.asarray(solid.face_ids))
        off += len(p)
    verts, corners, faces = np.concatenate(pos), np.concatenate(idx), np.concatenate(fid)
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    tol = 1e-6 * float(np.linalg.norm(hi - lo))
    out: dict[str, list[int]] = {"xmin": [], "xmax": [], "zmin": [], "zmax": [], "other": []}
    for face in sorted({int(f) for f in faces}):
        used = verts[np.unique(corners[faces == face])]
        c = used.mean(axis=0)
        flat = np.ptp(used, axis=0) <= tol
        if flat[0] and abs(c[0] - lo[0]) <= tol:
            out["xmin"].append(face)
        elif flat[0] and abs(c[0] - hi[0]) <= tol:
            out["xmax"].append(face)
        elif flat[2] and abs(c[2] - lo[2]) <= tol:
            out["zmin"].append(face)
        elif flat[2] and abs(c[2] - hi[2]) <= tol:
            out["zmax"].append(face)
        else:
            out["other"].append(face)
    return out


def _box(session: Session, size: tuple[float, float, float], at: tuple[float, float, float]) -> str:
    created = session.execute(
        "operation.create",
        type="primitive",
        params={
            "shape": "box",
            "size": [f"{v} [mm]" for v in size],
            "position": [f"{v} [mm]" for v in at],
        },
        wait=True,
    )
    return _output_part(session, created)


def _boundaries(session: Session, part: str, faces: dict[str, list[int]], velocity: float) -> None:
    for name, ids, kind, values in (
        ("inlet", faces["xmin"], "velocity_inlet", {"velocity": f"{velocity} [m/s]"}),
        ("outlet", faces["xmax"], "pressure_outlet", {"pressure": "0 [Pa]"}),
        ("walls", faces["other"], "wall", {}),
    ):
        tag = session.execute(
            "tag.create",
            name=name,
            members=[{"part": part, "faces": _ranges(ids)}],
            kind="boundary",
        )["id"]
        session.execute("boundary.assign", target=tag, kind=kind, values=values)


def _material(session: Session, part: str) -> None:
    material = session.execute("material.create", name="air", kind="fluid", template="gas")
    session.execute("region.assign", target=part, material=material["id"], name="fluid")


def _solve(session: Session, part: str, iterations: int) -> dict[str, Any]:
    del part
    session.execute(
        "solve.configure",
        values={"iterations": iterations, "residual_target": 1e-6, "write_interval": iterations},
    )
    started = session.execute("run.start", wait=True)
    from stokes.run.store import RunStore

    store = RunStore.of(session)
    return {"run": started["run"], "status": store.status(started["run"])}


def _nu(session: Session) -> float:
    from stokes.mesh.service import MeshService
    from stokes.run.snapshot import build_solve_snapshot

    status = MeshService.of(session).status()
    settings = session.require_project().find("/solver/settings")
    assert settings is not None, "the project has no solver settings"
    snap = build_solve_snapshot(session, settings, status)
    return float(snap.nu)


def _mesh(session: Session) -> dict[str, Any]:
    t0 = time.monotonic()
    session.execute("mesh.generate", wait=True)
    from stokes.mesh.service import MeshService

    status = MeshService.of(session).status()
    print(
        f"mesh: {status.get('state')} cells={status.get('cells')} ({time.monotonic() - t0:.0f} s)"
    )
    return status


def _patch_types(session: Session) -> dict[str, str]:
    project = session.require_project()
    return {
        str(node.name): str(node.params.get("patch_type"))
        for node in project.root.walk()
        if node.type == "mesh_patch"
    }


def channel(folder: Path) -> list[tuple[str, bool, str]]:
    """The planar channel on blockMesh, checked against plane Poiseuille."""
    session = Session()
    session.execute("project.new", path=str(folder / "channel2d"), name="channel2d")
    length, height = 0.2, 0.01
    part = _box(session, (200, 10, 1), (0, 0, 0))
    faces = _faces(session, part)
    # The front and back (zmin, zmax) are left untagged: the 2D mesher owns them.
    _boundaries(session, part, faces, 0.01)
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "domain": "part",
            "domain_part": part,
            "base_size": "0.5 [mm]",
            "two_d": "planar",
            "plane": "xy",
        },
    )
    mesh = _mesh(session)
    types = _patch_types(session)
    _material(session, part)
    nu = _nu(session)
    velocity = RE * nu / height
    inlet = next(
        n
        for n in session.require_project().root.walk()
        if n.type == "boundary" and n.name == "inlet"
    )
    session.execute("parameter.set", node=inlet.id, name="velocity", value=f"{velocity} [m/s]")
    solved = _solve(session, part, 3000)
    status = solved["status"]
    monitors = status.get("monitors") or {}
    p_in = (monitors.get("inlet") or {}).get("p_mean")
    p_out = (monitors.get("outlet") or {}).get("p_mean")
    q_in = (monitors.get("inlet") or {}).get("flow_rate")
    q_out = (monitors.get("outlet") or {}).get("flow_rate")
    expected = 12 * nu * velocity * length / height**2
    ratio = (p_in - p_out) / expected if p_in is not None and p_out is not None else math.nan
    checks = [
        # 400 or 401 along x (blockMesh ceils 0.2/0.0005 in floating point), 20 across, ONE thick.
        (
            "channel mesh one cell thick",
            mesh.get("cells") in (400 * 20, 401 * 20),
            f"cells={mesh.get('cells')}",
        ),
        ("channel frontAndBack is empty", types.get("frontAndBack") == "empty", str(types)),
        (
            "channel converged",
            bool(status.get("converged")),
            f"state={status.get('state')} it={status.get('iterations')}",
        ),
        (
            "channel mass balance",
            q_in is not None
            and q_out is not None
            and abs(abs(q_in) - abs(q_out)) <= 1e-3 * abs(q_in),
            f"in={q_in} out={q_out}",
        ),
        (
            "channel dp / Poiseuille in 1.00..1.10",
            1.0 <= ratio <= 1.10,
            f"ratio={ratio:.4f} (dp={p_in}-{p_out}, expected {expected:.4e})",
        ),
    ]
    session.execute("project.save")  # so the solved case can be opened in the app
    print(f"channel run {solved['run']} in {folder / 'channel2d'}")
    return checks


def step(folder: Path) -> list[tuple[str, bool, str]]:
    """A backward-facing step on snappyHexMesh, extruded to one cell."""
    session = Session()
    session.execute("project.new", path=str(folder / "step2d"), name="step2d")
    block = _box(session, (250, 10, 1), (-50, 0, 0))
    cutter = _box(session, (50, 5, 3), (-50, -0.0, -1))
    created = session.execute(
        "operation.create",
        type="boolean",
        inputs=[block, cutter],
        params={"op": "cut", "tools": [cutter]},
        wait=True,
    )
    part = _output_part(session, created)
    faces = _faces(session, part)
    nu_guess = 1.5e-5
    step_height = 0.005
    velocity = RE * nu_guess / step_height
    _boundaries(session, part, faces, velocity)
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={
            "domain": "part",
            "domain_part": part,
            "base_size": "0.5 [mm]",
            "two_d": "planar",
            "plane": "xy",
        },
    )
    mesh = _mesh(session)
    types = _patch_types(session)
    _material(session, part)
    solved = _solve(session, part, 3000)
    status = solved["status"]
    monitors = status.get("monitors") or {}
    q_in = (monitors.get("inlet") or {}).get("flow_rate")
    q_out = (monitors.get("outlet") or {}).get("flow_rate")
    checks = [
        ("step mesh done", mesh.get("state") == "done", str(mesh.get("error") or "")),
        # checkMesh's own verdict: a mesh whose front/back are not flat has no 2D direction and
        # fails it. (Stokes's `ok` also applies the settings' 70-degree non-orthogonality limit.)
        (
            "step mesh passes checkMesh",
            (mesh.get("quality") or {}).get("failed_checks") == 0,
            f"max_non_ortho={(mesh.get('quality') or {}).get('max_non_ortho')}",
        ),
        (
            "step front and back are empty",
            types.get("front") == "empty" and types.get("back") == "empty",
            str(types),
        ),
        (
            "step converged",
            bool(status.get("converged")),
            f"state={status.get('state')} it={status.get('iterations')}",
        ),
        (
            "step mass balance",
            q_in is not None
            and q_out is not None
            and abs(abs(q_in) - abs(q_out)) <= 1e-3 * abs(q_in),
            f"in={q_in} out={q_out}",
        ),
    ]
    session.execute("project.save")  # so the solved case can be opened in the app
    print(f"step run {solved['run']} in {folder / 'step2d'}")
    return checks


def main() -> int:
    folder = Path(sys.argv[1]).resolve()
    which = sys.argv[2:] or ["channel", "step"]
    checks: list[tuple[str, bool, str]] = []
    for name in which:
        checks += {"channel": channel, "step": step}[name](folder)
    for label, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {label}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
