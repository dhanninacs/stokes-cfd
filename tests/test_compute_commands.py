"""`compute.*` through Session.execute: the choice, the validation, and the Auto rule.

**The validation is the whole feature.** An install location becomes an environment script that the
agent sources, so `compute.add_install` is a boundary: a path that reaches the launcher must already
be a file we can read. Each refusal below is a way that could go wrong, and each is asserted with
the
words the user sees rather than only the code - because the message is what they act on.

**Every test runs against a private Stokes home** (`STOKES_HOME`), which is also what the workspace
store reads. One of my own probes wrote a `/tmp` install path into the real
`~/.stokes/workspace.json`
because it set `STOKES_USER_HOME` and not this, so the fixture here sets the one that matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.errors import StokesError

COMPUTE = "preferences.compute"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private Stokes home: the workspace store reads `$STOKES_HOME`, which isolates it."""
    path = tmp_path / "home"
    monkeypatch.setenv("STOKES_HOME", str(path))
    monkeypatch.setenv("STOKES_USER_HOME", str(path / "user"))
    return path


@pytest.fixture
def session(home: Path) -> Session:
    del home
    return Session()


def an_install(tmp_path: Path, name: str = "env.sh") -> Path:
    """A file that passes every rule: absolute, regular, readable."""
    script = tmp_path / name
    script.write_text("# an OpenFOAM environment script\n", encoding="utf-8")
    return script


def test_the_shipped_default_is_AUTO(session: Session) -> None:
    """**A user who never opens Compute still meshes.** Empty host means "choose for me", so
    `auto` is a stored flag rather than something inferred from an empty string - clearing a
    choice has to differ from never having made one.
    """
    reply = session.execute("compute.get")
    assert reply["auto"] is True
    assert reply["host"] == ""
    # And it resolves: the local host answers, and it has an install.
    assert reply["effective_host"] == "local"
    install = reply["effective_install"]
    # A sourceable environment script: bashrc or a .sh on Linux, the FoamWin .ps1 on Windows.
    assert "/etc/bashrc" in install or install.endswith((".sh", ".ps1"))


def test_setting_a_host_turns_auto_OFF_and_clearing_it_turns_it_back_on(
    session: Session, tmp_path: Path
) -> None:
    script = an_install(tmp_path)
    pinned = session.execute("compute.add_install", host="local", path=str(script))
    assert pinned["auto"] is False
    assert pinned["host"] == "local"
    assert pinned["install"] == str(script)

    cleared = session.execute("compute.set_default", host="")
    assert cleared["auto"] is True, "clearing the choice must mean Auto again"
    assert cleared["host"] == ""


def test_a_chosen_host_with_no_chosen_install_reports_its_DEFAULT(session: Session) -> None:
    """**Choosing a host must not leave the effective install blank.** The Settings dialog then
    showed the host's install row and a blank "effective" value at the same time - measured after
    choosing WSL. The host's own default is what a mesh uses, so the reply has to name it.
    """
    reply = session.execute("compute.set_default", host="local")
    assert reply["auto"] is False
    assert reply["install"] == ""
    if reply["installs"]:
        assert reply["effective_install"] == reply["installs"][0]["bashrc"]
        assert reply["effective_install"] != ""


def test_an_unknown_host_is_refused_and_the_refusal_NAMES_the_known_ones(session: Session) -> None:
    """**`compute` cannot introduce a machine.** Adding one is `host.trust`, with its fingerprint
    step; accepting a name here would be a second, weaker way in. The message lists what is known,
    so
    a user who mistyped can see the right spelling.
    """
    with pytest.raises(StokesError) as caught:
        session.execute("compute.set_default", host="not-a-machine")
    assert caught.value.code == "compute.unknown_host"
    assert "local" in str(caught.value), "the refusal does not say what IS known"


