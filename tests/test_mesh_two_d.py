"""The 2D mode (ADR-0031): settings, both drivers, the basis, and the solver's `empty`/`wedge`.

The blockMesh dictionaries here have been RUN on FoamWin (OpenFOAM 14): the planar channel meshes
one cell thick and checkMesh reports it as a 2-D mesh; the wedge meshes with its axis collapsed.
The goldens are those dictionaries.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stokes.api import Session, journal
from stokes.compile.of14 import write as write_fields
from stokes.compile.snapshot import KINDS, PatchCondition, SolveSnapshot
from stokes.errors import StokesError
from stokes.mesh import model as mesh_model
from stokes.mesh import two_d
from stokes.mesh.basis import mesh_basis, mesh_basis_parts
from stokes.mesh.drivers import blockmesh, snappy
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.snapshot import (
    SIDES,
    MeshSnapshot,
    _slab_surfaces,
    _two_d_sides,
    snapshot_for,
)

GOLDEN = Path(__file__).parent / "golden" / "mesh"

PLANAR = {"mode": "planar", "plane": "xy", "thickness": 0.0}
WEDGE = {"mode": "axisymmetric", "plane": "xy", "wedge_angle": 5.0}


def channel(**overrides: object) -> MeshSnapshot:
    """A 2D channel in xy: 0.2 x 0.01 m, 1 mm thick, base 1 mm; inlet, outlet, two walls."""
    base: dict[str, object] = {
        "settings_id": "n_chan",
        "mesher": "blockmesh",
        "base_size": 0.001,
        "bbox": (0.0, 0.0, 0.0, 0.2, 0.01, 0.001),
        "sides": {"xmin": "inlet", "xmax": "outlet", "ymin": "walls", "ymax": "walls"},
        "side_tags": {"xmin": "t_in", "xmax": "t_out", "ymin": "t_w", "ymax": "t_w"},
        "side_kinds": {
            "xmin": "velocity_inlet",
            "xmax": "pressure_outlet",
            "ymin": "wall",
            "ymax": "wall",
        },
        "unmapped": (),
        "max_cells": 5_000_000,
        "cores": 1,
        "max_non_ortho": 70.0,
        "max_skewness": 4.0,
        "domain_part": "n_part",
        "part_basis": "abc123",
        "tag_members": {},
        "two_d": dict(PLANAR),
    }
    return MeshSnapshot(**{**base, **overrides})  # type: ignore[arg-type]


# ------------------------------------------------------------------ the pure rules


def test_the_model_spells_the_same_choices_as_the_rules() -> None:
    """`stokes.mesh.model` keeps its stdlib-only import rule by spelling the lists itself."""
    assert mesh_model.TWO_D_MODES == two_d.MODES
    assert mesh_model.TWO_D_PLANES == two_d.PLANES


def test_off_is_none_and_a_missing_setting_is_off() -> None:
    assert two_d.from_settings({}) is None
    assert two_d.from_settings({"two_d": "off", "plane": "yz"}) is None


@pytest.mark.parametrize(
    ("plane", "normal", "sides", "axis", "radial"),
    [
        ("xy", 2, ("zmin", "zmax"), 0, 1),
        ("xz", 1, ("ymin", "ymax"), 0, 2),
        ("yz", 0, ("xmin", "xmax"), 1, 2),
    ],
)
def test_the_plane_names_the_normal_and_its_sides(
    plane: str, normal: int, sides: tuple[str, str], axis: int, radial: int
) -> None:
    mode = two_d.from_settings({"two_d": "planar", "plane": plane})
    assert mode is not None
    assert mode.normal == normal
    assert mode.normal_sides() == sides
    assert (mode.axis, mode.radial) == (axis, radial)


@pytest.mark.parametrize(
    "own",
    [
        {"two_d": "flat"},
        {"two_d": "planar", "plane": "zx"},
        {"two_d": "planar", "thickness": -1.0},
        {"two_d": "axisymmetric", "wedge_angle": 45.0},
    ],
)
def test_bad_settings_are_refused(own: dict[str, Any]) -> None:
    with pytest.raises(MeshInvalid):
        two_d.from_settings(own)


def test_the_record_round_trips() -> None:
    for record in (PLANAR, WEDGE):
        mode = two_d.from_record(record)
        assert mode is not None
        assert mode.record() == record
    assert two_d.from_record({}) is None


def test_thickness_is_centred_on_the_mid_plane() -> None:
    mode = two_d.TwoD("planar", "xy", thickness=0.002)
    assert two_d.with_thickness((0, 0, 0, 1, 1, 0.01), mode) == pytest.approx(
        (0, 0, 0.004, 1, 1, 0.006)
    )
    # zero keeps the box
    assert two_d.with_thickness((0, 0, 0, 1, 1, 0.01), two_d.TwoD("planar", "xy")) == (
        0,
        0,
        0,
        1,
        1,
        0.01,
    )


def test_a_wedge_corner_is_rotated_about_the_axis() -> None:
    mode = two_d.TwoD("axisymmetric", "xy", wedge_angle=4.0)
    half = math.radians(2.0)
    assert two_d.wedge_vertex((0.5, 0.1, 0.0), True, mode) == pytest.approx(
        (0.5, 0.1 * math.cos(half), 0.1 * math.sin(half))
    )
    assert two_d.wedge_vertex((0.5, 0.1, 9.0), False, mode) == pytest.approx(
        (0.5, 0.1 * math.cos(half), -0.1 * math.sin(half))
    )
    # On the axis both sides meet.
    assert two_d.wedge_vertex((0.5, 0.0, 1.0), True, mode) == pytest.approx((0.5, 0.0, 0.0))


def test_a_negative_radius_is_refused() -> None:
    mode = two_d.TwoD("axisymmetric", "xy")
    with pytest.raises(MeshInvalid, match="radius"):
        two_d.check_radius((0, -0.1, 0, 1, 1, 1), mode, 1e-9)
    two_d.check_radius((0, 0, 0, 1, 1, 1), mode, 1e-9)


@pytest.mark.parametrize("name", two_d.RESERVED)
def test_reserved_names_are_refused(name: str) -> None:
    with pytest.raises(MeshInvalid, match=name):
        two_d.refuse_reserved(["inlet", name])


# ------------------------------------------------------------------ blockMesh, planar


def test_planar_has_one_cell_across_the_plane() -> None:
    assert blockmesh.cells(channel()) == (200, 10, 1)
    assert blockmesh.cell_count(channel()) == 2000
    # Even when the box is thicker than many cells.
    thick = channel(bbox=(0.0, 0.0, 0.0, 0.2, 0.01, 0.05))
    assert blockmesh.cells(thick) == (200, 10, 1)


def test_planar_front_and_back_are_one_empty_patch() -> None:
    types = blockmesh.patch_types(channel())
    assert types == {
        "inlet": "patch",
        "outlet": "patch",
        "walls": "wall",
        "frontAndBack": "empty",
    }
    assert blockmesh.patches(channel())["frontAndBack"] == ("empty", ["zmin", "zmax"])


def test_planar_never_writes_an_unassigned_front() -> None:
    """The front/back sides are never the `unassigned` wall in 2D, even untagged."""
    bare = channel(sides={}, side_tags={}, side_kinds={})
    patches = blockmesh.patches(bare)
    assert patches["unassigned"][1] == ["xmin", "xmax", "ymin", "ymax"]
    assert patches["frontAndBack"] == ("empty", ["zmin", "zmax"])


def test_a_tag_named_like_the_front_and_back_is_refused() -> None:
    snap = channel(sides={"xmin": "frontAndBack"}, side_kinds={"xmin": "wall"})
    with pytest.raises(MeshInvalid, match="frontAndBack"):
        blockmesh.patches(snap)


def test_the_planar_channel_golden() -> None:
    text = blockmesh.write(channel())["system/blockMeshDict"]
    assert "(200 10 1)" in text
    assert text == (GOLDEN / "blockMeshDict.channel2d").read_text()


def test_a_3d_snapshot_is_untouched() -> None:
    """2D off: the duct's golden and its basis do not move."""
    snap = channel(two_d={}, bbox=(0.0, 0.0, 0.0, 0.2, 0.01, 0.01))
    assert blockmesh.cells(snap) == (200, 10, 10)
    assert "frontAndBack" not in blockmesh.patch_types(snap)
    assert "two_d" not in str(mesh_basis_parts(snap, "blockmesh/1", "org-14"))


