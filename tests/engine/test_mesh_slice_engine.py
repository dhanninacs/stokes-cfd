"""The duct, meshed end to end through the API (T-207).

    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \\
        python3 -m pytest -q tests/engine/test_mesh_slice_engine.py

**What only this test can check.** `tests/test_mesh_service.py` drives a fake runner, so it
proves the
service's bookkeeping. This builds a project with real commands, runs a real blockMesh through a
real
agent and reads the mesh off the disk — the whole chain a user gets when they press Mesh.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

pytestmark = [pytest.mark.engine, pytest.mark.slow]


def test_the_duct_is_meshed_through_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build, generate, and read the boundary file the job wrote.

    The three patches and their types are the assertion: `inlet` and `outlet` as `patch` and
    `walls` as
    `wall` is what the tag conditions asked for, seen in the mesh rather than in our own
    bookkeeping.
    """
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))

    from stokes.api import Session
    from tools.e2e_support import duct_project

    session = Session()
    ids = duct_project(session, tmp_path / "duct")
    assert ids["part"], "the primitive produced no part"

    info = session.execute("mesh.generate", wait=True)
    status = session.execute("mesh.status")
    assert status["state"] == "done", f"the mesh ended {status['state']}: {info}"
    # **25,000 cells**: 0.5 x 0.02 x 0.02 m at 2 mm, which is the brief's number.
    node = session.project.find(status["path"])
    assert node is not None and node.params["cells"] == 25_000
    assert node.params["ok"] is True

    # **The quality, from checkMesh through the agent's parser.** The duct is a perfectly
    # regular box, so
    # its non-orthogonality is 0 and its skewness is numerical noise — which is what makes an exact
    # assertion possible: a wrong parse cannot produce these numbers by accident.
    quality = node.params["quality"]
    assert quality["mesh_ok"] is True
    assert quality["max_non_ortho"] == pytest.approx(0.0, abs=1e-9)
    assert quality["max_skewness"] < 1e-6
    assert quality["max_aspect_ratio"] == pytest.approx(1.0)
    assert quality["failed_checks"] == 0
    assert quality["thresholds"] == {"max_non_ortho": 70.0, "max_skewness": 4.0}
    # The counts come from checkMesh, not from us: 250 x 10 x 10 cells is 30,371 points and
    # 80,100 faces.
    assert node.params["points"] == 30_371
    assert node.params["faces"] == 80_100
    # No warnings: every tag is on a side of the box, which is the whole reason the slice uses
    # the duct
    # rather than the heat sink.
    assert node.meta["warnings"] == []
    assert node.meta["patch_tags"], "the patch-to-tag map is empty"
    assert node.meta["patch_types"]["walls"] == "wall"

    # **The mesh on disk.** The job staged a case under the agent's home; its boundary file is
    # what T-210
    # will serve and T-214 will solve.
    job = str(node.params["job"])
    case = Path(os.environ["STOKES_AGENT_HOME"]) / "jobs" / job
    assert case.is_dir(), f"the job folder {case} is missing"
    boundary = (case / "constant" / "polyMesh" / "boundary").read_text()
    for name in ("inlet", "outlet", "walls"):
        assert f"\n    {name}\n" in boundary, f"{name} is not in the boundary file:\n{boundary}"
    assert boundary.count("type            patch;") == 2
    assert "type            wall;" in boundary
    assert "unassigned" not in boundary, "every side got a tag, so there should be no default patch"
