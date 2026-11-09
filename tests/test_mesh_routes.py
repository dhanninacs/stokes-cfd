"""The mesh patches and the routes that serve them (T-210, ADR-0024 5.3 and 5.5).

**No OpenFOAM and no agent.** A fake runner (the one of `tests/test_mesh_service.py`) answers
`extract.surfaces` and `artifact.get` from an in-memory artifact written by the agent's own
`pack_artifact`, so the desktop is tested against the real format while the host half stays a
fake. What is proved here: after a mesh job finishes the tree holds one `mesh_patch` per patch,
the cache holds the verified file, and the routes serve from it, **with no runner at all** once the
project is reopened.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from stokes.api import Session
from stokes.errors import StokesError
from stokes.geometry.transport import unpack
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.service import MeshService
from stokes.mesh.snapshot import MeshSnapshot
from stokes.mesh.surfaces import unpack_edges
from stokes.server.app import MAX_BATCH_IDS, NO_MESH, create_app
from tests.mesh_surface_support import SurfaceServer, artifact
from tests.test_mesh_service import FakeRunner, make

TOKEN = "t0k3n"
TAG_ID = "t_00000001"


def snapshot() -> MeshSnapshot:
    """Only the inlet is mapped, so the other five sides are the patch `unassigned`."""
    return MeshSnapshot(
        settings_id="n_s",
        mesher="blockmesh",
        base_size=0.1,
        bbox=(0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
        sides={"xmin": "inlet"},
        side_tags={"xmin": TAG_ID},
        side_kinds={"xmin": "velocity_inlet"},
        unmapped=(),
        max_cells=5_000_000,
        cores=1,
        max_non_ortho=70.0,
        max_skewness=4.0,
        domain_part=None,
        part_basis="",
        tag_members={},
    )


def finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> tuple[Session, FakeRunner, dict[str, Any]]:
    """A session whose mesh job has run to the end against the fake runner.

    **`STOKES_HOME` is pointed at `tmp_path` first** (claude-win2, reading 1, requirement 2):
    `project.new` now always creates a folder, and with the real home it created one under the
    DEVELOPER's `~/.stokes` - measured, as an extra `home` entry in the Save-as assertion below.
    A test must not write into the real one.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    # **The visible root as well, since 2026-10-17.** The default project folder follows
    # `STOKES_USER_HOME`, not `STOKES_HOME` - so with only the latter set, `project.new` wrote into
    # the DEVELOPER's real Documents folder and the Save-as assertion below counted one folder
    # fewer: the same failure this fixture's own docstring describes, one root further out.
    monkeypatch.setenv("STOKES_USER_HOME", str(tmp_path / "home"))
    session, fake = make(tmp_path, **kwargs)
    monkeypatch.setattr("stokes.mesh.snapshot.build_snapshot", lambda session, settings: snapshot())
    session.project.add("/geometry", "group", "tags")
    session.project.add("/geometry/tags", "tag", "inlet", {"color": "#ff0000"}, node_id=TAG_ID)
    info = session.execute("mesh.generate")
    fake.finish_with()
    assert MeshService.of(session).wait(10)
    return session, fake, info


def client_of(session: Session) -> TestClient:
    return TestClient(create_app(session, token=TOKEN, dist_dir=None))


def patches_of(session: Session) -> dict[str, Any]:
    result = session.project.find("/mesh/results/blockmesh")
    return {child.name: child for child in result.children if child.type == "mesh_patch"}


def surface_files(session: Session) -> list[Path]:
    folder = MeshService.of(session)._cache_base() / "mesh"
    return sorted(folder.glob("*")) if folder.exists() else []


def frames(body: bytes) -> dict[str, bytes | None]:
    """A batch reply as {id: payload or None}; the framing of `readBatch`."""
    (count,) = struct.unpack_from("<I", body, 0)
    at, out = 4, {}
    for _ in range(count):
        (n,) = struct.unpack_from("<I", body, at)
        nid = body[at + 4 : at + 4 + n].decode()
        (size,) = struct.unpack_from("<I", body, at + 4 + n)
        at += 8 + n
        if size == NO_MESH:
            out[nid] = None
        else:
            out[nid] = body[at : at + size]
            at += size
    assert at == len(body)
    return out


def test_a_finished_mesh_gets_one_patch_node_per_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _fake, info = finished(tmp_path, monkeypatch)
    patches = patches_of(session)
    assert list(patches) == ["inlet", "unassigned"]
    inlet, wall = patches["inlet"], patches["unassigned"]
    assert (inlet.params["index"], wall.params["index"]) == (0, 1)
    assert (inlet.params["faces"], inlet.params["triangles"]) == (1, 2)
    # **The tag of the mapped side**, and its colour; the side nobody tagged has neither.
    assert inlet.params["tag_id"] == TAG_ID
    assert inlet.params["color"] == "#ff0000"
    assert wall.params["tag_id"] == ""
    assert wall.params["color"] == "#9aa3ad"
    # The agent's header says `patch` for both (VTK does not expose the type): `meta.patch_types`
    # carries the driver's, and it decides.
    assert (inlet.params["patch_type"], wall.params["patch_type"]) == ("patch", "wall")
    assert session.project.find(info["path"]).meta["patch_tags"] == {"inlet": TAG_ID}
    # Patches are not undo steps, like the result: undoing the configure leaves them.
    session.execute("history.undo")
    assert list(patches_of(session)) == ["inlet", "unassigned"]