def test_an_install_belongs_to_a_host(session: Session, tmp_path: Path) -> None:
    """An install with no host has nowhere to be sourced, so it is refused rather than stored."""
    with pytest.raises(StokesError) as caught:
        session.execute("compute.set_default", install=str(an_install(tmp_path)))
    assert caught.value.code == "compute.install_needs_host"


@pytest.mark.parametrize(
    ("make", "why"),
    [
        (lambda tmp: "relative/env.sh", "absolute"),
        (lambda tmp: str(tmp / "nothing-here.sh"), "nothing"),
        (str, "folder"),
    ],
)
def test_a_bad_install_path_is_refused_IN_WORDS(
    session: Session, tmp_path: Path, make: object, why: str
) -> None:
    """Three of the four rules, each with the sentence a user reads.

    **The wording is the interface**: "the path must be absolute" tells somebody what to do, and a
    code does not. The fourth rule (a symlink that is not a file) needs a real link and is
    covered by its own test below.
    """
    path = make(tmp_path)  # type: ignore[operator]
    with pytest.raises(StokesError) as caught:
        session.execute("compute.add_install", host="local", path=path)
    assert caught.value.code == "compute.bad_install"
    assert why in str(caught.value).lower(), str(caught.value)


def _can_symlink() -> bool:
    """Whether this account may create symlinks (Windows needs a privilege or developer mode)."""
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "link").symlink_to(Path(folder) / "target")
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _can_symlink(), reason="symlinks need a privilege on this host")
def test_a_SYMLINK_that_is_not_a_file_is_refused(session: Session, tmp_path: Path) -> None:
    """The rule the staging whitelist already uses: *a link is not the thing it names.*

    A link named `env.sh` pointing at a directory would be sourced as the empty string, and one
    pointing at something private would become a way to read it. Checked rather than assumed - the
    path resolves, `exists()` is true, and only the link check refuses it.
    """
    target = tmp_path / "somewhere"
    target.mkdir()
    link = tmp_path / "env.sh"
    link.symlink_to(target)
    with pytest.raises(StokesError) as caught:
        session.execute("compute.add_install", host="local", path=str(link))
    assert caught.value.code == "compute.bad_install"
    assert (
        "folder" in str(caught.value).lower() or "not a regular file" in str(caught.value).lower()
    )


def test_a_bad_path_is_REFUSED_and_NOT_stored(session: Session, tmp_path: Path) -> None:
    """**The refusal has to leave the document alone.** Recording a path and failing later at the
    first mesh would move the failure to wherever the user is not looking - and a stored bad path
    would survive the session that produced it.
    """
    before = session.execute("compute.get")
    with pytest.raises(StokesError):
        session.execute("compute.add_install", host="local", path="/nowhere/at/all.sh")
    after = session.execute("compute.get")
    assert after["host"] == before["host"]
    assert after["install"] == before["install"]
    assert after["auto"] == before["auto"]


def test_compute_test_reports_what_a_user_needs(session: Session) -> None:
    """The reply has the version, the cores and the answer - not a status code."""
    reply = session.execute("compute.test")
    assert "ok" in reply and "why" in reply
    assert reply["cores"] is None or reply["cores"] >= 1
    assert isinstance(reply["installs"], list)


def test_testing_an_install_that_is_NOT_there_says_so(session: Session, tmp_path: Path) -> None:
    """A test of something absent is a failure with a reason, never an empty answer: the caller
    asked whether a specific thing works, and "no" is the answer.
    """
    reply = session.execute("compute.test", host="local", install=str(tmp_path / "gone.sh"))
    assert reply["ok"] is False
    assert str(tmp_path / "gone.sh") in reply["why"]


