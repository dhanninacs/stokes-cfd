"""Session extension hooks (``stokes.api.session.SessionExtension``): when project.new, .open, .save
and .close call them, what a failing extension does, and the two subsystems that follow the project
through them: the study store (Save as carries the design tables whether or not a study command ran)
and the geometry store (cache loading on open, cache writing on save, the read progress), tested
with a stand-in for the worker so that none of it needs OCP."""

from __future__ import annotations

import inspect
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, get_args

import pytest

from stokes.api import Session
from stokes.api.commands.workspace import PROJECT_FILE
from stokes.api.session import Hook, SessionExtension
from stokes.explore.study_store import StudyStore
from stokes.explore.study_table import (
    CSV_FILE,
    POINTS_FILE,
    TABLE_FILE,
    PointRow,
    StudyTable,
    TableMeta,
    read_table,
    table_dir,
    write_table,
)
from stokes.geometry import files, worker
from stokes.geometry.cache import cache_file, write_cache
from stokes.geometry.errors import GeometryWorkerError
from stokes.geometry.mesh import PartMesh
from stokes.geometry.sample import tube
from stokes.geometry.store import GeometryStore
from stokes.geometry.transport import pack_part
from stokes.geometry.worker import ReadInfo, SolidInfo
from stokes.model import Project
from stokes.workspace import WorkspaceStore
from stokes.workspace.merge import project_file
from tests.test_study_support import SWEEP, define_pipe, make_sweep

Call = tuple[str, dict[str, Any]]


