"""The ``cap``, ``unify``, ``slivers`` and ``defeature`` operations (ADR-0021 decision 5; T-028).

The four run in this process with ``run_op``, so the geometry is tested without a session or a
worker; the last two tests take the two chains the brief asks for through a session: a ``cap``
tag renamed by the user and handed to ``extract_fluid`` as its ``caps``, and a tag that follows a
``defeature`` run and the ``unify`` step inside it.

The face ids a test picks come from the fixture's own ``TopExp`` order or from the B-rep the
session cached, never from a hard-coded number that another OCP could reorder.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("OCP")

from stokes.api import Session
from stokes.geometry import shapes, worker
from stokes.geometry.errors import GeometryError
from stokes.geometry.operations import repair_common as rm
from stokes.geometry.store import GeometryStore
from tests import geometry_fixtures as fx
from tests.operations_support import create_and_wait, run_op

MM3 = 1e-9  # m^3 per mm^3
BOX_MM3 = 8000.0  # the 20 mm fixture box, in mm^3
TEE_RADIUS = 5.0
TEE_LENGTH = 30.0


@pytest.fixture(scope="module", autouse=True)
def warm() -> Iterator[None]:
    """One worker kept ahead for the session tests, as the service does."""
    worker.prewarm(1)
    yield None
    worker.shutdown_pool()


def cylinder_faces(shape: Any) -> list[int]:  # Any: OCP TopoDS_Shape
    """The ids of the faces of ``shape`` whose surface is a cylinder, in face-id order."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopoDS import TopoDS

    return [
        index
        for index, face in enumerate(rm.faces(shape))
        if BRepAdaptor_Surface(TopoDS.Face(face)).GetType() == GeomAbs_SurfaceType.GeomAbs_Cylinder
    ]


def planar_faces_at(shape: Any, axis: int, at: float) -> list[int]:  # Any: OCP TopoDS_Shape
    """The ids of the planar faces whose plane sits at ``at`` along ``axis``, in face-id order."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopoDS import TopoDS

    found: list[int] = []
    for index, face in enumerate(rm.faces(shape)):
        surface = BRepAdaptor_Surface(TopoDS.Face(face))
        if surface.GetType() != GeomAbs_SurfaceType.GeomAbs_Plane:
            continue
        point = surface.Plane().Location()
        if abs((point.X(), point.Y(), point.Z())[axis] - at) < 1e-6:
            found.append(index)
    return found


def cached_shape(session: Session, part: str) -> Any:  # Any: OCP TopoDS_Shape in mm
    """The B-rep the session cached for ``part``: how a test finds a fixture's face ids."""
    store = GeometryStore.of(session)
    path = shapes.brep_path(store.shape_dir(), part)
    entry = shapes.read_entry(path)
    assert entry is not None
    shape = shapes.read_shape(path, entry)
    assert shape is not None
    return shape


def tee_volume() -> float:
    """The pipe tee's own volume in mm^3: two run cylinders and a branch, less their overlap."""
    radius, length = TEE_RADIUS, TEE_LENGTH
    return math.pi * radius**2 * length + math.pi * radius**2 * length / 2 - 8 * radius**3 / 3


