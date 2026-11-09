"""Helper processes for a large import: frames, how many to start, and that they change nothing.

The point of the helpers is speed (the 4,500-solid pin grid imports in about 41 s instead of 75 s),
so what these tests pin is the opposite: the meshes a helper makes are the ones the worker makes
itself, a helper that fails or dies hands its batch back, and an import with helpers is the same
import.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import numpy as np
import pytest

from stokes.geometry import helpers

# ---- frames


def test_frames_round_trip_and_a_cut_frame_is_an_error() -> None:
    stream = io.BytesIO()
    helpers.write_frame(stream, b"hello")
    helpers.write_frame(stream, b"")
    helpers.write_frame(stream, memoryview(b"abc"))
    stream.seek(0)
    assert helpers.read_frame(stream) == b"hello"
    assert helpers.read_frame(stream) == b""
    assert helpers.read_frame(stream) == b"abc"
    assert helpers.read_frame(stream) is None, "a clean end of stream is None"
    cut = io.BytesIO(stream.getvalue()[:10])  # ends inside the first frame's body
    with pytest.raises(EOFError):
        helpers.read_frame(cut)


def test_a_frame_over_the_limit_is_refused_before_it_is_read() -> None:
    stream = io.BytesIO()
    helpers.write_frame(stream, b"x" * 100)
    stream.seek(0)
    with pytest.raises(ValueError, match="over the limit"):
        helpers.read_frame(stream, limit=10)


# ---- how many


@pytest.mark.parametrize(
    ("file_bytes", "cpus", "env", "expected"),
    [
        (50 * 1024**2, 24, None, 8),  # a big file on a big machine: the cap
        (50 * 1024**2, 8, None, 6),  # the CPUs less two
        (50 * 1024**2, 4, None, 2),
        (50 * 1024**2, 3, None, 0),  # too few CPUs for helpers to help
        (1 * 1024**2, 24, None, 0),  # a small file reads and meshes in seconds
        (1 * 1024**2, 24, "4", 4),  # the override wins, even over the size rule
        (50 * 1024**2, 24, "0", 0),  # and can turn them off
        (50 * 1024**2, 24, "99", 8),  # clamped
        (50 * 1024**2, 24, "-3", 0),
        (50 * 1024**2, 24, "many", 8),  # unreadable: ignored, the default applies
        (50 * 1024**2, 24, "  ", 8),
    ],
)
def test_helper_count(file_bytes: int, cpus: int, env: str | None, expected: int) -> None:
    assert helpers.helper_count(file_bytes, cpus=cpus, env=env) == expected


# ---- real shapes

ocp = pytest.importorskip("OCP")

from stokes.geometry import step, worker  # noqa: E402
from stokes.geometry import worker_main as wm  # noqa: E402
from stokes.geometry.step import StepSolid  # noqa: E402
from tests import geometry_fixtures as fx  # noqa: E402


class Collector:
    """A stand-in for the parent's Connection: keeps what a batch sends."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def send_bytes(self, data: bytes | memoryview) -> None:
        self.frames.append(bytes(data))


def solids_of(shape: object) -> list[StepSolid]:
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    found: list[StepSolid] = []
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        body = explorer.Current()
        found.append(StepSolid(f"pin_{len(found)}", None, body, step.face_count(body)))
        explorer.Next()
    return found


def solid_frames(frames: list[bytes]) -> list[tuple[dict[str, object], list[bytes]]]:
    """The JSON header and six arrays of each solid in a stream of frames."""
    out = []
    for at in range(0, len(frames), helpers.FRAMES_PER_SOLID):
        group = frames[at : at + helpers.FRAMES_PER_SOLID]
        out.append((json.loads(group[0]), group[1:]))
    return out


