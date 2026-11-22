"""The ``transform`` operation: move, rotate, mirror and scale B-rep bodies (ADR-0021 decision 8).

The module runs in this process with ``run_op`` for the geometry, and once through a session to
show that an operation-made box turns, keeps its faces and carries its tags; a mesh-only part is
refused with a message that says what to do instead.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry.errors import MeshOnly, OperationInvalid
from stokes.geometry.operations.base import OpInput
from stokes.geometry.operations.faces import face_list, map_history
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.operations_support import box_part, create_and_wait, part_id, run_op
from tests.selection_support import add_part, cube, new_session, part

MM3 = 1e-9  # m^3 per mm^3
ZERO = [0.0, 0.0, 0.0]


def transformed(kind: str, shapes: list[Any], **params: Any) -> Any:  # Any: OCP shapes, result
    base = {"kind": kind, "offset": ZERO, "centre": ZERO, "axis": "z", "angle": 0.0, "factor": 1.0}
    return run_op("transform", shapes, {**base, **params})


def bbox_mm(result: Any, index: int = 0) -> list[float]:  # Any: OpResult
    """Output ``index``'s bounding box in mm."""
    return [v * 1000.0 for v in result.outputs[index].report["check_after"]["bbox"]]


def centre_of(face: Any) -> tuple[float, float, float]:  # Any: OCP TopoDS_Face
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    point = props.CentreOfMass()
    return point.X(), point.Y(), point.Z()


def test_a_quarter_turn_about_z_swaps_the_x_and_y_extents_and_keeps_every_face() -> None:
    box = fx.box(10.0, 20.0, 30.0)
    result = transformed("rotate", [("block", box)], angle=math.pi / 2)
    output = result.outputs[0]
    assert output.name == "block"
    # (x, y) -> (-y, x): x spans [-20, 0], y spans [0, 10], z is untouched.
    assert bbox_mm(result) == pytest.approx([-20.0, 0.0, 0.0, 0.0, 10.0, 30.0], abs=1e-6)
    after = output.report["check_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 6 == len(face_list(box))
    assert after["volume"] == pytest.approx(6000 * MM3)
    # Face i of the output is face i of the input, turned: the history says so one to one.
    history = map_history(output.history, [OpInput(part_id(0), "block", box)], output.shape)
    assert history == {part_id(0): {i: [i] for i in range(6)}}
    for old, new in zip(face_list(box), face_list(output.shape), strict=True):
        x, y, z = centre_of(old)
        assert centre_of(new) == pytest.approx((-y, x, z), abs=1e-6)
    edges = map_history(output.history, [OpInput(part_id(0), "block", box)], output.shape, "edge")
    assert edges == {part_id(0): {i: [i] for i in range(12)}}


def test_a_rotation_turns_about_its_centre() -> None:
    box = fx.box(10.0, 10.0, 10.0)
    result = transformed("rotate", [box], angle=math.pi, centre=[0.01, 0.0, 0.0], axis="z")
    assert bbox_mm(result) == pytest.approx([10.0, -10.0, 0.0, 20.0, 0.0, 10.0], abs=1e-6)


def test_a_move_shifts_the_box_by_its_offset_in_metres() -> None:
    result = transformed("translate", [fx.box(10.0, 10.0, 10.0)], offset=[0.005, -0.002, 0.03])
    assert bbox_mm(result) == pytest.approx([5.0, -2.0, 30.0, 15.0, 8.0, 40.0], abs=1e-6)
    assert result.outputs[0].report["check_after"]["faces"] == 6


def test_a_mirror_reflects_in_the_plane_through_the_centre_and_stays_a_sound_solid() -> None:
    box = fx.box(10.0, 10.0, 10.0)
    result = transformed("mirror", [box], axis="x", centre=[0.02, 0.0, 0.0])
    assert bbox_mm(result) == pytest.approx([30.0, 0.0, 0.0, 40.0, 10.0, 10.0], abs=1e-6)
    after = result.outputs[0].report["check_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 6
    assert after["volume"] == pytest.approx(1000 * MM3)  # outward still: not inside out


def test_a_scale_grows_the_volume_by_the_cube_of_the_factor() -> None:
    result = transformed("scale", [fx.box(10.0, 10.0, 10.0)], factor=2.0)
    assert result.outputs[0].report["check_after"]["volume"] == pytest.approx(8000 * MM3)
    with pytest.raises(OperationInvalid, match="positive"):
        transformed("scale", [fx.box(1.0, 1.0, 1.0)], factor=0.0)


def test_every_input_becomes_an_output_of_its_own() -> None:
    a, b = fx.box(1.0, 1.0, 1.0), fx.cylinder(1.0, 2.0, at=(5.0, 0.0, 0.0))
    result = transformed("translate", [("a", a), ("b", b)], offset=[0.0, 0.0, 0.001])
    assert [o.name for o in result.outputs] == ["a", "b"]
    assert bbox_mm(result, 1)[2] == pytest.approx(1.0, abs=1e-6)
    assert result.report["kind"] == "translate" and len(result.report["check_after"]) == 2


def test_a_transform_refuses_what_it_cannot_do() -> None:
    with pytest.raises(OperationInvalid, match="kind must be one of"):
        transformed("shear", [fx.box(1.0, 1.0, 1.0)])
    with pytest.raises(OperationInvalid, match="unknown axis"):
        transformed("rotate", [fx.box(1.0, 1.0, 1.0)], axis="w")
    with pytest.raises(OperationInvalid, match="finite"):
        transformed("rotate", [fx.box(1.0, 1.0, 1.0)], angle=math.inf)


# ---------------------------------------------------------------- through a session


def test_rotating_a_box_through_the_api_keeps_its_faces_and_its_tags(tmp_path: Path) -> None:
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    box = box_part(session, size=(0.01, 0.02, 0.03))
    session.execute("tag.create", name="lid", members=[{"part": box, "faces": [5]}])
    info = create_and_wait(
        session, "transform", [box], {"kind": "rotate", "axis": "z", "angle": "90 [deg]"}
    )
    assert info["status"] == "ok", info["error"]
    assert info["label"] == "Transform" and len(info["outputs"]) == 1
    project = session.require_project()
    out = project.get(info["outputs"][0])
    assert out.params["faces"] == 6 and out.params["derived"] == info["id"]
    assert out.params["bbox"] == pytest.approx([-0.02, 0.0, 0.0, 0.0, 0.01, 0.03], abs=1e-9)
    assert project.get(box).params.get("visible") is False  # the input hides behind its output
    # ...and leaves the simulation: the turned body replaces it, so it is not meshed twice.
    assert project.get(box).params.get("simulate") is False
    lid = next(t for t in session.execute("tag.list") if t["name"] == "lid")
    members = json.loads(project.get(lid["id"]).params["members"])
    carried = [m for m in members if m["part"] == out.id]
    assert carried and carried[0]["faces"] == [[5, 5]]
    assert not info["report"].get("unmapped")
    GeometryStore.of(session).close()


def test_a_mesh_only_part_is_refused_with_what_to_do_instead(tmp_path: Path) -> None:
    session = new_session(tmp_path)
    mesh = add_part(session, "mesh", part(cube(0.01)))
    session.execute("parameter.set", node=mesh, name="family", value="mesh")
    with pytest.raises(MeshOnly, match="import the CAD file") as refused:
        session.execute("operation.create", type="transform", inputs=[mesh], params={})
    assert refused.value.code == "geometry.mesh_only"
