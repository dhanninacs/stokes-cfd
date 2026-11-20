"""coupledSimpleFoam's heated duct through Stokes: Nu = 7.54 (ADR-0035 step 5, M5's exit test).

    STOKES_ENGINE_TESTS=1 STOKES_COUPLED_HOST=wsl:Ubuntu-24.04 \\
        python -m pytest -q tests/engine/test_coupled_heated_duct_engine.py
    STOKES_ENGINE_TESTS=1 STOKES_COUPLED_HOST=local STOKES_FOAMWIN_ENV=<of14-env.ps1> \\
        python -m pytest -q tests/engine/test_coupled_heated_duct_engine.py

The case is the solver's own `tutorials/heatedDuct` built through Stokes' commands: a 10 m x 1 m
plane channel in 2D (blockMesh), nu = 0.1 m2/s, Pr = 7, a 1 m/s inlet at 300 K, walls at 350 K.
Fully developed laminar flow between isothermal plates has Nu = 7.5407 (Shah & London); the
solver's README reproduces it. The Nusselt number is measured with the repository's own
`tools/heated_duct_nusselt.py` (standard library only) on the job folder, so the check is the
solver's and the case is Stokes'.

Needs either a WSL host (`wsl:<distro>`) whose OpenFOAM 14 has coupledSimpleFoam on its PATH and
whose Python has msgpack, or `local` on Windows with a FoamWin install that has
`coupledSimpleFoam.exe` (FoamWin's `scripts/build-coupled.sh`). Skips without
`STOKES_COUPLED_HOST`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytestmark = [pytest.mark.engine, pytest.mark.slow]

HOST = os.environ.get("STOKES_COUPLED_HOST", "")
#: The exact fully developed value, and how close the four downstream stations must be.
NU_EXACT = 7.5407
NU_TOLERANCE = 0.01
#: The solver repository's post-processor, read from the WSL distribution or the repository.
NUSSELT_TOOL = "https://raw.githubusercontent.com/dhanninacs/coupledSimpleFoam/main/tools/heated_duct_nusselt.py"


def _faces(session: Any, part: str) -> dict[str, list[int]]:
    """The box's faces by side: inlet (x min), outlet (x max), front/back (z), walls (y)."""
    from stokes.geometry.store import GeometryStore

    mesh = GeometryStore.of(session).mesh(part)
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
    out: dict[str, list[int]] = {"inlet": [], "outlet": [], "z": [], "walls": []}
    for face in sorted({int(f) for f in faces}):
        used = verts[np.unique(corners[faces == face])]
        centre = used.mean(axis=0)
        if abs(centre[0] - lo[0]) <= tol:
            out["inlet"].append(face)
        elif abs(centre[0] - hi[0]) <= tol:
            out["outlet"].append(face)
        elif abs(centre[2] - lo[2]) <= tol or abs(centre[2] - hi[2]) <= tol:
            out["z"].append(face)
        else:
            out["walls"].append(face)
    return out


def _property(session: Any, material_path: str, kind: str, value: str) -> None:
    node = session.require_project().find(material_path)
    child = next(c for c in node.children if c.type == "property" and c.name == kind)
    session.execute("parameter.set", node=child.id, name="value", value=value)


def _wsl(distro: str, script: str) -> str:
    run = subprocess.run(
        ["wsl.exe", "-d", distro, "--", "bash", "-s"],
        input=script.encode(),
        capture_output=True,
        timeout=600,
        check=False,
    )
    return (run.stdout + run.stderr).decode(errors="replace").replace("\0", "")


def _native_nusselt(job: Path, cache: Path) -> str:
    """Cell centres with FoamWin's foamPostProcess, then the solver's tool on this Python."""
    env_script = os.environ.get("STOKES_FOAMWIN_ENV") or str(
        Path(os.environ.get("LOCALAPPDATA", "")) / "OpenFOAM-14-win" / "of14-env.ps1"
    )
    command = (
        f"$ErrorActionPreference='Stop'; . '{env_script}' | Out-Null; Set-Location '{job}'; "
        "foamPostProcess -latestTime -func writeCellCentres *> log.cellCentres"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, timeout=600)
    latest = max(
        (d.name for d in job.iterdir() if d.is_dir() and re.fullmatch(r"[0-9.eE+-]+", d.name)),
        key=float,
    )
    tool = cache / "heated_duct_nusselt.py"
    if not tool.is_file():
        # A fixed https URL in this module, never user input.
        with urllib.request.urlopen(NUSSELT_TOOL, timeout=60) as response:
            tool.write_bytes(response.read())
    run = subprocess.run(
        [sys.executable, "-I", str(tool), str(job), latest, "350"],
        capture_output=True,
        timeout=600,
        check=False,
    )
    return (run.stdout + run.stderr).decode(errors="replace")


