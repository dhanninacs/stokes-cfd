"""The blockMesh driver's output, run by real OpenFOAM (T-206).

    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \\
        python3 -m pytest -q tests/engine/test_mesh_blockmesh_engine.py

**What only this test can check.** The unit tests prove the driver writes what we intend. This
proves
OpenFOAM accepts it — and, more importantly, that the **side face vertex orders are oriented
correctly**:
`checkMesh` fails a wrongly-oriented face, so `Mesh OK.` is the evidence, and a unit test
comparing vertex
numbers cannot be.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from test_mesh_blockmesh import duct

from stokes.mesh.drivers.blockmesh import write

pytestmark = pytest.mark.engine


def test_the_duct_meshes_and_checks(tmp_path: Path) -> None:
    """25,000 cells, `Mesh OK.`, and a boundary file naming the three patches with the right types.

    The boundary file is the part worth reading: it is what T-147b serves to the viewport, so a
    wrong type
    there is a wrong patch in the UI rather than a meshing failure.
    """
    from stokes_agent.engine_env import get_env
    from stokes_agent.lint import lint_text

    tree = write(duct())
    # **The agent's lint first**, so the writer and the lint cannot disagree about what is safe
    # to stage.
    for rel, text in tree.items():
        found = lint_text(rel, text)
        assert not found, f"the agent's lint refused {rel}: {found[0].to_json()}"

    case = tmp_path / "duct"
    for rel, text in tree.items():
        target = case / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    env = get_env(None).env

    def run(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=case,
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    mesh = run(["blockMesh"], 300)
    assert mesh.returncode == 0, mesh.stdout[-3000:]
    assert "nCells: 25000" in mesh.stdout, mesh.stdout[-1500:]

    check = run(["checkMesh"], 300)
    assert check.returncode == 0, check.stdout[-3000:]
    assert "Mesh OK." in check.stdout, check.stdout[-3000:]
    assert "cells:            25000" in check.stdout, check.stdout[-1500:]

    boundary = (case / "constant" / "polyMesh" / "boundary").read_text()
    for name in ("inlet", "outlet", "walls"):
        assert f"\n    {name}\n" in boundary, f"{name} is not in the boundary file:\n{boundary}"
    # The types the kinds asked for. `walls` is a wall patch and the other two are ordinary patches.
    assert "type            wall;" in boundary
    assert boundary.count("type            patch;") == 2, boundary
    # And nothing else got a patch: three names, no `unassigned`.
    assert "unassigned" not in boundary
