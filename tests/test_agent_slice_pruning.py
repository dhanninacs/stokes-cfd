"""Opus M4: the host kept every slice artifact it ever cut, for ever.

**The defect.** Every distinct plane wrote a new `slice-*.stkfld` on the HOST, and nothing
The server has its own 16-plane cache; the agent had none. Dragging a slice across a model therefore
left hundreds of files on the remote machine - **unbounded disk growth on a host the user does not
own**, driven by mouse movement.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from stokes_agent.server import MAX_HOST_SLICE_ARTIFACTS, _prune_slice_artifacts


def a_job(tmp_path: Path) -> Path:
    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    return tmp_path


def write(folder: Path, name: str, *, age: float = 0.0) -> Path:
    path = folder / "artifacts" / name
    path.write_bytes(b"x")
    if age:
        stamp = time.time() - age
        import os

        os.utime(path, (stamp, stamp))
    return path


def test_the_host_keeps_only_the_newest_slices(tmp_path: Path) -> None:
    """The count is the defect, so the count is the assertion."""
    job = a_job(tmp_path)
    for number in range(20):
        write(job, f"slice-{number}.stkfld", age=20 - number)
    _prune_slice_artifacts(job, "j_" + "a" * 12)
    left = sorted(path.name for path in (job / "artifacts").glob("slice-*"))
    assert len(left) == MAX_HOST_SLICE_ARTIFACTS, left


def test_the_newest_survives_because_a_drag_revisits_it(tmp_path: Path) -> None:
    """**Newest, not first.** A drag revisits its own recent planes constantly, so pruning the wrong
    end would mean re-cutting what was just cut."""
    job = a_job(tmp_path)
    for number in range(20):
        write(job, f"slice-{number}.stkfld", age=20 - number)
    _prune_slice_artifacts(job, "j_" + "a" * 12)
    left = {path.name for path in (job / "artifacts").glob("slice-*")}
    assert "slice-19.stkfld" in left, "the newest was pruned"
    assert "slice-0.stkfld" not in left, "the oldest survived"


def test_a_runs_own_field_artifact_is_never_pruned(tmp_path: Path) -> None:
    """**The one that would break the results view.** `fields.stkfld` is what the server fetches for
    the whole results display - deleting it because a slice request arrived would take out far more
    than a slice."""
    job = a_job(tmp_path)
    field = write(job, "fields.stkfld")
    surfaces = write(job, "surfaces.stkmsh")
    for number in range(20):
        write(job, f"slice-{number}.stkfld", age=20 - number)
    _prune_slice_artifacts(job, "j_" + "a" * 12)
    assert field.is_file(), "a slice request deleted the run's own artifact"
    assert surfaces.is_file(), "a slice request deleted the run's surfaces"


def test_pruning_never_raises(tmp_path: Path) -> None:
    """**A prune must not fail a cut that succeeded.** The artifact is written and recorded by this
    point; the disk it saves is worth less than the answer the user is waiting for."""
    _prune_slice_artifacts(tmp_path / "does-not-exist", "j_" + "a" * 12)
    # A file where the folder should be, which makes `glob` raise on some platforms.
    odd = tmp_path / "odd"
    odd.write_text("not a folder")
    _prune_slice_artifacts(odd, "j_" + "a" * 12)


def test_the_RECORD_goes_with_the_file_it_pruned(tmp_path: Path) -> None:
    """**Opus recheck item 4c.** Deleting the slice files left their entries in `state.json`, so a
    long drag grew the index **without limit** and left it naming artifacts that no longer existed -
    **the disk was bounded and the bookkeeping was not.** An index listing files which are gone
    is worse than no index, because a caller trusts it.

    Asserted as a **set equality** rather than a count: the point is that the two agree, not that
    either is any particular size.
    """
    from stokes_agent.jobs import JobStore
    from stokes_agent.server import MAX_HOST_SLICE_ARTIFACTS, _prune_slice_artifacts

    store = JobStore(tmp_path / "jobs")
    job = "j_" + "a" * 12
    job_dir = store.job_dir(job)
    (job_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    # **The record is seeded directly, because `store.create` needs an OpenFOAM install** -
    # measured: it raises `EngineEnvError: no supported OpenFOAM install on this host`. What this
    # test is about is the artifact INDEX, and writing its initial state is the smallest way to
    # have one. The record lives at `<root>/.records/<job>.json`, NOT in the job folder - that
    # separation is deliberate and is why my first attempt raised `JobNotFound`.
    records = tmp_path / "jobs" / ".records"
    records.mkdir(parents=True, exist_ok=True)
    (records / f"{job}.json").write_text(json.dumps({"state": "done", "artifacts": {}}))
    # **Written through the store**, so the records exist the way a real cut creates them.
    for number in range(20):
        name = f"slice-{number}.stkfld"
        (job_dir / "artifacts" / name).write_bytes(b"x")
        store.record_artifact(job, name, 1, "a" * 64)
        time.sleep(0.01)
    (job_dir / "artifacts" / "fields.stkfld").write_bytes(b"y")
    store.record_artifact(job, "fields.stkfld", 1, "b" * 64)

    before = _records(job_dir)
    assert len(before) == 21, "the records were not written as expected"

    _prune_slice_artifacts(job_dir, job, store)

    after = _records(job_dir)
    files = {path.name for path in (job_dir / "artifacts").iterdir()}
    assert len(files) == MAX_HOST_SLICE_ARTIFACTS + 1, sorted(files)
    assert set(after) == files, (
        f"the index names {sorted(set(after) - files)} which are gone, and misses "
        f"{sorted(files - set(after))}"
    )
    # **The run's own artifact survives in BOTH places** - deleting it would take out far more than
    # a slice request.
    assert "fields.stkfld" in after and "fields.stkfld" in files
    assert len(after) < len(before), "the record did not shrink with the files"


def _records(job_dir: Path) -> dict[str, object]:
    """The artifact index from the job's RECORD, which lives outside the job folder.

    `record_file` puts it at `<root>/.records/<job>.json` **on purpose** - the record decides
    what `start` runs and whom `cancel` signals, so it must not be a path a client can write.
    Reading it from the folder raised `FileNotFoundError`, which was my helper's mistake.
    """
    record = job_dir.parent / ".records" / f"{job_dir.name}.json"
    return json.loads(record.read_text())["artifacts"]
