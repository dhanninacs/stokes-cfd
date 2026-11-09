"""The `-libs` the agent adds itself on native Windows, and nowhere else (the mixing elbow).

decomposePar stopped at "Unknown patchField type compressible::alphatJayatillekeWallFunction" and
foamRun at "Negative initial temperature" until the generic patch fields and the fvConstraints
library (for the energy case's `limitTemperature`) were loaded. The names are the agent's own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes_agent import jobs


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs.os, "name", "nt")


def test_splitters_load_the_generic_patch_fields(windows: None, tmp_path: Path) -> None:
    for utility in ("decomposePar", "reconstructPar"):
        assert jobs._launch_libs(utility, tmp_path) == ["-libs", jobs.GENERIC_FIELDS_LIBS]


def test_a_solver_gets_no_libs(windows: None, tmp_path: Path) -> None:
    """A quoted `-libs` value does not survive mpiexec's parsing on Windows; the solver loads
    the constraints itself."""
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "fvConstraints").write_text("limitT {}\n", encoding="utf-8")
    assert jobs._launch_libs("foamRun", tmp_path) == []
    assert jobs._launch_libs("blockMesh", tmp_path) == []


def test_nothing_is_added_off_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(jobs.os, "name", "posix")
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "fvConstraints").write_text("limitT {}\n", encoding="utf-8")
    for utility in ("decomposePar", "reconstructPar", "foamRun"):
        assert jobs._launch_libs(utility, tmp_path) == []
