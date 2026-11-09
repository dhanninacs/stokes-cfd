"""Acceptance tests for M1's visual and workflow claims (T-114).

The owner's rule from 2026-10-12: **a claim that a feature works needs a run on a proper multi-body
case, not on the demo pipe.** The pipe stays for unit tests. These cases exist so the glyph, pick,
hover and cost checks have real geometry: planar inlet and outlet faces, a fluid/solid interface of
several faces, a heat-flux face, walls, curved faces from a fillet (so there are circular edges and
shared vertices), and a face group of 20 or more faces for the cost checks.

The fixtures are **generated**, not committed (`tests/acceptance_fixtures.py`), and each test here
imports them through the real API and then runs the workflow the capture scripts run — so a case
that cannot be set up fails in CI rather than only on the owner's GPU.

Marked `slow`: the import of the 201-face sink takes seconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes.api import Session
from stokes.geometry import step as stepmod
from stokes.geometry.store import GeometryStore
from tests import acceptance_fixtures as af

pytestmark = pytest.mark.slow


def _import(session: Session, path: Path) -> dict[str, object]:
    """Import through the API and wait for the meshes, the way a client does."""
    result = session.execute("geometry.import_step", path=str(path))
    store = GeometryStore.of(session)
    assert store.wait(result["id"], timeout=600), "the import did not finish"
    return dict(result)


def _parts(session: Session) -> list[dict[str, object]]:
    """Every part node under ``/geometry``, in tree order."""
    root = session.execute("node.tree")["children"][0]

    def walk(node: dict[str, object]) -> list[dict[str, object]]:
        found = [node] if node["type"] == "part" else []
        for child in node.get("children") or []:
            found.extend(walk(child))
        return found

    return [node for node in walk(root) if node["name"] != root["name"]]


def _meshed(session: Session) -> dict[str, dict[str, object]]:
    """Parts with a mesh, by name."""
    out: dict[str, dict[str, object]] = {}
    for node in _parts(session):
        params = node.get("params") or {}
        if params.get("basis"):
            out[str(node["name"])] = node
    return out


# ------------------------------------------------------------------ case 1: the cold plate


@pytest.fixture
def cold_plate(tmp_path: Path) -> tuple[Session, Path]:
    session = Session()
    session.execute("project.new", name="cold_plate", path=str(tmp_path / "cold_plate"))
    path = af.write_cold_plate(tmp_path)
    _import(session, path)
    return session, path


def test_the_cold_plate_gives_three_named_meshed_bodies(cold_plate) -> None:
    """``plate``, ``chip`` and ``fluid`` — the three bodies the interface and heat-flux claims need.

    The names matter: the tree, the tags and the boundaries have to read like a real project for the
    glyph captures to mean anything.
    """
    session, _ = cold_plate
    meshed = _meshed(session)
    assert set(meshed) >= {"plate", "chip", "fluid"}, f"got {sorted(meshed)}"


def test_the_plate_has_enough_faces_for_the_cost_checks(cold_plate) -> None:
    """A face group of 20+ faces, which §15.2's cost checks and the Related menu need."""
    session, _ = cold_plate
    plate = _meshed(session)["plate"]
    params = plate.get("params") or {}
    assert int(params.get("faces") or 0) >= 20, f"the plate has {params.get('faces')} faces"


def test_the_fluid_is_one_body_not_several(cold_plate) -> None:
    """The channel must carve ONE void. It came out as five disconnected solids once.

    My first channel used rotated cylinders for the return bends, with the radius taken from the
    spacing between passes; they were the wrong size and failed to join the legs. A fluid served as
    five bodies would make the interface and the inlet/outlet claims meaningless, so this is
    asserted rather than assumed.
    """
    session, _ = cold_plate
    meshed = _meshed(session)
    fluid_like = [name for name in meshed if name == "fluid" or name.startswith("fluid_")]
    assert fluid_like == ["fluid"], f"the fluid split into {fluid_like}"