class Recorder:
    """An extension that implements every hook and keeps what it was called with."""

    def __init__(self) -> None:
        self.calls: list[Call] = []

    def project_opened(self, project: Project, path: Path | None) -> None:
        self.calls.append(("project_opened", {"project": project, "path": path}))

    def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
        self.calls.append(
            ("project_saved", {"project": project, "path": path, "previous": previous})
        )

    def project_closed(self) -> None:
        self.calls.append(("project_closed", {}))

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class Witness:
    """An extension that notes how the session looked, and which events it had sent, when a hook
    was called: (hook, whether the session already held what the hook reports, its folder, the
    events heard so far)."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.heard: list[str] = []
        self.seen: list[tuple[str, bool, Path | None, list[str]]] = []
        session.events.subscribe("*", lambda event, data: self.heard.append(event))

    def _note(self, hook: str, in_step: bool) -> None:
        self.seen.append((hook, in_step, self.session.project_path, list(self.heard)))

    def project_opened(self, project: Project, path: Path | None) -> None:
        self._note("project_opened", self.session.project is project)

    def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
        self._note("project_saved", self.session.project is project)

    def project_closed(self) -> None:
        self._note("project_closed", self.session.project is None)


@pytest.fixture
def logged() -> Iterator[list[logging.LogRecord]]:
    """The error records of ``stokes.api.session`` (the ``stokes`` logger does not propagate)."""
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Collect(level=logging.ERROR)
    logger = logging.getLogger("stokes.api.session")
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


def wait_for(seen: list[Any], count: int, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while len(seen) < count and time.monotonic() < deadline:
        time.sleep(0.02)


def join_threads(name: str, seconds: float = 30.0) -> None:
    for thread in threading.enumerate():
        if thread.name == name:
            thread.join(seconds)


# ---------------------------------------------------------------- the mechanism


def test_the_project_commands_call_the_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Rewritten for the new contract** (claude-win2, reading 1). Two things here changed:
    `project.new` reports a **folder** rather than `None`, and its first `project_saved` therefore
    has a `previous` **that is already a folder** rather than nothing.

    **The coverage is kept in full**: every command still reports the payload its hooks consume, and
    `previous` still distinguishes a first save from a Save as - which is the reason this test e
    because a hook that never ran is what the `notify_extensions` signature defect hid.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    spy = Recorder()
    session.extensions["spy"] = spy
    session.execute("project.new", name="a")
    first = session.require_project()
    # **A folder from the start**: `project.new` reports the one it created.
    created = session.project_path
    assert created is not None
    assert spy.calls == [("project_opened", {"project": first, "path": created})]
    session.execute("project.save", path=str(tmp_path / "a"))
    folder_a = session.project_path
    assert folder_a is not None and folder_a.name == "a.stokes"
    # **`previous` is the folder it came FROM**, which for a project created with a default
    # folder is a real one rather than `None` - the same relationship, one step later.
    saved = {"project": first, "path": folder_a, "previous": created}
    assert spy.calls[-1] == ("project_saved", saved)
    session.execute("project.save")  # a plain save: previous and path agree
    assert spy.calls[-1] == ("project_saved", {**saved, "previous": folder_a})
    session.execute("project.save", path=str(tmp_path / "b"))  # Save as
    folder_b = session.project_path
    assert folder_b is not None and folder_b != folder_a
    assert spy.calls[-1] == ("project_saved", {**saved, "path": folder_b, "previous": folder_a})
    session.execute("project.close")
    assert spy.calls[-1] == ("project_closed", {})
    session.execute("project.open", path=str(folder_a))
    second = session.require_project()
    assert spy.calls[-1] == ("project_opened", {"project": second, "path": folder_a})
    session.execute("project.new", name="c", path=str(tmp_path / "c"))  # replaces an open project
    third = session.require_project()
    assert spy.calls[-1] == ("project_opened", {"project": third, "path": session.project_path})
    assert spy.names == [
        "project_opened",
        "project_saved",
        "project_saved",
        "project_saved",
        "project_closed",
        "project_opened",
        "project_opened",  # the open project is replaced, not closed
    ]


def test_a_hook_runs_after_the_state_changed_and_before_the_event(tmp_path: Path) -> None:
    session = Session()
    witness = Witness(session)
    session.extensions["witness"] = witness
    session.execute("project.new", name="a", path=str(tmp_path / "a"))
    folder = session.project_path
    session.execute("project.save", path=str(tmp_path / "b"))
    saved_to = session.project_path
    session.execute("project.close")
    assert witness.seen == [
        ("project_opened", True, folder, []),
        ("project_saved", True, saved_to, ["project.opened"]),
        ("project_closed", True, None, ["project.opened", "project.saved"]),
    ]
    assert witness.heard == ["project.opened", "project.saved", "project.closed"]


def test_an_extension_implements_any_subset_and_other_values_are_skipped() -> None:
    class OnlyClosed:
        closed = 0

        def project_closed(self) -> None:
            self.closed += 1

    class NotCallable:
        project_opened = "not a method"

    only = OnlyClosed()
    session = Session()
    session.extensions.update(
        {"only": only, "text": NotCallable(), "plain": {"a": 1}, "number": 3, "nothing": None}
    )
    session.execute("project.new", name="a")
    assert only.closed == 0
    session.execute("project.close")
    assert only.closed == 1


def test_a_failing_extension_is_logged_and_never_breaks_a_command(
    tmp_path: Path, logged: list[logging.LogRecord]
) -> None:
    class Broken:
        def project_opened(self, project: Project, path: Path | None) -> None:
            raise RuntimeError("opened")

        def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
            raise RuntimeError("saved")

        def project_closed(self) -> None:
            raise RuntimeError("closed")

    session = Session()
    spy = Recorder()
    heard: list[str] = []
    session.events.subscribe("*", lambda event, data: heard.append(event))
    session.extensions["broken"] = Broken()
    session.extensions["after"] = spy  # later in the dict: it still hears of every change
    session.execute("project.new", name="a", path=str(tmp_path / "a"))
    session.execute("project.save")
    session.execute("project.close")
    session.execute("project.open", path=str(tmp_path / "a"))
    assert heard == ["project.opened", "project.saved", "project.closed", "project.opened"]
    assert spy.names == ["project_opened", "project_saved", "project_closed", "project_opened"]
    assert [vars(r)["stokes_fields"] for r in logged] == [
        {"extension": "broken", "hook": hook}
        for hook in ("project_opened", "project_saved", "project_closed", "project_opened")
    ]
    assert all(r.exc_info and r.getMessage() == "session extension failed" for r in logged)
    assert session.execute("project.info")["name"] == "a"


def test_an_extension_whose_hook_cannot_be_looked_up_is_logged_too(
    logged: list[logging.LogRecord],
) -> None:
    class Hostile:
        @property
        def project_closed(self) -> Callable[[], None]:
            raise RuntimeError("the lookup fails")

    session = Session()
    spy = Recorder()
    session.extensions.update({"hostile": Hostile(), "spy": spy})
    session.execute("project.new", name="a")
    session.execute("project.close")
    assert spy.names == ["project_opened", "project_closed"]
    assert [vars(r)["stokes_fields"] for r in logged] == [
        {"extension": "hostile", "hook": "project_closed"}
    ]


def test_a_hook_may_add_an_extension() -> None:
    late = Recorder()

    class Adder:
        def __init__(self, session: Session) -> None:
            self.session = session

        def project_opened(self, project: Project, path: Path | None) -> None:
            self.session.extensions["late"] = late

    session = Session()
    session.extensions["adder"] = Adder(session)
    session.execute("project.new", name="a")  # iterates a snapshot: no error, no call to `late`
    assert late.calls == []
    session.execute("project.close")
    assert late.names == ["project_closed"]


def test_emit_tells_the_extensions_after_the_listeners(logged: list[logging.LogRecord]) -> None:
    """``on_event`` (ADR-0021 decision 8.8): every event, after the bus, with the data the
    listeners got; one that raises is logged and the others still hear it."""
    session = Session()
    heard: list[str] = []
    session.events.subscribe("*", lambda event, data: heard.append(f"bus {event}"))

    class Listener:
        def on_event(self, name: str, data: dict[str, Any]) -> None:
            heard.append(f"extension {name} {data['x']}")

    class Broken:
        def on_event(self, name: str, data: dict[str, Any]) -> None:
            raise RuntimeError("the hook fails")

    session.extensions.update({"broken": Broken(), "listener": Listener(), "spy": Recorder()})
    session.emit("demo.happened", x=1)
    assert heard == ["bus demo.happened", "extension demo.happened 1"]
    assert [vars(r)["stokes_fields"] for r in logged] == [
        {"extension": "broken", "hook": "on_event"}
    ]
    assert inspect.signature(SessionExtension.on_event).parameters.keys() == {
        "self",
        "name",
        "data",
    }


def test_replay_settles_after_every_record() -> None:
    """``settle``: journal replay waits for the background work of each record before the next
    (ADR-0022 decision 10.1 relies on it for operations)."""
    from stokes.api import replay

    session = Session()
    session.execute("project.new", name="a")
    order: list[str] = []

    class Settler:
        def settle(self, timeout: float | None) -> None:
            order.append(f"settle {timeout}")

    session.extensions["settler"] = Settler()
    session.events.subscribe("node.added", lambda event, data: order.append(data["path"]))
    records = [
        {"command": "node.add", "params": {"parent": "/geometry", "type": "group", "name": n}}
        for n in ("one", "two")
    ]
    replay(session, records)
    assert order == ["/geometry/one", "settle None", "/geometry/two", "settle None"]


@pytest.mark.parametrize(
    "store",
    [StudyStore, GeometryStore, WorkspaceStore],
    ids=["study", "geometry", "workspace"],
)
def test_the_stores_implement_every_hook_with_the_protocols_signature(store: type) -> None:
    """Hooks are found by name and a bad call is logged, not raised: a store that renamed one or
    changed its arguments would go silently deaf, so this is where that shows."""
    for hook in get_args(Hook):
        wanted = list(inspect.signature(getattr(SessionExtension, hook)).parameters)
        assert list(inspect.signature(getattr(store, hook)).parameters) == wanted, hook


# ---------------------------------------------------------------- the study store


def evaluated_sweep(session: Session) -> str:
    """The pipe sweep, generated and evaluated, with the project saved; returns the study id."""
    define_pipe(session)
    make_sweep(session)
    study = str(session.execute("study.generate", study=SWEEP)["study"])
    session.execute("study.update", study=SWEEP)
    session.execute("project.save")
    return study


def test_save_as_carries_tables_when_no_study_command_has_run(
    session: Session, tmp_path: Path
) -> None:
    study = evaluated_sweep(session)
    assert session.project_path is not None
    original = table_dir(session.project_path, study)
    reopened = Session()
    reopened.execute("project.open", path=str(session.project_path))
    reopened.execute("project.save", path=str(tmp_path / "copy"))  # and no study.* before it
    copied = table_dir(tmp_path / "copy.stokes", study)
    for name in (POINTS_FILE, CSV_FILE, TABLE_FILE):
        assert (copied / name).read_bytes() == (original / name).read_bytes()
    assert reopened.execute("study.results", study=SWEEP)["counts"]["done"] == 3  # from the copy
    assert reopened.extensions["study"].folder == reopened.project_path
    third = Session()
    third.execute("project.open", path=str(tmp_path / "copy"))
    assert third.execute("study.results", study=SWEEP)["counts"]["done"] == 3


def test_SAVE_A_COPY_does_not_point_the_study_store_at_the_copy(
    session: Session, tmp_path: Path
) -> None:
    """**Opus F3: a snapshot must not become the session's home.**

    Save a copy writes a second folder while the session STAYS in the original. It sent
    `project_saved(path=copy)`, and this store takes that folder as its own - so a table written
    afterwards from the ORIGINAL session was written into the copy, and the user's real project
    silently stopped recording its studies.

    The fix splits the hook: `project_copied` tells the file-contributing stores to write into the
    snapshot, and the folder-owning stores ignore it.
    """
    study = evaluated_sweep(session)
    assert session.project_path is not None
    original_path = session.project_path
    copy_path = tmp_path / "snapshot.stokes"

    session.execute("project.save_copy", path=str(copy_path))

    # **The session did not move**, and neither did the store.
    assert session.project_path == original_path, "Save a copy moved the session"
    assert session.extensions["study"].folder == original_path, (
        "the study store followed the copy, so later tables would be written into the snapshot"
    )

    # **A new table lands in the ORIGINAL.** This is the assertion the old code failed.
    store = session.extensions["study"]
    store.replace(study, TableMeta(), [PointRow(0, {"x": 7.5})])
    store.project_saved(session.require_project(), original_path, original_path)
    written = read_table(table_dir(original_path, study))[0]
    assert written.rows and written.rows[0].inputs == {"x": 7.5}, (
        "the new table did not reach the original project"
    )


def test_a_project_without_studies_pays_nothing_for_the_study_store(
    session: Session, tmp_path: Path
) -> None:
    session.execute("project.save", path=str(tmp_path / "copy"))
    session.execute("project.close")
    assert "study" not in session.extensions


def test_the_store_carries_tables_from_the_folder_the_session_names(tmp_path: Path) -> None:
    old, new = tmp_path / "old.stokes", tmp_path / "new.stokes"
    project = Project.new("demo")
    study = project.add("/studies", "study", "s")
    rows = [PointRow(0, {"x": 1.0}), PointRow(1, {"x": 2.0})]
    write_table(table_dir(old, study.id), StudyTable(TableMeta(), rows))
    store = StudyStore(new)  # made during the save, when the session already counts `new`
    store.project_saved(project, new, old)
    assert store.folder == new
    assert read_table(table_dir(new, study.id))[0].rows == rows
    again = StudyStore(None)  # a project that had no folder: tables in memory are written
    again.replace(study.id, TableMeta(), [PointRow(0, {"x": 3.0})])
    again.project_saved(project, tmp_path / "third.stokes", None)
    assert read_table(table_dir(tmp_path / "third.stokes", study.id))[0].rows[0].inputs == {
        "x": 3.0
    }


def test_the_store_drops_its_tables_when_the_project_changes(tmp_path: Path) -> None:
    project = Project.new("demo")
    study = project.add("/studies", "study", "s")
    store = StudyStore(tmp_path)
    store.replace(study.id, TableMeta(), [PointRow(0, {"x": 1.0})])
    assert store.is_loaded(study.id)
    store.project_closed()
    assert not store.is_loaded(study.id) and store.folder is None
    store.replace(study.id, TableMeta(), [PointRow(0, {"x": 1.0})])
    store.project_opened(project, tmp_path / "other.stokes")
    assert not store.is_loaded(study.id) and store.folder == tmp_path / "other.stokes"


# ---------------------------------------------------------------- the workspace service


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private Stokes home: no test here reads or writes the real user workspace."""
    path = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(path))
    return path


