"""STEP reading and tessellation through OCP (stokes.geometry.step).

Fixtures are generated with OCP into a temporary folder: a box, a cylinder, two named and
coloured solids, a part whose two solids are coloured on labels of their own, a compound of
three solids, an assembly with two instances of one part, a box relabelled to metres and a
file with a STEP header but no content.

Reads that reach OCCT's label attributes run in a process of their own (``run_probe``): a fault
there is an access violation that ends the process with no Python exception, and in this one
it would end pytest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("OCP")

from stokes.geometry.errors import GeometryFileError
from stokes.geometry.mesh import TriMesh, bbox_list
from stokes.geometry.step import (
    COARSE,
    FINE,
    deflections,
    face_count,
    read_step,
    tessellate,
)
from tests import geometry_fixtures as fx

REPO = Path(__file__).resolve().parent.parent


def run_probe(*args: str) -> dict[str, Any]:
    """Run ``python -m tests.step_probe`` in a fresh interpreter and return its result.

    A native fault ends that interpreter, not pytest, and fails the calling test with the exit
    code and the traceback ``faulthandler`` writes (0xC0000005 / -1073741819 on Windows, -11
    on Linux are an access violation).
    """
    done = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-m", "tests.step_probe", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, (
        f"probe {args[0]} ended with exit code {done.returncode}:\n{done.stderr[-1500:]}"
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    assert len(lines) == 1, f"probe {args[0]} printed no result:\n{done.stdout[-1500:]}"
    result: dict[str, Any] = json.loads(lines[0].removeprefix("RESULT "))
    return result


@pytest.fixture(scope="module")
def cad(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("cad")
    box = fx.write_plain(fx.box(10.0, 20.0, 30.0), root / "box.step")
    garbage = root / "garbage.step"
    garbage.write_text("ISO-10303-21;\nHEADER;\nthis is not STEP\n", encoding="utf-8")
    return {
        "box": box,
        "cylinder": fx.write_plain(fx.cylinder(5.0, 40.0), root / "cylinder.stp"),
        "two": fx.write_named(
            [
                ("bracket", fx.box(10.0, 20.0, 30.0), fx.RED),
                ("pin", fx.moved(fx.cylinder(5.0, 40.0), 50.0, 0.0, 0.0), fx.BLUE),
            ],
            root / "two.step",
        ),
        "fixture": fx.write_instanced(root / "fixture.step"),
        # One part, two solids, each coloured on a sub-shape label of its own.
        "subsolids": fx.write_subsolids(root / "subsolids.step"),
        # One part label holding several solids, no sub-shape labels.
        "compound": fx.write_plain(
            fx.compound(
                [
                    fx.box(10.0, 20.0, 30.0),
                    fx.box(4.0, 5.0, 6.0, at=(40.0, 0.0, 0.0)),
                    fx.cylinder(3.0, 12.0, at=(80.0, 0.0, 0.0)),
                ]
            ),
            root / "compound.step",
        ),
        "metres": fx.relabel_units(box, root / "box_m.step", "$,.METRE."),
        "garbage": garbage,
    }


def mesh_of(path: Path, index: int = 0, quality: str = "coarse") -> TriMesh:
    model = read_step(path)
    solid = model.solids[index]
    linear, angular = deflections(solid.shape, COARSE if quality == "coarse" else FINE)
    return tessellate(
        solid.shape,
        linear,
        angular,
        name=solid.name,
        color=solid.color,
        quality=quality,
        unit=model.unit_scale,
    )


def test_plain_file_falls_back_to_positional_names(cad: dict[str, Path]) -> None:
    model = read_step(cad["box"])
    assert [(s.name, s.faces, s.kind) for s in model.solids] == [("solid_1", 6, "solid")]
    assert model.units == "mm" and model.unit_scale == pytest.approx(1e-3)
    assert model.solids[0].color is None and model.faces == 6


def test_names_colours_and_face_counts(cad: dict[str, Path]) -> None:
    model = read_step(cad["two"])
    assert [(s.name, s.faces) for s in model.solids] == [("bracket", 6), ("pin", 3)]
    assert model.solids[0].color == pytest.approx(fx.RED)
    assert model.solids[1].color == pytest.approx(fx.BLUE)


def test_instances_are_named_and_placed(cad: dict[str, Path]) -> None:
    model = read_step(cad["fixture"])
    assert [s.name for s in model.solids] == ["bolt:1", "bolt:2", "plate"]
    assert model.solids[0].color == pytest.approx(fx.GREEN)  # inherited from the part
    first, second = mesh_of(cad["fixture"], 0), mesh_of(cad["fixture"], 1)
    assert first.bbox is not None and second.bbox is not None
    assert second.bbox[0][0] - first.bbox[0][0] == pytest.approx(0.020, abs=1e-6)  # 20 mm apart


def test_box_tessellation_face_ids_normals_and_units(cad: dict[str, Path]) -> None:
    mesh = mesh_of(cad["box"])
    assert mesh.triangle_count == 12 and mesh.vertex_count == 24  # corners shared per face only
    assert mesh.face_ids is not None and mesh.faces == 6
    assert sorted(set(mesh.face_ids.tolist())) == list(range(6))  # dense
    assert bbox_list(mesh.bbox) == pytest.approx([0, 0, 0, 0.01, 0.02, 0.03], abs=1e-7)
    centre = mesh.positions.mean(axis=0)
    assert np.allclose(np.linalg.norm(mesh.normals, axis=1), 1.0, atol=1e-5)
    assert (np.einsum("ij,ij->i", mesh.normals, mesh.positions - centre) > 0).all()
    tri = mesh.positions[mesh.indices]
    facet = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    assert (np.einsum("ij,ij->i", facet, tri.mean(axis=1) - centre) > 0).all()  # outward winding


def test_cylinder_normals_are_radial_and_fine_is_finer(cad: dict[str, Path]) -> None:
    coarse, fine = mesh_of(cad["cylinder"]), mesh_of(cad["cylinder"], quality="fine")
    assert coarse.faces == fine.faces == 3 and fine.triangle_count > coarse.triangle_count
    assert fine.quality == "fine" and sorted(set(fine.face_ids.tolist())) == [0, 1, 2]  # type: ignore[union-attr]
    side = fine.normals[np.abs(fine.normals[:, 2]) < 0.5]
    radial = fine.positions[np.abs(fine.normals[:, 2]) < 0.5][:, :2]
    cosine = np.einsum("ij,ij->i", side[:, :2], radial) / np.linalg.norm(radial, axis=1)
    assert (cosine > 0.99).all()


def test_every_solid_numbers_its_faces_from_zero(cad: dict[str, Path]) -> None:
    pin = mesh_of(cad["two"], 1)  # each solid becomes a part of its own (decision G1)
    assert pin.face_base == 0 and sorted(set(pin.face_ids.tolist())) == [0, 1, 2]  # type: ignore[union-attr]


def test_declared_units_scale_the_mesh(cad: dict[str, Path]) -> None:
    model = read_step(cad["metres"])
    assert model.units == "m"
    assert bbox_list(mesh_of(cad["metres"]).bbox) == pytest.approx([0, 0, 0, 10, 20, 30], abs=1e-4)


def test_unreadable_step_is_a_clear_error(cad: dict[str, Path]) -> None:
    with pytest.raises(GeometryFileError) as info:
        read_step(cad["garbage"])
    assert info.value.code == "geometry.unreadable" and "garbage.step" in info.value.message


def test_a_part_with_several_solids_gives_one_body_each(cad: dict[str, Path]) -> None:
    """One part label holding three solids and no sub-shape labels."""
    model = read_step(cad["compound"])
    assert [s.faces for s in model.solids] == [6, 6, 3]
    assert [s.kind for s in model.solids] == ["solid", "solid", "solid"]
    names = [s.name for s in model.solids]
    assert len(set(names)) == 3 and all(names)


def test_solids_coloured_one_by_one_keep_their_colours(cad: dict[str, Path]) -> None:
    """Regression: a part whose solids carry colours on sub-shape labels crashed the import.

    The reader asked each of those labels for its name with ``FindAttribute``, which faults the
    process on a label that has none, and these have none (the writer drops solid names). It now
    asks ``IsAttribute`` first, and takes each solid's colour from the part's sub-shape labels.
    """
    solids = run_probe("read", str(cad["subsolids"]))["solids"]
    assert [faces for _, faces, _ in solids] == [6, 6]
    assert [name for name, _, _ in solids] == ["bracket", "bracket_2"]  # the part's, made unique
    assert solids[0][2] == pytest.approx(fx.RED)
    assert solids[1][2] == pytest.approx(fx.BLUE)


def test_a_sub_shape_label_names_its_solid() -> None:
    """Where a solid's own label has a name, that name beats the part's (walked in memory)."""
    bodies = run_probe("named_sub_labels")["bodies"]
    assert [name for name, _, _ in bodies] == ["left_block", "right_block"]
    assert bodies[0][1] == pytest.approx(fx.RED) and bodies[1][1] == pytest.approx(fx.BLUE)


def test_labels_without_a_name_read_as_unnamed() -> None:
    """Regression: ``_label_name`` on any label with no TDataStd_Name ended the process.

    The part and both solid sub-labels here have no name attribute; the colours on the
    sub-labels still come through.
    """
    result = run_probe("nameless_labels")
    assert result["part_name"] is None
    assert [name for name, _, _ in result["bodies"]] == [None, None]
    assert result["bodies"][0][1] == pytest.approx(fx.RED)
    assert result["bodies"][1][1] == pytest.approx(fx.BLUE)


def test_reader_never_looks_up_a_sub_shape_label_per_solid() -> None:
    """The reader takes a part's sub-shape labels in one ``GetSubShapes`` call.

    A ``FindSubShape`` call per solid is what it replaced: that is the lookup that reached the
    unguarded ``FindAttribute`` (see ``test_solids_coloured_one_by_one_keep_their_colours``),
    and a part with thousands of solids would make a call for each.
    """
    import ast
    import inspect

    import stokes.geometry.step as step_module

    tree = ast.parse(inspect.getsource(step_module))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "FindSubShape" not in called
    assert "GetSubShapes_s" in called


def test_face_count_and_bad_deflection(cad: dict[str, Path]) -> None:
    solid = read_step(cad["cylinder"]).solids[0]
    assert face_count(solid.shape) == 3
    with pytest.raises(Exception, match="positive"):
        tessellate(solid.shape, 0.0, 0.5)
