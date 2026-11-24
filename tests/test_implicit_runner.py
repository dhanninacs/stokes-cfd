"""The implicit mesher's runner and output file (ADR-0035 decisions 13, 16, 17 and 19).

The real ``stokes-implicit-mesh`` is not needed: the tests point ``STOKES_IMPLICIT_MESHER`` at
the reference mesher in ``tests/implicit/reference_mesher.py``, which speaks the same command line
and writes the same file. Tests of the real program run in the CI job that builds it.
"""

from __future__ import annotations

import math
import struct
import threading
from pathlib import Path

import numpy as np
import pytest

from stokes.implicit import features as F  # noqa: N812 - the builders read as F.box, F.sphere
from stokes.implicit import graph, meshfile, runner
from stokes.implicit.errors import ImplicitTooFine, MesherCancelled, MesherFailed, MesherMissing

REFERENCE = Path(__file__).parent / "implicit" / "reference_mesher.py"


@pytest.fixture
def reference(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(runner.ENV_OVERRIDE, str(REFERENCE))
    return REFERENCE


def _request(body: F.Body, cell: float) -> runner.MeshRequest:
    low, high = body.bounded()
    centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
    from stokes.implicit.frame import translation

    root = body.field(translation(tuple(-c for c in centre)))  # type: ignore[arg-type]
    local = (
        tuple(a - c for a, c in zip(low, centre, strict=True)),
        tuple(b - c for b, c in zip(high, centre, strict=True)),
    )
    return runner.MeshRequest(graph.to_text(root), runner.cube_for(local, cell))  # type: ignore[arg-type]


def _volume(mesh: meshfile.KernelMesh) -> float:
    p = mesh.positions.astype(np.float64)
    t = mesh.triangles.astype(np.int64)
    return float(np.einsum("ij,ij->i", p[t[:, 0]], np.cross(p[t[:, 1]], p[t[:, 2]])).sum() / 6)


def _edge_uses(mesh: meshfile.KernelMesh) -> np.ndarray:
    t = mesh.triangles.astype(np.int64)
    edges = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return counts


# ------------------------------------------------------------------------------- the file
def test_the_mesh_file_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "m.bin"
    meshfile.write(path, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], [7])
    mesh = meshfile.read(path)
    assert mesh.positions.shape == (3, 3) and mesh.triangles.tolist() == [[0, 1, 2]]
    assert mesh.labels.tolist() == [7]


@pytest.mark.parametrize(
    ("damage", "why"),
    [
        ("magic", "wrong magic"),
        ("short", "bytes where the counts say"),
        ("index", "does not exist"),
        ("nan", "not finite"),
        ("huge", "over the limit"),
    ],
)
def test_a_damaged_mesh_file_is_refused(tmp_path: Path, damage: str, why: str) -> None:
    path = tmp_path / "m.bin"
    positions = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    triangles = [[0, 1, 2]]
    if damage == "index":
        triangles = [[0, 1, 3]]
    if damage == "nan":
        positions = [[0, 0, 0], [math.nan, 0, 0], [0, 1, 0]]
    meshfile.write(path, positions, triangles)
    data = bytearray(path.read_bytes())
    if damage == "magic":
        data[:8] = b"NOTAMESH"
    elif damage == "short":
        data = data[:-4]
    elif damage == "huge":
        data[12:16] = struct.pack("<I", meshfile.MAX_TRIANGLES + 1)
    path.write_bytes(bytes(data))
    with pytest.raises(MesherFailed, match=why):
        meshfile.read(path)


# ------------------------------------------------------------------------------- the cube
def test_the_cube_covers_the_box_with_a_margin_and_an_irrational_shift() -> None:
    cube = runner.cube_for(((-10.0, -5.0, -1.0), (10.0, 5.0, 1.0)), 0.5)
    assert cube.cell <= 0.5
    assert cube.half >= 10.0 + runner.MARGIN_CELLS * cube.cell - 1e-9
    assert cube.centre == pytest.approx((runner.SHIFT * 0.5,) * 3)  # shifted by a cell fraction


def test_a_cell_that_needs_too_deep_an_octree_is_refused_with_the_finest_that_fits() -> None:
    with pytest.raises(ImplicitTooFine) as found:
        runner.cube_for(((-500.0, -500.0, -500.0), (500.0, 500.0, 500.0)), 0.001)
    finest = found.value.details["finest"]
    assert (
        runner.cube_for(((-500.0, -500.0, -500.0), (500.0, 500.0, 500.0)), finest * 1.01).depth
        <= 15
    )


