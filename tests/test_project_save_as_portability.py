"""What a Save as preserves, and the one thing it does not.

**A WORD ON TIMING, because it cost me three wrong answers.** The B-rep and the pack are written
by a **background task** (`GeometryStore.project_saved` runs `copy_shapes` off the calling
thread), so a check made straight after `project.save` returns **fails while the part is
perfectly fine**. Measured: five trials checked immediately and found nothing; three trials given
ten seconds found everything, every time. A check made too early is indistinguishable from a
failure, so `settle_cache` waits.

**The measured answer.** The note in `KNOWN-ISSUES.md` says "`imports/` is not copied on Save as,
so an uploaded CAD file does not travel with the project" - true, but easy to read as worse than
it is:

* **the geometry DOES travel** - the B-rep, its sidecar and the packed mesh cache are written
  into the new folder, so a project copied to another machine opens, draws and meshes;
* **the source file does not** - `node.params["source"]` still names the file the user picked, by
  absolute path, wherever it was.

So the loss is **the ability to re-import**, not the ability to use the part. And that is a
deliberate choice, stated in `imports_dir`'s docstring: *"a file the user picked is not silently
relocated under them."*

This test pins both halves, so that if either changes - the geometry failing to travel, or
the source file quietly being copied - it is noticed.
quietly being copied - it is noticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.geometry.store import GeometryStore
from tests.geometry_fixtures import box, write_brep


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return Session()


def settle_cache(folder: Path, part_id: str, timeout: float = 120.0) -> set[str]:
    """Wait for the background copy to put the part's files in `folder`. Returns the names seen.

    **The wait is the point**: `project.save` returns before `copy_shapes` has run, so a test that
    looks immediately reports a missing B-rep that is merely late.

    **It waits for the SIDECAR, not the B-rep.** `copy_shapes` writes sidecar out, B-rep,
    sidecar in - the sidecar is the commit, so its presence means the entry is complete. Waiting
    for the B-rep instead returned while the sidecar was still to come, and the caller then
    asserted on a file this helper had never waited for: **measured**, 4 of 6 trials failed on
    "the sidecar did not travel" and every one of them had the sidecar 2 s later. The wait was
    the bug, not the copy.

    **The timeout is generous because the delay is not a constant.** Measured on this machine: the
    copy lands in **0.03 s** on an idle one, appears within **10 s** in a fresh process under a
    small load, and a full-suite run - 2500 tests ahead of it - was enough to miss a **20 s**
    deadline. The write is off-thread and unordered with respect to the save, so the honest thing
    for a test is to wait long enough that only a real absence fails it, rather than to guess a
    duration and call a slow copy a missing file.
    """
    import time

    wanted = f"{part_id}.brep.json"
    deadline = time.monotonic() + timeout
    names: set[str] = set()
    while time.monotonic() < deadline:
        cache = folder / "cache"
        names = {path.name for path in cache.rglob("*")} if cache.is_dir() else set()
        if wanted in names:
            return names
        time.sleep(0.02)
    return names


def imported_part(session: Session, tmp_path: Path) -> tuple[str, Path]:
    """A project with one imported part, and the file it came from."""
    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "part.brep")
    session.execute("project.new", name="p")
    part_id = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part_id, timeout=180), "the import did not finish"
    return part_id, source


def test_the_geometry_travels_to_the_new_folder(session: Session, tmp_path: Path) -> None:
    """**The half that matters most.** A folder moved to another machine must open and mesh."""
    part_id, _ = imported_part(session, tmp_path)
    saved = tmp_path / "saved.stokes"
    session.execute("project.save", path=str(saved))

    names = settle_cache(saved, part_id)
    assert names, "the new folder has no cache at all"
    assert f"{part_id}.brep" in names, "the B-rep did not travel, so the part cannot be drawn"
    assert f"{part_id}.brep.json" in names, "the sidecar did not travel"
    assert f"{part_id}.mesh" in names, "the packed mesh did not travel, so the part must re-import"


def test_save_as_keeps_the_geometry_too(session: Session, tmp_path: Path) -> None:
    """A second save to a NEW folder must copy what the first one did, not merely leave the part
    reachable through the old folder."""
    part_id, _ = imported_part(session, tmp_path)
    session.execute("project.save", path=str(tmp_path / "a.stokes"))
    session.execute("project.save", path=str(tmp_path / "b.stokes"))

    names = settle_cache(tmp_path / "b.stokes", part_id)
    assert f"{part_id}.brep" in names, "the second save did not copy the B-rep"
    assert f"{part_id}.mesh" in names, "the second save did not copy the mesh"


def test_the_source_file_does_NOT_travel_and_that_is_deliberate(
    session: Session, tmp_path: Path
) -> None:
    """**The half that is lost, pinned so it cannot change silently.**

    `source` stays the absolute path of the file the user chose. Copying it into the project would
    relocate a file the user picked, which `imports_dir`'s docstring refuses on purpose - so the
    assertion here is that the path is **unchanged**, not that a copy exists.
    """
    part_id, source = imported_part(session, tmp_path)
    saved = tmp_path / "saved.stokes"
    session.execute("project.save", path=str(saved))

    node = session.require_project().find(part_id)
    assert node is not None
    assert node.params["source"] == str(source), (
        "the source path changed on save; if it now points into the project, a copy was made and "
        "the deliberate choice recorded in imports_dir's docstring has been reversed"
    )
    assert not (saved / "imports").exists(), "no imports folder is expected for a picked file"


def test_a_project_whose_source_vanished_still_works_from_its_cache(
    session: Session, tmp_path: Path
) -> None:
    """**Why the loss is smaller than it sounds.** The mesh and the B-rep are in the folder, so a
    part whose source file has gone still loads - only a RE-IMPORT is impossible."""
    part_id, source = imported_part(session, tmp_path)
    saved = tmp_path / "saved.stokes"
    session.execute("project.save", path=str(saved))
    settle_cache(saved, part_id)  # the cache must be there before the source goes
    source.unlink()

    reopened = Session()
    reopened.execute("project.open", path=str(saved))
    node = reopened.require_project().find(part_id)
    assert node is not None, "the part vanished from the reopened project"
    assert not Path(node.params["source"]).is_file(), "the source should be gone in this test"

    # **Asserted through the STORE, not by looking for a file by name.** My first version checked
    # `saved/cache/<id>.mesh` directly and failed while the part was in fact fine: the reopened
    # session resolves the cache folder itself, and where those bytes live - the per-part file or
    # the `meshes.bin` pack - is an implementation detail that has changed once already.
    # What matters is that the part loads from the cache with its source gone, so that is what
    # is asked.
    store = GeometryStore.of(reopened)
    # `transport` is what a client is served: version-2 bytes for the part, straight from the cache
    # the save copied. None means the part is not there at all.
    served = store.transport(part_id)
    assert served, "the part could not be served with its source gone, so the cache did not travel"