def test_the_patch_events_come_before_mesh_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, fake = make(tmp_path)
    monkeypatch.setattr("stokes.mesh.snapshot.build_snapshot", lambda session, settings: snapshot())
    events: list[tuple[str, str]] = []
    session.events.subscribe("*", lambda name, data: events.append((name, str(data.get("path")))))
    session.execute("mesh.generate")
    fake.finish_with()
    assert MeshService.of(session).wait(10)
    names = [name for name, _ in events]
    added = [path for name, path in events if name == "node.added" and "/inlet" in path]
    assert added == ["/mesh/results/blockmesh/inlet"]
    assert names.index("mesh.done") > max(i for i, (n, p) in enumerate(events) if "inlet" in p)


def test_a_patch_is_served_with_the_token_and_refused_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _fake, _info = finished(tmp_path, monkeypatch)
    client = client_of(session)
    inlet = patches_of(session)["inlet"]
    url = f"/api/mesh/{inlet.id}"
    assert client.get(url).status_code == 401
    assert client.get(f"{url}?token=wrong").status_code == 401
    reply = client.get(f"{url}?token={TOKEN}")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    group = unpack(reply.content).solids[0]
    assert (group.name, group.kind, group.vertex_count, group.triangle_count) == (
        "inlet",
        "mesh",
        4,
        2,
    )
    assert group.face_ids is not None and group.face_ids.tolist() == [0, 0]
    assert group.color == pytest.approx((1.0, 0.0, 0.0))
    # The header token works too (the viewport sends it that way); the version is the existing 2.
    assert client.get(url, headers={"X-Stokes-Token": TOKEN}).status_code == 200
    (length,) = struct.unpack_from("<I", reply.content)
    assert json.loads(reply.content[4 : 4 + length].rstrip(b"\0"))["version"] == 2


def test_the_batch_frames_both_patches_and_marks_an_unknown_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _fake, _info = finished(tmp_path, monkeypatch)
    client = client_of(session)
    patches = patches_of(session)
    ids = [patches["inlet"].id, patches["unassigned"].id, "n_00000000", patches["inlet"].id]
    assert client.get(f"/api/mesh/batch?ids={','.join(ids)}").status_code == 401
    reply = client.get(f"/api/mesh/batch?ids={','.join(ids)}&token={TOKEN}")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    framed = frames(reply.content)
    assert list(framed) == [ids[0], ids[1], "n_00000000"], "a repeated id is answered once"
    assert framed["n_00000000"] is None
    assert unpack(framed[ids[1]] or b"").solids[0].name == "unassigned"
    assert framed[ids[0]] == client.get(f"/api/mesh/{ids[0]}?token={TOKEN}").content
    many = ",".join(f"n_{i:08x}" for i in range(MAX_BATCH_IDS + 1))
    assert client.get(f"/api/mesh/batch?ids={many}&token={TOKEN}").status_code == 413


def test_the_edges_route_serves_the_cell_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _fake, _info = finished(tmp_path, monkeypatch)
    client = client_of(session)
    patches = patches_of(session)
    url = f"/api/mesh/edges/{patches['inlet'].id}"
    assert client.get(url).status_code == 401
    reply = client.get(f"{url}?token={TOKEN}")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    assert unpack_edges(reply.content).shape == (4, 6)
    wall = client.get(f"/api/mesh/edges/{patches['unassigned'].id}?token={TOKEN}")
    assert unpack_edges(wall.content).shape == (3, 6)


def test_an_unknown_id_and_a_node_that_is_not_a_patch_are_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _fake, info = finished(tmp_path, monkeypatch)
    client = client_of(session)
    result = session.project.find(info["path"])
    for node_id in ("n_00000000", str(result.id), "../x", "batch-not-a-route"):
        assert client.get(f"/api/mesh/{node_id}?token={TOKEN}").status_code == 404
        assert client.get(f"/api/mesh/edges/{node_id}?token={TOKEN}").status_code == 404


def test_a_sha256_mismatch_is_mesh_extract_and_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, fake = make(tmp_path)
    monkeypatch.setattr("stokes.mesh.snapshot.build_snapshot", lambda session, settings: snapshot())
    fake.surfaces = SurfaceServer(corrupt=True)
    events: list[tuple[str, dict[str, Any]]] = []
    session.events.subscribe("*", lambda name, data: events.append((name, dict(data))))
    session.execute("mesh.generate")
    fake.finish_with()
    assert MeshService.of(session).wait(10)
    result = session.project.find("/mesh/results/blockmesh")
    # **The mesh still exists on the host**, so the result is `done`; only the picture is missing.
    assert result.params["state"] == "done"
    assert result.params["error"].startswith("mesh.extract: ")
    assert patches_of(session) == {}
    assert surface_files(session) == [], "neither the .part nor the final file may be left"
    failed = [data for name, data in events if name == "mesh.failed"]
    assert [data["code"] for data in failed] == ["mesh.extract"]
    assert "mesh.done" not in [name for name, _ in events]
    status = MeshService.of(session).status()
    assert status["surfaces"] == "missing"
    assert client_of(session).get(f"/api/mesh/n_00000000?token={TOKEN}").status_code == 404


