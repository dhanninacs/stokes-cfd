"""The atomic renames that end a case import and a study-table write survive a brief Windows lock.

On Windows a rename fails with `PermissionError` (WinError 5) while anything holds the source or
target open, and a virus scanner or the search indexer opens a file the moment it is written.
`stokes.model.persist.replace_with_retry` already guarded project saves (T-094); the case store and
the study tables renamed bare, and a full test run on Windows hit both ("the case could not be
copied into the project: [WinError 5] Access is denied", "cannot write …\\points.csv"). These
tests drive the lock deterministically: a few refusals must be ridden out, and a lock that never
clears must still be reported as the caller's typed error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.cases import store
from stokes.cases.store import CaseStoreError
from stokes.explore import study_table
from stokes.explore.study_table import StudyTableError
from stokes.model.persist import replace_with_retry


def _refuse(monkeypatch: pytest.MonkeyPatch, prefix: str, times: int | None) -> dict[str, int]:
    """`Path.replace` refuses names starting with `prefix`, `times` times (None: always)."""
    real_replace = Path.replace
    seen = {"refused": 0}

    def locked_replace(self: Path, target: str | Path) -> Path:
        if self.name.startswith(prefix) and (times is None or seen["refused"] < times):
            seen["refused"] += 1
            raise PermissionError(13, "the file is in use by another process")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", locked_replace)
    return seen


def test_replace_with_retry_honours_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / ".x-1"
    source.write_text("new")
    seen = _refuse(monkeypatch, ".x-", None)
    with pytest.raises(PermissionError):
        replace_with_retry(source, tmp_path / "x", attempts=3)
    assert seen["refused"] == 3, "the helper did not try exactly the number of times asked"


def test_a_case_import_rides_out_a_brief_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = tmp_path / "blob"
    blob.write_bytes(b"(0 1 2)")
    entry = store.Entry("constant/polyMesh/faces", store.SOURCE_AREA, blob)
    seen = _refuse(monkeypatch, ".incoming-", 2)

    staged = store.stage(tmp_path, "n_22222222", [entry], lambda rel, path: [])

    assert seen["refused"] == 2, "the test did not exercise the retry"
    assert staged.ok and store.has_snapshot(tmp_path, "n_22222222", staged.snapshot)
    leftovers = [
        p.name for p in (tmp_path / store.CASES_FOLDER).iterdir() if p.name.startswith(".")
    ]
    assert leftovers == [], "the staging folder was left behind"


def test_a_case_import_that_stays_locked_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = tmp_path / "blob"
    blob.write_bytes(b"(0 1 2)")
    entry = store.Entry("constant/polyMesh/faces", store.SOURCE_AREA, blob)
    monkeypatch.setattr(store, "CASE_RENAME_ATTEMPTS", 2)  # keep the test fast
    _refuse(monkeypatch, ".incoming-", None)

    with pytest.raises(CaseStoreError, match="could not be copied into the project"):
        store.stage(tmp_path, "n_33333333", [entry], lambda rel, path: [])
    assert not (tmp_path / store.CASES_FOLDER / "n_33333333").exists()


def test_a_study_table_write_rides_out_a_brief_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "studies" / "points.csv"
    seen = _refuse(monkeypatch, ".points-", 2)

    study_table.write_text(target, "a,b\n1,2\n")

    assert seen["refused"] == 2, "the test did not exercise the retry"
    assert target.read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert [p.name for p in target.parent.iterdir()] == ["points.csv"]


def test_a_study_table_write_that_stays_locked_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "studies" / "points.csv"
    _refuse(monkeypatch, ".points-", None)

    with pytest.raises(StudyTableError, match=r"cannot write .*points\.csv"):
        study_table.write_text(target, "a\n")
    assert list(target.parent.iterdir()) == [], "the temporary file was left behind"
