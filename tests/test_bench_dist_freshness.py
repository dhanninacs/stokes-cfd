"""``tools/bench_scene.py`` must refuse to measure a ``web/dist`` that predates ``web/src``.

The defect this covers: the tool drives whatever bundle sits in ``web/dist``, which is gitignored,
so a build of a rejected attempt was measured while ``git status`` was clean and its numbers were
published for code that was never in the tree. ``dist_freshness`` is the pure function that makes
that state visible, and ``measure`` refuses at t=0 on the proof it returns.

Everything here runs against fabricated ``tmp_path`` trees whose mtimes are set with ``os.utime``:
no Chrome, no OCP and no real ``web/dist`` are needed, which is the property the function was
specified to have.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SECOND_NS = 1_000_000_000
"""One second in ``st_mtime_ns``, coarse enough that no filesystem rounds two stamps together."""


def load_bench() -> ModuleType:
    """``tools/bench_scene.py`` as a module: ``tools`` is a folder of scripts, not a package.

    Loading it by path is what lets this test exist without Chrome or OCP: the module's own heavy
    imports (``websockets``, OCP, ``stokes.geometry``) are all lazy, inside the functions that need
    them, so importing it costs nothing. It is registered in ``sys.modules`` first because
    ``dataclass`` resolves its string annotations through the defining module.
    """
    spec = importlib.util.spec_from_file_location("bench_scene", ROOT / "tools" / "bench_scene.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_scene"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bench() -> ModuleType:
    return load_bench()


def stamp(path: Path, seconds: float) -> None:
    """Set a file's mtime, creating its parents; ``seconds`` is a plain POSIX timestamp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    nanos = int(seconds * SECOND_NS)
    os.utime(path, ns=(nanos, nanos))


def tree(
    root: Path, *, dist_at: float | None, src_at: float, index: bool = True, src: bool = True
) -> Path:
    """A checkout-shaped tree: ``web/dist/index.html`` and ``web/src`` stamped as asked."""
    if index:
        stamp(root / "web" / "dist" / "index.html", dist_at if dist_at is not None else src_at)
    if src:
        stamp(root / "web" / "src" / "main.ts", src_at)
    return root


def test_fresh_when_dist_is_newer(bench: ModuleType, tmp_path: Path) -> None:
    found = bench.dist_freshness(tree(tmp_path, dist_at=2000.0, src_at=1000.0))
    assert found.state == "fresh"
    assert found.dist_mtime == 2000 * SECOND_NS
    assert found.src_mtime == 1000 * SECOND_NS
    assert not found.blocked


def test_fresh_when_the_stamps_are_equal(bench: ModuleType, tmp_path: Path) -> None:
    """Equal is fresh on purpose: a coarse timestamp can record the read and the write in one tick.

    Filesystems with one- or two-second granularity are the reason this boundary is inclusive: an
    install written immediately after its source can share its second, and calling that stale would
    refuse a correct run.
    """
    found = bench.dist_freshness(tree(tmp_path, dist_at=1000.0, src_at=1000.0))
    assert found.state == "fresh"
    assert found.dist_mtime == found.src_mtime


def test_stale_by_the_smallest_step_the_filesystem_stores(
    bench: ModuleType, tmp_path: Path
) -> None:
    """Any amount older is stale: there is no tolerance window to hide a real miss in.

    The step is the smallest one this filesystem actually keeps rather than one nanosecond: NTFS
    quantises mtimes to 100 ns, so a 1 ns ``utime`` is silently rounded away and the test would be
    asserting the filesystem's rounding instead of the check. 100 ns is still far below any
    tolerance a person would write, so it tests the same property.
    """
    found = tree(tmp_path, dist_at=1000.0, src_at=1000.0)
    source = found / "web" / "src" / "main.ts"
    os.utime(source, ns=(1000 * SECOND_NS + 100,) * 2)
    stored = source.stat().st_mtime_ns
    assert stored > 1000 * SECOND_NS, "this filesystem cannot store the difference under test"
    result = bench.dist_freshness(found)
    assert result.state == "stale"
    assert result.blocked
    assert result.dist_mtime is not None and result.src_mtime is not None
    assert result.dist_mtime < result.src_mtime