def workspace_changes(session: Session) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    session.events.subscribe("workspace.changed", lambda event, data: seen.append(data))
    return seen


def project_with_overrides(root: Path, name: str = "a") -> Path:
    """A saved project whose workspace overrides set the left panel to 360; returns its folder."""
    session = Session()
    session.execute("project.new", name="demo", path=str(root / name))
    session.execute("workspace.set", path="layout.left.size", value=360, scope="project")
    assert session.project_path is not None
    return session.project_path


def test_save_as_carries_workspace_overrides_when_no_workspace_command_has_run(
    tmp_path: Path, home: Path
) -> None:
    folder = project_with_overrides(tmp_path)
    reopened = Session()
    reopened.execute("project.open", path=str(folder))
    changes = workspace_changes(reopened)
    reopened.execute("project.save", path=str(tmp_path / "b"))  # and no workspace.* before it
    copied = tmp_path / "b.stokes" / PROJECT_FILE
    assert json.loads(copied.read_text(encoding="utf-8")) == json.loads(
        (folder / PROJECT_FILE).read_text(encoding="utf-8")
    )
    assert changes == [{"scope": "project", "path": None}]  # the project layer moved folders
    got = reopened.execute("workspace.get", path="layout.left.size")
    assert (got["value"], got["source"]) == (360, "project")