# ------------------------------------------------------------------------------- the program
def test_a_missing_mesher_names_the_build_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(runner.ENV_OVERRIDE, str(tmp_path / "nowhere.exe"))
    with pytest.raises(MesherMissing):
        runner.find_mesher()


def test_a_sphere_meshes_closed_with_its_volume(reference: Path, tmp_path: Path) -> None:
    body = F.sphere(10.0, (1000.0, -2000.0, 50.0))  # far from the origin: the local frame matters
    outcome = runner.mesh(_request(body, 0.5), tmp_path)
    mesh = outcome.mesh
    assert len(mesh.triangles) > 1000
    assert bool((_edge_uses(mesh) == 2).all())
    assert _volume(mesh) == pytest.approx(4 / 3 * math.pi * 1000, rel=0.02)
    assert outcome.stats["open_edges"] == 0 and not outcome.cached
    assert np.abs(mesh.positions).max() < 20.0  # kernel frame: centred on the part


def test_a_second_run_comes_from_the_cache(reference: Path, tmp_path: Path) -> None:
    request = _request(F.sphere(3.0, (0.0, 0.0, 0.0)), 0.2)
    first = runner.mesh(request, tmp_path)
    second = runner.mesh(request, tmp_path)
    assert second.cached and not first.cached
    assert np.array_equal(first.mesh.triangles, second.mesh.triangles)
    assert len(list(tmp_path.glob("*.mesh"))) == 1


def test_the_same_request_meshed_at_once_by_two_runs_both_finish(
    reference: Path, tmp_path: Path
) -> None:
    """Each run has its own work folder: a shared one was removed under the other run."""
    request = _request(F.sphere(3.0, (0.0, 0.0, 0.0)), 0.2)
    results: list[runner.MeshOutcome] = []
    errors: list[BaseException] = []

    def one() -> None:
        try:
            results.append(runner.mesh(request, tmp_path))
        except BaseException as exc:  # collected for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=one) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and len(results) == 4
    assert len(list(tmp_path.glob("*.mesh"))) == 1
    assert [p for p in tmp_path.iterdir() if p.is_dir()] == []


def test_the_cache_is_pruned_least_recently_used_first(tmp_path: Path) -> None:
    import os
    import time

    for i in range(3):
        path = tmp_path / f"{i}.mesh"
        path.write_bytes(b"x" * 100)
        (tmp_path / f"{i}.json").write_text("{}")
        os.utime(path, (time.time() - 100 + i, time.time() - 100 + i))
    assert runner.prune(tmp_path, 250) == 1
    assert sorted(p.name for p in tmp_path.glob("*.mesh")) == ["1.mesh", "2.mesh"]
    assert not (tmp_path / "0.json").exists()


def test_two_touching_boxes_mesh_as_one_closed_shell(reference: Path, tmp_path: Path) -> None:
    """Decision 19: the union of two boxes that share a face has a zero of the field inside the
    solid; the shifted cube keeps cell corners off it at every cell size."""
    joined = F.union(
        [F.box((10.0, 10.0, 10.0), (0.0, 0.0, 0.0)), F.box((10.0, 10.0, 10.0), (10.0, 0.0, 0.0))]
    )
    for cell in (0.4, 0.5, 0.625, 0.8, 1.0):
        mesh = runner.mesh(_request(joined, cell), tmp_path).mesh
        assert bool((_edge_uses(mesh) == 2).all()), cell
        assert _volume(mesh) == pytest.approx(2000.0, rel=0.08), cell


def test_a_cancelled_run_is_killed(reference: Path, tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(MesherCancelled):
        runner.mesh(_request(F.sphere(3.0, (0.0, 0.0, 0.0)), 0.1), tmp_path, cancel=cancel)
    assert not list(tmp_path.glob("*.mesh"))


def test_a_mesher_error_line_is_reported(reference: Path, tmp_path: Path) -> None:
    request = _request(F.sphere(3.0, (0.0, 0.0, 0.0)), 0.1)
    bad = runner.MeshRequest(request.text, runner.Cube(request.cube.centre, 4.0, 16))
    with pytest.raises(MesherFailed, match="depth"):
        runner.mesh(bad, tmp_path)