def test_a_host_without_vtk_says_so_in_the_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, fake = make(tmp_path)
    monkeypatch.setattr("stokes.mesh.snapshot.build_snapshot", lambda session, settings: snapshot())
    fake.surfaces.fail = StokesError("no VTK on the host", code="agent.no_vtk")
    session.execute("mesh.generate")
    assert MeshService.of(session).status()["surfaces"] == "pending"
    fake.finish_with()
    assert MeshService.of(session).wait(10)
    assert MeshService.of(session).status()["surfaces"] == "no_vtk"
    assert patches_of(session) == {}


def test_a_reopened_project_serves_its_patches_from_the_cache_with_no_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _fake, _info = finished(tmp_path, monkeypatch)
    folder = tmp_path / "saved"
    # The first save: the cache was in the scratch folder, and it moves with the project.
    session.execute("project.save", path=str(folder))
    expected = (
        client_of(session).get(f"/api/mesh/{patches_of(session)['inlet'].id}?token={TOKEN}").content
    )
    session.execute("project.close")

    # A new session: no `make`, so no fake runner is registered and there is no agent.
    reopened = Session()
    reopened.execute("project.open", path=str(folder))
    client = client_of(reopened)
    inlet = patches_of(reopened)["inlet"]
    reply = client.get(f"/api/mesh/{inlet.id}?token={TOKEN}")
    assert reply.status_code == 200
    assert reply.content == expected
    assert client.get(f"/api/mesh/edges/{inlet.id}?token={TOKEN}").status_code == 200
    assert MeshService.of(reopened).status()["surfaces"] == "ok"

    # The file gone: the patches stay in the tree, without geometry, and the status says so.
    for file in surface_files(reopened):
        file.unlink()
    fresh = client_of(reopened)
    assert fresh.get(f"/api/mesh/{inlet.id}?token={TOKEN}").status_code == 404
    assert MeshService.of(reopened).status()["surfaces"] == "missing"


def test_save_as_copies_the_surface_files_to_the_new_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, _fake, _info = finished(tmp_path, monkeypatch)
    first, second = tmp_path / "one", tmp_path / "two"
    session.execute("project.save", path=str(first))
    session.execute("project.save", path=str(second))
    name = f"{session.project.find('/mesh/results/blockmesh').id}.stkmsh"
    assert [f.name for f in surface_files(session)] == [name]
    # **Every folder that held the project holds its own copy**, which is the rule: the default
    # folder `project.new` created, then the two it was saved as. Asserted as a set of folders
    # rather than a fixed count, because the number is a consequence of the path, not the rule.
    held = {f.relative_to(tmp_path).parts[0]: f.name for f in tmp_path.rglob("*.stkmsh")}
    assert set(held) == {"home", "one.stokes", "two.stokes"}, held
    assert set(held.values()) == {name}, held
    patch = patches_of(session)["inlet"]
    assert client_of(session).get(f"/api/mesh/{patch.id}?token={TOKEN}").status_code == 200


def test_a_replaced_result_loses_its_cache_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, fake, info = finished(tmp_path, monkeypatch)
    old_id = str(session.project.find(info["path"]).id)
    assert [f.name for f in surface_files(session)] == [f"{old_id}.stkmsh"]
    # A second generate replaces the result (M2 keeps one); the old file must go with it.
    second = session.execute("mesh.generate", force=True)
    new_id = str(session.project.find(second["path"]).id)
    assert new_id != old_id
    assert surface_files(session) == []
    fake.finish_with()
    assert MeshService.of(session).wait(10)
    assert [f.name for f in surface_files(session)] == [f"{new_id}.stkmsh"]


def test_a_result_id_cannot_leave_the_mesh_cache(tmp_path: Path) -> None:
    session, _fake = make(tmp_path)
    service = MeshService.of(session)
    for bad in ("../evil", "n_1", "n_00000000/../x", ""):
        with pytest.raises(MeshInvalid):
            service._surface_path(bad)
    good = service._surface_path("n_0123abcd")
    assert good.parent == service._cache_base() / "mesh"


def test_only_two_results_surfaces_stay_in_memory(tmp_path: Path) -> None:
    session, _fake = make(tmp_path)
    service = MeshService.of(session)
    data, _ = artifact()
    for result_id in ("n_00000001", "n_00000002", "n_00000003"):
        path = service._surface_path(result_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        service._load(result_id)
    assert len(service._surfaces) == 2
    assert not any("n_00000001" in key for key in service._surfaces)
