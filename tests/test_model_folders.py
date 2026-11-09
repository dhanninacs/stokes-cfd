"""Choosing a project folder: the refusals, and what a refusal must not do.

**The rule the owner asked for** is that a new project takes a folder and everything is created
in it. The failure mode that matters is not "it picked the wrong folder" but **"it destroyed what
was in one"** - so these tests assert not only that a bad destination is refused but that **the
refusal left the folder exactly as it was**.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.model.folders import (
    FolderError,
    check_name,
    describe,
    inside,
    prepare,
    project_folder,
)


@pytest.mark.parametrize("fine", ["duct", "my duct", "a-b_c.d", "Pipe 2", "Ünïcode"])
def test_a_reasonable_name_is_kept(fine: str) -> None:
    """**The false-refusal half.** A rule that refuses everything would pass every test below and be
    useless; these are the names people actually use, including a space and a non-ASCII letter."""
    assert check_name(fine) == fine


@pytest.mark.parametrize(
    "hostile",
    ["", "   ", ".", "..", "a/b", "a\\b", "a:b", "-x", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b"],
)
def test_a_name_that_cannot_be_a_folder_is_refused(hostile: str) -> None:
    """A name is shown in a title bar AND used as a folder, so the two roles must agree on what is
    legal. A separator, a drive colon, a Windows-reserved character or a leading `-` is not."""
    with pytest.raises(FolderError) as caught:
        check_name(hostile)
    assert caught.value.code == "project.name_invalid"


def test_a_name_with_a_control_character_is_refused() -> None:
    """It would reach a path, a title and possibly a command line."""
    with pytest.raises(FolderError):
        check_name("a\nb")


# ------------------------------------------------------------------ where the folder is


def test_the_folder_is_the_parent_and_the_name(tmp_path: Path) -> None:
    assert project_folder(tmp_path, "duct") == (tmp_path / "duct").resolve()


def test_a_parent_that_is_not_a_folder_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "a-file"
    plain.write_text("x")
    with pytest.raises(FolderError):
        project_folder(plain, "duct")


def test_a_name_that_ESCAPES_the_parent_is_refused(tmp_path: Path) -> None:
    """**The check is on the RESULT, not the string.** `..` is refused by name; a SYMLINK with an
    innocent name must be refused too, and only resolving catches it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "duct").symlink_to(outside)
    with pytest.raises(FolderError) as caught:
        project_folder(parent, "duct")
    assert caught.value.code == "project.folder_invalid"


def test_a_missing_folder_is_created(tmp_path: Path) -> None:
    folder = tmp_path / "duct"
    assert prepare(folder) == folder
    assert folder.is_dir()


def test_an_EMPTY_folder_is_accepted(tmp_path: Path) -> None:
    """Choosing a folder the user already made is ordinary, and must not be refused."""
    folder = tmp_path / "duct"
    folder.mkdir()
    assert prepare(folder) == folder


def test_a_NON_EMPTY_folder_is_refused_with_its_own_code(tmp_path: Path) -> None:
    """**Its own code**, because the message is actionable in a way a generic one is not: "choose an
    empty folder"."""
    folder = tmp_path / "duct"
    folder.mkdir()
    (folder / "thesis.docx").write_text("months of work")
    with pytest.raises(FolderError) as caught:
        prepare(folder)
    assert caught.value.code == "project.folder_not_empty"


def test_a_refused_folder_is_left_EXACTLY_as_it_was(tmp_path: Path) -> None:
    """**The property that matters more than the refusal.** Not cleaned, not merged, not
    renamed - the file is still there, with its contents, and nothing new beside it."""
    folder = tmp_path / "duct"
    folder.mkdir()
    (folder / "thesis.docx").write_text("months of work")
    before = sorted(entry.name for entry in folder.iterdir())
    with pytest.raises(FolderError):
        prepare(folder)
    assert sorted(entry.name for entry in folder.iterdir()) == before
    assert (folder / "thesis.docx").read_text() == "months of work"


def test_a_file_where_the_folder_should_be_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "duct"
    plain.write_text("not a folder")
    with pytest.raises(FolderError):
        prepare(plain)
    assert plain.read_text() == "not a folder", "the refusal overwrote a file"


