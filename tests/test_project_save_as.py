"""Save as and Save a copy: the whole folder travels, and a copy is a different intention.

**claude-win2's 1805 message, decisions 3 and 4.** Two commands because they are two intentions:

* `project.save_as` copies the project to a new folder and **switches** to it;
* `project.save_copy` copies it and **stays** in the original - a snapshot.

**Why this file exists at all**: `project.save path=` used to carry only what each extension
remembered to carry. Measured before the change:

    MISSING from the copy: ['cases', 'imports', 'logs', 'preview.png']

Four subsystems each had to remember, and none of them owned the question.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import StokesError
from stokes.model import project_copy
from stokes.model.ids import new_id


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary `STOKES_HOME`, so no test can touch the developer's real one."""
    folder = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(folder))
    return folder


def _project(home: Path, name: str = "duct") -> Session:
    """A project with one file in every folder decision 3 names."""
    session = Session()
    folder = home / f"{name}.stokes"
    session.execute("project.new", name=name, path=str(folder))
    for sub in ("imports", "cases", "logs", "cache"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
        (folder / sub / "a-file.txt").write_text(f"{sub} content")
    (folder / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return session


# ------------------------------------------------------------------ the whole folder travels


@pytest.mark.parametrize("missing", ["imports", "cases", "logs", "preview.png"])
def test_SAVE_AS_carries_the_folders_that_used_to_be_left_behind(home: Path, missing: str) -> None:
    """**The measured defect, one assertion per folder.**

    `project.save path=` carried `cache/` and `runs/` because two extensions happened to carry them,
    and left `imports/`, `cases/`, `logs/` and `preview.png` behind. Asserted per name rather than
    as a count, so a regression that drops exactly one of them fails with the name in the message.
    """
    session = _project(home)
    source = Path(session.project_path)

    session.execute("project.save_as", name="copy", path=str(home / "copy.stokes"))
    copy = Path(session.project_path)

    assert copy != source
    present = sorted(p.name for p in copy.iterdir())
    assert (copy / missing).exists(), f"{missing} did not travel: {present}"


def test_SAVE_AS_switches_and_leaves_the_ORIGINAL_untouched(home: Path) -> None:
    """**Save as is a switch**: the session ends up in the copy, and the original is **read, never
    written** - so what the user had is still there afterwards, byte for byte."""
    session = _project(home)
    source = Path(session.project_path)
    before = _signature(source)

    session.execute("project.save_as", name="copy", path=str(home / "copy.stokes"))

    assert Path(session.project_path) == home / "copy.stokes"
    assert _signature(source) == before, "Save as modified the project it copied from"
    assert (home / "copy.stokes" / "project.json").is_file()


def test_SAVE_COPY_stays_and_leaves_the_original_untouched(home: Path) -> None:
    """**Save a copy is not a switch.** The user keeps working where they were; the copy is a
    snapshot beside it. That is the whole difference between the two commands."""
    session = _project(home)
    source = Path(session.project_path)
    before = _signature(source)

    session.execute("project.save_copy", name="snapshot", path=str(home / "snapshot.stokes"))

    assert Path(session.project_path) == source, "Save a copy moved the session"
    assert _signature(source) == before
    assert (home / "snapshot.stokes" / "project.json").is_file()


def test_the_copy_is_INDEPENDENT_of_the_original(home: Path) -> None:
    """A later edit in one must not appear in the other: they are folders, not links."""
    session = _project(home)
    source = Path(session.project_path)
    session.execute("project.save_copy", name="snap", path=str(home / "snap.stokes"))

    (source / "imports" / "mine.txt").write_text("my work")

    assert (source / "imports" / "mine.txt").is_file()
    assert not (home / "snap.stokes" / "imports" / "mine.txt").exists()


# ------------------------------------------------------------------ the refusals


def test_a_destination_INSIDE_the_source_is_refused(home: Path) -> None:
    """A copy into its own subfolder would read what it is writing, and the second generation of
    that is a folder that contains itself. Refused rather than special-cased.

    **Its own code**, because "you chose a folder inside the project" is actionable in a way a
    generic copy failure is not.
    """
    session = _project(home)
    source = Path(session.project_path)
    with pytest.raises(StokesError) as caught:
        session.execute("project.save_as", name="inner", path=str(source / "inner"))
    assert caught.value.code == "project.copy_inside_source", caught.value.code
    assert not (source / "inner").exists(), "the refusal created something anyway"


def test_a_NON_EMPTY_destination_is_refused_and_left_alone(home: Path) -> None:
    """**The property that matters more than the refusal.** The file that was there is still there,
    with its contents, and no `.partial` was left beside it."""
    session = _project(home)
    busy = home / "busy.stokes"
    busy.mkdir(parents=True)
    (busy / "theirs.txt").write_text("someone else's work")

    with pytest.raises(StokesError):
        session.execute("project.save_as", name="x", path=str(busy))

    assert (busy / "theirs.txt").read_text() == "someone else's work"
    assert not busy.with_name(busy.name + project_copy.PARTIAL_SUFFIX).exists()


def test_a_destination_that_is_a_FILE_is_refused(home: Path) -> None:
    """A file where the folder goes must not be overwritten."""
    session = _project(home)
    # **The `.stokes` suffix matters**: `project_dir` appends it, so a path without it names a
    # DIFFERENT folder that does not exist - and no refusal would be exercised at all.
    plain = home / "not-a-folder.stokes"
    plain.write_text("keep me")
    with pytest.raises(StokesError):
        session.execute("project.save_as", name="x", path=str(plain))
    assert plain.read_text() == "keep me"


# ------------------------------------------------------------------ atomicity


def test_a_FAILED_copy_leaves_neither_the_destination_nor_a_partial(home: Path) -> None:
    """**A half-copy is worse than none**: a project folder that opens but is missing its imports
    looks like the user's mistake, where an absent folder is obviously a failed copy.

    Driven at `copy_project`, because injecting a mid-copy failure needs the fault to happen after
    the destination has been created - which no command can be asked to do.
    """
    source = home / "src.stokes"
    source.mkdir(parents=True)
    (source / "one.txt").write_text("x")
    (source / "sub").mkdir()
    (source / "sub" / "two.txt").write_text("y")
    destination = home / "dest.stokes"

    # **`copy2`, not `copytree`**: the copy walks the tree itself now, so it can honour a skip at
    # any depth and refuse to follow a link (Opus F4, F5). Patching the old seam left the fault
    # un-injected and the test passed while asserting nothing.
    real_copy2 = project_copy.shutil.copy2

    def exploding(*args: object, **kwargs: object) -> None:
        raise OSError("the disk is full")

    project_copy.shutil.copy2 = exploding  # type: ignore[assignment]
    try:
        with pytest.raises(project_copy.CopyError):
            project_copy.copy_project(source, destination)
    finally:
        project_copy.shutil.copy2 = real_copy2  # type: ignore[assignment]

    assert not destination.exists(), "a failed copy left the destination"
    assert not destination.with_name(destination.name + project_copy.PARTIAL_SUFFIX).exists(), (
        "a failed copy left a partial behind"
    )


def test_a_copy_NEVER_creates_the_destination_before_the_rename(home: Path) -> None:
    """**F1, and it fails on any OS (Opus review of step 2).**

    The copy is committed by renaming `<dest>.partial` onto `<dest>`. The command validated the
    destination with `prepare()`, which **creates** it - and POSIX renames onto an existing empty
    directory happily, so Linux was green while Windows failed every Save as with `WinError 183`
    and left an empty destination folder behind.

    **Asserted on the state the rename sees**, not on the platform's rename semantics, so the test
    cannot pass by running somewhere permissive: the fault is injected right after the destination
    check, and the destination must not exist at that moment.
    """
    session = _project(home)
    destination = home / "copy.stokes"

    seen: dict[str, bool] = {}
    real_copy2 = project_copy.shutil.copy2

    def observe_then_fail(*args: object, **kwargs: object) -> None:
        # The first real work of the copy, i.e. after the command's destination check.
        seen["destination_existed"] = destination.exists()
        raise OSError("the disk is full")

    project_copy.shutil.copy2 = observe_then_fail  # type: ignore[assignment]
    try:
        with pytest.raises(StokesError):
            session.execute("project.save_as", path=str(destination))
    finally:
        project_copy.shutil.copy2 = real_copy2  # type: ignore[assignment]

    assert seen.get("destination_existed") is False, (
        "the destination already existed when the copy began; on Windows the commit rename onto "
        "it fails with WinError 183"
    )
    assert not destination.exists(), "a failed copy left an empty destination folder"
    assert not destination.with_name(destination.name + project_copy.PARTIAL_SUFFIX).exists(), (
        "a failed copy left a partial behind"
    )


def test_a_copy_SKIPS_a_link_and_says_so(home: Path) -> None:
    """**Opus F5, and it is a containment bug, not a tidiness one.**

    `copytree(symlinks=True)` copies a *symlink* but still WALKS INTO an NTFS junction, because
    `is_dir()` follows it - Opus's probe pulled an outside folder into a copy that way. A project
    is meant to be self-contained and a copy is meant to be movable, so a link is skipped with a
    warning: following it imports a folder the user did not choose, and copying it produces a copy
    that breaks on another machine.
    """
    session = _project(home)
    source = Path(session.project_path)

    # A file OUTSIDE the project, and a link to it from inside.
    outside = home / "outside-secret.txt"
    outside.write_text("not part of this project")
    link = source / "imports" / "linked.txt"
    link.symlink_to(outside)
    # A linked DIRECTORY too: the walk must not descend into it.
    outside_dir = home / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "inner.txt").write_text("also not part of the project")
    dir_link = source / "cases" / "linked-dir"
    dir_link.symlink_to(outside_dir, target_is_directory=True)

    reply = session.execute("project.save_copy", name="snap", path=str(home / "snap.stokes"))
    copied = home / "snap.stokes"

    assert not (copied / "imports" / "linked.txt").exists(), (
        "a link to a file outside the project was copied"
    )
    assert not (copied / "cases" / "linked-dir").exists(), "a linked folder was copied"
    assert not (copied / "cases" / "linked-dir" / "inner.txt").exists(), (
        "the walk descended into a linked folder and pulled an outside file in"
    )
    # **Said out loud**, so the user knows the copy is not identical.
    warnings = " ".join(reply.get("warnings") or [])
    assert "linked.txt" in warnings, warnings
    assert "linked-dir" in warnings, warnings


def test_a_SUCCESSFUL_copy_leaves_no_partial(home: Path) -> None:
    """**The false-refusal half**: the partial is the mechanism, not the result."""
    source = home / "src.stokes"
    source.mkdir(parents=True)
    (source / "one.txt").write_text("x")
    destination = home / "dest.stokes"

    warnings = project_copy.copy_project(source, destination)
    assert warnings == []
    assert (destination / "one.txt").read_text() == "x"
    assert not destination.with_name(destination.name + project_copy.PARTIAL_SUFFIX).exists()


# ------------------------------------------------------------------ a run in flight


def _pretend_a_run_is_running(session: Session) -> str:
    """Give the run store a record in a non-terminal state, and return its id.

    **Written into the store's own record shape** rather than by starting a real solve: the question
    here is what the copy commands do when `running()` reports something, not how a solve works.
    """
    from stokes.run.records import RunRecord
    from stokes.run.store import RunStore

    # **The store's own `add`, not a hand-written file.** A run's record is the store's data
    # structure: `add` validates the id, sets the record's `origin` folder, puts it in `_order` and
    # writes it. Writing `record.json` myself put it somewhere `running()` never looks - and my
    # first version called a method that does not exist at all (`store.run_folder`), measured as
    # `AttributeError`, because I guessed the API instead of reading it.
    store = RunStore.of(session)
    run_id = new_id("r")
    store.add(RunRecord(id=run_id, state="running", job="j_test", host="local"))
    return run_id


def test_SAVE_AS_is_REFUSED_while_a_run_is_in_flight(home: Path) -> None:
    """**Decision 4.** Switching away from a live job would abandon it, which is exactly what the
    `interrupted` marking exists to make visible. So Save as says no, and offers the alternative in
    the message - the text is part of the contract, not decoration."""
    session = _project(home)
    _pretend_a_run_is_running(session)

    with pytest.raises(StokesError) as caught:
        session.execute("project.save_as", name="x", path=str(home / "x.stokes"))

    assert caught.value.code == "project.busy", caught.value.code
    assert "Stop the run first" in str(caught.value), str(caught.value)
    assert "Save a copy" in str(caught.value), str(caught.value)
    assert not (home / "x.stokes").exists(), "the refusal copied anyway"
    assert Path(session.project_path) == home / "duct.stokes", "the refusal moved the session"


def test_SAVE_with_a_PATH_is_REFUSED_too_while_a_run_is_in_flight(home: Path) -> None:
    """**Opus F6: the refusal had a hole, and it was the documented alias.**

    `project.save path=` IS Save as (the same operation under its other name), but it called
    `_copy_to` directly and skipped the `project.busy` check that `project.save_as` performed - so a
    running job could still be abandoned by a client that spelled the command the other way. The
    check now lives in `_copy_to`, which every switching caller reaches.
    """
    session = _project(home)
    _pretend_a_run_is_running(session)

    with pytest.raises(StokesError) as caught:
        session.execute("project.save", path=str(home / "elsewhere.stokes"))

    assert caught.value.code == "project.busy", caught.value.code
    assert "Stop the run first" in str(caught.value), str(caught.value)
    assert not (home / "elsewhere.stokes").exists(), "the refusal copied anyway"
    assert Path(session.project_path) == home / "duct.stokes", "the refusal moved the session"


def test_SAVE_COPY_is_ALLOWED_while_a_run_is_in_flight_and_says_what_it_did(home: Path) -> None:
    """**The other half of decision 4.** A snapshot is allowed, with one exclusion: the in-flight
    run's folder is left out, because a solver is writing it and its contents are not consistent to
    read.

    **The copy is told the truth**: that run is recorded as `interrupted` - the existing semantics
    for "this copy has no live job", never a spinner - and the reply carries a warning naming it, so
    the user is not left to discover it.
    """
    session = _project(home)
    run_id = _pretend_a_run_is_running(session)
    source = Path(session.project_path)

    reply = session.execute("project.save_copy", name="snap", path=str(home / "snap.stokes"))

    # **The session stayed put**, which is what makes this a snapshot.
    assert Path(session.project_path) == source
    # **The warning names the run**, so the user can see why the copy differs.
    warnings = reply.get("warnings") or []
    assert warnings, "a copy taken during a run did not say so"
    assert run_id in " ".join(warnings), warnings
    assert "interrupted" in " ".join(warnings), warnings

    # **And the exclusion ACTUALLY HAPPENED** (Opus F4). The test above asserted only the warning
    # text, so it passed while the run folder was copied mid-write: `in_flight_folders` returned
    # bare names and `copy_project` matched them against TOP-LEVEL entries, so `runs/<id>` could
    # never match. A safeguard that reports success without acting is worse than none.
    copied = home / "snap.stokes"
    assert not (copied / "runs" / run_id).exists(), (
        f"the in-flight run {run_id} was copied while the solver was writing it"
    )
    # The case the solver writes goes with it - it lives inside the run folder.
    assert not (copied / "runs" / run_id / "case").exists()


def test_the_original_keeps_running_while_the_copy_is_taken(home: Path) -> None:
    """**The original is untouched**, which is the promise that makes Save a copy safe to use while
    something is running: nothing about the live run is changed by taking a snapshot."""
    session = _project(home)
    run_id = _pretend_a_run_is_running(session)
    source = Path(session.project_path)
    record = source / "runs" / run_id / "record.json"
    before = record.read_text()

    session.execute("project.save_copy", name="snap", path=str(home / "snap.stokes"))

    assert record.read_text() == before, "the copy changed the original's run record"
    assert json.loads(record.read_text())["state"] == "running"


# ------------------------------------------------------------------ the helper


def _signature(folder: Path) -> dict[str, str]:
    """Every file's contents, relative to `folder`, so "untouched" can be asserted rather than
    assumed. Small folders only, which is what these tests make."""
    return {
        str(path.relative_to(folder)): path.read_text(errors="replace")
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }
