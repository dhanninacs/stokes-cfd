"""Staleness when an operation reads a tag its own runs also write (T-026).

The brief's case: a ``cap`` fills the pipe tee's three openings under a tag its run created,
and ``extract_fluid`` reads that tag as its ``caps``. The extraction's run carries the tag too
— the cap faces become faces of the fluid — so the tag ends with a member for the extraction's
own output next to the member for the capped pipe it read. That member is the run's output, not
its input: the operation must stay ``ok``, it must not be re-run for it, and a re-run must not
refuse it as "not an input of this run". A user's own edit of the same tag must still make it
``stale`` (the tests here and ``test_a_parameter_change_re_runs_once_after_the_last_change``).

The face ids come from the fixture's own ``TopExp`` order through the session, never from a
number hard-coded here.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import worker
from stokes.geometry.opstore import OperationStore
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.operations_support import create_and_wait

TEE_RADIUS = 5.0
TEE_LENGTH = 30.0
DEBOUNCE_SETTLE = 1.0
"""Three debounces (300 ms each) plus slack: a re-run this bug scheduled would have started."""

Events = list[tuple[str, dict[str, Any]]]


@pytest.fixture(scope="module", autouse=True)
def warm() -> Iterator[None]:
    """One worker kept ahead for the session tests, as the service does."""
    worker.prewarm(1)
    yield None
    worker.shutdown_pool()


def sealed_case(
    tmp_path: Path, events: Events
) -> tuple[Session, dict[str, Any], str, list[dict[str, Any]]]:
    """The brief's three steps, up to the point where the extraction's run has ended.

    Returns the session, the extraction's ``operation.info``, the tag's id and the members the
    tag held *before* the extraction ran (the cap member the run reads)."""
    source = fx.write_brep(fx.pipe_tee(TEE_RADIUS, TEE_LENGTH), tmp_path / "tee.brep")
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    session.events.subscribe("*", lambda event, data: events.append((event, data)))
    pipe = session.execute("geometry.import", path=str(source), wait=True)["id"]
    capped = create_and_wait(session, "cap", [pipe])
    assert capped["status"] == "ok", capped["error"]
    project = session.require_project()
    output = capped["outputs"][0]
    tag = str(project.get(capped["id"]).meta["created_tags"][0])
    before = session.execute("tag.members", tag=tag)["members"]
    assert len(before) == 1 and before[0]["part"] == output and len(before[0]["faces"]) == 3
    fluid = create_and_wait(session, "extract_fluid", [output], {"caps": tag, "seed": [0.0] * 3})
    return session, fluid, tag, before


def done_runs(events: Events, op: str) -> int:
    return sum(1 for event, data in events if event == "geometry.op_done" and data["node_id"] == op)


def test_an_operation_that_reads_its_own_tag_members_stays_ok(tmp_path: Path) -> None:
    """The extraction is ``ok``, runs once, keeps the cap member, and is not re-run for its own."""
    events: Events = []
    session, fluid, tag, before = sealed_case(tmp_path, events)
    project = session.require_project()
    service = OperationStore.of(session)
    op = project.get(fluid["id"])
    assert fluid["status"] == "ok", fluid["error"]
    assert fluid["run"]["ok"] is True
    assert op.status == "ok" and op.meta["run"]["signature"] == service.signature(project, op)
    assert done_runs(events, op.id) == 1
    # The tag kept the member the run read, face for face, and gained one for the run's output.
    after = session.execute("tag.members", tag=tag)["members"]
    kept = [m for m in after if m["part"] == before[0]["part"]]
    assert len(kept) == 1 and kept[0]["faces"] == before[0]["faces"]
    assert kept[0]["stale"] is False
    assert any(m["part"] == fluid["outputs"][0] for m in after)
    # Nothing is scheduled: no second run, and the run record does not move.
    at = op.meta["run"]["at"]
    time.sleep(DEBOUNCE_SETTLE)
    assert op.status == "ok" and op.meta["run"]["at"] == at
    assert service.state(op.id)["state"] == "idle"
    # A user's next edit that does reach the operation still judges it fresh: no needless re-run.
    session.execute("parameter.set", node=before[0]["part"], name="color", value="#123456")
    time.sleep(DEBOUNCE_SETTLE)
    assert op.status == "ok" and op.meta["run"]["at"] == at
    assert service.state(op.id)["state"] == "idle"
    GeometryStore.of(session).close()


def test_a_manual_edit_of_that_tag_still_makes_the_operation_stale(tmp_path: Path) -> None:
    """The guard against over-fixing: a member the user adds is a change like any other."""
    events: Events = []
    session, fluid, tag, before = sealed_case(tmp_path, events)
    project = session.require_project()
    service = OperationStore.of(session)
    op = project.get(fluid["id"])
    assert fluid["status"] == "ok", fluid["error"]
    signature = op.meta["run"]["signature"]
    # auto_run off, so this test judges the staleness and not a re-run the debounce would start
    # (the re-run path is test_a_parameter_change_re_runs_once_after_the_last_change's).
    session.execute("parameter.set", node=op.id, name="auto_run", value=False)
    assert op.status == "ok"
    added = session.execute("tag.add", tag=tag, members=[{"part": before[0]["part"], "faces": [0]}])
    assert added["changed"] is True
    assert service.signature(project, op) != signature
    assert op.status == "stale"
    assert not service.busy(op.id) and service.state(op.id)["state"] == "idle"
    GeometryStore.of(session).close()


def test_a_re_run_of_that_operation_reads_its_own_tag_member_no_more(tmp_path: Path) -> None:
    """The tag holds the operation's own output when it runs again: its plan must ignore it."""
    events: Events = []
    session, fluid, tag, _before = sealed_case(tmp_path, events)
    project = session.require_project()
    op = project.get(fluid["id"])
    assert fluid["status"] == "ok", fluid["error"]
    after = session.execute("tag.members", tag=tag)["members"]
    assert any(m["part"] == fluid["outputs"][0] for m in after)
    again = session.execute("operation.run", op=op.id, wait=True)
    assert again["status"] == "ok", again.get("error")
    assert again["run"]["ok"] is True and again["stale"] is False
    assert op.status == "ok" and done_runs(events, op.id) == 2
    GeometryStore.of(session).close()