def test_a_project_without_overrides_pays_nothing_for_the_workspace_service(
    session: Session, tmp_path: Path, home: Path
) -> None:
    session.execute("project.save", path=str(tmp_path / "copy"))
    session.execute("project.close")
    assert "workspace" not in session.extensions


def test_the_workspace_service_follows_the_project_through_the_hooks(
    tmp_path: Path, home: Path
) -> None:
    folder = project_with_overrides(tmp_path)
    session = Session()
    session.execute("workspace.get")  # the service exists before any project does
    changes = workspace_changes(session)
    session.execute("project.open", path=str(folder))
    assert changes == [{"scope": "project", "path": None}]  # the project's layer appeared
    assert session.execute("workspace.get", path="layout.left.size")["source"] == "project"
    session.execute("project.close")
    assert changes[1:] == [{"scope": "project", "path": None}]  # and went away
    assert session.execute("workspace.get", path="layout.left.size")["source"] == "default"
    session.execute("project.new", name="other", path=str(tmp_path / "other"))
    assert len(changes) == 2  # a project without overrides announces nothing


def test_the_workspace_service_carries_overrides_from_the_folder_the_session_names(
    tmp_path: Path, home: Path
) -> None:
    old = project_with_overrides(tmp_path)
    new = tmp_path / "new.stokes"
    new.mkdir()
    changes: list[tuple[str, dict[str, Any]]] = []
    store = WorkspaceStore(
        notify=lambda event, data: changes.append((event, data)), project_dir=new
    )
    store.project_saved(object(), new, old)  # made during the save: it already counts `new`
    assert (new / PROJECT_FILE).read_bytes() == (old / PROJECT_FILE).read_bytes()
    assert changes == [("workspace.changed", {"scope": "project", "path": None})]
    store.project_closed()
    assert changes[1:] == [("workspace.changed", {"scope": "project", "path": None})]
    store.project_opened(object(), old)
    assert len(changes) == 3