def test_stale_reports_both_timestamps_and_the_remedy(bench: ModuleType, tmp_path: Path) -> None:
    """A refusal has to name what it compared and the one command that fixes it."""
    result = bench.dist_freshness(tree(tmp_path, dist_at=1000.0, src_at=2000.0))
    assert result.state == "stale"
    assert str(tmp_path / "web" / "src") in result.detail
    assert bench.REMEDY in result.detail


def test_stale_reads_the_newest_source_not_the_directory(bench: ModuleType, tmp_path: Path) -> None:
    """The newest source file decides, and it may sit in a nested folder the walk has to reach."""
    root = tree(tmp_path, dist_at=1500.0, src_at=1000.0)
    stamp(root / "web" / "src" / "viewport" / "selection.ts", 3000.0)
    result = bench.dist_freshness(root)
    assert result.state == "stale"
    assert result.src_mtime == 3000 * SECOND_NS


def test_config_outside_src_counts_as_a_source(bench: ModuleType, tmp_path: Path) -> None:
    """``vite.config.ts`` decides the bundle's bytes without living under ``web/src``.

    Without this input a rebuilt dist could still be published against a build configuration that
    has since changed, which is the same class of defect as a stale source file.
    """
    root = tree(tmp_path, dist_at=1500.0, src_at=1000.0)
    stamp(root / "web" / "vite.config.ts", 4000.0)
    result = bench.dist_freshness(root)
    assert result.state == "stale"
    assert result.src_mtime == 4000 * SECOND_NS


def test_missing_when_index_html_is_absent(bench: ModuleType, tmp_path: Path) -> None:
    found = tree(tmp_path, dist_at=None, src_at=1000.0, index=False)
    result = bench.dist_freshness(found)
    assert result.state == "missing"
    assert result.blocked
    assert result.dist_mtime is None
    assert result.src_mtime == 1000 * SECOND_NS
    assert "index.html" in result.detail


def test_missing_when_dist_only_holds_other_files(bench: ModuleType, tmp_path: Path) -> None:
    """A ``dist`` without its entry point is not a servable bundle, however new it is."""
    root = tree(tmp_path, dist_at=None, src_at=1000.0, index=False)
    stamp(root / "web" / "dist" / "assets" / "app.js", 5000.0)
    result = bench.dist_freshness(root)
    assert result.state == "missing"
    assert result.dist_mtime == 5000 * SECOND_NS


def test_unknown_when_src_is_absent(bench: ModuleType, tmp_path: Path) -> None:
    """A packaged checkout has a dist and no sources: never guess ``fresh``, but never refuse."""
    root = tree(tmp_path, dist_at=1000.0, src_at=1000.0, src=False)
    result = bench.dist_freshness(root)
    assert result.state == "unknown"
    assert not result.blocked
    assert result.src_mtime is None
    assert result.dist_mtime == 1000 * SECOND_NS


def test_unknown_when_dist_is_absent_and_src_is_absent(bench: ModuleType, tmp_path: Path) -> None:
    """Both sides gone is still ``missing``: no bundle at all is proof enough to stop."""
    result = bench.dist_freshness(tmp_path)
    assert result.state == "missing"
    assert result.dist_mtime is None and result.src_mtime is None


def test_unknown_is_still_unknown_when_only_config_files_remain(
    bench: ModuleType, tmp_path: Path
) -> None:
    """``package.json`` alone dates nothing about the code that was bundled.

    A packaged checkout ships the config files beside a built ``dist`` and no sources. If those
    files counted as sources on their own, they would read as ``stale`` and refuse a perfectly good
    run, so ``web/src`` being absent is what decides this, whatever else is present.
    """
    root = tree(tmp_path, dist_at=1000.0, src_at=1000.0, src=False)
    stamp(root / "web" / "package.json", 9000.0)
    result = bench.dist_freshness(root)
    assert result.state == "unknown"
    assert not result.blocked
    assert result.src_mtime is None
    assert result.dist_mtime == 1000 * SECOND_NS