# --------------------------------------------------------------------- cap
def test_cap_closes_a_box_missing_a_face() -> None:
    result = run_op("cap", [("block", fx.open_box())])
    output = result.outputs[0]
    assert output.name == "block_capped"
    after = output.report["check_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 6 and after["solids"] == 1
    assert after["volume"] == pytest.approx(BOX_MM3 * MM3)
    assert result.report["loops"] == 1
    assert (result.report["capped_planar"], result.report["capped_smooth"]) == (1, 0)
    assert result.report["left_open"] == 0 and result.report["closed"] is True
    assert result.warnings == []
    # The cap face is a new face, named under the input's tag so the user can rename it.
    assert list(output.tags) == ["block_caps"] and len(output.tags["block_caps"]) == 1


def test_cap_caps_a_cylinder_that_lost_an_end_with_planar_only() -> None:
    result = run_op(
        "cap", [("pipe", fx.open_cylinder(TEE_RADIUS, TEE_LENGTH))], {"planar_only": True}
    )
    after = result.outputs[0].report["check_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 3
    assert after["volume"] == pytest.approx(math.pi * TEE_RADIUS**2 * TEE_LENGTH * MM3)
    assert result.report["capped_planar"] == 1 and result.report["left_open"] == 0


def test_cap_fills_a_non_planar_opening_smoothly() -> None:
    # The box missing two adjacent faces has one free loop of six edges that spans z: no plane
    # fits it, so the cap comes from BRepFill_Filling.
    result = run_op("cap", [("corner", fx.corner_open_box())])
    after = result.outputs[0].report["check_after"]
    assert result.report["loops"] == 1 and result.report["capped_smooth"] == 1
    assert result.report["capped_planar"] == 0 and result.report["left_open"] == 0
    assert after["valid"] and after["closed"] and after["faces"] == 5
    assert len(result.outputs[0].tags["corner_caps"]) == 1


def test_cap_leaves_a_non_planar_opening_open_with_planar_only() -> None:
    result = run_op("cap", [("corner", fx.corner_open_box())], {"planar_only": True})
    after = result.outputs[0].report["check_after"]
    assert result.report["capped_planar"] == 0 and result.report["capped_smooth"] == 0
    assert result.report["left_open"] == 1 and result.report["closed"] is False
    assert after["faces"] == 4 and after["free_edges"] == 6
    assert result.outputs[0].tags == {}
    assert result.warnings and "left open" in result.warnings[0]


def test_cap_skips_an_opening_above_max_size() -> None:
    # 1e-4 m = 0.1 mm: the box's 28 mm loop is far above it and stays open.
    result = run_op("cap", [("block", fx.open_box())], {"max_size": 1e-4})
    assert result.report["loops"] == 1 and result.report["left_open"] == 1
    assert result.report["closed"] is False
    assert result.outputs[0].tags == {}
    # The other way round: a size above the loop caps it.
    capped = run_op("cap", [("block", fx.open_box())], {"max_size": 0.1})
    assert capped.report["capped_planar"] == 1 and capped.report["left_open"] == 0


def test_cap_tags_every_opening_of_a_pipe_body() -> None:
    result = run_op("cap", [("tee", fx.pipe_tee(TEE_RADIUS, TEE_LENGTH))])
    output = result.outputs[0]
    after = output.report["check_after"]
    assert result.report["loops"] == 3 and result.report["capped_planar"] == 3
    assert result.report["left_open"] == 0 and result.report["closed"] is True
    assert after["valid"] and after["closed"] and after["faces"] == 6 and after["solids"] == 1
    assert after["volume"] == pytest.approx(tee_volume() * MM3, rel=1e-6)
    assert len(output.tags["tee_caps"]) == 3
    assert not output.tags["tee_caps"][0].IsNull()


def test_cap_refuses_a_non_positive_tolerance() -> None:
    with pytest.raises(GeometryError, match="tolerance must be positive"):
        run_op("cap", [("block", fx.open_box())], {"tolerance": 0.0})


# --------------------------------------------------------------------- unify
def test_unify_merges_the_two_halves_of_a_split_cylinder() -> None:
    split = fx.split_cylinder(TEE_RADIUS, TEE_LENGTH)
    assert len(cylinder_faces(split)) == 2
    result = run_op("unify", [("pipe", split)])
    output = result.outputs[0]
    assert output.name == "pipe_unified"
    after = output.report["check_after"]
    assert result.report["faces_before"] == 6 and result.report["faces_after"] == 3
    assert result.report["edges_before"] > result.report["edges_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 3
    assert after["volume"] == pytest.approx(math.pi * TEE_RADIUS**2 * TEE_LENGTH * MM3, rel=1e-9)


def test_unify_keeps_the_faces_a_keep_tag_names_apart() -> None:
    split = fx.split_cylinder(TEE_RADIUS, TEE_LENGTH)
    sides = cylinder_faces(split)
    assert len(sides) == 2
    result = run_op(
        "unify",
        [("pipe", split)],
        {"keep_members": [{"part": "n_00000000", "faces": sides}]},
    )
    assert result.report["keep"] == 2 and result.report["faces_after"] == 4
    # ... while the same run without the tag merges the two halves into one face.
    plain = run_op("unify", [("pipe", fx.split_cylinder(TEE_RADIUS, TEE_LENGTH))])
    assert plain.report["faces_after"] == 3


def test_unify_refuses_a_keep_tag_that_is_not_a_member_list() -> None:
    with pytest.raises(GeometryError, match="list of tag members"):
        run_op("unify", [("pipe", fx.split_cylinder())], {"keep_members": {"part": "n_00000000"}})


# --------------------------------------------------------------------- slivers
def test_slivers_drops_the_step_faces_of_a_box() -> None:
    result = run_op("slivers", [("block", fx.stepped_box())], {"size": 1e-4})
    output = result.outputs[0]
    assert output.name == "block_slivered"
    after = output.report["check_after"]
    assert result.report["strip_faces"] == 2 and result.report["spot_faces"] == 0
    assert result.report["faces_before"] == 8 and result.report["faces_after"] == 6
    assert after["valid"] and after["closed"] and after["faces"] == 6
    assert result.report["holes"] is False and result.report["wires_removed"] == 0


def test_slivers_keeps_a_hole_it_was_not_asked_about() -> None:
    plate = fx.holed_box(size=(20.0, 20.0, 2.0), radius=0.01)
    result = run_op("slivers", [("plate", plate)], {"size": 1e-4})
    assert result.report["wires_removed"] == 0
    assert result.report["faces_after"] == result.report["faces_before"] == 7


def test_slivers_with_holes_removes_a_tiny_hole_and_keeps_a_real_one() -> None:
    # MinArea is size^2 = 0.01 mm^2: a 0.01 mm-radius hole (3e-4 mm^2) goes, a 3 mm one stays.
    tiny = fx.holed_box(size=(20.0, 20.0, 2.0), radius=0.01)
    result = run_op("slivers", [("plate", tiny)], {"size": 1e-4, "holes": True})
    assert result.report["wires_removed"] == 2 and result.report["holes"] is True
    assert result.outputs[0].report["check_after"]["valid"]
    real = run_op("slivers", [("block", fx.holed_box())], {"size": 1e-4, "holes": True})
    assert real.report["wires_removed"] == 0
    assert len(cylinder_faces(real.outputs[0].shape)) == 1  # the 3 mm bore is still there


def test_slivers_does_not_delete_a_body_with_no_slivers() -> None:
    """A part the fixers cannot help comes back whole, and the run says they were put aside.

    Regression: on a part whose faces all read as spots at the fixer's precision, every step in the
    chain called itself a success while the last one answered with 0 faces, no shell and no volume,
    and the operation returned that as a "slivered" output. The geometry worker then died on the
    empty shape, so the user saw a failed operation rather than a destroyed part. A repair may drop
    slivers; it may not drop the model.
    """
    tiny = fx.box(0.02, 0.02, 0.02)
    result = run_op("slivers", [("block", tiny)], {"size": 1e-4})
    after = result.outputs[0].report["check_after"]
    assert result.report["faces_before"] == 6
    assert result.report["faces_after"] == 6, "the body lost faces it had no reason to lose"
    assert after["faces"] == 6 and after["solids"] == 1
    assert after["valid"] and after["closed"], "the body came back empty or open"
    assert result.warnings and "left as it was" in result.warnings[0]


def test_slivers_leaves_a_healthy_body_alone_and_says_nothing() -> None:
    """The other side of the guard: a part with nothing to drop is not touched, and warns not."""
    result = run_op("slivers", [("block", fx.box(20.0, 20.0, 20.0))], {"size": 1e-4})
    after = result.outputs[0].report["check_after"]
    assert (result.report["faces_before"], result.report["faces_after"]) == (6, 6)
    assert after["valid"] and after["closed"] and after["faces"] == 6
    assert after["volume"] == pytest.approx(BOX_MM3 * MM3)
    assert result.warnings == []


def test_slivers_refuses_a_size_that_is_not_positive() -> None:
    with pytest.raises(GeometryError, match="size must be positive"):
        run_op("slivers", [("block", fx.holed_box())], {"size": 0.0})


# --------------------------------------------------------------------- defeature
def test_defeature_removes_a_through_hole() -> None:
    holed = fx.holed_box()
    bores = cylinder_faces(holed)
    assert len(bores) == 1
    result = run_op(
        "defeature", [("block", holed)], {"faces": [{"part": "n_00000000", "faces": bores}]}
    )
    output = result.outputs[0]
    assert output.name == "block_defeatured"
    after = output.report["check_after"]
    assert result.report["faces_removed"] == 1
    assert result.report["faces_before"] == 7 and result.report["faces_after"] == 6
    assert after["valid"] and after["closed"] and after["faces"] == 6
    assert after["volume"] == pytest.approx(BOX_MM3 * MM3)
    assert cylinder_faces(output.shape) == []


def test_defeature_without_unify_keeps_all_the_faces_it_made() -> None:
    holed = fx.holed_box()
    bores = cylinder_faces(holed)
    result = run_op(
        "defeature",
        [("block", holed)],
        {"faces": [{"part": "n_00000000", "faces": bores}], "unify": False},
    )
    assert result.report["unified"] is False
    assert result.report["faces_after"] == 6
    assert result.outputs[0].report["check_after"]["volume"] == pytest.approx(BOX_MM3 * MM3)


def test_defeature_needs_a_face_to_remove() -> None:
    with pytest.raises(GeometryError, match="at least one face"):
        run_op("defeature", [("block", fx.holed_box())], {"faces": []})


def test_defeature_reports_a_face_that_is_not_one_of_the_inputs() -> None:
    with pytest.raises(GeometryError, match="out of range"):
        run_op(
            "defeature",
            [("block", fx.holed_box())],
            {"faces": [{"part": "n_00000000", "faces": [99]}]},
        )


# --------------------------------------------------------------------- session
def test_a_cap_tag_seals_a_pipe_for_extract_fluid(tmp_path: Path) -> None:
    """The brief's "Done means": Close openings tags the pipe's caps, and the fluid follows."""
    source = fx.write_brep(fx.pipe_tee(TEE_RADIUS, TEE_LENGTH), tmp_path / "tee.brep")
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    pipe = session.execute("geometry.import", path=str(source), wait=True)["id"]
    capped = create_and_wait(session, "cap", [pipe])
    assert capped["status"] == "ok", capped["error"]
    project = session.require_project()
    output = capped["outputs"][0]
    assert project.get(output).path == "/geometry/parts/tee_capped"
    created = list(project.get(capped["id"]).meta["created_tags"])
    assert len(created) == 1
    tag = project.get(created[0])
    assert tag.name == "tee_caps" and tag.params["kind"] == "boundary"
    members = session.execute("tag.members", tag=created[0])["members"]
    assert len(members) == 1 and members[0]["part"] == output and len(members[0]["faces"]) == 3
    # The user calls them inlet and outlet; the fluid extraction takes the tag as its caps.
    session.execute("node.rename", node=tag.id, name="inlet")
    fluid = create_and_wait(
        session, "extract_fluid", [output], {"caps": created[0], "seed": [0.0, 0.0, 0.0]}
    )
    assert fluid["run"]["ok"] is True, fluid.get("error")
    assert fluid["report"]["check_after"]["volume"] == pytest.approx(tee_volume() * MM3, rel=1e-6)
    assert fluid["report"]["check_after"]["faces"] == 6
    assert [
        m for m in session.execute("tag.members", tag="inlet")["members"] if m["part"] == output
    ]
    GeometryStore.of(session).close()


def test_a_tag_follows_a_defeature_and_its_unify_through_a_session(tmp_path: Path) -> None:
    """A tag on the box's top face is still on the output's top face after both steps."""
    source = fx.write_brep(fx.holed_box(), tmp_path / "holed.brep")
    session = Session(journal_path=tmp_path / "demo.jsonl")
    session.execute("project.new", name="demo", path=str(tmp_path / "demo"))
    part = session.execute("geometry.import", path=str(source), wait=True)["id"]
    shape = cached_shape(session, part)
    bores = cylinder_faces(shape)
    top = planar_faces_at(shape, 2, 20.0)
    assert len(bores) == 1 and len(top) == 1
    session.execute("tag.create", name="top", members=[{"part": part, "faces": top}])
    result = create_and_wait(
        session, "defeature", [part], {"faces": [{"part": part, "faces": bores}]}
    )
    assert result["status"] == "ok", result["error"]
    output = result["outputs"][0]
    assert result["report"]["faces_removed"] == 1 and result["report"]["unified"] is True
    assert result["report"]["check_after"]["volume"] == pytest.approx(BOX_MM3 * MM3)
    members = session.execute("tag.members", tag="top")["members"]
    followed = [member for member in members if member["part"] == output]
    assert len(followed) == 1 and len(followed[0]["faces"]) == 1
    # ... and it is the output's own top face, not some other one.
    assert followed[0]["faces"] == planar_faces_at(cached_shape(session, output), 2, 20.0)
    GeometryStore.of(session).close()


def test_cap_consumes_a_tagged_edge_loop() -> None:
    """ADR-0022 §15.4: a tagged edge loop, not every opening, is what gets capped (T-059)."""
    shape = fx.open_box()
    # Enumerate the fixture's own edges the way the topology record numbers them, so the ids are
    # the ones a user would have picked rather than a hard-coded guess.
    import OCP.collections as occ
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp

    from stokes.geometry.operations.base import OpContext, OpInput

    mapping = occ.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, mapping)
    total = mapping.Extent()
    assert total == 12, "an open box has twelve edges"

    # Which of those edges lie on the single open loop? Ask the same helper the operation uses.
    loops = rm.closed_wires(shape, 1e-5)
    assert len(loops) == 1
    ctx = OpContext({}, [OpInput("n_00000000", "block", shape)])
    on_loop = [
        edge_id
        for edge_id in range(total)
        if _edge_lies_on(
            ctx.edges([{"part": "n_00000000", "faces": [], "edges": [edge_id]}])[0], loops[0]
        )
    ]
    assert len(on_loop) == 4, "the box's missing face is bounded by four edges"

    result = run_op(
        "cap",
        [("block", shape)],
        {"loop_members": [{"part": "n_00000000", "faces": [], "edges": on_loop}]},
    )
    after = result.outputs[0].report["check_after"]
    assert after["valid"] and after["closed"] and after["faces"] == 6
    assert result.report["loops"] == 1 and result.report["capped_planar"] == 1


def test_cap_without_a_loop_caps_every_opening() -> None:
    """The old behaviour is unchanged: no loop tag means every closed opening."""
    result = run_op("cap", [("block", fx.open_box())])
    assert result.report["loops"] == 1
    assert result.outputs[0].report["check_after"]["closed"] is True


def test_a_loop_tag_whose_edges_are_on_no_opening_is_refused() -> None:
    """A cap that capped nothing while reporting success would be worse than a refusal."""
    from stokes.geometry.errors import OperationInvalid

    # The corner-open box has one 6-edge loop; name an edge that is not on any free loop by naming
    # an id past the end of a *different* fixture's numbering is not honest, so instead offer a tag
    # whose member names an edge of the closed part of the box.
    shape = fx.open_box()
    import OCP.collections as occ
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp

    from stokes.geometry.operations.base import OpContext, OpInput

    mapping = occ.IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, mapping)
    ctx = OpContext({}, [OpInput("n_00000000", "block", shape)])
    loops = rm.closed_wires(shape, 1e-5)
    off_loop = [
        edge_id
        for edge_id in range(mapping.Extent())
        if not _edge_lies_on(
            ctx.edges([{"part": "n_00000000", "faces": [], "edges": [edge_id]}])[0], loops[0]
        )
    ]
    assert off_loop, "the fixture must have edges that are not on the free loop"
    with pytest.raises(OperationInvalid, match="no closed opening"):
        run_op(
            "cap",
            [("block", shape)],
            {"loop_members": [{"part": "n_00000000", "faces": [], "edges": off_loop}]},
        )


def test_a_loop_tag_with_no_edges_is_refused() -> None:
    from stokes.geometry.errors import OperationInvalid

    with pytest.raises(OperationInvalid, match="names no edges"):
        run_op(
            "cap",
            [("block", fx.open_box())],
            {"loop_members": [{"part": "n_00000000", "faces": []}]},
        )


def _edge_lies_on(edge: Any, wire: Any) -> bool:
    """Whether ``edge`` is one of ``wire``'s edges (the test's own copy, by ``IsSame``)."""
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    explorer = TopExp_Explorer(wire, TopAbs_ShapeEnum.TopAbs_EDGE)
    as_edge = TopoDS.Edge
    while explorer.More():
        if as_edge(explorer.Current()).IsSame(edge):
            return True
        explorer.Next()
    return False
