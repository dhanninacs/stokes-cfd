"""Mesh transfer budget: 4,500 parts reach the viewport through ``/api/geometry/batch`` (ADR-0019).

The parts are the 2,250 pin fins of ``tests.geometry_fixtures.pin_grid(50, 45)``, a box and a
cylinder each: 4,500 solids, 20,250 faces, 585 k triangles. The session fixture tessellates them
once per test session at the fine quality an import ends with, the way the worker does
(``tessellate_bodies`` over its batches), and writes each solid's transport bytes into the mesh
cache of a saved project through the import's own ``write_cache``: what an import leaves behind.
The test opens that project and asks for every mesh the way the viewport does (``BATCH_SIZE`` in
web/src/viewport/parts.ts): chunks of 256 ids, one request after another, gzip accepted as a
browser accepts it. The test client's address (``testclient``) is not a loopback one, so the server
compresses as it does for a remote browser, which is the path these budgets guard; the desktop host
is on loopback and gets the meshes uncompressed (``tools/bench_viewport.py`` measures both).

Two time budgets, scaled by the machine factor, and a size limit:

- **cold**: a store that has read nothing yet, so every mesh comes from the project's cache on disk,
  as when a project is opened. File-system bound: on the development laptop each cache file costs
  about 4 ms in ``stat``, ``open`` and ``read``, which the machine factor (a CPU loop) does not
  follow, so this target is wide and guards against a change of kind, not of degree;
- **warm**: the same requests again with the meshes in memory, a reconnecting client. This is the
  server's own work, framing and gzip, and the one that moves with the gzip level;
- **wire**: the gzipped replies, about a ninth of the meshes. Without gzip they are 25.8 MB.

Measured on the development laptop, 2026-10-04, while other sessions shared it: cold 16.8 to
20.1 s, warm 2.5 s, 25.8 MB of meshes as 2.83 MB on the wire. Gzip at level 6 costs about 85 ms
per 256-part chunk there; at Starlette's default of 9 it cost 580 ms for a reply 3 % smaller, which
would put the warm transfer near 11 s and fail here.
"""

from __future__ import annotations

import gzip
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stokes.api import Session
from stokes.geometry.cache import write_cache
from stokes.geometry.mesh import PartMesh
from stokes.geometry.transport import pack_part
from stokes.server.app import NO_MESH, create_app
from tests.perf.harness import budget

CHUNK = 256
TOKEN = "t0k3n"
COLD_TARGET_MS = 20000.0
WARM_TARGET_MS = 3000.0
MAX_WIRE_MB = 6.0


def frames(body: bytes) -> list[tuple[str, int]]:
    """(node id, payload length) for each part of a batch reply; -1 for the no-mesh marker."""
    (count,) = struct.unpack_from("<I", body, 0)
    at = 4
    out: list[tuple[str, int]] = []
    for _ in range(count):
        (id_length,) = struct.unpack_from("<I", body, at)
        node_id = body[at + 4 : at + 4 + id_length].decode("utf-8")
        at += 4 + id_length
        (size,) = struct.unpack_from("<I", body, at)
        at += 4
        if size == NO_MESH:
            out.append((node_id, -1))
            continue
        out.append((node_id, size))
        at += size
    assert at == len(body)
    return out


@pytest.fixture(scope="session")
def pin_project(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[str]]:
    """A saved project of the 4,500 pin-grid solids with their fine meshes cached; its part ids."""
    pytest.importorskip("OCP")
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    from stokes.geometry import step
    from stokes.geometry.worker_main import _batches
    from tests import geometry_fixtures as fx

    solids: list[step.StepSolid] = []
    explorer = TopExp_Explorer(fx.pin_grid(50, 45), TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        body = explorer.Current()
        solids.append(step.StepSolid(f"pin_{len(solids)}", None, body, step.face_count(body)))
        explorer.Next()
    meshes = []
    for batch in _batches(list(enumerate(solids))):
        bodies = [
            step.Body(s.shape, *step.deflections(s.shape, step.FINE), s.name) for _, s in batch
        ]
        meshes.extend(step.tessellate_bodies(bodies, quality=step.FINE.name))

    folder = tmp_path_factory.mktemp("transfer") / "pins.stokes"
    session = Session()
    session.execute("project.new", name="pins", path=str(folder))
    project = session.require_project()
    nodes = [project.add("/geometry", "part", mesh.name) for mesh in meshes]
    session.execute("project.save")
    # Writing a file costs mostly file-system latency (about 14 ms each on the development laptop,
    # a minute for all of them in a row), which overlaps well across threads.
    with ThreadPoolExecutor(max_workers=8) as pool:
        written = pool.map(
            lambda pair: write_cache(folder, pair[0].id, pack_part(PartMesh((pair[1],)))),
            zip(nodes, meshes, strict=True),
        )
        assert len(list(written)) == len(nodes)
    return folder, [node.id for node in nodes]


def fetch_all(client: TestClient, ids: list[str]) -> list[bytes]:
    """Every mesh as the viewport asks for it, CHUNK ids at a time: the replies as they came."""
    replies: list[bytes] = []
    headers = {"X-Stokes-Token": TOKEN, "Accept-Encoding": "gzip"}
    for start in range(0, len(ids), CHUNK):
        chunk = ",".join(ids[start : start + CHUNK])
        with client.stream(
            "GET", "/api/geometry/batch", params={"ids": chunk}, headers=headers
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-encoding"] == "gzip"
            replies.append(b"".join(response.iter_raw()))
    return replies


@pytest.mark.perf
def test_batch_transfer_of_4500_parts(pin_project: tuple[Path, list[str]]) -> None:
    folder, ids = pin_project
    assert len(ids) == 4500
    session = Session()
    session.execute("project.open", path=str(folder))
    client = TestClient(create_app(session, token=TOKEN, dist_dir=None))

    with budget("geometry.batch_transfer_4500_cold", target_ms=COLD_TARGET_MS):
        cold = fetch_all(client, ids)
    with budget("geometry.batch_transfer_4500_warm", target_ms=WARM_TARGET_MS):
        warm = fetch_all(client, ids)

    wire = sum(len(reply) for reply in cold)
    bodies = [gzip.decompress(reply) for reply in cold]
    assert [gzip.decompress(reply) for reply in warm] == bodies
    got = [part for body in bodies for part in frames(body)]
    assert [node_id for node_id, _ in got] == ids
    assert all(size > 0 for _, size in got), "every part has a mesh"
    assert wire <= MAX_WIRE_MB * 1e6, f"{wire / 1e6:.2f} MB on the wire: is the reply gzipped?"
