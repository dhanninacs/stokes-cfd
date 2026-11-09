"""The pipe with boundary layers, meshed by the real OpenFOAM on this install (brief M-504).

    STOKES_OPENFOAM_BASHRC=<the env script> STOKES_ENGINE_TESTS=1 PYTHONPATH=.:agent \\
        python3 -m pytest -q tests/engine/test_snappy_layers.py

**What only a real run can prove.** The unit tests prove the writer emits the keys and the
parser reads the table; this proves OpenFOAM accepts the keys, grows the layers, prints the
coverage table the parser reads, and that the whole service path — resolution to dictionary to
job to report — answers "how well did the layers grow?" on the mesh that was actually made.

The three assertions the brief names: `Mesh OK.`, the coverage reported per wall, and **more
cells than the same mesh without layers** — a layer pass that adds nothing is a silent no-op,
which is the failure the withdrawn 2026-10-18 control died of.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Session
from stokes.geometry.store import GeometryStore

pytestmark = pytest.mark.engine

#: The pipe: radius 3 mm, 30 mm long, along x — the cylinder primitive, whose caps are the two
#: faces at the x extremes and whose side is everything else (measured: 3 B-rep faces).
RADIUS_MM = 3.0
LENGTH_MM = 30.0

#: The layer count the settings ask for.
LAYERS = 3


def _pipe(session: Session, folder: Path) -> dict[str, Any]:
    """The pipe project: the part, its three tags, their boundaries, and snappy configured."""
    session.execute("project.new", path=str(folder), name="pipe")
    created = session.execute(
        "operation.create",
        type="primitive",
        params={
            "shape": "cylinder",
            "size": [f"{RADIUS_MM} [mm]", f"{LENGTH_MM} [mm]", "0 [mm]"],
            "position": ["0 [mm]", "0 [mm]", "0 [mm]"],
            "axis": "x",
        },
        wait=True,
    )
    part = str(created["outputs"][0])
    mesh = GeometryStore.of(session).mesh(part)
    assert mesh is not None, "the pipe has no display mesh"
    positions = np.concatenate([np.asarray(s.positions) for s in mesh.solids])
    indices = np.concatenate([np.asarray(s.indices) for s in mesh.solids])
    face_ids = np.concatenate([np.asarray(s.face_ids) for s in mesh.solids])
    xmin, xmax = float(positions[:, 0].min()), float(positions[:, 0].max())
    tol = 1e-6 * (xmax - xmin)
    groups: dict[str, list[int]] = {"inlet": [], "outlet": [], "walls": []}
    for face in sorted({int(f) for f in face_ids}):
        verts = np.unique(indices[face_ids == face])
        cx = float(positions[verts][:, 0].mean())
        if abs(cx - xmin) <= tol:
            groups["inlet"].append(face)
        elif abs(cx - xmax) <= tol:
            groups["outlet"].append(face)
        else:
            groups["walls"].append(face)

    def ranges(ids: list[int]) -> list[list[int]]:
        out: list[list[int]] = []
        for value in sorted(ids):
            if out and value == out[-1][1] + 1:
                out[-1][1] = value
            else:
                out.append([value, value])
        return out

    inlet = session.execute(
        "tag.create",
        name="inlet",
        members=[{"part": part, "faces": ranges(groups["inlet"])}],
        kind="boundary",
    )["id"]
    outlet = session.execute(
        "tag.create",
        name="outlet",
        members=[{"part": part, "faces": ranges(groups["outlet"])}],
        kind="boundary",
    )["id"]
    walls = session.execute(
        "tag.create",
        name="walls",
        members=[{"part": part, "faces": ranges(groups["walls"])}],
        kind="boundary",
    )["id"]
    session.execute(
        "boundary.assign", target=inlet, kind="velocity_inlet", values={"velocity": "0.1 [m/s]"}
    )
    session.execute(
        "boundary.assign", target=outlet, kind="pressure_outlet", values={"pressure": "0 [Pa]"}
    )
    session.execute("boundary.assign", target=walls, kind="wall")
    return {"part": part}


def _meshed(tmp_path_factory: pytest.TempPathFactory, layers: int | None) -> dict[str, Any]:
    """The pipe, meshed with the layer count given, as one module-ready result."""
    folder = tmp_path_factory.mktemp("layers") / f"pipe-{layers}"
    session = Session()
    ids = _pipe(session, folder)
    values: dict[str, Any] = {
        "domain": "part",
        "domain_part": ids["part"],
        "base_size": "1 [mm]",
    }
    if layers is not None:
        values["layers"] = layers
    session.execute("mesh.configure", mesher="snappy", values=values)
    done = session.execute("mesh.generate", wait=True)
    node = session.project.find(done["path"])
    return {"done": done, "node": node, "session": session}


@pytest.fixture(scope="module")
def with_layers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _meshed(tmp_path_factory, LAYERS)


@pytest.fixture(scope="module")
def without_layers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _meshed(tmp_path_factory, None)


def test_the_pipe_meshes_WITH_layers_and_checks(with_layers: dict[str, Any]) -> None:
    done, node = with_layers["done"], with_layers["node"]
    assert done["state"] == "done", done
    quality = node.params["quality"]
    assert quality["mesh_ok"] is True, quality
    assert node.params["ok"] is True
    assert quality["cells"] == node.params["cells"]


def test_the_coverage_is_reported_per_wall(with_layers: dict[str, Any]) -> None:
    """`meta.layers` — the table the snappy parser read from the run's own log — carries the
    wall, its faces, the mean layer count and the coverage."""
    node = with_layers["node"]
    table = node.meta.get("layers")
    assert isinstance(table, list) and table, f"no layer table on the result: {node.meta}"
    walls = next((row for row in table if row["patch"] == "walls"), None)
    assert walls is not None, table
    assert walls["faces"] > 0
    assert walls["layers_mean"] == pytest.approx(LAYERS, abs=0.5), walls
    assert walls["coverage_percent"] > 80.0, walls


def test_layers_add_cells(with_layers: dict[str, Any], without_layers: dict[str, Any]) -> None:
    """**The silent no-op guard.** The same pipe without layers must give fewer cells: a layer
    pass that adds none is exactly the dead control that was withdrawn once already."""
    with_cells = int(with_layers["node"].params["cells"])
    without_cells = int(without_layers["node"].params["cells"])
    assert with_cells > without_cells, (with_cells, without_cells)


def test_a_mesh_without_layers_carries_no_layer_table(
    without_layers: dict[str, Any],
) -> None:
    node = without_layers["node"]
    assert "layers" not in node.meta, node.meta.get("layers")
    assert node.meta["warnings"] == []
