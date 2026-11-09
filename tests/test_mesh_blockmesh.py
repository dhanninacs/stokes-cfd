"""The blockMesh driver: side mapping, dictionaries and basis (T-145a, T-206).

**The snapshot's output has been RUN.** The duct snapshot meshes to exactly 25,000 cells
(`blockMesh` exit
0) and passes `checkMesh` (`Mesh OK.`), with `constant/polyMesh/boundary` naming `inlet` and
`outlet` as
`patch` and `walls` as `wall` — which is what proves the side face VERTEX ORDERS are right, not
merely the
vertex sets: a wrong orientation is a `checkMesh` failure. The engine test reproduces that run.

The pure half (`side_mapping`) is tested on a synthetic box, so these tests need no geometry
store, no
OCP and no OpenFOAM.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from stokes.compile.of14 import write as write_fields
from stokes.compile.snapshot import SYMMETRY_PATCH_TYPE, PatchCondition, SolveSnapshot
from stokes.mesh.basis import mesh_basis
from stokes.mesh.drivers.blockmesh import (
    DEFAULT_PATCH,
    DRIVER_VERSION,
    SIDE_FACES,
    cells,
    patch_type,
    patch_types,
    steps,
    write,
)
from stokes.mesh.errors import MeshInvalid
from stokes.mesh.snapshot import SIDES, MeshSnapshot, side_mapping

UNIT = (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)


def unit_box() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A unit box as 12 triangles with face id 0-5, one per side, in `SIDES` order."""
    vertices = np.array(
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
        dtype="float32",
    )
    indices: list[list[int]] = []
    face_ids: list[int] = []
    for face, side in enumerate(SIDES):
        a, b, c, d = SIDE_FACES[side]
        indices += [[a, b, c], [a, c, d]]
        face_ids += [face, face]
    return vertices, np.array(indices, dtype="uint32"), np.array(face_ids, dtype="uint32")


def duct(**overrides: object) -> MeshSnapshot:
    """The duct: 0.5 x 0.02 x 0.02 m, base 0.002 m, three patches."""
    base: dict[str, object] = {
        "settings_id": "n_duct",
        "mesher": "blockmesh",
        "base_size": 0.002,
        "bbox": (0.0, 0.0, 0.0, 0.5, 0.02, 0.02),
        "sides": {
            "xmin": "inlet",
            "xmax": "outlet",
            "ymin": "walls",
            "ymax": "walls",
            "zmin": "walls",
            "zmax": "walls",
        },
        "side_tags": {"xmin": "t_in", "xmax": "t_out", "ymin": "t_w"},
        "side_kinds": {
            "xmin": "velocity_inlet",
            "xmax": "pressure_outlet",
            "ymin": "wall",
            "ymax": "wall",
            "zmin": "wall",
            "zmax": "wall",
        },
        "unmapped": (),
        "max_cells": 5_000_000,
        "cores": 1,
        "max_non_ortho": 70.0,
        "max_skewness": 4.0,
        "domain_part": "n_part",
        "part_basis": "abc123",
        "tag_members": {"inlet": {"members": [1]}},
    }
    return MeshSnapshot(**{**base, **overrides})  # type: ignore[arg-type]


# ---------------------------------------------------------------- the pure mapping


def test_side_mapping_on_a_synthetic_box() -> None:
    """Six sides, three tags: `inlet` and `outlet` on one each, `far_field` on the other four."""
    vertices, indices, face_ids = unit_box()
    sides, unmapped, conflicts = side_mapping(
        vertices,
        indices,
        face_ids,
        {"inlet": {0}, "outlet": {1}, "far_field": {2, 3, 4, 5}},
        UNIT,
        1e-6,
    )
    assert sides == {
        "xmin": "inlet",
        "xmax": "outlet",
        "ymin": "far_field",
        "ymax": "far_field",
        "zmin": "far_field",
        "zmax": "far_field",
    }
    assert unmapped == ()
    assert conflicts == {}


def test_a_tag_inside_the_box_is_unmapped() -> None:
    """**A tag whose faces are inside cannot be a blockMesh patch**, and the honest answer is to
    report it
    rather than to drop it or to guess a side for it."""
    vertices, indices, face_ids = unit_box()
    vertices = np.vstack([vertices, [[0.5, 0.5, 0.5]]])
    indices = np.vstack([indices, [[8, 0, 1]]])
    face_ids = np.append(face_ids, 6)
    sides, unmapped, conflicts = side_mapping(
        vertices, indices, face_ids, {"interior": {6}}, UNIT, 1e-6
    )
    assert sides == {}
    assert unmapped == ("interior",)
    assert conflicts == {}


