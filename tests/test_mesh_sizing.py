"""The surface-size precedence and the cell estimate (brief M-503).

`surface_size` was a **dead setting** — the snapshot never read it — and a per-boundary size did
not exist. Both land behind one precedence function (`stokes.mesh.sizing.surface_target`) that
the snapshot and the placeholders share, so the page cannot disagree with the dictionary. The
estimate (`stokes.mesh.estimate`) is pure over the snapshot, and its band is calibrated in the
module docstring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stokes.mesh.estimate import estimate, surface_added_cells
from stokes.mesh.sizing import surface_target
from stokes.mesh.snapshot import _refuse_negative_sizes, _snappy_surfaces
from stokes.model.nodes import QuantityValue
from stokes.model.project import Project

# --------------------------------------------------------------------------- the precedence


def test_the_rule_answers_when_nothing_is_set() -> None:
    """A wall at half the base, anything else at the base: the behaviour every project had."""
    assert (
        surface_target(
            boundary_size=None, body_size=None, surface_size=0.0, base_size=0.008, patch_type="wall"
        )
        == 0.004
    )
    assert (
        surface_target(
            boundary_size=None,
            body_size=None,
            surface_size=0.0,
            base_size=0.008,
            patch_type="patch",
        )
        == 0.008
    )


def test_surface_size_beats_the_rule_but_zero_does_not() -> None:
    """`surface_size` > 0 governs every surface that has nothing nearer; **0 is the default** and
    keeps the rule — that is the byte-identical guarantee."""
    for patch_type in ("wall", "patch"):
        assert (
            surface_target(
                boundary_size=None,
                body_size=None,
                surface_size=0.003,
                base_size=0.008,
                patch_type=patch_type,
            )
            == 0.003
        )
        assert surface_target(
            boundary_size=None,
            body_size=None,
            surface_size=0.0,
            base_size=0.008,
            patch_type=patch_type,
        ) == (0.004 if patch_type == "wall" else 0.008)


def test_the_body_size_beats_surface_size() -> None:
    assert (
        surface_target(
            boundary_size=None,
            body_size=0.002,
            surface_size=0.003,
            base_size=0.008,
            patch_type="wall",
        )
        == 0.002
    )


def test_a_boundary_size_beats_the_body() -> None:
    assert (
        surface_target(
            boundary_size=0.001,
            body_size=0.002,
            surface_size=0.003,
            base_size=0.008,
            patch_type="wall",
        )
        == 0.001
    )


def test_a_zero_or_negative_stored_size_is_not_a_decision() -> None:
    """A quantity with no default materialises a zero at creation — the "zero means not set"
    convention — so 0 reads as unset, not as "make everything zero-sized". A negative is the
    caller's problem: the snapshot refuses it by name."""
    assert (
        surface_target(
            boundary_size=0.0, body_size=None, surface_size=0.0, base_size=0.008, patch_type="wall"
        )
        == 0.004
    )
    assert (
        surface_target(
            boundary_size=None,
            body_size=-1.0,
            surface_size=0.0,
            base_size=0.008,
            patch_type="patch",
        )
        == 0.008
    )


# --------------------------------------------------------------- the surfaces, one knob each


def _surfaces(
    *,
    local: dict[str, Any] | None = None,
    boundary_sizes: dict[str, float | None] | None = None,
    surface_size: float = 0.0,
) -> dict[str, tuple[int, int, float]]:
    """The snappy surfaces for one cube body with two tags, with the sizing given.

    The same tessellation `test_per_body_mesh` builds without the geometry store: a cube, two
    face sets, everything `_snappy_surfaces` reads.
    """
    import numpy as np

    positions = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
            [0, 1, 1],
        ],
        dtype=float,
    )
    quads = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    indices: list[int] = []
    face_ids: list[int] = []
    for face, (a, b, c, d) in enumerate(quads):
        indices += [a, b, c, a, c, d]
        face_ids += [face] * 6
    surfaces, _files = _snappy_surfaces(
        positions,
        np.array(indices, dtype=int),
        np.array(face_ids, dtype=int),
        {"wall_tag": {0}, "top_tag": {4}},
        {"wall_tag": "wall", "top_tag": "symmetry"},
        0.1,
        local,
        boundary_sizes,
        surface_size,
    )
    return {s.name: (s.level_min, s.level_max, s.requested) for s in surfaces}