def test_the_cold_plate_has_circular_edges_for_the_edge_and_point_modes(cold_plate) -> None:
    """The case must have curved faces, or Edge and Point mode captures show nothing interesting.

    §15.2's Edge mode exists for circular edges and shared vertices. **The plate's fillet adds
    none** — I measured it, and filleting all 176 of its edges leaves 176 — so the curved geometry
    comes from four bolt holes, and this test stops that regressing to a case with no curves.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_CurveType
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    _session, path = cold_plate
    model = stepmod.read_step(path)
    plate = next(s for s in model.solids if s.name == "plate")
    circles = 0
    explorer = TopExp_Explorer(plate.shape, TopAbs_EDGE)
    while explorer.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
        if curve.GetType() == GeomAbs_CurveType.GeomAbs_Circle:
            circles += 1
        explorer.Next()
    assert circles >= 8, f"the plate has {circles} circular edges; the bolt holes should give 8"


def test_the_cold_plate_tags_and_boundaries_can_be_created(cold_plate) -> None:
    """The workflow the capture script runs: tag real faces, assign a typed boundary, run the check.

    A tag needs real face ids from the transport, and `boundary.assign` needs the tag to resolve —
    both are the things that silently did nothing on the demo pipe.
    """
    session, _ = cold_plate
    meshed = _meshed(session)
    plate_id = str(meshed["plate"]["id"])
    fluid_id = str(meshed["fluid"]["id"])

    # An inlet on the fluid's first faces; the exact ids matter less than that they resolve.
    tag = session.execute(
        "tag.create", name="inlet", members=[{"part": fluid_id, "faces": [[0, 0]]}]
    )
    assert tag.get("id"), "tag.create returned no id"

    anchor = session.execute("tag.anchor", tags=[tag["id"]])["anchors"][0]
    assert anchor["faces"] >= 1, "the tag holds no faces, so no glyph could be placed"
    assert not anchor["partial"], "the anchor is partial: the mesh was not ready"

    boundary = session.execute(
        "boundary.assign",
        target=[{"part": fluid_id, "faces": [[0, 0]]}],
        kind="velocity_inlet",
        values={"velocity": "2 [m/s]"},
    )
    assert boundary["boundary"]["kind"] == "velocity_inlet"

    # The plate gets walls, so the case has a solid side too.
    walls = session.execute(
        "boundary.assign",
        target=[{"part": plate_id, "faces": [[0, 1]]}],
        kind="wall",
        values={},
    )
    assert walls["boundary"]["kind"] == "wall"

    check = session.execute("setup.check")
    assert "findings" in check and "counts" in check


# ------------------------------------------------------------------ case 2: the heat sink


@pytest.fixture
def heat_sink(tmp_path: Path) -> tuple[Session, Path]:
    session = Session()
    session.execute("project.new", name="heat_sink", path=str(tmp_path / "heat_sink"))
    path = af.write_heat_sink(tmp_path)
    _import(session, path)
    return session, path


def test_the_heat_sink_gives_a_finned_solid_and_an_enclosure(heat_sink) -> None:
    """``sink`` and ``enclosure``: a solid with a large face count and a box around it.

    The finned block is deliberately the heaviest part in either case (201 faces, ~29k triangles),
    so the hover-latency and 50-glyph probes have something real to measure on.
    """
    session, _ = heat_sink
    meshed = _meshed(session)
    assert set(meshed) >= {"sink", "enclosure"}, f"got {sorted(meshed)}"
    sink = meshed["sink"].get("params") or {}
    assert int(sink.get("faces") or 0) >= 100, f"the sink has only {sink.get('faces')} faces"


def test_the_enclosure_fits_around_the_sink(heat_sink) -> None:
    """The enclosure must fully contain the sink, or the external-flow case is not one."""
    session, _ = heat_sink
    meshed = _meshed(session)
    sink = meshed["sink"].get("params") or {}
    box = meshed["enclosure"].get("params") or {}
    sink_bbox, box_bbox = sink.get("bbox"), box.get("bbox")
    assert sink_bbox and box_bbox, "a bbox is missing"
    for axis in range(3):
        assert box_bbox[axis] <= sink_bbox[axis], (
            f"the enclosure does not contain the sink on {axis}"
        )
        assert box_bbox[axis + 3] >= sink_bbox[axis + 3], f"the enclosure is inside on {axis}"


def test_the_heat_sink_runs_the_external_flow_workflow(heat_sink) -> None:
    """`setup.defaults` on the real case, which is the leg that could never run on the pipe."""
    session, _ = heat_sink
    meshed = _meshed(session)
    sink_id = str(meshed["sink"]["id"])

    answer = session.execute(
        "setup.defaults",
        target=sink_id,
        template="external",
        velocity="5 [m/s]",
    )
    assert answer.get("region"), "setup.defaults named no region"
    assert answer.get("boundaries"), "setup.defaults created no boundaries"
    assert answer.get("material"), "setup.defaults chose no material"