def test_the_commands_name_the_project_file_the_workspace_package_uses(tmp_path: Path) -> None:
    assert project_file(tmp_path).name == PROJECT_FILE


# ---------------------------------------------------------------- the geometry store


def cached_project(root: Path) -> tuple[Path, str]:
    """A saved project with one part whose mesh cache is on disk; returns its folder and part id."""
    session = Session()
    session.execute("project.new", name="demo", path=str(root / "demo"))
    part = session.require_project().add("/geometry", "part", "pipe")
    session.execute("project.save")
    folder = session.project_path
    assert folder is not None
    mesh = tube("wall", "wall", (0.8, 0.8, 0.8), 0.03, 0.0, 0.3, 8)
    write_cache(folder, part.id, pack_part(PartMesh((mesh,))))
    return folder, part.id


def forbid_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> float:
        raise AssertionError("no worker may be asked here")

    monkeypatch.setattr(worker, "run_import", refuse)


def part_ready(session: Session) -> list[tuple[str, str, bool]]:
    seen: list[tuple[str, str, bool]] = []
    session.events.subscribe(
        "geometry.part_ready", lambda e, d: seen.append((d["node_id"], d["pass"], d["complete"]))
    )
    return seen


def test_opening_a_project_restores_its_parts_and_closing_drops_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, part_id = cached_project(tmp_path)
    forbid_workers(monkeypatch)
    session = Session()
    store = GeometryStore.of(session)
    seen = part_ready(session)
    store.activate()  # no project yet: nothing to restore
    join_threads("stokes-geometry-restore")
    assert seen == []
    session.execute("project.open", path=str(folder))  # the hook restores on a thread
    wait_for(seen, 1)
    assert seen == [(part_id, "cache", True)]
    assert store.status(part_id) == {"status": "ready"} and store.transport(part_id) is not None
    session.execute("project.open", path=str(folder))  # the old project's meshes went first
    wait_for(seen, 2)
    assert seen == [(part_id, "cache", True)] * 2
    join_threads("stokes-geometry-restore")
    session.execute("project.close")
    assert store.status(part_id) == {"status": "missing"} and store.mesh(part_id) is None
    store.close()


