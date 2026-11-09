"""The ``primitive`` and ``boolean`` operations and the face numbering they report through
(ADR-0021 decisions 6.3, 8.5 and 8.6; T-024), run in this process with ``run_op``."""

from __future__ import annotations

import math

import pytest

pytest.importorskip("OCP")

from stokes.geometry.errors import OperationInvalid
from stokes.geometry.model import OPERATION_TYPE_NAMES
from stokes.geometry.operations import is_built, load, output_basis, output_id, seed_of
from stokes.geometry.operations.base import OpContext, OpInput
from stokes.geometry.operations.faces import face_list, face_table, ids_of, map_history
from stokes.geometry.step import face_count
from tests import geometry_fixtures as fx
from tests.operations_support import part_id, run_op

MM3 = 1e-9  # m^3 per mm^3


def test_a_box_cylinder_and_sphere_with_their_volumes() -> None:
    box = run_op("primitive", [], {"shape": "box", "size": [0.01, 0.02, 0.03]})
    cylinder = run_op(
        "primitive", [], {"shape": "cylinder", "size": [0.002, 0.01, 0.0], "axis": "x"}
    )
    sphere = run_op("primitive", [], {"shape": "sphere", "size": [0.005, 0.0, 0.0]})
    assert box.report["check_after"]["volume"] == pytest.approx(6000 * MM3)
    assert cylinder.report["check_after"]["volume"] == pytest.approx(math.pi * 4 * 10 * MM3)
    assert sphere.report["check_after"]["volume"] == pytest.approx(4 / 3 * math.pi * 125 * MM3)
    assert [r.outputs[0].name for r in (box, cylinder, sphere)] == ["box", "cylinder", "sphere"]
    assert all(
        r.report["check_after"]["valid"] and r.report["check_after"]["closed"]
        for r in (box, cylinder, sphere)
    )
    bbox = cylinder.report["check_after"]["bbox"]
    x0, x1 = bbox[0], bbox[3]
    assert (x0, x1) == (pytest.approx(0.0, abs=1e-9), pytest.approx(0.01, abs=1e-9))  # along x


def test_a_primitive_refuses_sizes_that_are_not_positive() -> None:
    with pytest.raises(OperationInvalid, match="positive"):
        run_op("primitive", [], {"shape": "box", "size": [0.01, 0.0, 0.01]})
    with pytest.raises(OperationInvalid, match="three lengths"):
        run_op("primitive", [], {"size": [0.01]})


def test_a_cut_maps_every_face_through_its_history() -> None:
    block = fx.box(10.0, 10.0, 10.0)
    pin = fx.cylinder(2.0, 12.0, at=(5.0, 5.0, -1.0))
    progress: list[tuple[str, float, str]] = []
    result = run_op(
        "boolean",
        [("block", block), ("pin", pin)],
        {"op": "cut", "tools": [part_id(1)]},
        progress=progress,
    )
    output = result.outputs[0]
    assert output.name == "block_cut" and face_count(output.shape) == 7
    after = result.report["check_after"]
    assert after["valid"] and after["closed"] and after["solids"] == 1
    assert after["volume"] == pytest.approx((1000 - math.pi * 4 * 10) * MM3, rel=1e-6)
    assert [c["volume"] for c in result.report["check_before"]] == [
        pytest.approx(1000 * MM3),
        pytest.approx(math.pi * 4 * 12 * MM3),
    ]
    inputs = [OpInput(part_id(0), "block", block), OpInput(part_id(1), "pin", pin)]
    history = map_history(output.history, inputs, output.shape)
    block_map, pin_map = history[part_id(0)], history[part_id(1)]
    assert sorted(block_map) == [0, 1, 2, 3, 4, 5]  # every face of the block survives
    assert len({tuple(v) for v in block_map.values()}) == 6  # each into its own face
    assert sorted(pin_map) == [0]  # the side becomes the bore; the caps are cut away
    bore = pin_map[0]
    assert len(bore) == 1 and bore[0] not in {i for v in block_map.values() for i in v}
    assert progress[0][0] == "boolean" and progress[-1][1] == 1.0


def test_a_fuse_simplifies_and_a_common_keeps_the_overlap() -> None:
    a = fx.box(10.0, 10.0, 10.0)
    b = fx.box(10.0, 10.0, 10.0, at=(5.0, 0.0, 0.0))
    fused = run_op("boolean", [a, b], {"op": "fuse"})
    plain = run_op("boolean", [a, b], {"op": "fuse", "simplify": False})
    assert face_count(fused.outputs[0].shape) == 6  # one 15 x 10 x 10 box
    assert face_count(plain.outputs[0].shape) > 6
    assert fused.report["check_after"]["volume"] == pytest.approx(1500 * MM3)
    common = run_op("boolean", [a, b], {"op": "common"})
    assert common.report["check_after"]["volume"] == pytest.approx(500 * MM3)
    assert not common.report["simplified"]
    inputs = [OpInput(part_id(0), "body_0", a), OpInput(part_id(1), "body_1", b)]
    history = map_history(fused.outputs[0].history, inputs, fused.outputs[0].shape)
    assert history[part_id(0)] and history[part_id(1)]  # the unify step's history is merged