def test_an_operation_whose_input_was_dropped_is_stale(tmp_path: Path) -> None:
    """A re-run can produce fewer outputs than the last one; the parts the old run made are then
    dropped from the project. An operation that read one of them must not stay ``ok``: its input
    is gone, so re-judging it must find it stale."""
    events: Events = []
    session, fluid, _tag, _before = sealed_case(tmp_path, events)
    project = session.require_project()
    service = OperationStore.of(session)
    downstream = create_and_wait(session, "orient", [fluid["outputs"][0]])
    assert downstream["status"] == "ok", downstream.get("error")
    reader = project.get(downstream["id"])
    dropped = fluid["outputs"][0]

    # The old run's output goes away, as `_drop_outputs` leaves it when a re-run makes fewer.
    session.execute("node.remove", node=dropped)
    assert project.find(dropped) is None
    # Judged again with no event in play (the re-run path calls `_reconsider` directly): the
    # operation's input is gone, so it cannot still be `ok`.
    service._reconsider([reader.id])
    assert reader.status == "stale", (
        f"the operation still reads {reader.status!r} although part {dropped} is gone"
    )
    GeometryStore.of(session).close()


def test_a_downstream_run_does_not_make_the_operation_stale(tmp_path: Path) -> None:
    """An operation chained after it writes the same tag: that member is not the extraction's.

    ``cap`` → ``extract_fluid`` → ``orient`` is the normal workflow, and every run in the chain
    carries the tag: the orient's output lands in it too. The extraction reads neither, so it
    stays ``ok``, a later edit starts no re-run, and an explicit run still succeeds.
    """
    events: Events = []
    session, fluid, tag, before = sealed_case(tmp_path, events)
    project = session.require_project()
    service = OperationStore.of(session)
    op = project.get(fluid["id"])
    assert fluid["status"] == "ok", fluid["error"]
    signature = op.meta["run"]["signature"]
    # The chained operation: it reads the fluid and carries the same tag, so the tag gains a
    # member on the orient's own output as well.
    down = create_and_wait(session, "orient", [fluid["outputs"][0]])
    assert down["status"] == "ok", down.get("error")
    after = session.execute("tag.members", tag=tag)["members"]
    assert any(m["part"] == down["outputs"][0] for m in after)
    time.sleep(DEBOUNCE_SETTLE)
    # The downstream member is not one of the extraction's parameters: still ok, still one run.
    assert op.status == "ok"
    assert service.signature(project, op) == signature
    assert done_runs(events, op.id) == 1
    assert service.state(op.id)["state"] == "idle"
    # An edit that does reach it re-judges it, and still finds it fresh: no re-run starts.
    session.execute("parameter.set", node=before[0]["part"], name="color", value="#123456")
    time.sleep(DEBOUNCE_SETTLE)
    assert op.status == "ok" and service.state(op.id)["state"] == "idle"
    assert done_runs(events, op.id) == 1
    # And the explicit run the Run button issues succeeds rather than refusing the tag members.
    again = session.execute("operation.run", op=op.id, wait=True)
    assert again["status"] == "ok", again.get("error")
    assert again["run"]["ok"] is True and again["stale"] is False
    assert done_runs(events, op.id) == 2
    failed = [str(d.get("error")) for e, d in events if e == "geometry.op_failed"]
    assert not any("not an input of this run" in text for text in failed), failed
    GeometryStore.of(session).close()
