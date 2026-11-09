"""The ``enclosure`` operation (ADR-0021 decision 6.2; T-025): a box, cylinder or sphere around
the bodies with per-side margins, symmetry half-models, optional subtraction and tagged faces.

The geometry runs in this process with ``run_op`` on fixture shapes; the last test makes one
through a session, so the derived part, its mesh and the tags it creates are covered too.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import worker
from stokes.geometry.errors import OperationInvalid
from stokes.geometry.operations import is_built, load, output_id
from stokes.geometry.operations.enclosure import run
from stokes.geometry.operations.faces import ids_of
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.operations_support import box_part, create_and_wait, run_op

MM3 = 1e-9  # m^3 per mm^3


def counts(result: Any) -> dict[str, int]:  # Any: OpResult
    """The output's tags by name and how many faces each holds."""
    return {name: len(faces) for name, faces in result.outputs[0].tags.items()}


def members(session: Session, tag: str) -> list[dict[str, Any]]:  # Any: JSON
    return list(session.execute("tag.members", tag=tag)["members"])


def faces_of(session: Session, tag: str, part: str) -> list[int]:
    for member in members(session, tag):
        if member["part"] == part:
            return list(member["faces"])
    return []


def test_a_box_enclosure_with_the_defaults() -> None:
    body = fx.box(1.0, 1.0, 1.0)
    progress: list[tuple[str, float, str]] = []
    result = run_op("enclosure", [("body", body)], progress=progress)
    output = result.outputs[0]
    assert output.name == "enclosure" and output.history is not None
    after = result.report["check_after"]
    assert after["valid"] and after["closed"] and after["solids"] == 1
    # 3 mm upstream, 8 mm downstream, 3 mm on every other side of a 1 mm body.
    assert result.report["bbox"] == pytest.approx([-3e-3, -3e-3, -3e-3, 9e-3, 4e-3, 4e-3], abs=1e-6)
    assert after["volume"] == pytest.approx((7 * 7 * 12 - 1) * MM3, rel=1e-6)
    assert result.report["margins"] == pytest.approx([3e-3, 8e-3, 3e-3, 3e-3, 3e-3, 3e-3])
    assert len(result.report["check_before"]) == 1
    assert result.report["check_before"][0]["volume"] == pytest.approx(MM3)
    assert (result.report["shape"], result.report["flow"], result.report["subtract"]) == (
        "box",
        "+x",
        True,
    )
    assert counts(result) == {"inlet": 1, "outlet": 1, "far_field": 4, "walls": 6}
    # Every face of the output is under exactly one tag, and the tags hold real faces.
    tagged = [index for faces in output.tags.values() for index in ids_of(output.shape, faces)]
    assert sorted(tagged) == list(range(after["faces"]))
    # A 1 x 1 mm body in a 7 x 7 mm section: 2 %, over the warning threshold.
    assert result.report["blockage"] == pytest.approx(1.0 / 49.0, rel=1e-6)
    assert result.warnings == ["blockage above 1 %: 2.0%"]
    assert progress[0][0] == "build" and progress[-1][1] == 1.0


def test_symmetry_keeps_the_positive_half() -> None:
    result = run_op("enclosure", [fx.box(1.0, 1.0, 1.0)], {"symmetry": ["y"]})
    after = result.report["check_after"]
    assert after["valid"] and after["solids"] == 1
    assert after["bbox"] == pytest.approx([-3e-3, 0.5e-3, -3e-3, 9e-3, 4e-3, 4e-3], abs=1e-6)
    assert after["volume"] == pytest.approx((7 * 12 * 3.5 - 0.5) * MM3, rel=1e-6)
    assert result.report["symmetry"] == ["y"]
    assert counts(result) == {
        "inlet": 1,
        "outlet": 1,
        "far_field": 3,
        "symmetry": 1,
        "walls": 5,
    }
    both = run_op("enclosure", [fx.box(1.0, 1.0, 1.0)], {"symmetry": ["x", "y"]})
    assert counts(both) == {"outlet": 1, "far_field": 3, "symmetry": 2, "walls": 4}
    assert both.report["symmetry"] == ["x", "y"]