def test_an_inactive_store_waits_for_the_server_to_activate_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, part_id = cached_project(tmp_path)
    forbid_workers(monkeypatch)
    session = Session()
    store = GeometryStore.of(session)
    seen = part_ready(session)
    session.execute("project.open", path=str(folder))
    join_threads("stokes-geometry-restore")
    assert seen == [] and store.status(part_id) == {"status": "missing"}
    store.activate()  # the first WebSocket client: restores the project that is already open
    wait_for(seen, 1)
    assert seen == [(part_id, "cache", True)]
    store.close()


def test_save_as_writes_the_mesh_caches_the_new_folder_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, part_id = cached_project(tmp_path)
    forbid_workers(monkeypatch)
    session = Session()
    store = GeometryStore.of(session)
    seen = part_ready(session)
    store.activate()
    session.execute("project.open", path=str(folder))
    wait_for(seen, 1)
    session.execute("project.save", path=str(tmp_path / "copy"))
    join_threads("stokes-geometry-cache")
    assert session.project_path is not None
    copied = cache_file(session.project_path, part_id)
    assert copied.read_bytes() == store.transport(part_id)
    copied.unlink()
    session.execute("project.save")  # known from memory: a save checks no files
    join_threads("stokes-geometry-cache")
    assert not copied.exists()
    store.close()