def test_a_folder_holding_only_DOT_FILES_counts_as_empty(tmp_path: Path) -> None:
    """A folder a user has opened in a file manager often has a `.DS_Store` or similar in it, and
    refusing that would be a false refusal for an empty folder."""
    folder = tmp_path / "duct"
    folder.mkdir()
    (folder / ".DS_Store").write_bytes(b"junk")
    assert prepare(folder) == folder


def test_a_whole_project_creates_NOTHING_in_the_temp_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The owner asked for exactly this**: "all files etc should be created in this folder". So a
    project made from nothing must leave the temporary directory **completely empty**.

    **`TMPDIR` is pointed at a fresh directory of our own** (claude-win2, reading 1, requirement 3).
    Listing the system temp root would prove nothing here: pytest's `tmp_path` is itself under
    `/tmp`, so the directory is full of other people's files and my first version could not tell my
    own folder from pytest's - the "meaningless `/tmp` assertion" claude-win2 named, which failed
    on correct code.

    A fresh root makes the assertion exact: **the set is empty, not merely unchanged.**

    **The whole flow, not just `prepare`**: creating the project writes `project.json` and the stage
    settings, and those are what a scratch-directory bug would scatter.
    """
    from stokes.api import Session

    temp_root = tmp_path / "temp-root"
    temp_root.mkdir()
    monkeypatch.setenv("TMPDIR", str(temp_root))
    monkeypatch.setenv("TEMP", str(temp_root))
    monkeypatch.setenv("TMP", str(temp_root))
    # **`tempfile` caches its directory**, so the variables alone would not move it.
    monkeypatch.setattr("tempfile.tempdir", str(temp_root))
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))

    parent = tmp_path / "work"
    parent.mkdir()
    session = Session()
    session.execute("project.new", name="duct", path=str(parent / "duct"))

    assert list(temp_root.iterdir()) == [], (
        f"creating a project left {sorted(p.name for p in temp_root.iterdir())} in the temp root"
    )
    # **And it is really somewhere**: an empty temp root would also pass if nothing had happened.
    folder = Path(session.project_path)
    assert (folder / "project.json").is_file()
    assert folder.is_relative_to(parent)


# ------------------------------------------------------------------ the helper the copies use


def test_inside_answers_for_a_path_against_its_root(tmp_path: Path) -> None:
    """Used to refuse a copy onto its own source - the case where `save_as` would delete what it is
    reading."""
    root = tmp_path / "a"
    root.mkdir()
    """**The property that matters more than the refusal.** Not cleaned, not merged, not
    renamed - the file is still there, with its contents, and nothing new beside it."""
    assert inside(tmp_path / "elsewhere", root) is False


def test_describe_is_read_only_and_never_raises(tmp_path: Path) -> None:
    missing = describe(tmp_path / "nope")
    assert missing["exists"] is False and missing["entries"] == []
    folder = tmp_path / "here"
    folder.mkdir()
    (folder / "x").write_text("y")
    assert describe(folder)["entries"] == ["x"]


# ------------------------------------------------------------------ a project always has a folder


def test_a_new_project_gets_a_REAL_folder_when_none_is_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Decision 2 of claude-win2's 1745 message: there is no folder-less project any more.**

    The owner asked for a new project to take a folder with everything created inside it. A
    folder-less project is what the three scratch directories existed to serve - `/tmp` runs, `/tmp`
    imports, `/tmp` shapes - so removing the concept removes the cause rather than sweeping up after
    it, which is what `SCRATCH_PREFIXES` and its 24-hour sweep do.

    **Driven through the command**, because that is where the choice is made. `path` stays optional
    so the existing call sites keep working, and the fallback is a real folder rather than None.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    # **The VISIBLE root is what the default follows since 2026-10-17**, not `STOKES_HOME`:
    # the owner's 0700 order moved projects to `<Documents>/Stokes/Projects` so they can be found
    # in a file manager. `STOKES_HOME` stays the APP root (recent list, hosts, logs) - nothing a
    # user would want to find or back up.
    monkeypatch.setenv("STOKES_USER_HOME", str(tmp_path / "visible"))
    from stokes.api import Session

    session = Session()
    session.execute("project.new", name="duct")
    folder = Path(session.project_path)

    assert folder.is_absolute(), folder
    # **It follows the user root**, which is the property: a folder the user can see rather than a
    # hidden one or a temporary one. **Not "it is not under /tmp"**, which was my first assertion
    # and is meaningless here - pytest's `tmp_path` is itself under `/tmp`, so it failed on
    # correct code.
    assert folder.is_relative_to(tmp_path / "visible"), folder
    assert folder.parent.name == "Projects", folder
    # **The project file is INSIDE it**, so it is a project rather than an empty folder.
    assert (folder / "project.json").is_file()


def test_a_second_project_of_the_same_name_does_NOT_overwrite_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The bug my first version had.** `save_project` runs every path through `project_dir`, which
    **appends `.stokes`** - so checking `<base>/duct` for a collision while the file went to
    `<base>/duct.stokes` meant two projects called `duct` in one session both landed on the same
    folder, the second overwriting the first. Measured before normalising the name.

    Numbered rather than refused, because the caller did not ask for a particular folder and failing
    would be a surprise.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    from stokes.api import Session

    first = Session()
    first.execute("project.new", name="duct")
    # **A marker in the first project**, so overwriting it would be visible rather than inferred.
    marker = Path(first.project_path) / "the-first-one.txt"
    marker.write_text("still here")

    second = Session()
    second.execute("project.new", name="duct")

    assert Path(second.project_path) != Path(first.project_path), (
        "the second project reused the first one's folder"
    )
    assert marker.read_text() == "still here", "the second project overwrote the first"


def test_an_EXPLICIT_folder_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**The false-refusal half**: a caller that names a folder gets it. `project_dir` appends
    `.stokes` when the suffix is absent, which is pre-existing behaviour and is what the assertion
    accounts for rather than fights.
    """
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    from stokes.api import Session

    chosen = tmp_path / "chosen"
    session = Session()
    session.execute("project.new", name="mosfet", path=str(chosen))
    got = Path(session.project_path)
    assert got == chosen.with_name(chosen.name + ".stokes"), got
    assert (got / "project.json").is_file()
    # **And nothing was made under the default home**, because a folder was given.
    assert not (tmp_path / "home" / "projects").exists(), (
        "a default folder was created as well as the chosen one"
    )