def test_ground_puts_the_floor_at_the_bodies_lowest_point() -> None:
    body = fx.box(1.0, 1.0, 1.0, at=(0.0, 0.0, 2.0))
    result = run_op("enclosure", [body], {"ground": True})
    after = result.report["check_after"]
    assert after["valid"] and after["closed"] and after["solids"] == 1
    assert (after["bbox"][2], after["bbox"][5]) == (
        pytest.approx(2e-3, abs=1e-6),  # the -z factor is ignored: the floor is the body's
        pytest.approx(6e-3, abs=1e-6),  # the +z margin is still 3 extents
    )
    assert after["volume"] == pytest.approx((7 * 12 * 4 - 1) * MM3, rel=1e-6)
    assert counts(result)["ground"] == 1 and "far_field" in counts(result)
    # Every output face is under exactly one tag: the body sits on the floor, so its own bottom
    # face is cut away and the floor keeps its `ground` tag.
    tagged = [
        index
        for faces in result.outputs[0].tags.values()
        for index in ids_of(result.outputs[0].shape, faces)
    ]
    assert sorted(tagged) == list(range(after["faces"]))
    # Absolute margins are kept when ground is set: only a relative -z margin is dropped.
    absolute = run_op(
        "enclosure",
        [body],
        {"ground": True, "relative": False, "margins": [1e-3, 1e-3, 1e-3, 1e-3, 1e-3, 1e-3]},
    )
    assert absolute.report["bbox"][2] == pytest.approx(1e-3, abs=1e-6)


def test_without_subtract_the_primitive_is_the_output() -> None:
    result = run_op("enclosure", [fx.box(1.0, 1.0, 1.0)], {"subtract": False})
    after = result.report["check_after"]
    assert after["faces"] == 6 and after["solids"] == 1
    assert after["volume"] == pytest.approx(7 * 7 * 12 * MM3, rel=1e-6)
    assert result.outputs[0].history is None
    assert counts(result) == {"inlet": 1, "outlet": 1, "far_field": 4}


def test_cylinder_and_sphere_enclosures_are_valid_solids() -> None:
    body = fx.box(1.0, 1.0, 1.0)
    cylinder = run_op("enclosure", [body], {"shape": "cylinder", "axis": "x"})
    after = cylinder.report["check_after"]
    assert after["valid"] and after["closed"] and after["solids"] == 1
    # A 3.5 mm radius (half the 1 mm lateral extent, the larger 3 mm lateral margin) and 12 mm.
    assert cylinder.report["bbox"] == pytest.approx(
        [-3e-3, -3e-3, -3e-3, 9e-3, 4e-3, 4e-3], abs=1e-6
    )
    assert counts(cylinder) == {"inlet": 1, "outlet": 1, "far_field": 1, "walls": 6}
    sphere = run_op("enclosure", [body], {"shape": "sphere"})
    radius = 0.5 * math.sqrt(3.0) + 8.0  # half the diagonal plus the largest margin (8 mm)
    assert sphere.report["check_after"]["valid"] and sphere.report["check_after"]["closed"]
    assert sphere.report["bbox"] == pytest.approx(
        [
            (0.5 - radius) * 1e-3,
            (0.5 - radius) * 1e-3,
            (0.5 - radius) * 1e-3,
            (0.5 + radius) * 1e-3,
            (0.5 + radius) * 1e-3,
            (0.5 + radius) * 1e-3,
        ],
        abs=1e-6,
    )
    assert counts(sphere) == {"far_field": 1, "walls": 6}
    # A cylindrical body has three faces: the side and the two caps all become walls.
    round_body = fx.cylinder(0.2, 0.5, at=(-0.1, -0.1, -0.25))
    assert counts(run_op("enclosure", [round_body], {}))["walls"] == 3


