"""No test may write into the developer's real `~/.stokes`.

**This exists because one did.** `project.new` now always creates a project folder, `STOKES_HOME`
defaults to `~/.stokes`, and 55 tests set the variable for themselves - so the tests that *did not*
wrote into the real folder. Measured: the mesh fixture created a project there, and it surfaced
extra folder in a Save-as assertion rather than as anything that looked like a mistake.

`conftest._isolated_stokes_home` points the variable somewhere temporary for the whole run. **This
file is what makes that guard checkable**: a test that unsets or overrides it back fails here in
of quietly leaving files in the home of whoever ran the suite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _real_home() -> Path:
    """Where the real Stokes folder would be, **ignoring our own redirection**."""
    return Path.home() / ".stokes"


def test_STOKES_HOME_is_redirected_for_the_whole_run() -> None:
    """The guard itself. If this fails, every test below it is meaningless."""
    redirected = os.environ.get("STOKES_HOME")
    assert redirected, "STOKES_HOME is not set: the session fixture did not run"
    assert Path(redirected) != _real_home(), "STOKES_HOME points at the real folder"
    # **And the code that reads it agrees**, which is the part that matters - a variable nobody
    # consults would pass the assertion above.
    from stokes.workspace.merge import stokes_home

    assert stokes_home() == Path(redirected)


def test_the_AGENT_state_folder_is_redirected_too() -> None:
    """The agent writes a secret and a session record; a test that started one left them in the
    real folder, which is the same mistake one process further out."""
    redirected = os.environ.get("STOKES_AGENT_STATE_DIR")
    assert redirected, "STOKES_AGENT_STATE_DIR is not set"
    assert not Path(redirected).is_relative_to(_real_home())


def test_a_NEW_PROJECT_lands_under_the_redirected_home() -> None:
    """**The behaviour the guard protects**, asserted end to end: creating a project with no path
    puts it in the temporary home and **nowhere near the real one**.

    This is the case that broke - `project.new` without a `path` used to make a folder-less project,
    so it wrote nothing; now it creates one, and the default location is the Stokes home.
    """
    from stokes.api import Session

    session = Session()
    session.execute("project.new", name="guarded")
    folder = Path(session.project_path)
    # **The VISIBLE root is where a project goes now**, so that is the one to check - and it is the
    # more dangerous of the two, because it is the user's real Documents folder rather than a
    # dot-folder. The app root is checked as well, so a redirect that set only one of them fails
    # here rather than in whichever test notices first.
    #
    # A sibling test that sets the variable and does not restore it leaves a stale value
    # behind, and asserting against that failed on correct code - measured. The resolver is
    # what the code under test consults, so agreeing with it is the real assertion.
    from stokes.workspace_root import user_root

    root = user_root()
    assert str(root) != str(_real_home()), "the visible root points at the real home"
    assert folder.is_relative_to(root), folder
    assert not folder.is_relative_to(_real_home()), folder


def test_the_real_home_listing_is_UNCHANGED_across_a_project_being_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Measured, not assumed.** The session guard redirects `STOKES_HOME`; this checks that with
    the redirection REMOVED a project would have gone to the real folder - so the guard is doing
    something - and that with it in place nothing appears there.

    A listing comparison is what makes this a real assertion: my first version compared a
    `stat` value with itself and would have passed whatever happened.

    **The real folder is read, never written**, and the comparison is of the set of names, so a
    developer with a populated `~/.stokes` is not failed for having one.
    """
    real = _real_home()

    def listing() -> set[str]:
        if not real.is_dir():
            return set()
        return {entry.name for entry in real.iterdir()}

    before = listing()
    from stokes.api import Session

    for number in range(3):
        session = Session()
        session.execute("project.new", name=f"guarded-{number}")
        # **Both roots, and through the resolvers** - the project goes to the VISIBLE one since
        # 2026-10-17, and `os.environ` can hold a stale value left by a sibling test.
        from stokes.workspace_root import home, user_root

        folder = Path(session.project_path)
        assert folder.is_relative_to(user_root()) or folder.is_relative_to(home()), folder
        assert not folder.is_relative_to(real), folder
    assert listing() == before, (
        f"this run changed the real {real}: {sorted(listing() - before)} appeared"
    )


def test_without_the_redirect_a_project_would_go_to_the_REAL_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The guard is load-bearing, and this proves it rather than asserting it.**

    With `STOKES_HOME` removed - which is what the tests that broke had effectively done by never
    setting it - `stokes_home()` falls back to the real `~/.stokes`. So the session fixture is not
    decoration: without it, `project.new` creates a folder in the developer's home.

    **Nothing is created here**: the assertion is on where the path POINTS, not on making it, so the
    real folder is not touched by the test that exists to protect it.
    """
    from stokes.workspace.merge import stokes_home

    monkeypatch.delenv("STOKES_HOME", raising=False)
    assert stokes_home() == _real_home(), (
        "the fallback is not the real home, so the session guard would be unnecessary"
    )
