"""The four commands a start screen calls: recent, recent.remove, browse, preview.set.

**`project.new` recording is the case that was missing**, and it is the commonest one: a user
creates a project from the start screen and expects to see it there next time. It went unrecorded
because `project.new` saves through a different branch from `project.open` and `project.save`,
so recording in those two alone covered every path except the one a start screen creates first.
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import StokesError
from stokes.model.browse import PROJECT_FILE
from stokes.model.preview import PREVIEW_FILE
from stokes.model.recent import recent_path


def a_png(width: int = 320, height: int = 200) -> str:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    head = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + head).decode()


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return Session()


def test_a_new_project_with_a_folder_is_remembered(session: Session, tmp_path: Path) -> None:
    """**The missing case.** A project created from the start screen must appear on it next time."""
    folder = tmp_path / "duct.stokes"
    session.execute("project.new", name="duct", path=str(folder))
    recent = session.execute("project.recent")["projects"]
    assert [row["name"] for row in recent] == ["duct"]
    assert recent[0]["path"] == str(folder)
    assert recent[0]["exists"] is True


def test_a_REFUSED_new_leaves_the_OPEN_project_untouched(session: Session, tmp_path: Path) -> None:
    """**Opus F2, and it was data loss.** A refused `project.new` must not change the session.

    `project.new` assigned `session.project` BEFORE validating the folder. A refusal therefore left
    the session holding the new EMPTY project while `project_path` still named the old folder - so
    the next Save wrote the empty project over the first project's `project.json` and destroyed it.
    Opus reproduced it on Windows.

    Asserted through the commands, because the bug lived in the ORDER of two statements in one
    command: the file on disk is the only witness that can see it.
    """
    first = tmp_path / "first.stokes"
    session.execute("project.new", name="first", path=str(first))
    # **A change worth losing**, so an overwrite is visible: the mesh base size is on the default
    # schema every project has.
    session.execute("mesh.configure", values={"base_size": "0.25 [m]"})
    session.execute("project.save")
    before = (first / "project.json").read_text()

    # A folder that is NOT empty: the refusal the user hits by picking the wrong place.
    busy = tmp_path / "busy.stokes"
    busy.mkdir()
    (busy / "important.txt").write_text("the user's file")
    with pytest.raises(StokesError):
        session.execute("project.new", name="second", path=str(busy))

    # **The session still points at the first project**, so a Save writes there - and that write
    # must be the SAME project, not an empty one.
    assert session.project_path == first, "a refused New moved the session's folder"
    session.execute("project.save")
    assert (first / "project.json").read_text() == before, (
        "a refused New let the next Save overwrite the open project with an empty one"
    )
    # And the refused folder is exactly as the user left it.
    assert (busy / "important.txt").read_text() == "the user's file"


def test_a_NEW_project_IS_remembered(session: Session) -> None:
    """**Rewritten for the new contract** (claude-win2, reading 1). This asserted that an unsaved
    project was ABSENT from the recent list, because "a project with no folder has nothing to
    reopen, so a row for it would open nothing".

    **A new project has a folder now**, so the opposite is true and the row is what the user needs:
    the start screen is where they go back to what they were doing, and a project they just made was
    the commonest case missing from it. This is the same defect the old `if path:` branch in
    `project.new` had - the one that made a project created from the start screen appear nowhere on
    it - and the new contract removes the branch rather than the symptom.
    """
    session.execute("project.new", name="scratch")
    rows = session.execute("project.recent")["projects"]
    assert [row["name"] for row in rows] == ["scratch"]
    # **The row points at a real folder**, or it would open nothing - which is what the old test
    # was protecting against, and it is still worth asserting.
    assert Path(rows[0]["path"]).is_dir()
    assert (Path(rows[0]["path"]) / "project.json").is_file()


def test_opening_and_saving_both_remember(session: Session, tmp_path: Path) -> None:
    folder = tmp_path / "duct.stokes"
    session.execute("project.new", name="duct", path=str(folder))
    session.execute("project.recent.remove", path=str(folder))
    assert session.execute("project.recent")["projects"] == []

    session.execute("project.open", path=str(folder))
    assert [row["name"] for row in session.execute("project.recent")["projects"]] == ["duct"]

    session.execute("project.recent.remove", path=str(folder))
    session.execute("project.save")
    assert [row["name"] for row in session.execute("project.recent")["projects"]] == ["duct"]


def test_removing_a_recent_entry_never_touches_the_folder(session: Session, tmp_path: Path) -> None:
    """**The distinction the command exists for.** Clearing a row is tidying a list, not deleting
    work - and a user who removes the wrong row must still have their project."""
    folder = tmp_path / "duct.stokes"
    session.execute("project.new", name="duct", path=str(folder))
    session.execute("project.recent.remove", path=str(folder))
    assert session.execute("project.recent")["projects"] == []
    assert (folder / PROJECT_FILE).is_file(), "the project folder was touched"


def test_a_preview_is_stored_and_reported(session: Session, tmp_path: Path) -> None:
    folder = tmp_path / "duct.stokes"
    session.execute("project.new", name="duct", path=str(folder))
    reply = session.execute("project.preview.set", png_base64=a_png())
    assert Path(reply["path"]).name == PREVIEW_FILE
    assert reply["bytes"] > 0
    assert session.execute("project.recent")["projects"][0]["has_preview"] is True


def test_a_preview_WORKS_for_a_new_project(session: Session) -> None:
    """**Rewritten for the new contract** (claude-win2, reading 1). This asserted that an unsaved
    project was REFUSED a preview, because "an unsaved project has nowhere to put the file, and
    saying so is better than writing the image into a temporary folder the user will never find".

    **The reasoning was right and the premise is gone**: a new project has a folder now, so the
    preview has somewhere to go and the refusal would be a false one. The old test's concern - that
    the image must not end up somewhere the user will never find - is exactly what is asserted, by
    checking it is inside the project.
    """
    session.execute("project.new", name="scratch")
    reply = session.execute("project.preview.set", png_base64=a_png())
    written = Path(reply["path"])
    assert written.is_file()
    assert written.parent == Path(session.project_path), written


def test_a_bad_image_is_refused_before_anything_is_written(
    session: Session, tmp_path: Path
) -> None:
    folder = tmp_path / "duct.stokes"
    session.execute("project.new", name="duct", path=str(folder))
    with pytest.raises(Exception) as caught:
        session.execute("project.preview.set", png_base64=base64.b64encode(b"GIF89a").decode())
    assert "preview_invalid" in str(getattr(caught.value, "code", ""))
    assert not (folder / PREVIEW_FILE).exists(), "a refused image was still written"


def test_storing_a_preview_does_not_dirty_the_project(session: Session, tmp_path: Path) -> None:
    """**`mutates=False`**: a picture is not model state. A user who moved a window has not changed
    their model, and the project must not report itself as modified."""
    folder = tmp_path / "duct.stokes"
    session.execute("project.new", name="duct", path=str(folder))
    before = json.loads((folder / PROJECT_FILE).read_text())
    session.execute("project.preview.set", png_base64=a_png())
    after = json.loads((folder / PROJECT_FILE).read_text())
    assert after["modified"] == before["modified"], "storing a preview touched the project file"


def test_browse_answers_the_same_shape_the_screen_needs(session: Session) -> None:
    keys = set(session.execute("project.browse"))
    assert keys == {"path", "parent", "roots", "entries", "truncated"}


def test_a_broken_recent_file_does_not_stop_the_session(session: Session, tmp_path: Path) -> None:
    """The start screen reads this first; a damaged list must not stop a project opening."""
    path = recent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert session.execute("project.recent")["projects"] == []


def test_defaults_resolves_the_full_path_the_dialog_shows(session: Session) -> None:
    """**The menu spec's "will be created at <full path>"**, answered by the server because only it
    knows the three rules: `project_dir` appends `.stokes`, `~` is resolved, and the USER root
    decides the folder. A front end that duplicated them could disagree with the command that
    actually creates the folder - and the user would see one path and get another.
    """
    from stokes.workspace_root import user_root

    plain = session.execute("project.defaults")
    # **`Projects`, capitalised, directly under the VISIBLE root** (the owner's 0700 order). It used
    # to be `$STOKES_HOME/projects` under a hidden `~/.stokes`. Asserted against the RESOLVER rather
    # than a literal suffix, because a sibling test that leaked an environment variable into the run
    # made the literal comparison fail on correct code.
    assert Path(plain["folder"]) == user_root() / "Projects"
    assert plain["suffix"] == ".stokes"
    assert "path" not in plain, "a path needs a name"

    # **A name no other test uses.** The first version asked about `duct`, which a sibling test in
    # the same run had already created in the shared temporary root - so `exists` came back True and
    # the precondition failed on correct code. Measured: `assert True is False`.
    name = "defaults_probe"
    resolved = session.execute("project.defaults", name=name)
    # The suffix is appended by the same normalisation `project.new` applies.
    assert resolved["path"] == str(user_root() / "Projects" / f"{name}.stokes")
    assert resolved["exists"] is False

    session.execute("project.new", name=name)
    assert session.execute("project.defaults", name=name)["exists"] is True, (
        "the dialog would offer a name that already exists"
    )


def test_defaults_resolves_a_chosen_folder(session: Session, tmp_path: Path) -> None:
    """**The path line follows Browse.** The dialog sends `<folder>/<name>` to `project.new`; the
    line said the projects folder whatever folder was chosen, so it showed one path and created
    another. With `folder` it answers for the folder chosen, and creates nothing."""
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    resolved = session.execute("project.defaults", name="browsed", folder=str(chosen))
    assert resolved["path"] == str((chosen / "browsed.stokes").resolve())
    assert resolved["exists"] is False and list(chosen.iterdir()) == []
    assert Path(resolved["folder"]) == Path(session.execute("project.defaults")["folder"])
    session.execute("project.new", name="browsed", path=f"{chosen.as_posix()}/browsed")
    assert session.execute("project.defaults", name="browsed", folder=str(chosen))["exists"] is True
    blank = session.execute("project.defaults", name="browsed", folder="  ")
    assert blank["path"] == session.execute("project.defaults", name="browsed")["path"]


def test_defaults_is_read_only(session: Session) -> None:
    """**The dialog calls it on every keystroke**, so it must not create anything - a command that
    made the folder while the user typed would leave litter for every name they tried.
    """
    from pathlib import Path

    folder = Path(session.execute("project.defaults")["folder"])
    before = sorted(p.name for p in folder.iterdir())
    for name in ("a", "b", "c"):
        session.execute("project.defaults", name=name)
    assert sorted(p.name for p in folder.iterdir()) == before


def test_dirty_tracks_unsaved_changes_across_a_save(session: Session) -> None:
    """**The Close prompt's foundation** (menu spec rule 2), and the flag is a REVISION comparison.

    My first attempt compared `project.modified`, the wall-clock stamp. **Measured: `node.add` does
    not move it**, so a project with a new node compared equal to its saved copy - and the Close
    prompt would have offered to discard nothing. `Project.revision` counts `_touch` (structural and
    parameter edits, including `add`) and `_mark` (a value that moved when an expression was
    re-evaluated), which between them are every route a change reaches the model.
    """
    created = session.execute("project.new", name="dirty_test")
    # **A new project is DIRTY: it has never been written.** `project.new` builds the model and
    # hands it to the session; the file appears when the user saves. Measured True here - and a
    # probe that printed False had called `project.save` first and was showing the saved state, so
    # the flag was right and my reading of my own instrument was wrong.
    assert created["dirty"] is True

    assert session.execute("project.save")["dirty"] is False, "the write is what makes it clean"

    session.execute("node.add", parent="/geometry", type="group", name="g1")
    assert session.execute("project.info")["dirty"] is True, (
        "a change after a save must make it dirty - this is the case the stamp missed"
    )

    assert session.execute("project.save")["dirty"] is False


def test_a_reloaded_project_starts_clean(session: Session, tmp_path: Path) -> None:
    """Opening what was just written must not look dirty, or every Close would prompt."""
    session.execute("project.new", name="reloaded")
    session.execute("project.save")
    path = session.execute("project.info")["path"]
    assert session.execute("project.close") is not None
    opened = session.execute("project.open", path=path)
    assert opened["dirty"] is False
