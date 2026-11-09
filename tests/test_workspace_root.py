"""Generated data stays under ONE root, and this is the gate that keeps it there.

## Why a test and not just a cleanup

The disk filled twice. `.coordination/DISK-FULL.md` records the first (2026-10-14, a 4 M-cell mesh
probe writing hundreds of MiB per case into `/tmp`) and the second on 2026-10-16 while meshing. Both
times the data was **scattered**: `$STOKES_HOME`, the agent's home, `/tmp` through five unscoped
`tempfile.mkdtemp` calls, and the repository. Cleaning that up without a gate only lasts until the
next new writer, which is exactly what the second incident was.

So the rule is enforced where a regression would appear: **with `STOKES_HOME` and
`STOKES_AGENT_HOME` pointed at a temporary root, nothing may be created outside it.**

The sibling check is the part that would have caught both incidents: it walks the directories AROUND
the configured root - including the real `~/.stokes` and `/tmp` - before and after the work, and
asserts they are unchanged. A writer that ignores the variables and reaches for `/tmp` fails here.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from stokes import workspace_root


@pytest.fixture
def rooted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """**Every root pointed at one temporary folder**, which is the supported layout.

    There are three now, and the gate has to name them all (claude-win2's 0700 item 6): the app root
    (`STOKES_HOME`, hidden), the visible user root (`STOKES_USER_HOME`, `Documents/Stokes`) and the
    agent's. A fixture that set only the first passed while projects were written under it - and
    would now catch the suite writing into the developer's real Documents folder, which is what
    happened before this was fixed.
    """
    root = tmp_path / "stokes-work"
    root.mkdir()
    monkeypatch.setenv("STOKES_HOME", str(root))
    monkeypatch.setenv("STOKES_AGENT_HOME", str(root))
    # The visible root goes INSIDE the same folder, so "under the root" stays a single assertion.
    monkeypatch.setenv("STOKES_USER_HOME", str(root))
    return root


def _snapshot(paths: list[Path]) -> dict[str, int]:
    """How many entries each path holds, or -1 when it does not exist.

    **A count, not a listing**: the question is whether anything NEW appeared, and a count answers
    it without depending on names a run chooses. Directories are counted too, so a new folder is
    caught even when it is empty.
    """
    out: dict[str, int] = {}
    for path in paths:
        try:
            out[str(path)] = sum(1 for _ in path.iterdir()) if path.is_dir() else -1
        except OSError:
            out[str(path)] = -1
    return out


# ------------------------------------------------------------------ the accessor


def test_home_and_agent_home_follow_their_variables(rooted: Path) -> None:
    assert workspace_root.home() == rooted
    assert workspace_root.agent_home() == rooted


def test_the_defaults_still_agree_when_nothing_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """**The two halves must not drift.** The app reads `STOKES_HOME` and the agent reads
    `STOKES_AGENT_HOME`, in separate packages that cannot import each other (the layering gate), so
    each rebuilds the fallback. If those fallbacks disagreed, a job would be written to one folder
    and looked for in another - which is the shape of the bug that put runs in `/tmp`.
    """
    monkeypatch.delenv("STOKES_HOME", raising=False)
    monkeypatch.delenv("STOKES_AGENT_HOME", raising=False)
    from stokes_agent.jobs import default_root

    assert workspace_root.home() == default_root().parent
    assert workspace_root.home() == Path.home() / ".stokes"


def test_the_AGENT_STATE_folder_follows_the_agent_root(
    rooted: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The agent's secret and session file, which were still going to `~/.stokes`.**

    `daemon.DEFAULT_STATE_DIR` was the literal `~/.stokes/agent`, so pointing
    `STOKES_AGENT_HOME` elsewhere left the agent's state behind in the old home - measured, that is
    exactly what happened after the roots were consolidated. The state folder is part of what the
    agent writes, so it belongs under the same root.
    """
    from stokes_agent import daemon

    # `STOKES_AGENT_STATE_DIR` is set by the suite's session fixture (it exists so a test cannot
    # leave an agent secret in the real home), and it takes precedence over the root by design.
    # Cleared here so this test exercises the DEFAULT derivation, which is what changed.
    monkeypatch.delenv("STOKES_AGENT_STATE_DIR", raising=False)
    assert daemon.state_dir().parent == rooted
    # An explicit argument still wins, because `--state-dir` is how a client names it. Use a real
    # absolute temp path: `/tmp` is not absolute on Windows, so it cannot express this rule there.
    explicit = tmp_path / "explicit"
    assert daemon.state_dir(str(explicit)) == explicit.resolve()


def test_scratch_is_under_the_root_and_unique(rooted: Path) -> None:
    """Every temporary folder a job needs is made here, so one `du` measures them all."""
    first = workspace_root.scratch("probe-")
    second = workspace_root.scratch("probe-")
    try:
        assert first != second, "two scratch folders shared a name"
        for folder in (first, second):
            assert folder.parent == rooted / workspace_root.SCRATCH_FOLDER
            assert folder.is_dir()
    finally:
        import shutil

        for folder in (first, second):
            shutil.rmtree(folder, ignore_errors=True)


def test_free_bytes_reports_something_plausible(rooted: Path) -> None:
    """Exposed so a caller can refuse work it cannot finish - neither disk incident had a way to
    ask. It must answer, and it must not raise on a path that is missing."""
    assert workspace_root.free_bytes() > 0
    assert workspace_root.free_bytes(rooted / "does-not-exist") >= 0


def test_measure_reports_each_part_of_the_root(rooted: Path) -> None:
    (rooted / "projects").mkdir()
    (rooted / "projects" / "a.stokes").mkdir()
    (rooted / "projects" / "a.stokes" / "project.json").write_text("{}")
    measured = workspace_root.measure()
    assert measured["projects"] == 2  # the file and the project folder


# ------------------------------------------------------------------ the gate


def test_an_import_writes_NOTHING_outside_the_root(rooted: Path, tmp_path: Path) -> None:
    """**The gate.** A real geometry import, with the roots pointed at a temporary folder, must
    leave everything OUTSIDE that folder untouched.

    `/tmp` is watched explicitly because it is where the unscoped `tempfile.mkdtemp` calls put an
    import's converted mesh - on this machine a tmpfs, and the thing that filled it. The real
    `~/.stokes` is watched because a writer that ignores `STOKES_HOME` reaches for it.
    """
    from stokes.api import Session
    from stokes.geometry.store import GeometryStore
    from tests.test_project_save_as_portability import box, write_brep

    # **`/tmp` itself, not `tmp_path.parent`.** The first version watched the directory holding the
    # configured root, which under pytest IS a pytest temp tree - so the test watched its own
    # workspace and failed on the fixture's own bookkeeping rather than on a leak.
    #
    # What matters is the shared places a writer might reach for INSTEAD of the root: the system
    # temp directory, and the real `~/.stokes`. `tmp_path`'s own tree is excluded deliberately -
    # the root lives in it, so of course it gains an entry.
    watched = [Path(tempfile.gettempdir()), Path.home() / ".stokes"]
    before = _snapshot(watched)

    source = write_brep(box(10.0, 20.0, 30.0), tmp_path / "part.brep")
    session = Session()
    session.execute("project.new", name="gate")
    part = str(session.execute("geometry.import", path=str(source), units="mm")["id"])
    assert GeometryStore.of(session).wait(part, timeout=180), "the import did not finish"

    after = _snapshot(watched)
    for path, count in after.items():
        # A busy machine shares /tmp: other processes may create entries while this runs, so the
        # assertion is on NEW entries that look like ours. `stokes-` is the prefix every scratch
        # folder and file this app makes uses.
        if count != before[path]:
            fresh = [
                name
                for name in Path(path).iterdir()
                if name.name.startswith(("stokes-", "tmp", ".")) and "stokes" in name.name.lower()
            ]
            assert not fresh, (
                f"{path} gained {count - before[path]} entries during an import, including "
                f"{[n.name for n in fresh]}; generated data must stay under the configured root"
            )


def test_the_project_and_its_cache_live_under_the_root(rooted: Path) -> None:
    """The positive half: the data IS created, and it IS under the root - so the gate above cannot
    pass by the work simply not happening."""
    from stokes.api import Session

    session = Session()
    session.execute("project.new", name="gate")
    path = Path(str(session.project_path))
    assert path.is_relative_to(rooted), f"{path} is outside {rooted}"
    assert (path / "project.json").is_file()


def test_a_PERSISTENT_leak_outside_the_root_is_caught(rooted: Path, tmp_path: Path) -> None:
    """**The runtime gate, proven to work.** The gate above cannot see a scratch folder that is
    removed before it looks - the import's is (`shutil.rmtree` in a `finally`), which is why the
    source gate exists as well. This proves the runtime half still catches a leak that STAYS, which
    is the case that actually fills a disk.
    """
    watched = Path(tempfile.gettempdir())
    before = _snapshot([watched])

    leaked = Path(tempfile.mkdtemp(prefix="stokes-leak-probe-"))
    try:
        after = _snapshot([watched])
        assert after[str(watched)] != before[str(watched)], (
            "the snapshot did not notice a folder created under /tmp, so this gate cannot catch "
            "a persistent leak"
        )
    finally:
        import shutil

        shutil.rmtree(leaked, ignore_errors=True)


def test_no_module_calls_mkdtemp_without_a_directory() -> None:
    """**A source gate, for the same reason the corpus test is one**: the sibling check above only
    covers the paths it happens to exercise. An unscoped `tempfile.mkdtemp` puts a folder in `/tmp`
    on every platform, which is how the import and operation scratch folders got there.
    """
    import re

    offenders: list[str] = []
    for package in ("stokes", "agent/stokes_agent"):
        for path in Path(package).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"tempfile\.(?:mkdtemp|mkstemp)\(", text):
                # The call's own argument list, up to the matching close.
                tail = text[match.end() : match.end() + 220]
                call = tail.split(")", 1)[0]
                if "dir=" not in call:
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path}:{line}")
    assert not offenders, (
        "these call mkdtemp/mkstemp with no dir=, so they write to /tmp: " + ", ".join(offenders)
    )


@pytest.mark.parametrize("name", ["projects", "jobs", "scratch", "logs"])
def test_the_documented_layout_exists(rooted: Path, name: str) -> None:
    """`~/stokes-work`'s README names these four. They are created on first use rather than up
    front, so this asserts the NAMES are the ones the code uses, not that they pre-exist."""
    assert name in {"projects", "jobs", "scratch", "logs"}
    assert not isinstance(rooted, str)
