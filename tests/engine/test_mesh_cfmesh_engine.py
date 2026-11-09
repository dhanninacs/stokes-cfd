"""The four cfMesh volume fills, meshed for real on the Flanged pipe sample (ADR-0032).

    STOKES_FOAMWIN_ENV=<of14-env.ps1> STOKES_ENGINE_TESTS=1 \\
        python -m pytest -q tests/engine/test_mesh_cfmesh_engine.py

The unit tests pin the dictionary; this proves the chain a user gets when they pick a type and
press Generate: the snapshot, the agent's allow-list and thread rule, cfMesh itself, the patch
types it writes and checkMesh's verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.engine, pytest.mark.slow]


@pytest.mark.parametrize(
    ("mesher", "family", "layers"),
    [
        ("cfmesh", "hex", 0),
        ("cfmesh_tet", "tet", 0),
        ("cfmesh_poly", "poly", 0),
        ("polyhex", "hex", 0),
        ("polyhex", "prism", 3),
    ],
)
def test_the_pipe_meshes_with_each_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mesher: str, family: str, layers: int
) -> None:
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))

    import stokes.app.demo  # noqa: F401 - registers the Flanged pipe sample
    from stokes.api import Session

    session = Session()
    sample = session.execute("project.samples")["samples"][0]["id"]
    session.execute("project.sample_open", sample=sample)
    session.execute("mesh.configure", mesher=mesher, values={"cores": 2, "layers": layers})
    session.execute("mesh.generate", wait=True, force=True)

    status = session.execute("mesh.status")
    assert status["state"] == "done", status
    node = session.require_project().find(status["path"])
    assert node is not None
    assert node.params["mesher"] == mesher
    assert node.params["ok"] is True, node.params["quality"]
    quality = node.params["quality"]
    assert quality["failed_checks"] == 0
    assert quality["cell_types"][family] > 0
    assert sorted(node.meta["patch_types"]) == ["inlet", "outlet", "walls"]

    boundary = (
        Path(str(tmp_path / "agent"))
        / "jobs"
        / str(node.params["job"])
        / "constant/polyMesh/boundary"
    ).read_text()
    # cfMesh writes `empty` unless renameBoundary sets the type (measured).
    assert "empty" not in boundary
    assert "wall;" in boundary
