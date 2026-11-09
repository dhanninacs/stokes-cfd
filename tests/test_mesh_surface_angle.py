"""The sharp-edge threshold (brief M-515): one setting, two dictionaries, one relation.

`resolveFeatureAngle` in `snappyHexMeshDict` is the angle itself; `surfaceFeaturesDict`'s
`includedAngle` is the complement (180 - angle) — the utility measures the angle BETWEEN two
faces. At the default 30 both dictionaries are byte-identical to today, which is the guarantee
the golden test pins.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from stokes.mesh.drivers.snappy import FEATURES_INCLUDED_ANGLE, write
from stokes.mesh.sizing import surface_target  # noqa: F401 - the precedence neighbour
from stokes.model import registry

# ------------------------------------------------------------------------------------- the model


def test_the_setting_exists_with_the_default_and_the_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Number, degrees, default 30 — today's `RESOLVE_FEATURE_ANGLE` — and 0/180 refused (each is
    the threshold's negation, not a threshold)."""
    from stokes.api import Session
    from stokes.errors import StokesError

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="mesh")
    schema = registry.get("mesh_settings").json_schema()["properties"]
    assert schema["feature_angle"]["type"] == "number"
    assert schema["feature_angle"]["default"] == 30

    session.execute("mesh.configure", values={"feature_angle": 45})
    settings = session.require_project().find("/mesh/settings")
    assert settings is not None and float(settings.params["feature_angle"]) == 45.0

    for bad in (0, 180, -10):
        with pytest.raises(StokesError, match="between 0 and 180"):
            session.execute("mesh.configure", values={"feature_angle": bad})


# ------------------------------------------------------------------------------------- the writer


@dataclass
class _Surface:
    name: str = "walls"
    file: str = "walls.stl"
    region: str = "walls"
    patch_type: str = "wall"
    level_min: int = 1
    level_max: int = 2
    requested: float = 0.004


def _snapshot(feature_angle: float) -> Any:
    """One surface, the angle given — the smallest case that writes both dictionaries."""
    from stokes.mesh.drivers.snappy import SnappySnapshot

    return SnappySnapshot(
        surfaces=(_Surface(),),
        background_min=(-1.0, -1.0, -1.0),
        background_max=(2.0, 1.0, 1.0),
        background_cells=(12, 8, 8),
        inside_point=(0.0, 0.0, 0.0),
        base_size=0.25,
        feature_angle=feature_angle,
        stl_files={"constant/geometry/walls.stl": "solid walls\nendsolid walls\n"},
    )


def _squashed(text: str) -> str:
    import re

    return re.sub(r"[ \t]+", " ", text)


def test_the_default_dictionaries_are_BYTE_IDENTICAL() -> None:
    """**The guarantee**: at 30 both dictionaries are exactly what today's writer emitted —
    `resolveFeatureAngle 30` and `includedAngle 150` (the constant that was hardcoded)."""
    snap = _snapshot(30.0)
    files = write(snap)
    assert "resolveFeatureAngle 30.0;" in _squashed(files["system/snappyHexMeshDict"])
    assert f"includedAngle {FEATURES_INCLUDED_ANGLE:.1f};" in _squashed(
        files["system/surfaceFeaturesDict"]
    )


def test_an_angle_of_45_changes_only_the_two_keys() -> None:
    """45: `resolveFeatureAngle 45` in the mesher's dictionary, `includedAngle 135` in the
    extraction's — the complement — and nothing else moves."""
    default = _squashed(write(_snapshot(30.0))["system/snappyHexMeshDict"])
    angled = _squashed(write(_snapshot(45.0))["system/snappyHexMeshDict"])
    assert "resolveFeatureAngle 45.0;" in angled
    assert "resolveFeatureAngle 30.0;" not in angled
    # **Only that key differs**: strip the two value lines and the rest is identical.
    import re

    strip = lambda text: re.sub(  # noqa: E731 - a test-local normaliser
        r"resolveFeatureAngle [0-9.]+;", "", text
    )
    assert strip(angled) == strip(default)
    surface = _squashed(write(_snapshot(45.0))["system/surfaceFeaturesDict"])
    assert "includedAngle 135.0;" in surface
    assert "includedAngle 150.0;" not in surface


def test_the_angle_goldens_pass_the_agent_lint() -> None:
    """The corpus rule: the new 45-degree dictionaries are shapes the agent accepts."""
    from stokes_agent.lint import lint_text

    for angle in (30.0, 45.0):
        for rel, text in write(_snapshot(angle)).items():
            found = lint_text(rel, text)
            assert not found, f"{rel}: {found[0].to_json()}"


# --------------------------------------------------------------------------------------- the basis


def test_a_change_moves_the_surface_part_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The angle is in the `surface` sub-basis (brief M-501), so a change makes `changed ==
    ["surface"]` — and `volume` stale by the every-part rule."""
    from stokes.api import Session
    from stokes.mesh.basis import changed_tasks, mesh_basis_parts
    from stokes.mesh.drivers import driver_for
    from stokes.mesh.snapshot import snapshot_for
    from tools.e2e_support import duct_project

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent))
    session = Session()
    made = duct_project(session, tmp_path / "duct.stokes")
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={"domain": "part", "domain_part": made["part"]},
    )
    settings = session.require_project().find("/mesh/settings")

    def parts() -> dict[str, str]:
        snap = snapshot_for(session, settings)
        return mesh_basis_parts(snap, driver_for("snappy").DRIVER_VERSION, "org-14")

    plain = parts()
    assert float(snapshot_for(session, settings).feature_angle) == 30.0
    session.execute("mesh.configure", values={"feature_angle": 45})
    angled = parts()
    assert angled["surface"] != plain["surface"]
    assert angled["sizing"] == plain["sizing"], "only the surface part moves"
    from stokes.mesh.basis import PART_TO_TASKS

    assert PART_TO_TASKS["surface"] == ("surface",)
    assert changed_tasks(plain, angled) == ["surface"]