def test_a_helper_meshes_a_batch_as_the_worker_does(tmp_path: Path) -> None:
    solids = solids_of(fx.pin_grid(3, 2))  # 12 solids
    batch = list(enumerate(solids))
    specs = [wm._spec_of(i, s, step.COARSE) for i, s in batch]
    own = Collector()
    wm.mesh_batch(
        own,
        [s.shape for _, s in batch],
        specs,
        quality="coarse",
        unit=1e-3,
        workdir=tmp_path,
        count=len(solids),
        final=True,
    )
    target = tmp_path / "batch.bin"
    wm._write_batch(target, [s.shape for _, s in batch])
    described = tmp_path / "batch.json"
    described.write_text(json.dumps(specs), encoding="utf-8")
    pool = helpers.HelperPool.start(2)
    try:
        assert pool.size == 2
        # Both helpers, so a round-robin pair of batches uses each: the same batch twice.
        ids = [
            pool.submit(
                {
                    "path": str(target),
                    "bodies": str(described),
                    "quality": "coarse",
                    "unit": 1e-3,
                    "count": len(solids),
                    "final": True,
                }
            )
            for _ in range(2)
        ]
        outcomes = [pool.take(i, 120) for i in ids]
    finally:
        pool.close()
    expected = solid_frames(own.frames)
    for outcome in outcomes:
        assert outcome.ok, outcome.message
        got = solid_frames(outcome.frames)
        assert len(got) == len(expected) == 12
        for (head_a, arrays_a), (head_b, arrays_b) in zip(expected, got, strict=True):
            for key in ("index", "name", "faces", "vertices", "triangles", "count", "pass"):
                assert head_a[key] == head_b[key], key
            # positions, normals, indices, face ids, adjacency: the same bytes
            for a, b in zip(arrays_a, arrays_b, strict=True):
                assert a == b


def test_a_batch_that_cannot_be_read_comes_back_failed_with_the_reason(tmp_path: Path) -> None:
    described = tmp_path / "none.json"
    spec = {"index": 0, "name": "a", "kind": "solid", "color": None, "linear": 1.0, "angular": 0.5}
    described.write_text(json.dumps([spec]), encoding="utf-8")
    pool = helpers.HelperPool.start(1)
    try:
        batch = pool.submit(
            {
                "path": str(tmp_path / "missing.bin"),
                "bodies": str(described),
                "quality": "coarse",
                "unit": 1e-3,
                "count": 0,
                "final": False,
            }
        )
        outcome = pool.take(batch, 120)
    finally:
        pool.close()
    assert not outcome.ok and outcome.message, "the worker is told why, and meshes it itself"


def test_a_dead_helper_fails_its_batches_instead_of_hanging() -> None:
    pool = helpers.HelperPool.start(1)
    try:
        pool._helpers[0].process.kill()
        time.sleep(0.5)  # the drain thread sees the end of the stream
        batch = pool.submit({"path": "x", "bodies": "y", "quality": "coarse", "unit": 1.0})
        outcome = pool.take(batch, 10)
    finally:
        pool.close()
    assert not outcome.ok


def test_a_pool_with_no_helpers_fails_every_batch_at_once() -> None:
    pool = helpers.HelperPool()
    outcome = pool.take(pool.submit({}), 1)
    assert not outcome.ok and "no helper" in outcome.message


# ---- an import


def collect(path: Path, workdir: Path) -> dict[tuple[str, int], tuple[int, int, bytes, bytes]]:
    seen: dict[tuple[str, int], tuple[int, int, bytes, bytes]] = {}

    def on_solid(result: worker.SolidResult) -> None:
        mesh = result.mesh
        seen[(result.pass_name, result.index)] = (
            mesh.vertex_count,
            mesh.triangle_count,
            np.ascontiguousarray(mesh.positions).tobytes(),
            np.ascontiguousarray(mesh.face_ids).tobytes() if mesh.face_ids is not None else b"",
        )

    worker.run_import(path, workdir=workdir, on_solid=on_solid)
    return seen


def test_an_import_with_helpers_is_the_same_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same solids, passes, counts, positions and face ids with helpers as without."""
    source = fx.write_named(
        [(f"pin_{i}", s.shape, None) for i, s in enumerate(solids_of(fx.pin_grid(20, 18)))],
        tmp_path / "pins.step",
    )
    worker.shutdown_pool()
    monkeypatch.setenv(helpers.ENV_COUNT, "0")
    (tmp_path / "w0").mkdir()
    alone = collect(source, tmp_path / "w0")
    worker.shutdown_pool()
    monkeypatch.setenv(helpers.ENV_COUNT, "3")
    (tmp_path / "w3").mkdir()
    helped = collect(source, tmp_path / "w3")
    worker.shutdown_pool()
    assert len(alone) == len(helped) == 2 * 720, "every solid at both passes"
    assert set(alone) == set(helped)
    for key in sorted(alone):
        assert alone[key][:2] == helped[key][:2], key
        assert alone[key][3] == helped[key][3], f"face ids {key}"
        # Meshing is deterministic, but a helper turns OCCT's own threading off: positions agree
        # to float32 precision rather than being asserted bit for bit.
        a = np.frombuffer(alone[key][2], dtype=np.float32)
        b = np.frombuffer(helped[key][2], dtype=np.float32)
        assert np.allclose(a, b, atol=1e-6), key