# ------------------------------------------------------------------ blockMesh, axisymmetric


def pipe(**overrides: object) -> MeshSnapshot:
    """A pipe of radius 0.01 m and length 0.2 m, axis x, as a 5-degree wedge."""
    base: dict[str, object] = {
        "bbox": (0.0, 0.0, 0.0, 0.2, 0.01, 0.001),
        "sides": {"xmin": "inlet", "xmax": "outlet", "ymax": "walls"},
        "side_kinds": {"xmin": "velocity_inlet", "xmax": "pressure_outlet", "ymax": "wall"},
        "two_d": dict(WEDGE),
    }
    return channel(**{**base, **overrides})


def test_the_wedge_has_front_back_and_a_collapsed_axis() -> None:
    patches = blockmesh.patches(pipe())
    assert patches["front"] == ("wedge", ["zmax"])
    assert patches["back"] == ("wedge", ["zmin"])
    assert patches["axis"] == ("empty", ["ymin"])
    assert "unassigned" not in patches


def test_an_annular_wedge_has_no_axis_patch() -> None:
    snap = pipe(bbox=(0.0, 0.005, 0.0, 0.2, 0.01, 0.001))
    patches = blockmesh.patches(snap)
    assert "axis" not in patches
    assert patches["unassigned"] == ("wall", ["ymin"])