def test_two_tags_on_one_side_conflict() -> None:
    """A side takes ONE patch, so a conflict is reported with both names rather than resolved."""
    vertices, indices, face_ids = unit_box()
    sides, _, conflicts = side_mapping(
        vertices, indices, face_ids, {"inlet": {0}, "walls": {0}}, UNIT, 1e-6
    )
    assert conflicts == {"xmin": ["inlet", "walls"]}
    assert sides == {"xmin": "inlet"}, (
        "the first name in order keeps the side; the conflict is the report"
    )


def test_the_mapping_does_not_depend_on_the_dict_order() -> None:
    """Tags are processed in NAME order, so two calls on equal input agree."""
    vertices, indices, face_ids = unit_box()
    first = side_mapping(vertices, indices, face_ids, {"b": {0}, "a": {1}}, UNIT, 1e-6)
    second = side_mapping(vertices, indices, face_ids, {"a": {1}, "b": {0}}, UNIT, 1e-6)
    assert first == second


# ---------------------------------------------------------------- cells


def test_cells_counts_up_and_never_below_one() -> None:
    """`ceil(length / base_size)` per axis; a length shorter than the base size still gets one
    cell."""
    snap = duct(bbox=(0.0, 0.0, 0.0, 0.08, 0.06, 0.036), base_size=0.004)
    assert cells(snap) == (20, 15, 9)
    tiny = duct(bbox=(0.0, 0.0, 0.0, 0.001, 0.001, 0.001), base_size=0.5)
    assert cells(tiny) == (1, 1, 1)


def test_the_duct_is_25000_cells() -> None:
    """The brief's number: 0.5 x 0.02 x 0.02 m at 0.002 m."""
    assert cells(duct()) == (250, 10, 10)
    assert cells(duct())[0] * cells(duct())[1] * cells(duct())[2] == 25_000


def test_too_many_cells_is_refused_with_the_counts() -> None:
    """The refusal carries both numbers, so a message can say how far over it is without
    recomputing."""
    with pytest.raises(MeshInvalid) as caught:
        write(duct(base_size=1e-5, max_cells=1000))
    assert caught.value.code == "mesh.invalid"
    assert caught.value.details["max_cells"] == 1000
    assert caught.value.details["cells"] > 1000


def test_a_non_positive_base_size_is_refused() -> None:
    with pytest.raises(MeshInvalid):
        cells(duct(base_size=0.0))


# ---------------------------------------------------------------- the dictionary


def test_the_patch_type_comes_from_the_boundary_kind() -> None:
    """**The rule the brief fixes**: a wall kind and the default patch are `wall`, symmetry is
    `symmetryPlane`, everything else is `patch`."""
    assert patch_type("wall") == "wall"
    assert patch_type("") == "patch", "an unassigned tag is an ordinary patch"
    assert patch_type("symmetry") == "symmetryPlane" == SYMMETRY_PATCH_TYPE
    assert patch_type("velocity_inlet") == "patch"
    assert patch_type("pressure_outlet") == "patch"
    assert patch_type("anything_else") == "patch"


def test_the_duct_dict_has_one_entry_per_patch_in_side_order() -> None:
    """Inlet, outlet, walls — in the order their first side appears in `SIDES`, so a diff
    between two runs
    is about the model rather than about sorting."""
    text = write(duct())["system/blockMeshDict"]
    order = [name for name in ("inlet", "outlet", "walls") if f"\n    {name}\n" in text]
    assert order == ["inlet", "outlet", "walls"]
    # `walls` holds four sides; the two faces of each, so eight face lines plus the tags' two.
    assert text.count("(1 5 4 0)") == 1 and text.count("(4 5 6 7)") == 1
    assert "type            patch;" in text and "type            wall;" in text


def test_an_untagged_side_becomes_an_explicit_unassigned_patch() -> None:
    """**Written explicitly**, not as a `defaultPatch` keyword: an explicit entry is visible in the
    boundary file, which is what T-147b reads."""
    snap = duct(sides={"xmin": "inlet"}, side_kinds={"xmin": "velocity_inlet"})
    text = write(snap)["system/blockMeshDict"]
    assert f"\n    {DEFAULT_PATCH}\n" in text
    assert "defaultPatch" not in text
    # Five sides in it, each once. **Spaced, as the dictionary writes them** — `str(tuple)`
    # would give
    # `(2, 6, 5, 1)` with commas, which is not what a dictionary contains.
    for side in ("xmax", "ymin", "ymax", "zmin", "zmax"):
        face = "(" + " ".join(str(v) for v in SIDE_FACES[side]) + ")"
        assert text.count(face) == 1, f"{side}'s face {face} appears {text.count(face)} times"


