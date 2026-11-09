"""B-rep checks and STEP export (ADR-0020 decisions 6 and 7; ADR-0021 decision 5).

The unit checks need OCP; the worker tests run the real worker process, which takes seconds to
import OCP, so the module prewarms it. A two-solid STEP and a shell missing its top face stand in
for real files.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import brep, worker
from stokes.geometry.checkstore import CheckStore
from stokes.geometry.errors import GeometryFileError
from stokes.geometry.step import read_step
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.selection_support import cube, events_of, part, put_mesh

GROUP = "/geometry/parts/two"
OPEN_PART = "/geometry/parts/open"


def _spec(source: Path, index: int, name: str) -> dict[str, Any]:
    """The shape-provider spec of solid ``index`` of ``source``, uncached and named ``name``."""
    return {
        "part": f"n_{index:08x}",
        "name": name,
        "basis": "",
        "source": str(source),
        "solid": index,
        "units": None,
        "brep": None,
        "derived": "",
    }


@pytest.fixture(scope="module")
def two_solids(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    worker.prewarm(3)
    yield fx.write_named(
        [
            ("bracket", fx.box(10.0, 20.0, 30.0), fx.RED),
            ("pin", fx.moved(fx.cylinder(5.0, 40.0), 50.0, 0.0, 0.0), fx.BLUE),
        ],
        tmp_path_factory.mktemp("cad") / "two.step",
    )
    worker.shutdown_pool()


@pytest.fixture(scope="module")
def open_step(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """One shell part: a 20 mm box whose +z face (face id 5) is gone."""
    worker.prewarm(3)
    yield fx.write_plain(
        fx.open_box((20.0, 20.0, 20.0)), tmp_path_factory.mktemp("cad") / "open.step"
    )
    worker.shutdown_pool()


def one_face_shell() -> Any:  # Any: OCP shape
    """A shell of a single face: its four boundary edges are free."""
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shell

    face = TopExp_Explorer(fx.box(10.0, 20.0, 30.0), TopAbs_ShapeEnum.TopAbs_FACE).Current()
    builder = BRep_Builder()
    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    builder.Add(shell, face)
    return shell


# ------------------------------------------------------------------ the checker


def test_a_closed_box_has_a_clean_brep() -> None:
    (found,) = brep.check_brep([(0, "box", fx.box(10.0, 20.0, 30.0))], unit_scale=1e-3)
    assert found.valid and found.ok
    assert (found.free_edges, found.open_shells, found.small_edges, found.small_faces) == (
        0,
        0,
        0,
        0,
    )
    assert found.to_json()["ok"] is True


def test_an_open_shell_reports_its_free_edges() -> None:
    (found,) = brep.check_brep([(0, "open", one_face_shell())], unit_scale=1.0)
    assert not found.ok and found.free_edges == 4 and found.open_shells == 1


def test_a_box_missing_a_face_reports_the_four_free_edge_faces() -> None:
    """A 20 mm box with its +z face removed: that face is id 5, so the four side faces (0 to 3)
    each hold one of the free edges of the opening."""
    (found,) = brep.check_brep([(0, "open", fx.open_box((20.0, 20.0, 20.0)))], unit_scale=1e-3)
    assert (found.free_edges, found.open_shells) == (4, 1)
    assert found.free_edge_faces == (0, 1, 2, 3)
    assert found.to_json()["free_edge_faces"] == [0, 1, 2, 3]
    assert found.truncated is False


def test_a_loose_compound_is_open_and_names_its_faces() -> None:
    """T-027's measured bug: ``ShapeAnalysis_FreeBounds`` reports 0 free edges for an unsewn
    compound of loose faces, so the check has to walk the faces itself (6 faces, 24 edges, all
    free). ``brep._free_edges`` keeps that blind count for the operations framework."""
    shapes = fx.loose_faces((20.0, 20.0, 20.0))
    (found,) = brep.check_brep([(0, "loose", shapes)], unit_scale=1e-3)
    assert (found.free_edges, found.free_edge_faces) == (24, (0, 1, 2, 3, 4, 5))
    assert found.ok is False and brep._free_edges(shapes, 1e-6) == 0


def test_tiny_edges_and_faces_are_counted_once() -> None:
    """A 0.5 micrometre cube (the fixtures are in millimetres): 12 edges and 6 faces."""
    (found,) = brep.check_brep([(0, "speck", fx.box(0.0005, 0.0005, 0.0005))], unit_scale=1e-3)
    assert (found.small_edges, found.small_faces) == (12, 6) and not found.ok


def test_a_sphere_is_clean() -> None:
    """The degenerate edges at a sphere's poles have no length: they are not small edges."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    (found,) = brep.check_brep([(0, "ball", BRepPrimAPI_MakeSphere(7.0).Shape())], unit_scale=1e-3)
    assert found.ok


# ------------------------------------------------------------------ the worker jobs


