"""Gmsh meshed for real on a 20 x 10 x 10 mm duct, with and without prism layers (ADR-0043).

    STOKES_FOAMWIN_ENV=<of14-env.ps1> STOKES_ENGINE_TESTS=1 \\
        python -m pytest -q tests/engine/test_mesh_gmsh_engine.py -s

The unit tests pin the script; this proves the chain a user gets when they pick Gmsh and press
Generate: the snapshot, the agent's grammar and allow-list, Gmsh itself, `gmshToFoam`, the patch
types and checkMesh's verdict - and the one limit measured on this shape, the layers folding at the
duct's sharp inside edges when the stack is thicker than about 0.6 of the wall cell.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.engine, pytest.mark.slow]

#: The duct and its cells, in millimetres.
SIZE_MM = (20, 10, 10)
BASE_MM = 1.5


#: The pipe: radius 5 mm, 20 mm long on x - smooth walls, where the layers have room.
PIPE_MM = (5, 20, 0)


def _part(session: Any, folder: Path, shape: str) -> str:
    """The duct or the pipe with `inlet`, `outlet` and `walls` tagged, as the GUI makes it; the
    part's id. The ends are the faces at the lowest and highest x."""
    from tools.e2e_support import _faces_by_side, _output_part

    session.execute("project.new", path=str(folder), name=shape)
    size = SIZE_MM if shape == "box" else PIPE_MM
    created = session.execute(
        "operation.create",
        type="primitive",
        params={
            "shape": shape,
            "size": [f"{n} [mm]" for n in size],
            "position": ["0 [mm]", "0 [mm]", "0 [mm]"],
            **({"axis": "x"} if shape == "cylinder" else {}),
        },
        wait=True,
    )
    part = _output_part(session, created)
    faces = _faces_by_side(session, part)
    kinds = {"inlet": "velocity_inlet", "outlet": "pressure_outlet", "walls": "wall"}
    for name, kind in kinds.items():
        tag = session.execute(
            "tag.create", name=name, members=[{"part": part, "faces": faces[name]}], kind="boundary"
        )["id"]
        session.execute("boundary.assign", target=tag, kind=kind)
    return part


def _mesh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, Any],
    shape: str = "box",
    base_mm: float = BASE_MM,
) -> tuple[Any, dict[str, Any], float]:
    """Mesh the part with Gmsh and the given settings; the session, the status, the seconds."""
    monkeypatch.setenv("STOKES_AGENT_HOME", str(tmp_path / "agent"))
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent.parent))
    from stokes.api import Session

    session = Session()
    part = _part(session, tmp_path / f"{shape}.stokes", shape)
    session.execute(
        "mesh.configure",
        mesher="gmsh",
        values={"domain": "part", "domain_part": part, "base_size": f"{base_mm} [mm]", **values},
    )
    started = time.monotonic()
    session.execute("mesh.generate", wait=True, force=True)
    return session, session.execute("mesh.status"), time.monotonic() - started


#: Three layers from a 0.1 mm first layer: 0.36 mm, under half of the duct's 0.75 mm wall cell.
FIRST_HEIGHT = {"layers": 3, "layer_mode": "first_height", "first_layer_height": "0.1 [mm]"}


@pytest.mark.parametrize(
    ("shape", "layers", "values", "bar"),
    [
        ("box", 0, {"layers": 0}, True),
        # **checkMesh passes, Stokes's 65 degree bar does not**: the duct's sharp inside edges
        # leave the cells beside the layers' corners at 66 to 86 degrees (measured over several
        # stacks), so the result is a warning, not a failure.
        ("box", 3, FIRST_HEIGHT, False),
        # A smooth wall has room: the default stack (final ratio 0.3) passes every bar.
        ("cylinder", 3, {"layers": 3}, True),
    ],
)
def test_the_part_meshes_with_gmsh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    layers: int,
    values: dict[str, Any],
    bar: bool,
) -> None:
    session, status, seconds = _mesh(
        tmp_path, monkeypatch, values, shape, 1.5 if shape == "box" else 1.0
    )
    assert status["state"] == "done", status
    node = session.require_project().find(status["path"])
    assert node is not None
    assert node.params["mesher"] == "gmsh"
    quality = node.params["quality"]
    assert quality["failed_checks"] == 0, quality
    if bar:
        assert node.params["ok"] is True, quality
    types = quality["cell_types"]
    assert types["tet"] > 0
    assert sorted(node.meta["patch_types"]) == ["inlet", "outlet", "walls"]
    boundary = (
        tmp_path / "agent" / "jobs" / str(node.params["job"]) / "constant/polyMesh/boundary"
    ).read_text()
    # gmshToFoam writes every patch as `patch`; the type step makes the walls walls.
    walls = boundary[boundary.index("walls") :]
    assert "type            wall;" in walls[: walls.index("}")]
    if layers:
        wall_faces = int(walls[walls.index("nFaces") :].split()[1].rstrip(";"))
        # **The layers are prisms on every wall face**, `layers` deep, and nothing else is.
        assert types["prism"] == layers * wall_faces
    else:
        assert types.get("prism", 0) == 0
    print(  # the numbers the ADR records
        f"\ngmsh {shape} layers={layers}: cells {node.params['cells']}, tet {types['tet']}, "
        f"prism {types.get('prism', 0)}, max non-ortho {quality.get('max_non_ortho')}, "
        f"max skewness {quality.get('max_skewness')}, checkMesh failed {quality['failed_checks']}, "
        f"ok {node.params['ok']}, {seconds:.1f} s"
    )


def test_layers_thicker_than_the_corner_allows_fail_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The measured limit**: three layers at the default final ratio (0.3, ratio 1.2) are 0.76
    of the duct's 0.75 mm wall cell, and their fronts fold into each other at its inside edges. The
    job fails, and the message says why and what to change rather than "gmsh exited 1"."""
    session, status, seconds = _mesh(tmp_path, monkeypatch, {"layers": 3})
    assert status["state"] == "failed", status
    node = session.require_project().find(status["path"])
    assert node is not None
    message = str(node.params.get("error") or status.get("error") or "")
    assert "fold into each other at a sharp inside corner" in message, (status, node.params)
    print(f"\ngmsh duct default layers: failed as measured in {seconds:.1f} s: {message}")
