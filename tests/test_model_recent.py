"""The recent-projects store behind a start screen.

**Two properties are the point**, and a start screen is where they matter most because it is
the first thing a user sees:

* **it must always appear** - a missing, unreadable or damaged list is an empty list, never an
  exception, because a start screen that fails to open is worse than one with no recent rows;
* **a project that has moved or been deleted is still LISTED**, with `exists` false, so a user
  greyed row they can act on rather than a project that silently vanished from their history.
"""

from __future__ import annotations

import json
from pathlib import Path

from stokes.model.recent import (
    MAX_RECENT,
    RecentProject,
    forget,
    read_recent,
    recent_path,
    remember,
)


def a_project(folder: Path, name: str = "duct", modified: str = "2026-10-15T10:00:00Z") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.json").write_text(
        json.dumps({"name": name, "modified": modified}), encoding="utf-8"
    )
    return folder


def test_a_missing_or_damaged_list_is_empty_never_an_error(tmp_path: Path) -> None:
    """**The start screen must appear.** A list nobody needs cannot be allowed to stop it."""
    path = tmp_path / "recent.json"
    assert read_recent(path) == []

    path.write_text("{not json")
    assert read_recent(path) == []

    path.write_text(json.dumps({"version": 99, "projects": []}))
    assert read_recent(path) == []

    path.write_text(json.dumps({"version": 1, "projects": "not a list"}))
    assert read_recent(path) == []


def test_a_project_is_remembered_with_its_own_name(tmp_path: Path) -> None:
    """The name comes from the **project's own file**, not the folder, so a renamed folder does
    not rename the project in the list."""
    home = tmp_path / "home"
    project = a_project(tmp_path / "some-odd-folder-name", name="the duct")
    remember(project, home=home)
    found = read_recent(recent_path(home))
    assert [entry.name for entry in found] == ["the duct"]


def test_opening_a_project_twice_is_one_row(tmp_path: Path) -> None:
    """**Keyed by resolved path.** Two rows for one project is the classic recent-list bug."""
    home = tmp_path / "home"
    project = a_project(tmp_path / "p.stokes")
    remember(project, home=home)
    remember(project, home=home)
    assert len(read_recent(recent_path(home))) == 1


def test_a_path_spelled_differently_is_still_one_row(tmp_path: Path) -> None:
    """`/x/../p.stokes` and `/p.stokes` are one project; a start screen must not show both."""
    home = tmp_path / "home"
    a_project(tmp_path / "p.stokes")
    remember(tmp_path / "p.stokes", home=home)
    remember(tmp_path / "anything" / ".." / "p.stokes", home=home)
    assert len(read_recent(recent_path(home))) == 1


def test_a_deleted_project_is_still_listed_and_says_it_is_gone(tmp_path: Path) -> None:
    """**A row saying the folder is missing beats a project vanishing from history.** The user
    did open it; silently forgetting is a list that cannot be trusted."""
    import shutil

    home = tmp_path / "home"
    project = a_project(tmp_path / "p.stokes", name="duct")
    remember(project, home=home)
    assert read_recent(recent_path(home))[0].exists is True

    shutil.rmtree(project)
    entry = read_recent(recent_path(home))[0]
    assert entry.name == "duct", "the name is stored, so it survives the folder"
    assert entry.exists is False, "a deleted project must say so"


def test_the_list_is_capped_and_most_recent_first(tmp_path: Path) -> None:
    """A start screen is not a file manager: past a few dozen rows a Browse button is better,
    and every row costs a `stat`."""
    home = tmp_path / "home"
    for number in range(MAX_RECENT + 5):
        remember(a_project(tmp_path / f"p{number}.stokes", name=f"p{number}"), home=home)
    found = read_recent(recent_path(home))
    assert len(found) == MAX_RECENT
    assert found[0].name == f"p{MAX_RECENT + 4}", "the newest must be first"
    assert "p0" not in {entry.name for entry in found}, "the oldest must have fallen off"


def test_forget_removes_one_row(tmp_path: Path) -> None:
    home = tmp_path / "home"
    keep = a_project(tmp_path / "keep.stokes", name="keep")
    drop = a_project(tmp_path / "drop.stokes", name="drop")
    remember(keep, home=home)
    remember(drop, home=home)
    forget(drop, home=home)
    assert [entry.name for entry in read_recent(recent_path(home))] == ["keep"]


def test_the_file_is_owner_only(tmp_path: Path) -> None:
    """It lists the paths of a user's projects, which is nobody else's business."""
    home = tmp_path / "home"
    remember(a_project(tmp_path / "p.stokes"), home=home)
    assert recent_path(home).stat().st_mode & 0o077 == 0, oct(recent_path(home).stat().st_mode)


def test_an_entry_that_is_not_usable_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A hand-edited list must not stop the screen: good rows show, the bad one does not."""
    path = tmp_path / "recent.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "projects": [
                    {"name": "good", "path": str(tmp_path / "good.stokes")},
                    {"name": "no path"},
                    "not an object",
                    {"path": ""},
                    {"path": str(tmp_path / "also-good.stokes")},
                ],
            }
        )
    )
    assert [entry.name for entry in read_recent(path)] == ["good", "also-good"]
    # And an entry with no name falls back to the folder's stem rather than showing a blank row.
    entry = read_recent(path)[1]
    assert entry.name == "also-good"
    assert isinstance(entry, RecentProject)
