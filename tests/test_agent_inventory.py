"""The install inventory in `host.probe` (ADR-0029, plan T-304), on a fake install tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes_agent import engine_env
from stokes_agent.probe import install_inventory, library_name


def _touch(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _install(root: Path) -> dict[str, str]:
    """A fake OpenFOAM tree: binaries, libraries, etc files and tutorials. Nothing is runnable."""
    appbin = root / "platforms" / "opt" / "bin"
    libbin = root / "platforms" / "opt" / "lib"
    etc = root / "etc"
    for name in ("foamRun.exe", "blockMesh.exe", "readme.txt"):
        _touch(appbin / name)
    for name in (
        "libincompressibleFluidSolver.dll",
        "libincompressibleFluidSolver.a",
        "libincompressibleFluidSolver.def",
        "libfiniteVolume.so",
        "libSolver.so",
        "notes.txt",
        "msmpi/libPstream.dll",
    ):
        _touch(libbin / name)
    for name in (
        "caseDicts/setConstraintTypes",
        "caseDicts/functions/forces/forces",
        "caseDicts/postProcessing/streamlines",
        "caseDicts/mesh/generation/meshQualityDict",
    ):
        _touch(etc / name)
    (root / "tutorials").mkdir()
    return {
        "WM_PROJECT_DIR": str(root),
        "FOAM_APPBIN": str(appbin),
        "FOAM_LIBBIN": str(libbin),
        "FOAM_ETC": str(etc),
    }


def test_the_inventory_lists_the_fake_install(tmp_path: Path) -> None:
    env = _install(tmp_path)
    found = install_inventory(env)
    assert found["solver_modules"] == ["incompressibleFluid"]
    assert found["utilities"] == ["blockMesh", "foamRun"]
    assert found["libraries"] == ["Solver", "finiteVolume", "incompressibleFluidSolver"]
    assert found["etc_functions"] == ["forces", "streamlines"]
    assert found["etc_case_dicts"] == [
        "caseDicts/functions/forces/forces",
        "caseDicts/mesh/generation/meshQualityDict",
        "caseDicts/postProcessing/streamlines",
        "caseDicts/setConstraintTypes",
    ]
    # WM_PROJECT_DIR/tutorials when FOAM_TUTORIALS is not set; FOAM_TUTORIALS wins when it is.
    assert found["tutorials_root"] == str(tmp_path / "tutorials")
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert install_inventory({**env, "FOAM_TUTORIALS": str(other)})["tutorials_root"] == str(other)


def test_an_empty_or_partial_environment_gives_empty_lists(tmp_path: Path) -> None:
    empty = install_inventory({})
    assert empty == {
        "solver_modules": [],
        "utilities": [],
        "libraries": [],
        "etc_functions": [],
        "etc_case_dicts": [],
        "tutorials_root": None,
    }
    relative = install_inventory({"FOAM_APPBIN": "relative/bin", "FOAM_ETC": str(tmp_path)})
    assert relative["utilities"] == [] and relative["etc_case_dicts"] == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("libfoo.so", "foo"),
        ("libfoo.DLL", "foo"),
        ("foo.dylib", "foo"),
        ("libfoo.a", None),
        ("README", None),
        (".so", None),
        ("lib.so", None),
    ],
)
def test_library_name(name: str, expected: str | None) -> None:
    assert library_name(name) == expected


def test_host_record_carries_the_inventory_at_the_top_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no supported install the agent lists its own FOAM_* environment; the keys are always
    there, so a client never has to tell absent from empty."""
    env = _install(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("FOAM_TUTORIALS", raising=False)
    monkeypatch.setattr(engine_env, "probe_host", lambda: {"hostname": "test"})
    monkeypatch.setattr(engine_env, "vtk_version", lambda: None)
    monkeypatch.setattr(engine_env, "bashrc_candidates", lambda: [])
    record = engine_env.host_record()
    assert record["solver_modules"] == ["incompressibleFluid"]
    assert record["tutorials_root"] == str(tmp_path / "tutorials")
    assert record["installs"] == []


def test_a_supported_install_contributes_its_own_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _install(tmp_path)
    captured = engine_env.EngineEnv(str(tmp_path / "bashrc"), env, "OpenFOAM", "14", "org", 0.0)
    monkeypatch.setattr(engine_env, "probe_host", lambda: {"hostname": "test"})
    monkeypatch.setattr(engine_env, "vtk_version", lambda: None)
    monkeypatch.setattr(engine_env, "bashrc_candidates", lambda: [tmp_path / "bashrc"])
    monkeypatch.setattr(engine_env, "cached_env", lambda _path: captured)
    monkeypatch.setattr(engine_env, "utilities", lambda _env: {})
    record = engine_env.host_record()
    assert record["installs"][0]["inventory"]["utilities"] == ["blockMesh", "foamRun"]
    assert record["utilities"] == ["blockMesh", "foamRun"]
    assert record["etc_functions"] == ["forces", "streamlines"]


def test_the_registry_inventory_is_none_for_an_older_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes.api import Session
    from stokes.run.hosts import INVENTORY_KEYS, HostRegistry

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    records: list[dict[str, Any]] = [
        {"installs": []},
        {key: [] for key in INVENTORY_KEYS},
    ]

    class Runner:
        name = "local"
        available = True
        why = ""

        def call(self, method: str, timeout: float = 60.0, **params: Any) -> Any:
            return records.pop(0)

        def close(self) -> None:
            return

    registry = HostRegistry.of(Session())
    registry._runners["local"] = Runner()  # type: ignore[assignment]
    assert registry.inventory() is None
    registry._records.clear()
    assert registry.inventory() == {key: [] for key in INVENTORY_KEYS}