def test_the_wedge_vertices_are_symmetric_about_the_plane() -> None:
    text = blockmesh.write(pipe())["system/blockMeshDict"]
    half = math.radians(2.5)
    z = 0.01 * math.sin(half)
    assert f"{z:.6g}"[:6] in text
    assert f"-{z:.6g}"[:7] in text
    assert "(200 10 1)" in text


def test_the_wedge_golden() -> None:
    text = blockmesh.write(pipe())["system/blockMeshDict"]
    assert text == (GOLDEN / "blockMeshDict.wedge2d").read_text()


# ------------------------------------------------------------------ the side mapping in 2D


def test_front_and_back_sides_leave_the_users_mapping() -> None:
    mode = two_d.TwoD("planar", "xy")
    sides = {"xmin": "inlet", "zmin": "walls", "zmax": "walls", "ymin": "walls", "zmax2": "x"}
    sides.pop("zmax2")
    kept, unmapped, conflicts = _two_d_sides(
        {**sides, "zmax": "skin"},
        (),
        {"zmin": ["a", "b"], "xmax": ["c", "d"]},
        mode,
        (0, 0, 0, 1, 1, 1),
        1e-9,
    )
    assert kept == {"xmin": "inlet", "ymin": "walls"}
    # `skin` held only a front side: it is now unmapped; `walls` keeps ymin.
    assert unmapped == ("skin",)
    # A conflict on a front/back side is no conflict; elsewhere it still is.
    assert conflicts == {"xmax": ["c", "d"]}


def test_the_axis_side_is_taken_only_at_radius_zero() -> None:
    mode = two_d.TwoD("axisymmetric", "xy")
    kept, _, _ = _two_d_sides({"ymin": "w"}, (), {}, mode, (0, 0, 0, 1, 1, 1), 1e-9)
    assert kept == {}
    kept, _, _ = _two_d_sides({"ymin": "w"}, (), {}, mode, (0, 0.2, 0, 1, 1, 1), 1e-9)
    assert kept == {"ymin": "w"}


# ------------------------------------------------------------------ the basis


def test_the_basis_moves_with_2d_and_stays_put_without_it() -> None:
    flat = channel(two_d={})
    assert mesh_basis(flat, "blockmesh/1", "org-14") != mesh_basis(
        channel(), "blockmesh/1", "org-14"
    )
    assert mesh_basis(channel(), "blockmesh/1", "org-14") != mesh_basis(
        channel(two_d={**PLANAR, "plane": "xz"}), "blockmesh/1", "org-14"
    )


# ------------------------------------------------------------------ snappy on a slab


