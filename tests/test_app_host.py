"""The desktop host's rules that need no window: the files a page may touch, the launch theme."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stokes.api import Session
from stokes.app import textfiles
from stokes.app.__main__ import THEMES, chosen_theme, default_theme, workspace_theme
from stokes.app.textfiles import PickedTextFiles


# ---------------------------------------------------------------------------------- picked files
@pytest.fixture
def files() -> PickedTextFiles:
    return PickedTextFiles()


def test_a_page_reads_only_what_the_user_picked_to_open(
    tmp_path: Path, files: PickedTextFiles
) -> None:
    picked = tmp_path / "picked.json"
    other = tmp_path / "other.json"
    picked.write_text('{"a": 1}', encoding="utf-8")
    other.write_text("{}", encoding="utf-8")
    assert files.read_text(str(picked)) == ""
    files.allow_read(str(picked))
    assert files.read_text(str(picked)) == '{"a": 1}'
    assert files.read_text(str(other)) == ""
    assert files.read_text("") == ""


def test_a_page_writes_only_what_the_user_picked_to_save(
    tmp_path: Path, files: PickedTextFiles
) -> None:
    target = tmp_path / "export.json"
    assert files.write_text(str(target), "{}") is False
    assert not target.exists()
    files.allow_write(str(target))
    assert files.write_text(str(target), '{"é": "µ"}\n') is True
    assert target.read_bytes() == '{"é": "µ"}\n'.encode()
    assert files.write_text(str(tmp_path / "sibling.json"), "{}") is False
    assert not (tmp_path / "sibling.json").exists()


def test_a_path_picked_for_one_use_is_not_open_for_the_other(
    tmp_path: Path, files: PickedTextFiles
) -> None:
    opened = tmp_path / "opened.json"
    saved = tmp_path / "saved.json"
    opened.write_text("keep me", encoding="utf-8")
    saved.write_text("keep me too", encoding="utf-8")
    files.allow_read(str(opened))
    files.allow_write(str(saved))
    assert files.write_text(str(opened), "overwritten") is False
    assert files.read_text(str(saved)) == ""
    assert opened.read_text(encoding="utf-8") == "keep me"
    assert saved.read_text(encoding="utf-8") == "keep me too"


def test_a_picked_path_matches_however_the_page_spells_it(
    tmp_path: Path, files: PickedTextFiles
) -> None:
    (tmp_path / "sub").mkdir()
    target = tmp_path / "sub" / "doc.json"
    target.write_text("{}", encoding="utf-8")
    files.allow_read(str(target))
    detour = str(tmp_path / "sub" / ".." / "sub" / "doc.json")
    assert files.read_text(detour) == "{}"
    assert files.read_text(str(tmp_path / "sub" / ".." / "doc.json")) == ""


def test_reading_tolerates_a_byte_order_mark_and_refuses_what_is_not_text(
    tmp_path: Path, files: PickedTextFiles
) -> None:
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf{}")
    binary = tmp_path / "binary.json"
    binary.write_bytes(b"\xff\xfe\x00{")
    folder = tmp_path / "folder"
    folder.mkdir()
    for path in (bom, binary, folder, tmp_path / "missing.json"):
        files.allow_read(str(path))
    assert files.read_text(str(bom)) == "{}"
    assert files.read_text(str(binary)) == ""
    assert files.read_text(str(folder)) == ""
    assert files.read_text(str(tmp_path / "missing.json")) == ""


def test_text_over_the_limit_is_refused_both_ways(
    tmp_path: Path, files: PickedTextFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(textfiles, "MAX_TEXT_BYTES", 8)
    big = tmp_path / "big.json"
    big.write_text("123456789", encoding="utf-8")
    exact = tmp_path / "exact.json"
    exact.write_text("12345678", encoding="utf-8")
    out = tmp_path / "out.json"
    for path in (big, exact):
        files.allow_read(str(path))
    files.allow_write(str(out))
    assert files.read_text(str(big)) == ""
    assert files.read_text(str(exact)) == "12345678"
    assert files.write_text(str(out), "123456789") is False
    assert not out.exists()
    assert files.write_text(str(out), "12345678") is True


def test_a_failed_write_says_so(tmp_path: Path, files: PickedTextFiles) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    files.allow_write(str(folder))
    files.allow_write(str(tmp_path / "no_such_dir" / "out.json"))
    assert files.write_text(str(folder), "x") is False
    assert files.write_text(str(tmp_path / "no_such_dir" / "out.json"), "x") is False
    assert files.write_text(str(folder), "\ud800") is False  # a lone surrogate is not UTF-8


# ---------------------------------------------------------------------------------- launch theme
@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("STOKES_HOME", str(path))
    return path


def write_theme(folder: Path, theme: object) -> None:
    (folder / "workspace.json").write_text(
        json.dumps({"version": 1, "theme": theme}), encoding="utf-8"
    )


def test_without_a_workspace_file_the_theme_is_auto(home: Path) -> None:
    assert workspace_theme(Session()) == "auto"


@pytest.mark.parametrize("name", [t for t in THEMES if t != "auto"])
def test_a_built_in_theme_goes_by_its_name(home: Path, name: str) -> None:
    write_theme(home, {"name": name})
    assert workspace_theme(Session()) == name


def test_a_custom_theme_goes_by_its_base(home: Path) -> None:
    write_theme(
        home, {"base": "cupertino-dark", "title": "Night", "tokens": {"--accent": "#ff8a3d"}}
    )
    assert workspace_theme(Session()) == "cupertino-dark"


def test_a_workspace_file_that_cannot_be_read_leaves_the_choice_to_the_document(home: Path) -> None:
    (home / "workspace.json").write_text("{not json", encoding="utf-8")
    assert workspace_theme(Session()) == "auto"
    write_theme(home, {"name": "no-such-theme"})
    assert workspace_theme(Session()) == "auto"


def test_the_projects_own_theme_wins_over_the_users(home: Path, tmp_path: Path) -> None:
    write_theme(home, {"name": "light"})
    session = Session()
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    assert session.project_path is not None
    assert workspace_theme(session) == "light"
    write_theme(session.project_path, {"base": "cupertino", "tokens": {}})
    assert workspace_theme(session) == "cupertino"


# ------------------------------------------------ a chosen theme against the untouched default
def test_no_chosen_theme_reads_as_none_so_the_platform_default_can_apply(home: Path) -> None:
    """Nothing chosen: the caller, not this function, decides the family (Ubuntu on Linux)."""
    assert chosen_theme(Session()) is None
    assert workspace_theme(Session()) == "auto"


def test_an_explicitly_chosen_auto_is_not_the_untouched_default(home: Path) -> None:
    """The bug this guards: *Stokes + Auto* writes ``{"name": "auto"}``, exactly the fresh-install
    value. Only the workspace's ``source`` tells them apart; reading the value alone let the Linux
    fallback silently override a theme the user had picked."""
    assert chosen_theme(Session()) is None  # fresh: source is "default"
    write_theme(home, {"name": "auto"})  # what the Style picker writes for Stokes + Auto
    assert chosen_theme(Session()) == "auto"  # source is "user", so it is a real choice


def test_a_chosen_theme_survives_the_linux_default(home: Path) -> None:
    """A real choice must win on every platform, whatever ``default_theme`` would say."""
    write_theme(home, {"name": "auto"})
    assert chosen_theme(Session()) or default_theme() == "auto"


@pytest.mark.parametrize("name", [t for t in THEMES if t != "auto"])
def test_every_built_in_theme_is_a_chosen_theme(home: Path, name: str) -> None:
    write_theme(home, {"name": name})
    assert chosen_theme(Session()) == name