def test_the_worker_checks_parts_through_the_provider(two_solids: Path, tmp_path: Path) -> None:
    """A cached B-rep is checked without reading the source file; without the cache the job reads
    it once. The provider summary is what T-031 asserts stays at 0 source reads on a repeat."""
    from stokes.geometry.shapes import MM, brep_path, write_shape

    folder = tmp_path / "cache"
    part, basis = "n_0000000a", "abc123"
    write_shape(
        brep_path(folder, part),
        read_step(two_solids).solids[0].shape,
        part=part,
        basis=basis,
        scale=MM,
    )
    spec = {
        **_spec(two_solids, 0, "bracket"),
        "part": part,
        "basis": basis,
        "brep": str(brep_path(folder, part)),
    }
    cached = worker.acquire().check_brep([spec])
    assert cached["provider"] == {"source_reads": 0, "cache_hits": 1}
    assert cached["parts"][0]["part"] == part
    assert cached["parts"][0]["valid"] and cached["parts"][0]["ok"]
    uncached = worker.acquire().check_brep([{**_spec(two_solids, 0, "bracket"), "part": part}])
    assert uncached["provider"] == {"source_reads": 1, "cache_hits": 0}


def test_the_worker_checks_the_chosen_solids(two_solids: Path) -> None:
    payload = worker.acquire().check_brep([_spec(two_solids, 0, "bracket")])
    parts = payload["parts"]
    assert [p["index"] for p in parts] == [0]
    assert parts[0]["valid"] and parts[0]["ok"]


