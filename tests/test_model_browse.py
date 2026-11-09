"""Browsing for a project folder - and the rules that stop it being a file browser.

**The security of this feature is one decision**: what a listing may reach. Everything here is an
attempt to get outside the two roots - with `..`, with a symlink, with an absolute path - and each
must fail the SAME check, because the check is on the resolved result rather than on the string a
client sent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stokes.model.browse import (
    MAX_ENTRIES,
    PROJECT_FILE,
    BrowseError,
    list_folder,
    projects_folder,
    resolve_inside,
    roots,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home with a projects folder inside it, which is what the roots are built from.

    **Both roots, and the second is what this file now depends on.** `projects_folder()` with no
    argument resolves the VISIBLE root (`<Documents>/Stokes/Projects` since 2026-10-17), not
    `STOKES_HOME` - so a fixture that set only the latter made every test in this file share one
    folder with the developer's real one, and `test_hidden_names_are_skipped` (which asserts an
    EMPTY listing) failed on whatever a sibling test had left behind.
    """
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("STOKES_HOME", str(folder / ".stokes"))
    monkeypatch.setenv("STOKES_USER_HOME", str(folder))
    return folder


def a_project(folder: Path, name: str) -> Path:
    made = folder / name
    made.mkdir(parents=True, exist_ok=True)
    (made / PROJECT_FILE).write_text(json.dumps({"name": name}), encoding="utf-8")
    return made


def test_nothing_outside_the_roots_resolves(home: Path) -> None:
    """**The one check everything depends on.** `..`, an absolute path and another user's home are
    all the same refusal, because the test is on where the path LANDS."""
    projects_folder()
    for bad, why in (
        ("/etc", "an absolute path outside"),
        ("/root", "another user's home"),
        (str(projects_folder() / ".." / ".." / ".." / "etc"), "traversal"),
        (str(home.parent / "elsewhere"), "a sibling of home"),
    ):
        with pytest.raises(BrowseError) as caught:
            resolve_inside(bad)
        assert caught.value.code == "project.browse_denied", why
        # **The refused path is not echoed back** - it is a client's string.
        assert bad not in str(caught.value), why


def test_a_SIBLING_whose_name_starts_with_a_root_is_refused(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The case that tells a real containment check from a string one.**

    `projects-evil` is a different directory from `projects`, but
    `"/x/projects-evil".startswith("/x/projects")` is **True**. So a check written on the string
    accepts a folder that is not inside the root at all - and my first version of this test suite
    passed against exactly that mistake, because no sibling with a matching prefix existed to try.
    """
    projects_folder()
    # A sibling of the HOME root that shares its name as a prefix: home itself is a root, so a
    # folder inside it would be allowed and prove nothing. The root is the test's home: on Windows
    # the real home holds pytest's temporary folder, which would make every path here "inside".
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    evil = home.parent / f"{home.name}-evil"
    evil.mkdir(parents=True)
    a_project(evil, "stolen.stokes")
    with pytest.raises(BrowseError) as caught:
        resolve_inside(str(evil))
    assert caught.value.code == "project.browse_denied"


def test_a_path_that_is_not_a_folder_is_refused(home: Path) -> None:
    projects_folder()
    with pytest.raises(BrowseError):
        resolve_inside(str(projects_folder() / "nope"))


def test_the_default_listing_is_the_projects_folder_not_the_home(home: Path) -> None:
    """**Home is a root but a bad place to OPEN at.** A start screen that greets a user with
    `Videos` and their source trees is a file browser, and it shows whatever the home holds."""
    a_project(projects_folder(), "duct.stokes")
    (home / "Videos").mkdir()
    listing = list_folder(None)
    assert listing["path"] == str(projects_folder().resolve())
    assert [entry["name"] for entry in listing["entries"]] == ["duct.stokes"]
    assert listing["parent"] is None, "a root has no parent, so there is no 'up' that would refuse"


def test_a_project_is_distinguished_from_a_plain_folder(home: Path) -> None:
    """The kind is decided by the same marker `project.open` looks for, so what the screen offers is
    what will actually open."""
    a_project(projects_folder(), "duct.stokes")
    (projects_folder() / "scratch").mkdir()
    kinds = {entry["name"]: entry["kind"] for entry in list_folder(None)["entries"]}
    assert kinds == {"duct.stokes": "project", "scratch": "dir"}


def test_a_symlink_is_skipped_not_followed(home: Path) -> None:
    """**The classic way out of a root.** A link inside the root that points at `/etc` is never
    listed, so a listing cannot reach outside by naming something inside."""
    folder = projects_folder()
    (folder / "escape").symlink_to("/etc")
    a_project(folder, "real.stokes")
    names = [entry["name"] for entry in list_folder(None)["entries"]]
    assert "escape" not in names
    assert "real.stokes" in names


def test_hidden_names_are_skipped(home: Path) -> None:
    """`.ssh`, `.git` and `.config` are never projects, and offering them invites a click that goes
    nowhere."""
    a_project(projects_folder(), ".hidden.stokes")
    (projects_folder() / ".ssh").mkdir()
    assert list_folder(None)["entries"] == []


def test_the_parent_stops_at_a_root(home: Path) -> None:
    """Inside the projects folder there IS an up, and it goes to the folder's parent - but a root
    reports None, so the screen shows no "up" that would earn a refusal."""
    inner = projects_folder() / "group"
    inner.mkdir()
    a_project(inner, "p.stokes")
    listing = list_folder(str(inner))
    assert listing["parent"] == str(projects_folder().resolve())
    assert listing["roots"] == [str(root) for root in roots()]


def test_a_listing_is_capped_and_says_so(home: Path) -> None:
    """A directory with 200,000 entries must not be able to exhaust memory."""
    folder = projects_folder()
    for number in range(MAX_ENTRIES + 20):
        (folder / f"d{number:04d}").mkdir()
    listing = list_folder(None)
    assert len(listing["entries"]) == MAX_ENTRIES
    assert listing["truncated"] is True


def test_the_projects_folder_is_created_on_demand(home: Path) -> None:
    """A new install has none, and the start screen must still have somewhere to show."""
    folder = projects_folder()
    assert folder.is_dir()
    assert folder in roots()