def slab_mesh() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A 1 x 0.5 x 0.05 slab as 12 triangles, one CAD face per side in `SIDES` order."""
    vertices = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [1, 0.5, 0],
            [0, 0.5, 0],
            [0, 0, 0.05],
            [1, 0, 0.05],
            [1, 0.5, 0.05],
            [0, 0.5, 0.05],
        ],
        dtype="float32",
    )
    indices: list[list[int]] = []
    faces: list[int] = []
    for face, side in enumerate(SIDES):
        a, b, c, d = blockmesh.SIDE_FACES[side]
        indices += [[a, b, c], [a, c, d]]
        faces += [face, face]
    return vertices, np.array(indices, dtype="uint32"), np.array(faces, dtype="uint32")


def test_the_slabs_faces_become_front_and_back_and_leave_the_tags() -> None:
    positions, indices, faces = slab_mesh()
    tags = {"inlet": {0}, "walls": {2, 3, 4}, "lid": {5}}
    kinds = {"inlet": "velocity_inlet", "walls": "wall", "lid": "wall"}
    out, out_kinds, record = _slab_surfaces(
        positions,
        indices,
        faces,
        tags,
        kinds,
        (0.0, 0.0, 0.0, 1.0, 0.5, 0.05),
        two_d.TwoD("planar", "xy"),
        "slab",
    )
    assert out["front"] == {5}
    assert out["back"] == {4}
    assert out["walls"] == {2, 3}
    assert "lid" not in out  # only ever on the front: gone
    assert out_kinds["front"] == "patch"
    assert record == {"mode": "planar", "plane": "xy", "thickness": pytest.approx(0.05)}


def test_a_part_with_no_face_on_the_plane_is_refused() -> None:
    positions, indices, faces = slab_mesh()
    with pytest.raises(MeshInvalid, match="slab"):
        _slab_surfaces(
            positions,
            indices,
            faces,
            {},
            {},
            (0.0, 0.0, -1.0, 1.0, 0.5, 0.05),
            two_d.TwoD("planar", "xy"),
            "slab",
        )


def slab_snapshot(**overrides: object) -> snappy.SnappySnapshot:
    surfaces = tuple(
        snappy.Surface(name=name, file=f"{name}.stl", region=name, patch_type=kind)
        for name, kind in (
            ("inlet", "patch"),
            ("walls", "wall"),
            ("front", "patch"),
            ("back", "patch"),
        )
    )
    base: dict[str, object] = {
        "surfaces": surfaces,
        "background_min": (-0.05, -0.05, 0.0),
        "background_max": (1.05, 0.55, 0.05),
        "background_cells": (22, 12, 1),
        "inside_point": (0.5, 0.25, 0.025),
        "base_size": 0.05,
        "layer_settings": {
            "mode": "final_ratio",
            "expansion_ratio": 1.2,
            "final_ratio": 0.3,
            "first_layer_height": 0.0,
            "min_thickness_ratio": 0.1,
            "feature_angle": 100.0,
        },
        "two_d": {"mode": "planar", "plane": "xy", "thickness": 0.05},
    }
    base.update(overrides)
    snap = snappy.SnappySnapshot(**base)  # type: ignore[arg-type]
    staged = {
        f"{snappy.GEOMETRY_DIR}/{s.file}": f"solid {s.name}\nendsolid {s.name}\n"
        for s in snap.surfaces
    }
    return dataclasses.replace(snap, stl_files=staged)


def test_snappy_2d_writes_the_extrusion() -> None:
    """Measured on FoamWin: this dictionary on a slab gives one layer inside the slab, with
    `front` and `back` both `empty` (checkMesh: 2 solution directions, Mesh OK)."""
    files = snappy.write(slab_snapshot())
    assert files["system/extrudeMeshDict"] == (GOLDEN / "extrudeMeshDict.slab").read_text()
    assert "flipNormals     true;" in files["system/extrudeMeshDict"]
    assert "system/createPatchDict" not in files


def test_snappy_2d_steps_extrude_then_check() -> None:
    names = [step["name"] for step in snappy.steps(slab_snapshot())]
    assert names[-2:] == ["extrudeMesh", "checkMesh"]
    argv = {step["name"]: step["argv"] for step in snappy.steps(slab_snapshot())}
    assert argv["extrudeMesh"] == ["extrudeMesh"]
    # In parallel the extrusion runs on the reconstructed mesh.
    parallel = [step["name"] for step in snappy.steps(slab_snapshot(cores=2))]
    assert parallel.index("reconstructPar") < parallel.index("extrudeMesh")


def test_snappy_2d_patch_types_make_the_slab_faces_empty() -> None:
    assert snappy.patch_types(slab_snapshot()) == {
        "inlet": "patch",
        "walls": "wall",
        "front": "empty",
        "back": "empty",
    }


def test_snappy_3d_has_no_2d_files_or_steps() -> None:
    snap = slab_snapshot(two_d={})
    assert "system/extrudeMeshDict" not in snappy.write(snap)
    assert "extrudeMesh" not in [step["name"] for step in snappy.steps(snap)]


def test_a_zero_extrusion_is_refused() -> None:
    with pytest.raises(MeshInvalid, match="thickness"):
        snappy.write(slab_snapshot(two_d={"mode": "planar", "plane": "xy", "thickness": 0.0}))


# ------------------------------------------------------------------ the solver side


def solve_with(patches: tuple[PatchCondition, ...], turbulence: str = "laminar") -> dict[str, str]:
    return write_fields(
        SolveSnapshot(
            settings_id="n",
            engine="of14.incompressibleFluid",
            turbulence=turbulence,
            iterations=10,
            write_interval=10,
            residual_target=1e-5,
            nu=1e-5,
            rho=1.0,
            patches=patches,
        )
    )


@pytest.mark.parametrize("kind", ["empty", "wedge"])
@pytest.mark.parametrize("turbulence", ["laminar", "kOmegaSST"])
def test_every_field_says_empty_or_wedge_on_the_2d_patches(kind: str, turbulence: str) -> None:
    assert kind in KINDS
    names = ("frontAndBack",) if kind == "empty" else ("front", "back")
    patches = (
        PatchCondition(
            "inlet",
            "patch",
            "velocity_inlet",
            velocity=(1.0, 0.0, 0.0),
            turbulence_mode="intensity_ratio",
            intensity=0.05,
            viscosity_ratio=10.0,
        ),
        PatchCondition("outlet", "patch", "pressure_outlet", pressure=0.0),
        PatchCondition("walls", "wall", "wall"),
        *(PatchCondition(name, kind, kind) for name in names),
    )
    files = solve_with(patches, turbulence)
    fields = [path for path in files if path.startswith("0/")]
    assert "0/U" in fields
    assert "0/p" in fields
    for path in fields:
        for name in names:
            block = files[path].split(f"    {name}\n", 1)[1].split("}", 1)[0]
            assert f"type            {kind};" in block, (path, name)


def test_an_empty_condition_on_a_non_empty_patch_is_refused() -> None:
    with pytest.raises(StokesError):
        solve_with(
            (
                PatchCondition("inlet", "patch", "velocity_inlet", velocity=(1.0, 0.0, 0.0)),
                PatchCondition("frontAndBack", "patch", "empty"),
            )
        )


def test_the_solve_snapshot_gives_a_2d_patch_its_own_kind_whatever_names_it() -> None:
    """The user cannot reassign front/back: the mesh type decides the kind."""
    from stokes.run.snapshot import _patches

    class _Project:
        root = type("R", (), {"walk": staticmethod(lambda: iter(()))})()

        def referrers(self, _: str) -> list[Any]:
            return []

    status = {
        "patches": ["walls", "frontAndBack"],
        "patch_types": {"walls": "wall", "frontAndBack": "empty"},
    }
    out = _patches(None, _Project(), status)  # type: ignore[arg-type]
    kinds = {patch.name: patch.kind for patch in out}
    assert kinds == {"walls": "unassigned", "frontAndBack": "empty"}


# ------------------------------------------------------------------ through the API


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    made = Session()
    made.execute("project.new", name="flat")
    return made


def _settings(session: Session) -> Any:
    return session.require_project().find("/mesh/settings")


def test_configure_sets_2d_and_a_box_snapshot_is_one_cell_thick(session: Session) -> None:
    session.execute(
        "mesh.configure",
        mesher="blockmesh",
        values={
            "base_size": "1 [mm]",
            "domain": "box",
            "box": ["0 [mm]", "0 [mm]", "0 [mm]", "100 [mm]", "10 [mm]", "20 [mm]"],
            "two_d": "planar",
            "plane": "xy",
            "thickness": "2 [mm]",
        },
    )
    node = _settings(session)
    assert node.params["two_d"] == "planar"
    snap = snapshot_for(session, node)
    assert snap.two_d == {"mode": "planar", "plane": "xy", "thickness": pytest.approx(0.002)}
    assert snap.bbox[2] == pytest.approx(0.009)
    assert snap.bbox[5] == pytest.approx(0.011)
    assert blockmesh.cells(snap) == (100, 10, 1)
    assert blockmesh.patch_types(snap) == {"unassigned": "wall", "frontAndBack": "empty"}


def test_configure_refuses_an_unknown_mode_and_leaves_the_tree(session: Session) -> None:
    before = _settings(session).to_json()
    with pytest.raises(StokesError):
        session.execute("mesh.configure", values={"two_d": "flat"})
    with pytest.raises(StokesError):
        session.execute("mesh.configure", values={"wedge_angle": 30})
    assert _settings(session).to_json() == before


def test_snappy_refuses_axisymmetric_and_a_box(session: Session) -> None:
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={"two_d": "axisymmetric", "domain": "box", "box": [0, 0, 0, 1, 1, 1]},
    )
    with pytest.raises(MeshInvalid, match="blockMesh"):
        snapshot_for(session, _settings(session))
    session.execute("mesh.configure", values={"two_d": "planar"})
    with pytest.raises(MeshInvalid, match="slab"):
        snapshot_for(session, _settings(session))


def test_2d_settings_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOKES_HOME", str(tmp_path / "home"))
    path = tmp_path / "journal.jsonl"
    first = Session(journal_path=path)
    first.execute("project.new", name="replay")
    first.execute("mesh.configure", values={"two_d": "axisymmetric", "plane": "xz"})
    first.execute("mesh.configure", values={"wedge_angle": 2.5, "thickness": "3 [mm]"})
    before = _settings(first).to_json()
    second = Session()
    second.execute("project.new", name="replay")
    journal.replay(second, journal.Journal.read(path))
    assert _settings(second).to_json() == before


def test_snappy_2d_background_has_flat_front_and_back_patches() -> None:
    """The background block's own faces across the plane are the 2D planes (wingMotion's layout)."""
    text = snappy.write(slab_snapshot())["system/blockMeshDict"]
    boundary = text.split("boundary", 1)[1]
    assert "background" in boundary
    front = boundary.split("\n    front\n", 1)[1].split("}", 1)[0]
    back = boundary.split("\n    back\n", 1)[1].split("}", 1)[0]
    assert "(4 5 6 7)" in front
    assert "(0 1 2 3)" in back
    background = boundary.split("background", 1)[1].split("}", 1)[0]
    assert "(4 5 6 7)" not in background
    assert "(0 1 2 3)" not in background
    # 3D keeps the single background patch.
    flat = snappy.write(slab_snapshot(two_d={}))["system/blockMeshDict"]
    assert "front" not in flat


def test_the_slab_background_spans_exactly_the_slab() -> None:
    from stokes.mesh.snapshot import _drop_slab_surfaces, _slab_background

    box, point, cells = _slab_background(
        (-0.1, -0.1, -0.1, 1.1, 0.6, 0.2),
        (0.5, 0.25, 0.15),
        (24, 14, 6),
        (0.0, 0.0, 0.0, 1.0, 0.5, 0.05),
        two_d.TwoD("planar", "xy"),
        0.025,
    )
    assert box == (-0.1, -0.1, 0.0, 1.1, 0.6, 0.05)
    assert point == pytest.approx((0.5, 0.25, 0.025))
    assert cells == (24, 14, 2)
    surfaces = slab_snapshot().surfaces
    files = {s.name: s.file for s in surfaces}
    stl = {f"{snappy.GEOMETRY_DIR}/{s.file}": "x" for s in surfaces}
    kept, kept_files, kept_stl = _drop_slab_surfaces(surfaces, files, stl)
    assert [s.name for s in kept] == ["inlet", "walls"]
    assert set(kept_files) == {"inlet", "walls"}
    assert len(kept_stl) == 2
