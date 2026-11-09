"""Shared fixtures. Only synthetic data; no engines, no display."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from stokes.api import Session


@pytest.fixture(scope="session", autouse=True)
def _isolated_stokes_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Point `STOKES_HOME` at a temporary folder for **every** test in the run.

    **Why session-scoped and autouse, rather than per test** (claude-win2, reading 1,
    requirement 2): `project.new` now always creates a folder, so **any** test that creates a
    project writes into the Stokes home unless something redirects it. 55 places set
    `STOKES_HOME` for themselves, and the ones that did not were the ones that broke - measured:
    the mesh fixture created a project in the **developer's real `~/.stokes`**, which showed up
    as an extra folder in a Save-as assertion.

    A guard that depends on each test remembering is not a guard. This sets it once, for the
    process, and `tests/test_no_real_home_writes.py` asserts the real folder is untouched - so a
    test that unsets the variable fails rather than quietly writing where it should not.

    **`STOKES_AGENT_STATE_DIR` too**, for the same reason at one remove: the agent writes a
    secret and a session record there, and a test that started one left them in the real folder.
    """
    folder = tmp_path_factory.mktemp("stokes-home")
    os.environ["STOKES_HOME"] = str(folder)
    os.environ["STOKES_AGENT_STATE_DIR"] = str(folder / "agent")
    # **`STOKES_USER_HOME` too, since 2026-10-17.** The visible root - where projects and sample
    # copies now go - is `<Documents>/Stokes`, and Documents is the USER'S OWN directory. Without
    # this the suite would create real projects in the developer's Documents folder: measured, a
    # gate test failed because a project it created was outside its temporary root, which is the
    # guard working. One line here keeps every test in its temp root, and it is the same argument as
    # the comment above: a guard that depends on each test remembering is not a guard.
    os.environ["STOKES_USER_HOME"] = str(folder / "user")
    return folder


@pytest.fixture
def session(tmp_path: Path) -> Session:
    """A session with a fresh project saved under ``tmp_path``."""
    s = Session(journal_path=tmp_path / "journal.jsonl")
    s.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    return s


@pytest.fixture
def velocity_parameter(session: Session) -> Session:
    session.execute("parameter.define", name="U_in", dimension="velocity", value="2 [m/s]")
    session.execute("parameter.define", name="rho", dimension="density", value="1.2 [kg/m**3]")
    return session


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip every `engine` test unless `STOKES_ENGINE_TESTS=1` (testing.md promises this hook).

    Engine tests drive real OpenFOAM, which is only present on a machine that has it installed.
    Without the hook they would fail on a laptop and in CI rather than skip, and a failing gate that
    is really a missing dependency teaches people to ignore the gate.
    """
    _skip_what_this_host_cannot_test(items)
    if os.environ.get("STOKES_ENGINE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="engine test: set STOKES_ENGINE_TESTS=1 to run")
    for item in items:
        if "engine" in item.keywords:
            item.add_marker(skip)


#: Tests whose subject is a POSIX file mode or the OpenSSH tunnel, which Windows does not have.
POSIX_ONLY: tuple[str, ...] = (
    "owner_only",
    "test_ssh_host_trust.py",
    "test_the_option_list_pins_everything_a_config_could_choose",
)


def _can_symlink() -> bool:
    """Whether this account may create symlinks (Windows needs a privilege or developer mode)."""
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "link").symlink_to(Path(folder) / "target")
    except OSError:
        return False
    return True


def _skip_what_this_host_cannot_test(items: list[pytest.Item]) -> None:
    """Skip, with the reason, the tests that need a POSIX mode, an OpenSSH tunnel or a symlink.

    Each of them tests a guard that exists for those things, so on a host without them there is
    nothing to measure; the reason names the missing capability rather than failing the gate.
    """
    symlinks = _can_symlink()
    for item in items:
        name = item.nodeid.lower()
        if os.name == "nt" and any(word.lower() in name for word in POSIX_ONLY):
            item.add_marker(
                pytest.mark.skip(reason="needs POSIX file modes or OpenSSH (not Windows)")
            )
        elif not symlinks and (
            "symlink" in name or "a_link" in name or "escapes_the_parent" in name
        ):
            item.add_marker(pytest.mark.skip(reason="symlinks need a privilege on this host"))