def _wsl_nusselt(distro: str, job: str) -> str:
    """The same measurement inside the WSL distribution, on the agent's default job root."""
    return _wsl(
        distro,
        f"""set -e
source /opt/openfoam14/etc/bashrc >/dev/null 2>&1
cd ~/.stokes/jobs/{job}
latest=$(foamListTimes -latestTime 2>/dev/null | tail -1)
foamPostProcess -latestTime -func writeCellCentres >/dev/null 2>&1
tool=/tmp/stokes_heated_duct_nusselt.py
[ -f "$tool" ] || curl -fsSL {NUSSELT_TOOL} -o "$tool"
python3 "$tool" . "$latest" 350
""",
    )


@pytest.mark.skipif(
    not (HOST.startswith("wsl:") or HOST == "local"),
    reason="set STOKES_COUPLED_HOST=wsl:<distro> or local",
)
def test_the_heated_duct_reproduces_nu_7_54(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    if HOST == "local":
        monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    from stokes.api import Session

    session = Session()
    session.execute("project.new", path=str(tmp_path / "duct"), name="duct")
    created = session.execute(
        "operation.create",
        type="primitive",
        params={
            "shape": "box",
            "size": ["10000 [mm]", "1000 [mm]", "50 [mm]"],
            "position": ["0 [mm]", "0 [mm]", "0 [mm]"],
        },
        wait=True,
    )
    from tools.e2e_support import _output_part, _ranges

    part = _output_part(session, created)
    faces = _faces(session, part)
    for name, kind, values in (
        ("inlet", "velocity_inlet", {"velocity": "1 [m/s]", "temperature": "300 [K]"}),
        ("outlet", "pressure_outlet", {"pressure": "0 [Pa]", "backflow_temperature": "300 [K]"}),
        ("walls", "wall", {"thermal": "temperature", "temperature": "350 [K]"}),
    ):
        tag = session.execute(
            "tag.create",
            name=name,
            members=[{"part": part, "faces": _ranges(faces[name])}],
            kind="boundary",
        )["id"]
        session.execute("boundary.assign", target=tag, kind=kind, values=values)
    material = session.execute(
        "material.create", name="duct_fluid", kind="fluid", template="liquid"
    )
    # nu = mu/rho = 0.1 m2/s and Pr = mu*cp/kappa = 7, as the tutorial.
    _property(session, material["path"], "density", "1 [kg/m^3]")
    _property(session, material["path"], "viscosity", "0.1 [Pa*s]")
    _property(session, material["path"], "specific_heat", "1000 [J/(kg*K)]")
    _property(session, material["path"], "thermal_conductivity", f"{100 / 7!r} [W/(m*K)]")
    session.execute("region.assign", target=part, material=material["id"], name="fluid")

    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "domain": "part",
            "domain_part": part,
            "base_size": "25 [mm]",
            "two_d": "planar",
            "plane": "xy",
            "host": HOST,
        },
    )
    session.execute("mesh.generate", wait=True)
    assert session.execute("mesh.status")["state"] == "done"

    session.execute(
        "solve.configure",
        engine="coupled.simple",
        values={"energy": True, "iterations": 200, "residual_target": 1e-7, "host": HOST},
    )
    started = session.execute("run.start", wait=True)
    from stokes.run.store import RunStore

    record = RunStore.of(session).status(started["run"])
    assert record["state"] == "done", record
    assert record.get("converged") is True, record

    job = str(record.get("job") or "")
    assert re.fullmatch(r"j_[0-9a-f]{6,32}", job), record
    if HOST == "local":
        output = _native_nusselt(tmp_path / "agent" / "jobs" / job, tmp_path)
    else:
        output = _wsl_nusselt(HOST.removeprefix("wsl:"), job)
    # The tool prints one row per station: x, Tb, Tw - Tb, |dT/dy|_wall, Nu, Nu/7.5407.
    number = r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?"
    rows = re.findall(
        rf"^\s*({number})\s+({number})\s+({number})\s+({number})\s+({number})"
        rf"\s+({number})\s*$",
        output,
        flags=re.MULTILINE,
    )
    values = [float(row[4]) for row in rows]
    assert values, output
    worst = max(abs(v - NU_EXACT) / NU_EXACT for v in values)
    assert worst <= NU_TOLERANCE, f"Nu {values} against {NU_EXACT}\n{output}"
