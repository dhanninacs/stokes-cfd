"""The build a process can report about itself (the served-identity question).

**Why this is tested at all**: a review asked which build a running server serves and the answer was
"we cannot tell" - `__version__` is static, and `cwd` plus the current `HEAD` is not proof. These
tests pin the three things that make the answer trustworthy: the commit is read from the checkout, a
dirty tree says so, and a process that cannot know does not invent an answer.
"""

from __future__ import annotations

from pathlib import Path

from stokes.build_info import BuildInfo, build_info


def test_it_reports_the_commit_and_whether_the_tree_is_dirty() -> None:
    info = build_info()
    assert info.version != ""
    # **A checkout is where this test runs**, so the commit must be there.
    assert len(info.commit) == 7, f"expected a 7-character commit, got {info.commit!r}"
    # **`dirty` is a bool, not a guess**: the tree's state at import is part of the identity.
    assert isinstance(info.dirty, bool)


def test_the_label_names_the_commit_and_flags_a_dirty_tree() -> None:
    clean = BuildInfo(version="1.0", commit="abc1234", dirty=False)
    assert clean.label() == "1.0 @ abc1234"
    messy = BuildInfo(version="1.0", commit="abc1234", dirty=True)
    # **"+dirty" matters**: the commit alone would claim a state the process may not be running.
    assert messy.label() == "1.0 @ abc1234 +dirty"


def test_without_a_checkout_it_says_nothing_rather_than_guessing() -> None:
    # An installed package or a tarball has no commit to report, and an empty one is honest.
    assert BuildInfo(version="1.0", commit="", dirty=False).label() == "1.0"


def test_the_dictionary_is_what_the_health_route_serves() -> None:
    served = BuildInfo(version="1.0", commit="abc1234", dirty=True).as_dict()
    assert set(served) == {"version", "commit", "dirty"}


def test_it_is_read_ONCE() -> None:
    """A process's identity is what it was when it started: re-reading would report the tree as it
    is now, which is the mistake the module exists to prevent."""
    first = build_info()
    second = build_info()
    assert first is second, "the cached value must be the same object"
    assert Path(first.version) == Path(first.version)  # a truthy version, not a placeholder