def test_the_choice_is_USER_scope_and_a_project_cannot_override_it(
    session: Session, tmp_path: Path
) -> None:
    """**A project must not decide which machine it runs on.** Opening somebody else's project would
    otherwise point your meshes at their host - which is a security question, not a preference one.
    `_stored` reads and writes with `project_dir=None`, so a project's overrides are never
    consulted.
    """
    project = tmp_path / "proj"
    session.execute("project.new", name="proj", path=str(project))
    script = an_install(tmp_path)
    session.execute("compute.add_install", host="local", path=str(script))

    # A project-scope write is refused: `preferences` is user-only in the workspace schema.
    with pytest.raises(StokesError):
        session.execute(
            "workspace.set",
            path=COMPUTE + ".install",
            value="/somewhere/else.sh",
            scope="project",
        )
    assert session.execute("compute.get")["install"] == str(script)


def test_the_default_host_resolves_an_empty_project_host(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The Execution field is empty by default**, and this is what fills it at use time.

    The rule matches `compute.get`: an explicit choice wins even when it is not answering (the run
    is then `skipped` with that reason), and Auto takes the first host that does. Nothing is
    written into the project: the choice belongs in Settings, and resolving it when a mesh starts
    means a project opened elsewhere does not have to rewrite its document.
    """
    from stokes.api.commands import compute
    from stokes.api.registry import CommandContext

    class Registry:
        def runner(self, name: str) -> object:
            return type("Runner", (), {"available": False})()

        def hosts(self) -> list[dict[str, object]]:
            return [
                {"name": "local", "available": False},
                {"name": "wsl:Ubuntu-24.04", "available": True},
            ]

        def probe(self, host: str, refresh: bool = False) -> dict[str, object]:
            return {"host": host, "available": True, "why": "", "record": {}}

    monkeypatch.setattr(compute, "_registry", lambda ctx: Registry())
    ctx = CommandContext(session)
    assert compute.default_host(ctx) == "wsl:Ubuntu-24.04"

    # An explicit host is the project's answer even if that host is down: the run says `skipped`
    # with the reason, rather than silently going somewhere else.
    session.execute("compute.set_default", host="local")
    assert compute.default_host(ctx) == "local"


def test_the_default_host_is_local_when_nothing_answers(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name for the result when no host is available: the last resort, not an empty string."""
    from stokes.api.commands import compute
    from stokes.api.registry import CommandContext

    class Registry:
        def runner(self, name: str) -> object:
            return type("Runner", (), {"available": False})()

        def hosts(self) -> list[dict[str, object]]:
            return [{"name": "local", "available": False}]

    monkeypatch.setattr(compute, "_registry", lambda ctx: Registry())
    assert compute.default_host(CommandContext(session)) == "local"


def test_an_install_the_user_chose_is_marked_USER_ADDED(session: Session) -> None:
    """Claude's 1630 checklist item 1: the added install shows in the list as "added by you".

    before = {
        row["bashrc"]: row.get("found_by") for row in session.execute("compute.get")["installs"]
    }
    a machine, not a preference. The test uses the install the scan FOUND and marks it as chosen, so
    it also pins the rule that being chosen wins over being discovered - a path the user picked is
    their choice even though a glob would have found it anyway.
    """
    found = session.execute("compute.get")["effective_install"]
    assert found, "this host has no install to choose; the test cannot run"

    before = {
        row["bashrc"]: row.get("found_by") for row in session.execute("compute.get")["installs"]
    }
    assert before.get(found) != "user-added", "the install was already marked before being chosen"

    session.execute("compute.add_install", host="local", path=found, select=True)
    after = {
        row["bashrc"]: row.get("found_by") for row in session.execute("compute.get")["installs"]
    }
    assert after[found] == "user-added"


def test_an_install_is_only_marked_when_it_is_the_CHOSEN_one(
    session: Session, tmp_path: Path
) -> None:
    """The label says what the user did, so an unchosen install keeps the source the scan gave it.

    **A label that marks everything is decoration.** With Auto in effect and nothing chosen, no row
    may say "added by you" - otherwise the word stops distinguishing anything.
    """
    reply = session.execute("compute.get")
    assert reply["auto"] is True
    for row in reply["installs"]:
        assert row.get("found_by") != "user-added", row