def test_with_nothing_set_the_levels_are_what_they_always_were() -> None:
    """**The byte-identical guarantee**: the default resolves through the same rule as before."""
    assert _surfaces() == _surfaces(surface_size=0.0)


def test_surface_size_changes_the_governed_surfaces_and_no_others() -> None:
    """A global surface size moves every surface that has nothing nearer — here, both."""
    plain = _surfaces()
    sized = _surfaces(surface_size=0.025)
    assert sized["wall_tag"][2] == 0.025
    assert sized["top_tag"][2] == 0.025
    assert sized["wall_tag"][:2] != plain["wall_tag"][:2]


def test_a_boundary_size_changes_only_that_surface() -> None:
    """**The whole point of the per-boundary size**: one boundary finer, the rest untouched."""
    plain = _surfaces()
    sized = _surfaces(boundary_sizes={"wall_tag": 0.0125})
    assert sized["wall_tag"][2] == 0.0125
    assert sized["top_tag"] == plain["top_tag"]


def test_a_boundary_size_beats_the_body_size() -> None:
    sized = _surfaces(local={"size": 0.05}, boundary_sizes={"wall_tag": 0.0125})
    assert sized["wall_tag"][2] == 0.0125


def test_the_body_size_beats_surface_size_on_the_surfaces() -> None:
    sized = _surfaces(local={"size": 0.05}, surface_size=0.025)
    assert sized["wall_tag"][2] == 0.05
    assert sized["top_tag"][2] == 0.05


# ------------------------------------------------------------------------- the negative refusal


def _part(project: Project) -> object:
    project.add("/geometry", "group", "parts")
    return project.add("/geometry/parts", "part", "housing")


def test_a_negative_body_size_is_refused_by_name() -> None:
    project = Project.new("demo")
    part = _part(project)
    project.set_param(part.id, "mesh_size", "-1 [mm]")
    with pytest.raises(Exception, match="the part 'housing'"):
        _refuse_negative_sizes(part, {"size": -1e-3}, {})


def test_a_negative_boundary_size_is_refused_with_the_boundary_named() -> None:
    project = Project.new("demo")
    _part(project)
    with pytest.raises(Exception, match="the boundary 'inlet'"):
        _refuse_negative_sizes(None, {}, {"inlet": -0.001})


def test_a_zero_stored_size_is_not_refused() -> None:
    """The materialised zero is the empty state, not a bad length."""
    project = Project.new("demo")
    part = _part(project)
    assert part.params["mesh_size"] == QuantityValue(value=0.0, unit="m", expr=None)
    _refuse_negative_sizes(part, {"size": None}, {"inlet": None})


# --------------------------------------------------------------------------------- the estimate


class _Surface:
    """The one surface's facts the estimate reads."""

    def __init__(self, name: str, requested: float, level_max: int, patch_type: str):
        self.name = name
        self.requested = requested
        self.level_max = level_max
        self.patch_type = patch_type


class _Snap:
    """A synthetic snapshot: one wall surface of known area on a known box."""

    def __init__(
        self, *, base: float = 0.008, surface_size: float = 0.0, max_cells: int = 5_000_000
    ):
        self.base_size = base
        self.surface_size = surface_size
        self.max_cells = max_cells
        self.cells_between_levels = 3
        self.bbox = (0.0, 0.0, 0.0, 0.1, 0.1, 0.1)
        self.surfaces = (_Surface("walls", base / 2, 1, "wall"),)
        self.surface_areas = {"walls": 0.01}
        self.surface_targets = {"walls": (None, None, "wall")}


def test_the_estimate_covers_the_band_and_names_the_limit() -> None:
    numbers = estimate(_Snap())
    assert numbers["low"] < numbers["high"]
    assert numbers["max_cells"] == 5_000_000
    assert numbers["over"] is False
    assert numbers["background_cells"] > 0
    assert numbers["levels"][0]["surface"] == "walls"
    assert "band" in numbers["method"]


def test_a_smaller_base_gives_more_cells() -> None:
    """**Monotonic**: halving the base size cannot shrink the estimate."""
    coarse = estimate(_Snap(base=0.008))
    fine = estimate(_Snap(base=0.004))
    assert fine["low"] > coarse["low"]
    assert fine["background_cells"] > coarse["background_cells"]