def test_a_fuzzy_cut_takes_metres() -> None:
    a = fx.box(10.0, 10.0, 10.0)
    b = fx.box(10.0, 10.0, 10.0, at=(10.0 + 1e-4, 0.0, 0.0))  # 0.1 µm apart
    result = run_op("boolean", [a, b], {"op": "fuse", "fuzzy": 1e-6})  # 1 µm
    assert result.report["check_after"]["solids"] == 1
    with pytest.raises(OperationInvalid, match="negative"):
        run_op("boolean", [a, b], {"op": "fuse", "fuzzy": -1e-6})


def test_a_boolean_refuses_what_it_cannot_combine() -> None:
    a = fx.box(1.0, 1.0, 1.0)
    with pytest.raises(OperationInvalid, match="a cut needs tools"):
        run_op("boolean", [a, fx.box(1.0, 1.0, 1.0)], {"op": "cut"})
    with pytest.raises(OperationInvalid, match="two bodies"):
        run_op("boolean", [a], {"op": "fuse"})
    with pytest.raises(OperationInvalid, match="not an input"):
        run_op("boolean", [a], {"op": "cut", "tools": ["n_0000ffff"]})


def test_face_ids_count_repeats_as_the_mesh_does() -> None:
    """A face the explorer visits twice has two ids, as ``step._triangle_counts`` numbers it."""
    box = fx.box(1.0, 1.0, 1.0)
    twice = fx.compound([box, box])
    faces = face_list(twice)
    assert len(faces) == 12 == face_count(twice)
    assert ids_of(twice, [faces[0]]) == [0, 6]
    assert ids_of(twice, [faces[0].Reversed()]) == [0, 6]  # orientation does not matter
    table = face_table(box)
    assert ids_of(box, face_list(box)[::-1], table=table) == list(range(6))
    assert ids_of(box, face_list(fx.box(1.0, 1.0, 1.0))) == []  # other faces, same geometry
    identity = map_history(None, [OpInput(part_id(0), "box", box)], box)
    assert identity == {part_id(0): {i: [i] for i in range(6)}}


def test_ctx_faces_resolves_members_of_the_inputs() -> None:
    box = fx.box(1.0, 1.0, 1.0)
    ctx = OpContext({}, [OpInput(part_id(0), "box", box)])
    faces = face_list(box)
    found = ctx.faces([{"part": part_id(0), "faces": [[1, 2], 4]}])
    assert [ids_of(box, [f]) for f in found] == [[1], [2], [4]]
    assert len(ctx.faces([{"part": part_id(0), "faces": "all"}])) == len(faces)
    with pytest.raises(OperationInvalid, match="out of range"):
        ctx.faces([{"part": part_id(0), "faces": [6]}])
    with pytest.raises(OperationInvalid, match="not an input"):
        ctx.faces([{"part": "n_0000ffff", "faces": "all"}])
    assert (ctx.mm(0.001), ctx.mm2(1e-6), ctx.mm3(1e-9)) == pytest.approx((1.0, 1.0, 1.0))


def test_versions_and_ids_of_outputs_derive_from_the_run() -> None:
    seed = seed_of({"type": "boolean", "params": {"op": "cut"}, "inputs": [["n_00000001", "a"]]})
    assert seed == seed_of(
        {"inputs": [["n_00000001", "a"]], "params": {"op": "cut"}, "type": "boolean"}
    )
    assert seed != seed_of(
        {"type": "boolean", "params": {"op": "fuse"}, "inputs": [["n_00000001", "a"]]}
    )
    basis = output_basis(seed, "8.0.1.0", 0)
    assert len(basis) == 12 and basis != output_basis(seed, "8.0.1.0", 1)
    assert basis != output_basis(seed, "8.0.2.0", 0)  # another OCCT, another numbering
    assert output_id("n_0000abcd", 0) == output_id("n_0000abcd", 0) != output_id("n_0000abcd", 1)


def test_operations_not_built_yet_say_so() -> None:
    # Which modules exist is not this test's business (T-025 to T-028 each add one): ask the
    # registry for a declared type it says is not built, and check the refusal that follows.
    assert is_built("primitive") and is_built("boolean")
    assert not is_built("no_such_type")  # never declared: unknown, not merely unbuilt
    unbuilt = [name for name in OPERATION_TYPE_NAMES if not is_built(name)]
    if unbuilt:
        with pytest.raises(OperationInvalid, match="not built yet"):
            load(unbuilt[0])
    with pytest.raises(OperationInvalid, match="unknown operation type"):
        load("no_such_type")