def test_a_closed_store_ignores_the_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder, part_id = cached_project(tmp_path)
    forbid_workers(monkeypatch)
    session = Session()
    store = GeometryStore.of(session)
    seen = part_ready(session)
    store.activate()
    session.execute("project.open", path=str(folder))
    wait_for(seen, 1)
    store.close()
    session.execute("project.open", path=str(folder))
    session.execute("project.save", path=str(tmp_path / "copy"))
    join_threads("stokes-geometry-restore")
    join_threads("stokes-geometry-cache")
    session.execute("project.close")
    assert seen == [(part_id, "cache", True)]  # nothing restored again, nothing dropped
    assert store.status(part_id) == {"status": "ready"}
    assert not cache_file((tmp_path / "copy.stokes").resolve(), part_id).exists()


def refusing_worker(
    heard: list[dict[str, Any]], entered: list[list[dict[str, Any]]], solids: int = 0
) -> Callable[..., float]:
    """A ``worker.run_import`` that notes what had been announced when it was asked, optionally
    reports the read of a file of ``solids`` solids, and then fails like a worker that died."""

    def run(path: Any, **kwargs: Any) -> float:
        entered.append(list(heard))
        if solids:
            infos = tuple(SolidInfo(i, f"solid_{i}", "solid", None, 6) for i in range(solids))
            kwargs["on_read"](ReadInfo(infos, 6 * solids, "mm", 0.0))
        raise GeometryWorkerError("no worker in this test")

    return run


READ = {"pass": "read", "solid": None, "index": 0, "triangles": 0}


def test_an_import_announces_its_read_before_the_worker_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "two.step"
    source.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    monkeypatch.setattr(files, "cad_available", lambda: True)
    heard: list[dict[str, Any]] = []
    entered: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(worker, "run_import", refusing_worker(heard, entered, solids=2))
    session = Session()
    session.execute("project.new", name="demo")
    session.events.subscribe("geometry.import_progress", lambda e, d: heard.append(d))
    top = session.execute("geometry.import_step", path=str(source), name="asm")
    store = GeometryStore.of(session)
    assert store.wait(top["id"], timeout=30)
    announced = {"node_id": top["id"], "path": "/geometry/parts/asm", "count": 0, **READ}
    assert entered == [[announced]]  # said before the worker was asked to read anything
    assert heard == [announced, {**announced, "count": 2}]  # then once more, when the file is read
    assert session.require_project().get(top["id"]).type == "group"  # the two solids became parts
    store.close()


def test_a_reopened_project_announces_the_read_of_each_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = {name: tmp_path / f"{name}.step" for name in ("asm", "lid")}
    for source in sources.values():
        source.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    session = Session()
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    project = session.require_project()
    group = project.add("/geometry", "group", "asm")
    for index, name in enumerate(("bracket", "pin")):
        project.add(group.id, "part", name, {"source": str(sources["asm"]), "solid": index})
    alone = project.add("/geometry", "part", "lid", {"source": str(sources["lid"])})
    session.execute("project.save")
    heard: list[dict[str, Any]] = []
    entered: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(worker, "run_import", refusing_worker(heard, entered))
    reopened = Session()
    reopened.events.subscribe("geometry.import_progress", lambda e, d: heard.append(d))
    reopened.execute("project.open", path=str(tmp_path / "demo"))
    store = GeometryStore.of(reopened)
    store.activate()
    join_threads("stokes-geometry-restore")
    assert store.wait(timeout=30)
    # One job for the two parts of the group (named by the group), one for the lid on its own.
    by_node = {d["node_id"]: d for d in heard}
    assert set(by_node) == {group.id, alone.id} and len(heard) == 2 == len(entered)
    assert by_node[group.id] == {"node_id": group.id, "path": "/geometry/asm", "count": 0, **READ}
    assert by_node[alone.id] == {"node_id": alone.id, "path": "/geometry/lid", "count": 0, **READ}
    assert all(seen for seen in entered)  # each was announced before its worker was asked
    store.close()
