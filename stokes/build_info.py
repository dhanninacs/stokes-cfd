"""Which build is this: the version, the commit, and whether the tree it ran from was dirty.

**Why it exists**: a review asked which build a running server serves, and the honest answer was "we
cannot tell" - `__version__` is a static string that changes only at a release, so two servers weeks
apart report the same thing. `cwd` plus the current `HEAD` is not proof either: the process may
have started from an older commit, or from a checkout that has moved since.

**Read once, at import**, because a process's identity is what it was when it started. Re-reading
later would report the tree's present state, which is the mistake this module exists to prevent.

**No git dependency**: the values are read from the checkout if one is present and otherwise left
empty. A server running from an installed package, or from a tarball, simply has no commit to
report - and says so rather than guessing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from stokes import __version__

#: How long the one `git` read may take. **A deadline, not a budget**: a hung `git` on a network
#: share must not delay a server's start-up, and an empty answer is better than a slow one.
_GIT_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class BuildInfo:
    """What a running process can say about itself."""

    version: str
    #: The commit the checkout was on, or "" when there is no checkout to ask.
    commit: str
    #: True when the checkout had uncommitted changes, which a commit alone would hide.
    dirty: bool

    def as_dict(self) -> dict[str, object]:
        return {"version": self.version, "commit": self.commit, "dirty": self.dirty}

    def label(self) -> str:
        """`0.0.1.dev0 @ 65d07c5` or `0.0.1.dev0 @ 65d07c5 +dirty`, for a log line or a header."""
        if self.commit == "":
            return self.version
        return f"{self.version} @ {self.commit}{' +dirty' if self.dirty else ''}"


def _git(root: Path, *args: str) -> str | None:
    """One `git` read, or None when git is absent, slow or the directory is not a checkout."""
    try:
        done = subprocess.run(  # noqa: S603 - argv list, fixed binary, no shell
            ["git", "-C", str(root), *args],  # noqa: S607 - `git` from PATH is the tool we mean
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


@cache
def build_info() -> BuildInfo:
    """This process's build information, read once.

    The repository root is the directory **two levels above this module**, which is where the
    package lives inside a checkout. Elsewhere there is no answer, and an empty commit says so.
    """
    root = Path(__file__).resolve().parent.parent
    commit = _git(root, "rev-parse", "--short=7", "HEAD") or ""
    # **A dirty tree is part of the identity**: "commit 65d07c5" would claim a state the process may
    # not have been running, and the difference is exactly what a review needs to know.
    status = _git(root, "status", "--porcelain") if commit != "" else None
    return BuildInfo(version=__version__, commit=commit, dirty=bool(status))