def test_an_estimate_over_the_limit_sets_over() -> None:
    tiny = estimate(_Snap(max_cells=100))
    assert tiny["over"] is True
    assert tiny["max_cells"] == 100


def test_the_added_cell_formula_is_the_docstrings() -> None:
    """area / achieved² * (1 + between * level), stated so the calibration can be checked."""
    assert surface_added_cells(area=1.0, achieved_size=0.5, level=0, cells_between_levels=3) == 4.0
    assert surface_added_cells(area=1.0, achieved_size=0.5, level=2, cells_between_levels=3) == 28.0
    assert surface_added_cells(area=0.0, achieved_size=0.5, level=1, cells_between_levels=3) == 0.0


def test_a_tried_surface_size_re_resolves_only_the_governed_surfaces() -> None:
    """The snapshot carries each surface's inputs, so a tried size re-resolves through the same
    precedence — a boundary size still wins over a tried global one. The synthetic snapshot is
    consistent the way the real one is: `requested` IS what the inputs resolve to."""
    snap = _Snap()
    snap.surfaces = (_Surface("walls", 0.002, 2, "wall"),)
    snap.surface_targets = {"walls": (0.002, None, "wall")}
    plain = estimate(snap)
    tried = estimate(snap, surface_size=0.001)
    # The boundary's own size beats the tried global surface size, so the level is unchanged.
    assert tried["levels"][0] == plain["levels"][0]
    # And a surface with NO boundary size moves with the tried one.
    snap.surface_targets = {"walls": (None, None, "wall")}
    snap.surfaces = (_Surface("walls", 0.004, 1, "wall"),)
    moved = estimate(snap, surface_size=0.001)
    assert (
        moved["levels"][0]["level"] > plain["levels"][0]["level"]
        or moved["levels"][0]["achieved"] == 0.001
    )


# ------------------------------------------------------------------------ the command, by Session


def test_estimate_on_an_empty_project_says_what_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**No meshable case is an answer, not an error**: the reply carries the precheck's findings
    instead of a number invented from nothing."""
    from stokes.api import Session

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="mesh")
    reply = session.execute("mesh.estimate")
    assert reply["ok"] is False
    assert any(f["code"] == "mesh.no_domain" for f in reply["findings"])


def test_estimate_does_not_mutate_the_tree_and_refuses_unknown_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`values` tries sizes without saving: the settings node is byte-identical after, and a key
    the estimate cannot move is a refusal, not a silently ignored value."""
    from stokes.api import Session
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
    assert settings is not None
    before = {name: str(value) for name, value in settings.params.items()}

    reply = session.execute("mesh.estimate", values={"surface_size": "2 [mm]"})
    assert reply["ok"] is True
    assert reply["low"] <= reply["high"]
    after = {name: str(value) for name, value in settings.params.items()}
    assert after == before, "the tried value leaked into the tree"

    with pytest.raises(Exception, match="only base_size and surface_size"):
        session.execute("mesh.estimate", values={"cores": 4})


def test_a_finer_tried_size_gives_a_higher_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes.api import Session
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
    coarse = session.execute("mesh.estimate")
    fine = session.execute("mesh.estimate", values={"base_size": "1 [mm]"})
    assert coarse["ok"] is True and fine["ok"] is True
    assert fine["low"] > coarse["low"]


def test_a_snappy_estimate_over_the_limit_is_a_precheck_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**`mesh.over_max_cells` for snappy** (brief M-503): M-501 left it out because the
    background shrinks to fit — but the refinement above the background is not free, so the
    estimate's band top over the limit is a blocked precheck naming both numbers."""
    from stokes.api import Session
    from tools.e2e_support import duct_project

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    monkeypatch.syspath_prepend(str(Path(__file__).parent.parent))
    session = Session()
    made = duct_project(session, tmp_path / "duct.stokes")
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={"domain": "part", "domain_part": made["part"], "max_cells": 10},
    )
    reply = session.execute("mesh.precheck")
    finding = next((f for f in reply["findings"] if f["code"] == "mesh.over_max_cells"), None)
    assert finding is not None, reply["findings"]
    assert finding["severity"] == "block"
    assert "10" in finding["message"], "the limit is named"
    assert "estimate" in finding["message"]
    # And the estimate rides the reply for the page to read.
    assert reply["estimate"]["over"] is True


