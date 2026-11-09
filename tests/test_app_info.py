"""`app.info` and `app.notices`: what the About page reads (T-177).

**Two rules shape these, and both are asserted rather than intended.**

`app.info` must not leak the machine or the user. `platform.platform()` is the OS and its version;
`platform.node()` is the machine's own name, which is exactly what the IP gate exists to keep out
of a reply. The paths ARE the user's and are shown to their owner - on Windows they carry the login
name - so the test allows a path to be a path and checks the non-path keys strictly.
"""

from __future__ import annotations

import getpass
import json
import platform
from pathlib import Path
from typing import Any

import pytest

from stokes import __version__
from stokes.api import Session
from stokes.api.commands import info as info_module
from stokes.errors import StokesError


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    return Session()


def test_app_info_names_the_version_the_home_and_the_platform(session: Session) -> None:
    reply = session.execute("app.info")
    assert reply["version"] == __version__
    assert reply["python"] == platform.python_version()
    assert reply["platform"] == platform.platform()
    # **The home is under STOKES_HOME**, which this test sets - so the resolver is the one in use.
    assert Path(reply["home"]).is_absolute()
    assert reply["home"] == str(Path(reply["home"]))
    assert reply["home"].startswith(str(Path(__import__("os").environ["STOKES_HOME"])))
    assert reply["workspace"]["user"] == str(Path(reply["home"]) / "workspace.json")
    # No project is open, so there is no project file - `null`, not a guess.
    assert reply["workspace"]["project"] is None


def test_app_info_does_not_leak_the_machine_or_the_user(session: Session) -> None:
    """**The security-relevant half.** `platform.node()` is the machine's name and the brief forbids
    it; `getpass.getuser()` is used rather than `os.getlogin()`, which raises without a console.

    Paths are allowed to contain the user - they are the user's own - so the check is on the keys
    that are FACTS ABOUT THE MACHINE rather than locations in it.
    """
    reply = session.execute("app.info")
    node = platform.node()
    user = getpass.getuser()
    assert "hosted" not in reply, (
        "`hosted` was reported, but nothing sets a session flag for it - `stokes/app/host.py` has "
        "a frameless window option and no session state, so any value here would be invented"
    )
    non_path_keys = ("version", "python", "platform")
    for key in non_path_keys:
        value = str(reply[key])
        assert node not in value, f"{key} contains the machine name"
        assert user not in value, f"{key} contains the user name"
    # And nothing anywhere dumped the environment.
    text = json.dumps(reply)
    assert "PATH" not in text and "HOME=" not in text


def test_app_notices_returns_the_notices_and_says_where_from(session: Session) -> None:
    reply = session.execute("app.notices")
    assert reply["source"] in ("packaged", "repository")
    text = reply["text"]
    assert text, "the notices are empty"
    assert text.lstrip().startswith("#"), "the notices are Markdown and start with a heading"
    # **A dependency the project actually uses**, so this is the real file and not any text at all.
    listed = Path("docs/engineering/dependencies.md").read_text(encoding="utf-8")
    for name in ("three", "pint"):
        assert name in listed, f"{name} is not in the dependency list; pick another"
        assert name.lower() in text.lower(), f"the notices do not name {name}"


def test_app_notices_prefers_the_packaged_copy(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Packaged first, repository second.** A wheel has no checkout, so the packaged file is what
    a shipped build reads; a development tree has no packaged file and falls back.

    `source` is asserted because it is the only way to tell which answered - the two files are
    identical by construction, so the text alone cannot distinguish them.
    """
    packaged = tmp_path / "assets" / "THIRD-PARTY-NOTICES.md"
    packaged.parent.mkdir(parents=True)
    packaged.write_text("# packaged notices\n\nthree\n", encoding="utf-8")
    repository = tmp_path / "repo" / "THIRD-PARTY-NOTICES.md"
    repository.parent.mkdir(parents=True)
    repository.write_text("# repository notices\n\npint\n", encoding="utf-8")
    monkeypatch.setattr(info_module, "_notices_paths", lambda: (packaged, repository))
    reply = session.execute("app.notices")
    assert reply["source"] == "packaged"
    assert "packaged notices" in reply["text"]

    packaged.unlink()
    reply = session.execute("app.notices")
    assert reply["source"] == "repository"
    assert "repository notices" in reply["text"]


def test_app_notices_missing_in_both_places_names_both(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The About page shows this message instead of a blank**, so it must name both paths - a
    reader has to know what to restore."""
    packaged = tmp_path / "assets" / "THIRD-PARTY-NOTICES.md"
    repository = tmp_path / "repo" / "THIRD-PARTY-NOTICES.md"
    monkeypatch.setattr(info_module, "_notices_paths", lambda: (packaged, repository))
    with pytest.raises(StokesError) as caught:
        session.execute("app.notices")
    assert caught.value.code == "app.notices_missing"
    message = str(caught.value)
    assert str(packaged) in message and str(repository) in message, message


def test_both_commands_are_reads_and_are_listed_that_way(tmp_path: Path) -> None:
    """**Rule 4 of CLAUDE.md**: an API command with a schema before it has a widget. Both are pure
    reads, so neither writes a journal record and neither needs a project.

    The journal is the proof of `mutates=False`: `Journal.read` shows what was recorded, and a read
    that recorded anything would be a command whose replay does not reproduce the session.
    """
    from stokes.api import Journal

    journal = tmp_path / "journal.jsonl"
    session = Session(journal_path=journal)
    # `schema.commands` answers the LIST itself, not a dict wrapping one - measured.
    listed: dict[str, Any] = {
        command["name"]: command for command in session.execute("schema.commands")
    }
    for name in ("app.info", "app.notices"):
        assert name in listed, f"{name} is not registered"
        assert listed[name]["mutates"] is False
        params = listed[name].get("params") or []
        assert [p for p in params if p.get("required")] == [], params

    session.execute("app.info")
    session.execute("app.notices")
    # **No file at all is the strongest form of "nothing was recorded"**: the journal is created on
    # the first write, so two reads leaving no file means neither wrote.
    assert not journal.exists() or [r["command"] for r in Journal.read(journal)] == [], (
        "a read wrote a journal record"
    )


def test_the_no_openfoam_message_says_what_to_do() -> None:
    """**claude-win2's item 3.** "Skipped: the host has no OpenFOAM install" reads as a hardware
    fault and leaves the reader with nothing to act on.

    The commonest cause is not a missing install at all: the agent discovers standard prefixes,
    and a **user-space install** (a tarball under `~/opt`, a normal way to have one) is
    invisible to it. Measured on this box: with `STOKES_OPENFOAM_BASHRC` unset,
    `bashrc_candidates()` is `[]`; with it set the install is found. So the message names it.
    """
    from stokes.mesh.errors import NO_INSTALL_ADVICE

    assert "STOKES_OPENFOAM_BASHRC" in NO_INSTALL_ADVICE, "the message does not name the variable"
    assert "standard prefix" in NO_INSTALL_ADVICE
    # It still says what is wrong, so it is a message and not only advice.
    assert NO_INSTALL_ADVICE.startswith("the host has no OpenFOAM install")
