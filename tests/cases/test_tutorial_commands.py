"""The `tutorial.*` commands through `Session.execute`, with journal replay (T-304)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from stokes.api import Session, replay
from stokes.api.journal import Journal
from stokes.errors import StokesError

from .synthetic import INVENTORY, build_tree, make_case


class FakeRunner:
    """A local host whose probe record carries the T-304 inventory."""

    name = "local"
    available = True
    why = ""

    def __init__(self, record: dict[str, Any] | None) -> None:
        self.record = record
        self.calls = 0

    def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
        assert method == "host.probe"
        self.calls += 1
        return self.record

    def close(self) -> None:
        return


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(folder))
    return folder


def _session(record: dict[str, Any] | None, journal: Path | None = None) -> Session:
    from stokes.run.hosts import HostRegistry

    session = Session(journal_path=journal)
    HostRegistry.of(session)._runners["local"] = FakeRunner(record)  # type: ignore[assignment]
    return session


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    out: dict[str, tuple[int, bytes | None]] = {}
    for folder, dirs, files in os.walk(root):
        out[folder] = (Path(folder).stat().st_mtime_ns, None)
        for name in files:
            path = Path(folder) / name
            out[str(path)] = (path.stat().st_mtime_ns, path.read_bytes())
        dirs.sort()
    return out


def test_catalogue_lists_the_install_root_and_never_writes_it(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "tutorials"
    expected = build_tree(root)
    before = _snapshot(root)
    session = _session({**INVENTORY, "tutorials_root": str(root)})
    reply = session.execute("tutorial.catalogue")
    assert reply["root"] == str(root.resolve())
    assert reply["probed"] is True
    assert {entry["id"]: entry["status"] for entry in reply["cases"]} == expected
    assert sum(reply["counts"].values()) == len(expected)
    assert _snapshot(root) == before, "tutorial.catalogue changed the folder it read"
    assert not (home / "tutorials.json").exists(), "a read wrote the user settings"


def test_catalogue_filters_and_an_explicit_root(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "t"
    build_tree(root)
    session = _session(None)
    reply = session.execute("tutorial.catalogue", root=str(root), category="legacy")
    assert [entry["id"] for entry in reply["cases"]] == ["legacy/potential"]
    assert reply["probed"] is False and reply["cases"][0]["status"] == "unverified"
    reply = session.execute("tutorial.catalogue", root=str(root), text="heater")
    assert [entry["id"] for entry in reply["cases"]] == ["multiRegion/heater"]


def test_totals_ignore_the_filter(tmp_path: Path, home: Path) -> None:
    # The Start card's header reads `totals`: a filter narrows `cases` and `counts`, never `totals`.
    root = tmp_path / "t"
    expected = build_tree(root)
    session = _session(None)
    reply = session.execute("tutorial.catalogue", root=str(root), text="heater")
    assert sum(reply["totals"].values()) == len(expected)
    assert sum(reply["counts"].values()) == 1


def test_catalogue_without_any_root_is_refused(home: Path) -> None:
    with pytest.raises(StokesError) as caught:
        _session(INVENTORY).execute("tutorial.catalogue")
    assert caught.value.code == "tutorial.no_root"


def test_roots_lists_the_install_folder_and_the_users(tmp_path: Path, home: Path) -> None:
    install = tmp_path / "install"
    make_case(install, "a/b")
    user = tmp_path / "mine"
    make_case(user, "x/y")
    session = _session({**INVENTORY, "tutorials_root": str(install)})
    reply = session.execute("tutorial.roots")
    assert reply == {
        "active": str(install),
        "user": None,
        "install": str(install),
        "roots": [{"path": str(install), "source": "install", "exists": True}],
    }
    reply = session.execute("tutorial.set_root", path=str(user))
    assert reply["active"] == str(user.resolve()) == reply["user"]
    assert [row["source"] for row in reply["roots"]] == ["user", "install"]
    assert (home / "tutorials.json").is_file(), "the folder is a user setting"
    assert session.project is None, "no project was needed or created"


def test_set_root_validation(tmp_path: Path, home: Path) -> None:
    session = _session(INVENTORY)
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "system").mkdir()
    cases = {
        str(tmp_path / "absent"): "tutorial.root_missing",
        str(tmp_path / "file.txt"): "tutorial.root_not_folder",
        str(tmp_path / "empty"): "tutorial.root_empty",
        "relative/folder": "tutorial.root_relative",
        "": "tutorial.root_invalid",
    }
    for path, code in cases.items():
        with pytest.raises(StokesError) as caught:
            session.execute("tutorial.set_root", path=path)
        assert caught.value.code == code, path
    assert not (home / "tutorials.json").exists(), "a refused folder was stored"
    with pytest.raises(StokesError) as caught:
        session.execute("tutorial.catalogue", root=str(tmp_path / "empty"))
    assert caught.value.code == "tutorial.root_empty"


def test_set_root_is_journaled_and_replays(tmp_path: Path, home: Path) -> None:
    user = tmp_path / "mine"
    make_case(user, "x/y")
    journal = tmp_path / "journal.jsonl"
    session = _session(None, journal)
    session.execute("tutorial.roots")
    session.execute("tutorial.catalogue", root=str(user))
    session.execute("tutorial.set_root", path=str(user))
    records = Journal.read(journal)
    assert [record["command"] for record in records] == ["tutorial.set_root"], (
        "only the setting change is journaled; reads are not"
    )
    session.execute("tutorial.set_root", path=None)
    assert session.execute("tutorial.roots")["user"] is None
    fresh = _session(None)
    replay(fresh, records)
    assert fresh.execute("tutorial.roots")["active"] == str(user.resolve())


def test_a_damaged_settings_file_reads_as_unset(tmp_path: Path, home: Path) -> None:
    from stokes.cases.roots import read_root, write_root

    home.mkdir(parents=True)
    target = home / "tutorials.json"
    target.write_text("{not json", encoding="utf-8")
    assert read_root() is None
    target.write_text('{"version": 99, "root": "/x"}', encoding="utf-8")
    assert read_root() is None
    write_root("/some/where")
    assert read_root() == "/some/where"
    assert read_root(tmp_path / "missing.json") is None


def test_settings_that_cannot_be_written_raise_a_typed_error(tmp_path: Path) -> None:
    from stokes.cases.roots import write_root

    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(StokesError) as caught:
        write_root("/x", blocker / "tutorials.json")
    assert caught.value.code == "tutorial.settings_write"


def test_the_commands_have_schemas() -> None:
    from stokes.api import default_registry

    schema = {entry["name"]: entry for entry in default_registry().json_schema()}
    assert schema["tutorial.catalogue"]["mutates"] is False
    assert schema["tutorial.roots"]["mutates"] is False
    assert schema["tutorial.set_root"]["mutates"] is True
    assert set(schema["tutorial.catalogue"]["input_schema"]["properties"]) == {
        "root",
        "category",
        "text",
    }
    assert schema["tutorial.set_root"]["input_schema"]["required"] == ["path"]
