"""``operation.delete`` takes the outputs out of the tags and puts them back on undo.

A tag's ``members`` are JSON, not ``ref`` parameters, so the model cannot prune them when a part
is removed (``Project.referrers`` says so explicitly). Without its own strip, deleting an operation
left every tag pointing at parts that no longer existed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import worker
from stokes.geometry.store import GeometryStore
from tests.operations_support import create_and_wait


@pytest.fixture(scope="module", autouse=True)
def warm() -> Iterator[None]:
    worker.prewarm(1)
    yield None
    worker.shutdown_pool()


def tagged_output(tmp_path: Path) -> tuple[Session, str, str, str]:
    """A primitive box, a tag holding a face of its output. Returns the session, the op id, the
    output part id and the tag id."""
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    made = create_and_wait(session, "primitive", [], {"shape": "box", "size": [0.01, 0.02, 0.03]})
    assert made["status"] == "ok", made.get("error")
    output = made["outputs"][0]
    tag = session.execute("tag.create", name="face_tag", members=[{"part": output, "faces": [0]}])
    return session, made["id"], output, str(tag["id"])


def test_delete_takes_the_outputs_out_of_the_tags(tmp_path: Path) -> None:
    session, op, output, tag = tagged_output(tmp_path)
    project = session.require_project()
    assert [m["part"] for m in session.execute("tag.members", tag=tag)["members"]] == [output]
    session.execute("operation.delete", op=op)
    # The tag lost the member naming the part that went away, and the tag the run created is gone.
    assert session.execute("tag.members", tag=tag)["members"] == []
    assert project.find(op) is None and project.find(output) is None
    GeometryStore.of(session).close()


def test_undo_of_a_delete_brings_the_tag_member_back(tmp_path: Path) -> None:
    session, op, output, tag = tagged_output(tmp_path)
    project = session.require_project()
    before = session.execute("tag.members", tag=tag)["members"]
    session.execute("operation.delete", op=op)
    assert session.execute("tag.members", tag=tag)["members"] == []
    session.execute("history.undo")
    assert project.find(op) is not None and project.find(output) is not None
    assert session.execute("tag.members", tag=tag)["members"] == before
    GeometryStore.of(session).close()


def test_a_cancelled_run_writes_no_mesh(tmp_path: Path) -> None:
    """`_solid` writes an output's mesh through the cancellation-aware session path, not through a
    bare `job.cancel` check that a Cancel or an Undo can slip past in between."""
    from stokes.geometry.mesh import PartMesh
    from stokes.geometry.opstore import OperationStore, RunJob
    from stokes.geometry.store import FINAL_PASS, MeshEntry
    from stokes.geometry.worker import SolidResult
    from tests.selection_support import cube

    session, op, output, _tag = tagged_output(tmp_path)
    project = session.require_project()
    geometry = GeometryStore.of(session)
    service = OperationStore.of(session)
    job = RunJob(op=op, project=project, reason="run")
    job.outputs[0] = output
    job.cancel.set()  # cancelled before the mesh lands
    geometry.put(output, MeshEntry(PartMesh((cube(0.01, name="old"),), "m", None)))
    result = SolidResult(
        pass_name=FINAL_PASS,
        index=0,
        count=1,
        mesh=cube(0.02, name="new"),
        untriangulated=0,
        seconds=0.0,
    )

    service._solid(job, result)
    # The cancelled run wrote nothing: the part still holds the mesh it had.
    held = geometry.mesh(output)
    assert held is not None
    assert held.solids[0].name == "old", "a cancelled run overwrote the part's mesh"
    GeometryStore.of(session).close()
