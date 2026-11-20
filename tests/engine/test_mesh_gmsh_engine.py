"""The Gmsh mesher, meshed for real on the Flanged pipe sample (ADR-0007 decision 5).

    STOKES_FOAMWIN_ENV=<of14-env.ps1> STOKES_ENGINE_TESTS=1 \\
        python -m pytest -q tests/engine/test_mesh_gmsh_engine.py

The unit tests pin the script and the agent's checks; this proves the chain a user gets when they
pick *Tetrahedral (Gmsh)* and press Generate: the STEP export, the agent's script lint and argv
boundary, Gmsh's face-numbering check, `gmshToFoam`, the patch types and checkMesh's verdict.
Needs Gmsh (FoamWin's `gmsh` folder, or `gmsh` on a Linux PATH).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.engine, pytest.mark.slow]


def test_the_pipe_meshes_with_gmsh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))

    import stokes.app.demo  # noqa: F401 - registers the Flanged pipe sample
    from stokes.api import Session

    session = Session()
    sample = session.execute("project.samples")["samples"][0]["id"]
    session.execute("project.sample_open", sample=sample)
    session.execute("mesh.configure", mesher="gmsh", values={"cores": 2, "layers": 0})
    session.execute("mesh.generate", wait=True, force=True)

    status = session.execute("mesh.status")
    assert status["state"] == "done", status
    node = session.require_project().find(status["path"])
    assert node is not None
    assert node.params["mesher"] == "gmsh"
    assert node.params["ok"] is True, node.params["quality"]
    quality = node.params["quality"]
    assert quality["failed_checks"] == 0
    assert quality["cell_types"]["tet"] == quality["cells"] > 0
    assert sorted(node.meta["patch_types"]) == ["inlet", "outlet", "walls"]

    job = Path(str(tmp_path / "agent")) / "jobs" / str(node.params["job"])
    boundary = (job / "constant/polyMesh/boundary").read_text()
    # gmshToFoam writes every patch as `patch`; the driver's foamDictionary steps type the walls.
    assert "wall;" in boundary
    # The numbering check ran and passed: Gmsh printed no error from the script.
    assert "Error" not in (job / "log.gmsh").read_text(errors="replace")