def test_the_worker_writes_step_with_names_and_metres(two_solids: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.step"
    payload = worker.acquire().export_step(
        [_spec(two_solids, 0, "bracket"), _spec(two_solids, 1, "pin")], out
    )
    assert payload["solids"] == 2
    again = read_step(out)
    assert [s.name for s in again.solids] == ["bracket", "pin"]
    assert again.units == "m"
    # `read_step` gives shapes in its working unit (millimetres); in metres the bracket box is
    # 0.01 x 0.02 x 0.03 across. A file scaled twice would read back a thousand times smaller.
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bounds = Bnd_Box()
    BRepBndLib.Add_s(again.solids[0].shape, bounds)
    lo, hi = bounds.CornerMin(), bounds.CornerMax()
    extent = (hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z())
    assert [e * again.unit_scale for e in extent] == pytest.approx([0.01, 0.02, 0.03], rel=1e-5)


def test_a_write_that_fails_is_an_error(two_solids: Path, tmp_path: Path) -> None:
    with pytest.raises(GeometryFileError):
        worker.acquire().export_step([_spec(two_solids, 0, "bracket")], tmp_path)  # a folder


# ------------------------------------------------------------------ the command


def imported(folder: Path, source: Path) -> Session:
    session = Session(journal_path=folder / "journal.jsonl")
    session.execute("project.new", name="demo", path=str(folder / "demo"))
    session.execute("geometry.import_step", path=str(source), wait=True)
    return session


def _started_jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Record the check jobs the command starts, so a test can assert none was."""
    seen: list[dict[str, str]] = []

    def start(self: Any, specs: Any, bases: dict[str, str], *, project: Any) -> None:
        seen.append(dict(bases))
        return

    monkeypatch.setattr(CheckStore, "start", start)
    return seen


def test_geometry_check_merges_the_brep_findings(two_solids: Path, tmp_path: Path) -> None:
    session = imported(tmp_path / "check", two_solids)
    single = session.execute("geometry.check", part=f"{GROUP}/bracket", wait=True)
    assert single["brep"]["checked"] is True
    assert single["brep"]["valid"] is True and single["brep"]["ok"] is True
    group = session.execute("geometry.check", part=GROUP, wait=True)
    assert all(item["brep"]["checked"] for item in group["parts"])


def test_geometry_check_reports_the_free_edge_faces(open_step: Path, tmp_path: Path) -> None:
    """A derived or imported part is checked through the provider and reports the face ids of its
    free-edge faces, in the part's own numbering."""
    session = imported(tmp_path / "open", open_step)
    result = session.execute("geometry.check", part=OPEN_PART, wait=True)
    assert result["brep"]["checked"] is True
    assert (result["brep"]["free_edges"], result["brep"]["open_shells"]) == (4, 1)
    assert result["brep"]["free_edge_faces"] == [0, 1, 2, 3]
    node = session.require_project().get(OPEN_PART)
    assert result["brep"]["basis"] == GeometryStore.of(session).basis(node)


def test_a_check_without_wait_reports_running_and_announces_the_result(
    open_step: Path, tmp_path: Path
) -> None:
    session = imported(tmp_path / "async", open_step)
    part_id = session.require_project().get(OPEN_PART).id
    seen = events_of(session)
    immediate = session.execute("geometry.check", part=OPEN_PART)
    assert immediate["brep"] == {"checked": False, "reason": "running"}
    assert CheckStore.of(session).wait()
    brep = session.require_project().get(OPEN_PART).meta["check"]["brep"]
    assert brep["checked"] is True and brep["free_edge_faces"] == [0, 1, 2, 3]
    # the parts that were already checked come back from meta.check.brep, with no job
    again = session.execute("geometry.check", part=OPEN_PART)
    assert again["brep"]["checked"] is True and again["brep"]["at"] == brep["at"]
    assert [d["node_id"] for e, d in seen if e == "node.changed"] == [part_id] * 3
    progress = [d for e, d in seen if e == "geometry.check_progress"]
    assert progress and progress[0] == {
        "node_id": part_id,
        "path": OPEN_PART,
        "index": 0,
        "count": 1,
    }


def test_a_second_check_starts_no_job(
    open_step: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = imported(tmp_path / "repeat", open_step)
    first = session.execute("geometry.check", part=OPEN_PART, wait=True)
    assert first["brep"]["checked"] is True
    at = session.require_project().get(OPEN_PART).meta["check"]["brep"]["at"]
    started = _started_jobs(monkeypatch)
    second = session.execute("geometry.check", part=OPEN_PART, wait=True)
    assert started == []
    assert second["brep"]["checked"] is True
    assert session.require_project().get(OPEN_PART).meta["check"]["brep"]["at"] == at


def test_force_checks_the_brep_again(
    open_step: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = imported(tmp_path / "force", open_step)
    session.execute("geometry.check", part=OPEN_PART, wait=True)
    started = _started_jobs(monkeypatch)
    session.execute("geometry.check", part=OPEN_PART, force=True, wait=True)
    basis = session.require_project().get(OPEN_PART).params["basis"]
    assert started == [{session.require_project().get(OPEN_PART).id: basis}]


def test_a_part_that_changes_while_checked_is_queued_for_another_check(
    open_step: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request at a newer basis than the running job must not be dropped: it is queued, and the
    job's end starts it. Without that, a part changed mid-check never had its new version checked.
    """
    from stokes.geometry.checkstore import CheckJob
    from stokes.geometry.shapes import part_spec

    session = imported(tmp_path / "queued", open_step)
    project = session.require_project()
    store = CheckStore.of(session)
    geometry = GeometryStore.of(session)
    node = project.get(OPEN_PART)
    running = CheckJob(project=project, specs=[], bases={node.id: "the-old-basis"})
    store._jobs[node.id] = running  # a job is checking this part at the old version

    current = geometry.basis(node)
    spec = part_spec(node, geometry.shape_dir(), current)
    # The same part, at the basis it holds now: queued, not dropped, and no second job starts.
    # The command names parts by path; the job keys them by node id, and both must line up.
    started = store.start([spec], {OPEN_PART: current}, project=project)
    assert node.id in store._followups, (
        f"not queued: current={current!r} spec_part={spec.get('part')!r} "
        f"started={started!r} jobs={list(store._jobs)!r}"
    )

    seen: list[tuple[list[dict[str, Any]], dict[str, str]]] = []

    def start(self: Any, specs: Any, bases: dict[str, str], *, project: Any) -> None:
        seen.append((list(specs), dict(bases)))

    monkeypatch.setattr(CheckStore, "start", start)
    store._drain_followups(running, {node.id: store._followups.pop(node.id)})
    assert len(seen) == 1
    specs, bases = seen[0]
    assert bases == {node.id: current}
    assert specs and specs[0]["part"] == node.id
    GeometryStore.of(session).close()


def test_the_check_follows_a_derived_part_through_the_shape_cache(
    two_solids: Path, tmp_path: Path
) -> None:
    """The gap T-031 closes: a part made by an operation has no source file, and the check used to
    skip it silently. Its B-rep is in the shape cache, so the job reads that (no source read)."""
    from stokes.geometry.shapes import MM, brep_path, fresh, write_shape

    session = imported(tmp_path / "derived", two_solids)
    store = GeometryStore.of(session)
    derived = session.execute(
        "node.add",
        parent="/geometry/parts",
        type="part",
        name="bracket_copy",
        params={"derived": "n_00000000", "basis": "deadbeef", "faces": 6},
    )["id"]
    put_mesh(session, derived, part(cube(0.01, name="bracket_copy")))
    # its B-rep is the bracket's, cached under the derived part's id at the version it claims
    write_shape(
        brep_path(store.shape_dir(), derived),
        read_step(two_solids).solids[0].shape,
        part=derived,
        basis="deadbeef",
        scale=MM,
    )
    assert fresh(brep_path(store.shape_dir(), derived), "deadbeef")
    result = session.execute("geometry.check", part=derived, wait=True)
    assert result["brep"]["checked"] is True
    assert result["brep"]["valid"] is True and result["brep"]["ok"] is True


def test_geometry_export_step_writes_the_parts(two_solids: Path, tmp_path: Path) -> None:
    session = imported(tmp_path / "export", two_solids)
    project = Path(str(session.project_path))
    target = project / "parts.step"
    result = session.execute("geometry.export_step", parts=[GROUP], path=str(target))
    assert result["solids"] == 2 and result["units"] == "m"
    assert Path(result["file"]).is_file()
    assert [s.name for s in read_step(target).solids] == ["bracket", "pin"]