def test_blockage_above_one_percent_warns() -> None:
    # A 0.6 mm body with 1/3-extent margins: it is 60 % of the 1.0 mm section linearly, so
    # 0.36 of it by area. The ratio is a ratio of areas, not of lengths.
    blocked = run_op("enclosure", [fx.box(0.6, 0.6, 0.6)], {"factors": [1 / 3] * 6})
    assert blocked.report["blockage"] == pytest.approx(0.36, rel=1e-6)
    assert blocked.warnings == ["blockage above 1 %: 36.0%"]
    # The same body in a wide enclosure is quiet.
    calm = run_op("enclosure", [fx.box(0.6, 0.6, 0.6)], {"factors": [30.0] * 6})
    assert calm.report["blockage"] < 0.01 and calm.warnings == []


def test_the_enclosure_refuses_what_it_cannot_use() -> None:
    body = fx.box(1.0, 1.0, 1.0)
    with pytest.raises(OperationInvalid, match="at least one body"):
        run_op("enclosure", [], {})
    with pytest.raises(OperationInvalid, match="unknown enclosure shape"):
        run_op("enclosure", [body], {"shape": "torus"})
    with pytest.raises(OperationInvalid, match="flow direction"):
        run_op("enclosure", [body], {"flow": "up"})
    with pytest.raises(OperationInvalid, match="cylinder axis"):
        run_op("enclosure", [body], {"axis": "w"})
    with pytest.raises(OperationInvalid, match="six values"):
        run_op("enclosure", [body], {"factors": [1.0]})
    with pytest.raises(OperationInvalid, match="six values"):
        run_op("enclosure", [body], {"margins": [0.0] * 7})
    with pytest.raises(OperationInvalid, match="factors cannot be negative"):
        run_op("enclosure", [body], {"factors": [-1.0] * 6})
    with pytest.raises(OperationInvalid, match="margins cannot be negative"):
        run_op("enclosure", [body], {"relative": False, "margins": [-1e-3] * 6})
    with pytest.raises(OperationInvalid, match="negative"):
        run_op("enclosure", [body], {"fuzzy": -1e-6})
    with pytest.raises(OperationInvalid, match="symmetry plane"):
        run_op("enclosure", [body], {"symmetry": ["w"]})


def test_the_framework_finds_the_module() -> None:
    assert is_built("enclosure")
    assert load("enclosure") is run


def test_through_the_session_it_makes_a_tagged_derived_part(tmp_path: Path) -> None:
    worker.prewarm(1)
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    try:
        body = box_part(session)  # a 20 mm box at the origin
        info = create_and_wait(session, "enclosure", ["box"], {"flow": "+x"})
        assert info["status"] == "ok", info["error"]
        out = info["outputs"][0]
        assert out == output_id(info["id"], 0)
        project = session.require_project()
        node = project.get(out)
        assert node.params["derived"] == info["id"] and node.params["family"] == "brep"
        assert node.params["units"] == "mm" and node.params["faces"] == 12
        assert node.params["visible"] is True
        assert GeometryStore.of(session).mesh(out) is not None
        assert {t["name"] for t in session.execute("tag.list")} >= {
            "inlet",
            "outlet",
            "far_field",
            "walls",
        }
        assert len(faces_of(session, "walls", out)) == 6
        assert len(faces_of(session, "inlet", out)) == 1
        assert len(faces_of(session, "outlet", out)) == 1
        assert len(faces_of(session, "far_field", out)) == 4
        assert info["report"]["blockage"] == pytest.approx(0.0204, rel=1e-2)
        session.execute("history.undo")
        assert project.find(out) is None and project.find(info["id"]) is None
        assert project.get(body).params["visible"] is True
        # The run created those tags: undo removes the ones it leaves empty.
        assert session.execute("tag.list") == []
        GeometryStore.of(session).close()
    finally:
        worker.shutdown_pool()
