"""The run record's atomic write rides out Windows' "Access is denied" on the rename (2026-11-01).

The tutorial sweep logged `[WinError 5] Access is denied: .record.json.tmp -> record.json` on
nearly every run once the warning carried its error: `_write` runs after every change from two
threads, and a rename over a file another handle holds is refused on Windows, not queued.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.run.store import _replace_with_retries


def test_a_refused_rename_is_retried_and_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp = tmp_path / ".record.json.tmp"
    target = tmp_path / "record.json"
    temp.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    real = Path.replace
    refusals = {"left": 2}

    def flaky(self: Path, other: Path) -> Path:
        if self == temp and refusals["left"] > 0:
            refusals["left"] -= 1
            raise PermissionError(5, "Access is denied")
        return real(self, other)

    monkeypatch.setattr(Path, "replace", flaky)
    _replace_with_retries(temp, target, tries=4, wait=0.001)
    assert target.read_text(encoding="utf-8") == "new"
    assert refusals["left"] == 0


def test_a_rename_refused_every_time_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp = tmp_path / "a.tmp"
    temp.write_text("x", encoding="utf-8")

    def refused(self: Path, other: Path) -> Path:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "replace", refused)
    with pytest.raises(PermissionError):
        _replace_with_retries(temp, tmp_path / "b.json", tries=3, wait=0.001)