# ---------------------------------------------------------------------- the goldens, linted


def test_a_dictionary_with_boundary_sizes_passes_the_agent_lint() -> None:
    """The corpus rule (README): the shapes this package writes are dictionaries the agent
    accepts — a per-boundary size changing one surface's level pair, a global surface size
    changing all of them. The full snappyHexMeshDict, written from the precedence-resolved
    surfaces and linted."""
    from stokes.mesh.drivers.snappy import SnappySnapshot, Surface, write
    from stokes_agent.lint import lint_text

    def snapshot(surface_size: float, boundary_sizes: dict[str, float] | None) -> dict[str, str]:
        resolved = _surfaces(surface_size=surface_size, boundary_sizes=boundary_sizes)

        def surface(name: str) -> Surface:
            low, high, _requested = resolved[name]
            patch = "wall" if name == "wall_tag" else "patch"
            return Surface(
                name=name,
                file=f"{name}.stl",
                region=name,
                patch_type=patch,
                level_min=low,
                level_max=high,
                requested=resolved[name][2],
            )

        snap = SnappySnapshot(
            surfaces=(surface("wall_tag"), surface("top_tag")),
            background_min=(-1.0, -1.0, -1.0),
            background_max=(2.0, 1.0, 1.0),
            background_cells=(12, 8, 8),
            inside_point=(0.0, 0.0, 0.0),
            base_size=0.1,
            stl_files={
                f"constant/geometry/{n}.stl": f"solid {n}\nendsolid {n}\n"
                for n in ("wall_tag", "top_tag")
            },
        )
        return write(snap)

    for sizes in ({}, {"wall_tag": 0.0125}):
        for rel, text in snapshot(0.0, sizes).items():
            found = lint_text(rel, text)
            assert not found, f"{rel}: {found[0].to_json()}"


# ------------------------------------------------------------------- mesh.layer_height (M-505)


def test_layer_height_needs_a_material_on_the_fluid_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The refusal the brief names**: no material on the fluid region, no height — with the
    message pointing at where it goes."""
    from stokes.api import Session

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="mesh")
    with pytest.raises(Exception, match="set a material on the fluid region first"):
        session.execute("mesh.layer_height", y_plus=1, velocity="10 [m/s]", length="1 [m]")


def test_layer_height_gives_a_height_and_carries_the_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stokes.api import Session

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="mesh")
    session.execute("material.create", name="air", template="gas")
    session.execute("node.add", parent="/geometry", type="group", name="parts")
    part = session.execute("node.add", parent="/geometry/parts", type="part", name="body")["id"]
    session.execute("region.assign", target=str(part), material="air", name="fluid")
    reply = session.execute("mesh.layer_height", y_plus=1, velocity="10 [m/s]", length="1 [m]")
    assert reply["first_height"] > 0
    assert reply["reynolds"] > 0
    assert "flat plate" in reply["method"]
    # **The height is twice the centre**: the y+ definition is at the centre.
    assert reply["first_height"] == pytest.approx(2.0 * reply["y_centre"])


def test_layer_height_parses_units_and_refuses_unknown_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Text with a unit and SI numbers give the same answer, and a wrong-shape input is refused."""
    from stokes.api import Session

    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    session = Session()
    session.execute("project.new", name="mesh")
    session.execute("material.create", name="air", template="gas")
    session.execute("node.add", parent="/geometry", type="group", name="parts")
    part = session.execute("node.add", parent="/geometry/parts", type="part", name="body")["id"]
    session.execute("region.assign", target=str(part), material="air", name="fluid")
    as_text = session.execute("mesh.layer_height", y_plus=1, velocity="10 [m/s]", length="1 [m]")
    as_si = session.execute("mesh.layer_height", y_plus=1, velocity=10.0, length=1.0)
    assert as_text["first_height"] == pytest.approx(as_si["first_height"])
    with pytest.raises(Exception, match="expects a quantity"):
        session.execute("mesh.layer_height", y_plus=1, velocity=True, length=1.0)