def test_states_are_pure_and_repeatable(bench: ModuleType, tmp_path: Path) -> None:
    """Two calls on an unchanged tree agree, and neither writes anything into it."""
    root = tree(tmp_path, dist_at=1000.0, src_at=2000.0)
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    first = bench.dist_freshness(root)
    second = bench.dist_freshness(root)
    after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    assert first == second
    assert first.state == "stale"
    assert before == after


def test_freshness_row_carries_the_state_and_both_times(bench: ModuleType, tmp_path: Path) -> None:
    """The report's ``dist`` record: a figure must never be quotable without this beside it."""
    row = bench.freshness_row(bench.dist_freshness(tree(tmp_path, dist_at=1000.0, src_at=2000.0)))
    assert row["state"] == "stale"
    assert row["dist_mtime"] == 1000 * SECOND_NS
    assert row["src_mtime"] == 2000 * SECOND_NS
    assert isinstance(row["detail"], str) and row["detail"]
    assert row["dist_time"] != row["src_time"]


class Args:
    """The two fields ``check_dist`` reads; the rest of the namespace is irrelevant to it."""

    def __init__(self, *, allow_stale_dist: bool = False) -> None:
        self.allow_stale_dist = allow_stale_dist


def point_at(monkeypatch: pytest.MonkeyPatch, bench: ModuleType, root: Path) -> None:
    """Make the module's ``ROOT`` the fabricated tree, so no real ``web/dist`` is consulted."""
    monkeypatch.setattr(bench, "ROOT", root)


def test_check_dist_returns_fresh_without_a_word(
    bench: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    point_at(monkeypatch, bench, tree(tmp_path, dist_at=2000.0, src_at=1000.0))
    assert bench.check_dist(Args()).state == "fresh"
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("state,index,src", [("stale", True, True), ("missing", False, True)])
def test_check_dist_refuses_stale_and_missing(
    bench: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: str,
    index: bool,
    src: bool,
) -> None:
    """A refusal is a non-zero exit, said loudly, with the remedy in it."""
    point_at(
        monkeypatch,
        bench,
        tree(tmp_path, dist_at=1000.0 if index else None, src_at=2000.0, index=index, src=src),
    )
    with pytest.raises(SystemExit) as raised:
        bench.check_dist(Args())
    assert raised.value.code != 0
    err = capsys.readouterr().err
    assert state.upper() in err
    assert bench.REMEDY in err
    assert str(tmp_path / "web" / "dist") in err


def test_check_dist_warns_and_continues_when_unknown(
    bench: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse only on proof, never on ignorance: an undatable source is a warning, not an exit."""
    point_at(monkeypatch, bench, tree(tmp_path, dist_at=1000.0, src_at=1000.0, src=False))
    assert bench.check_dist(Args()).state == "unknown"
    err = capsys.readouterr().err
    assert "cannot be checked" in err
    assert bench.REMEDY not in err


def test_allow_stale_dist_overrides_the_refusal(
    bench: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The escape hatch measures anyway, and still says what the numbers describe."""
    point_at(monkeypatch, bench, tree(tmp_path, dist_at=1000.0, src_at=2000.0))
    found = bench.check_dist(Args(allow_stale_dist=True))
    assert found.state == "stale"
    err = capsys.readouterr().err
    assert "STALE" in err
    assert "--allow-stale-dist" in err


def test_measure_records_the_dist_before_it_starts_anything(
    bench: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refusing happens before a server or a browser exists, which the fake constructors prove.

    If the guard ran after the server started, these stand-ins would have been reached and the
    test would fail on the assertion rather than on the refusal: that is the point of them.
    """
    reached: list[str] = []

    class NoServer:
        def __init__(self, *_args: object) -> None:
            reached.append("server")

    class NoChrome:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            reached.append("chrome")

    monkeypatch.setattr(bench, "Server", NoServer)
    monkeypatch.setattr(bench, "Chrome", NoChrome)
    point_at(monkeypatch, bench, tree(tmp_path, dist_at=1000.0, src_at=2000.0))
    args = Args()
    args.project = tmp_path / "dummy.stokes"  # type: ignore[attr-defined]
    args.browser = tmp_path / "chrome.exe"  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as raised:
        bench.measure(args)
    assert raised.value.code != 0
    assert reached == [], f"something was started before the guard refused: {reached}"
    capsys.readouterr()