def test_the_scale_keyword_is_scale_not_converttometers() -> None:
    """**Measured.** OF14 uses `scale`; `convertToMeters` is the pre-14 spelling and is ignored,
    which
    gives a mesh 1000x the intended size."""
    text = write(duct())["system/blockMeshDict"]
    assert "scale           1.0;" in text
    assert "convertToMeters" not in text


def test_the_control_dict_pins_the_write_format() -> None:
    """T-147b reads the mesh blockMesh writes, so the format cannot be left to a default the reader
    cannot see."""
    control = write(duct())["system/controlDict"]
    assert "writeFormat     ascii;" in control
    assert "writeCompression off;" in control
    # And only these two files: `checkMesh` needs no schemes (measured), and the solver's files are
    # T-212's.
    assert sorted(write(duct())) == ["system/blockMeshDict", "system/controlDict"]


def test_the_duct_golden() -> None:
    """**Byte for byte.** Written from the driver's output **after being read by eye and then RUN**:
    `blockMesh` 25,000 cells, `checkMesh` `Mesh OK.`"""
    from pathlib import Path

    golden = Path(__file__).parent / "golden" / "mesh" / "blockMeshDict.duct"
    assert write(duct())["system/blockMeshDict"] == golden.read_text()


def test_steps_is_blockmesh_then_checkmesh() -> None:
    """Two serial steps. **`checkMesh` takes no flags**: the captured log's own invocation is
    `Exec   : checkMesh`, and it reads `constant/polyMesh` from the working directory.

    blockMesh has no parallel mode, which is why `cores` is not in the basis.
    """
    assert steps(duct()) == [
        {"name": "blockMesh", "argv": ["blockMesh"], "timeout_s": 600},
        {"name": "checkMesh", "argv": ["checkMesh"], "timeout_s": 600, "parse": "checkMesh"},
    ]


def test_a_patch_holding_sides_of_different_kinds_is_refused() -> None:
    """One patch has ONE type, so a tag whose sides carry different conditions cannot be
    expressed."""

    class Fake:
        pass

    snap = duct(
        sides={"xmin": "walls", "xmax": "walls"},
        side_kinds={"xmin": "wall", "xmax": "symmetry"},
    )
    with pytest.raises(MeshInvalid) as caught:
        write(snap)
    assert "different conditions" in str(caught.value)


# ---------------------------------------------------------------- the basis


def test_the_basis_is_forty_hex_characters_and_stable() -> None:
    first = mesh_basis(duct(), DRIVER_VERSION, "org-14")
    assert len(first) == 40
    assert int(first, 16) >= 0, "it must be hex"
    assert first == mesh_basis(duct(), DRIVER_VERSION, "org-14")


@pytest.mark.parametrize(
    ("what", "change"),
    (
        ("base_size", {"base_size": 0.004}),
        ("bbox", {"bbox": (0.0, 0.0, 0.0, 0.5, 0.02, 0.04)}),
        ("sides", {"sides": {"xmin": "inlet"}}),
        ("part_basis", {"part_basis": "different"}),
        ("tag_members", {"tag_members": {"inlet": {"members": [2]}}}),
        ("max_cells", {"max_cells": 100}),
        ("side_kinds", {"side_kinds": {"xmin": "wall"}}),
    ),
)
def test_the_basis_changes_with_what_decides_the_mesh(what: str, change: dict[str, object]) -> None:
    """Each of these changes what blockMesh writes, so a cached mesh made from the old value is
    stale."""
    assert mesh_basis(duct(**change), DRIVER_VERSION, "org-14") != mesh_basis(
        duct(), DRIVER_VERSION, "org-14"
    ), f"the basis did not change with {what}"


@pytest.mark.parametrize(
    ("what", "change"),
    (
        ("cores", {"cores": 8}),
        ("max_non_ortho", {"max_non_ortho": 45.0}),
        ("max_skewness", {"max_skewness": 1.0}),
        ("settings_id", {"settings_id": "other"}),
        ("unmapped", {"unmapped": ("walls",)}),
    ),
)
def test_the_basis_does_not_change_with_what_does_not(what: str, change: dict[str, object]) -> None:
    """**A basis that changes for no reason is one users learn to ignore.** `cores` cannot
    affect a serial
    mesh; the thresholds decide whether it PASSES, not what it is."""
    assert mesh_basis(duct(**change), DRIVER_VERSION, "org-14") == mesh_basis(
        duct(), DRIVER_VERSION, "org-14"
    ), f"the basis changed with {what}"