# ------------------------------------------------------------------ wired into project.new


def test_the_folder_rules_are_REACHABLE_from_project_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The refusals must be reachable from the command, not only from this module.**

    `stokes.model.folders` had 35 tests and **nothing in production called `prepare` or
    `project_folder`** - measured - so the owner's rules were enforced nowhere. This is the trap of
    a test that sets up its own machinery: it exercises the module directly and **cannot see whether
    the product wires it up**. Writing a mutant against `prepare` is what found it, because the
    mutant passed.

    **The caller names the folder the project will use**, which for `project.new` is the folder
    itself - `project_dir` appends `.stokes` when the suffix is absent, so a path without it names a
    DIFFERENT folder, and that is a pre-existing normalisation rather than a rule about emptiness.
    """
    from stokes.api import Session
    from stokes.errors import StokesError

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))

    # **A non-empty folder, named as the project will use it**, is refused and left alone.
    busy = tmp_path / "busy.stokes"
    busy.mkdir()
    (busy / "thesis.txt").write_text("months of work")
    with pytest.raises(StokesError) as caught:
        Session().execute("project.new", name="duct", path=str(busy))
    assert caught.value.code == "project.folder_not_empty", caught.value.code
    assert (busy / "thesis.txt").read_text() == "months of work", "the refusal destroyed a file"

    # **An empty one is accepted**, so this is not a blanket refusal of existing folders.
    free = tmp_path / "free.stokes"
    free.mkdir()
    session = Session()
    session.execute("project.new", name="duct", path=str(free))
    assert (free / "project.json").is_file()
    assert Path(session.project_path) == free


def test_a_new_projects_default_folder_is_also_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default path goes through `prepare` too, so the rule is the same on both branches -
    a caller who named nothing does not get a weaker check than one who named a folder.
    """
    from stokes.api import Session

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    # The visible root, as above: the default follows it, not the app root.
    monkeypatch.setenv("STOKES_USER_HOME", str(tmp_path / "visible"))
    session = Session()
    session.execute("project.new", name="duct")
    folder = Path(session.project_path)
    # **Created, usable, and private to this test's root**, which is what `prepare` guarantees.
    assert folder.is_dir() and (folder / "project.json").is_file()
    assert folder.is_relative_to(tmp_path / "visible")
