"""The bundled open-source adjoint (ADR-0041, G30): the probe and the job launcher look beside the
install's solvers first and in Stokes' own ``bin/openfoam-adjoint`` folder second; only allow-listed
names are looked up there, and a link out of the folder does not count."""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes_agent import bundled, engine_env
from stokes_agent.engine_env import EngineEnv, resolve_utility, utilities


def _env(tools: dict[str, str]) -> EngineEnv:
    return EngineEnv("of14-env.ps1", {"PATH": ""}, "OpenFOAM", "14", "org", 0.0, native_tools=tools)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(bundled, "bundled_roots", lambda: [tmp_path])
    return tmp_path


def _bundle(root: Path) -> Path:
    folder = root / "bin" / "openfoam-adjoint"
    folder.mkdir(parents=True)
    program = folder / bundled.program_file("adjointFoam")
    program.write_bytes(b"")
    return program


def test_the_folder_comes_from_the_package_location() -> None:
    import stokes

    package = Path(stokes.__file__).resolve().parent
    assert bundled.bundled_folder("adjointFoam") == [
        package.parent / "bin" / "openfoam-adjoint",
        package / "bin" / "openfoam-adjoint",
    ]
    assert bundled.bundled_folder("foamRun") == []  # only bundled programs are looked up there


def test_the_install_copy_wins_and_the_bundled_one_is_the_fallback(root: Path) -> None:
    program = _bundle(root)
    install = root / "appbin" / bundled.program_file("adjointFoam")
    install.parent.mkdir()
    install.write_bytes(b"")
    found = utilities(_env({"foamRun": "x", "adjointFoam": str(install)}))
    assert found["adjointFoam"] == str(install)
    assert resolve_utility(_env({"adjointFoam": str(install)}), "adjointFoam") == str(install)
    fallback = utilities(_env({"foamRun": "x"}))
    assert fallback["adjointFoam"] == str(program.resolve())
    assert resolve_utility(_env({"foamRun": "x"}), "adjointFoam") == str(program.resolve())
    # Another name is never taken from the bundled folder.
    (program.parent / bundled.program_file("foamRun")).write_bytes(b"")
    assert resolve_utility(_env({"blockMesh": "x"}), "foamRun") is None


def test_no_bundled_copy_reports_null(root: Path) -> None:
    assert utilities(_env({"foamRun": "x"}))["adjointFoam"] is None
    assert bundled.bundled_tool("adjointFoam") is None


def test_a_directory_with_the_program_name_does_not_count(root: Path) -> None:
    (root / "bin" / "openfoam-adjoint" / bundled.program_file("adjointFoam")).mkdir(parents=True)
    assert bundled.bundled_tool("adjointFoam") is None


def test_the_utilities_list_still_names_the_program() -> None:
    assert "adjointFoam" in engine_env.UTILITIES
    assert set(bundled.BUNDLED) <= set(engine_env.UTILITIES)