def test_the_basis_changes_with_the_driver_and_the_engine() -> None:
    """A changed writer or a different OpenFOAM version can produce a different mesh from identical
    input, which would be a silent difference in the answer."""
    base = mesh_basis(duct(), DRIVER_VERSION, "org-14")
    assert mesh_basis(duct(), "blockmesh/2", "org-14") != base
    assert mesh_basis(duct(), DRIVER_VERSION, "com-v2512") != base


def test_floats_are_rounded_so_a_round_trip_does_not_change_the_basis() -> None:
    """`0.1 + 0.2` and `0.3` are the same mesh, and the basis must say so."""
    assert mesh_basis(duct(base_size=0.002), DRIVER_VERSION, "org-14") == mesh_basis(
        duct(base_size=0.0020000000000000001), DRIVER_VERSION, "org-14"
    )


# ---------------------------------------------------------------- FIXES-B: mesh and fields agree

_KIND_OF = {
    "inlet": "velocity_inlet",
    "outlet": "pressure_outlet",
    "walls": "wall",
    "sym": "symmetry",
    DEFAULT_PATCH: "unassigned",
}


_ENTRY = re.compile(r"\n    (\w+)\n    \{\n        type            (\w+);")


def type_words(text: str) -> dict[str, str]:
    """Patch name -> `type` word, read back out of a written `blockMeshDict` (its `boundary`
    list) or a written `0/` field (its `boundaryField`): both indent an entry the same way."""
    return dict(_ENTRY.findall(text))


def compile_for(snap: MeshSnapshot) -> dict[str, str]:
    """The `0/` files for the patches this mesh has: what the solve step does with a mesh result."""
    mesh_types = patch_types(snap)
    conditions = tuple(
        PatchCondition(
            name,
            mesh_type,
            _KIND_OF[name],
            velocity=(1.0, 0.0, 0.0) if name == "inlet" else None,
            pressure=0.0 if name == "outlet" else None,
        )
        for name, mesh_type in mesh_types.items()
    )
    return write_fields(
        SolveSnapshot(
            settings_id="n",
            engine="of14.incompressibleFluid",
            turbulence="laminar",
            iterations=10,
            write_interval=10,
            residual_target=1e-5,
            nu=1e-5,
            rho=1.0,
            patches=conditions,
        )
    )


def test_a_symmetry_side_has_the_same_type_word_in_the_mesh_and_in_both_fields() -> None:
    """**B1.** OpenFOAM 14 stops with "inconsistent patch and patchField types" when the mesh
    says `symmetryPlane` and `0/p` says `symmetry`. Compile a case with a symmetry side and
    compare the three words."""
    snap = duct(
        sides={"xmin": "inlet", "xmax": "outlet", "ymin": "sym", "ymax": "walls"},
        side_kinds={
            "xmin": "velocity_inlet",
            "xmax": "pressure_outlet",
            "ymin": "symmetry",
            "ymax": "wall",
        },
    )
    mesh = type_words(write(snap)["system/blockMeshDict"])
    tree = compile_for(snap)
    for field in ("0/U", "0/p"):
        fields = type_words(tree[field])
        assert fields["sym"] == mesh["sym"] == SYMMETRY_PATCH_TYPE, field


def test_an_untagged_side_is_an_unassigned_wall_in_the_mesh_the_list_and_the_fields() -> None:
    """**B4.** A duct with only inlet and outlet tagged: the four other sides are ONE patch,
    `unassigned`, of type `wall` — in the boundary, in `patch_types` and in both `0/` fields."""
    snap = duct(
        sides={"xmin": "inlet", "xmax": "outlet"},
        side_kinds={"xmin": "velocity_inlet", "xmax": "pressure_outlet"},
    )
    mesh = type_words(write(snap)["system/blockMeshDict"])
    assert mesh == {"inlet": "patch", "outlet": "patch", DEFAULT_PATCH: "wall"}
    assert patch_types(snap) == mesh, "the result's list must be the dictionary's, entry for entry"
    tree = compile_for(snap)
    assert type_words(tree["0/U"])[DEFAULT_PATCH] == "noSlip"
    assert type_words(tree["0/p"])[DEFAULT_PATCH] == "zeroGradient"


def test_a_tag_named_like_the_default_patch_is_refused() -> None:
    """The name `unassigned` belongs to the sides nobody tagged; a tag using it would merge into
    that patch and take its type."""
    snap = duct(sides={"xmin": DEFAULT_PATCH}, side_kinds={"xmin": "velocity_inlet"})
    with pytest.raises(MeshInvalid):
        write(snap)
