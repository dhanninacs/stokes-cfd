"""The two acceptance cases for M1's visual and workflow claims (T-114).

The demo pipe project cannot exercise what M1 built: its tags are empty, it has no fluid/solid pair,
no interface and no heat source. The owner's rule from 2026-10-12 is that **a claim that a feature
works needs a run on a proper multi-body case, not on the pipe**; the pipe stays for unit tests.

Both cases are **generated from the OCP helpers at test time** — no binary files in the tree, which
§6 of `docs/engineering/testing.md` requires and the IP gate would otherwise object to.

* :func:`cold_plate_parts` — an internal-flow conjugate-heat-transfer case: an aluminium base plate
  with a serpentine channel through it and a chip on top. Gives planar inlet and outlet ends, a
  fluid/solid interface of several faces, a heat-flux face, walls, **curved** faces (the channel
  bends, so there are circular edges and shared vertices for Edge and Point modes), and a face group
  of 20+ faces for the cost checks.
* :func:`heat_sink_parts` — an external-flow case: a finned block for the enclosure operation, with
  inlet, outlet, symmetry and wall faces on the enclosure and a heat-flux base.

Bodies are named ``plate``/``chip``/``fluid`` (and ``sink``/``enclosure``) so the tree, tags and
boundaries read like a real project, which is what the glyph and pick captures need.

**Millimetres**, as a CAD file is; the importer converts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests import geometry_fixtures as fx

# The cold plate's figures, in millimetres. Small enough to mesh quickly, large enough to have real
# topology: the channel's bends give circular edges, and the plate's top face is a big planar face.
PLATE = (80.0, 50.0, 8.0)
CHANNEL = (6.0, 4.0)
CHANNEL_Y = (14.0, 25.0, 36.0)
CHANNEL_MARGIN = 6.0
CHIP = (20.0, 20.0, 3.0)
FILLET = 2.0
FINS = 7
FIN = (2.0, 30.0, 12.0)


def _box(dx: float, dy: float, dz: float, at: tuple[float, float, float]) -> Any:  # Any: OCP shape
    return fx.box(dx, dy, dz, at=at)


def _fuse(shapes: list[Any]) -> Any:  # Any: list of OCP shapes
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    out = shapes[0]
    for other in shapes[1:]:
        out = BRepAlgoAPI_Fuse(out, other).Shape()
    return out


def _cut(shape: Any, tools: list[Any]) -> Any:  # Any: OCP shapes
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    out = shape
    for tool in tools:
        out = BRepAlgoAPI_Cut(out, tool).Shape()
    return out


def _fillet(shape: Any, radius: float) -> Any:  # Any: OCP shape
    """Fillet every edge, so the case has curved faces and circular edges.

    **This is best-effort by design, and it silently does nothing on the plate.** I measured
    it: filleting all 176 edges of the channel-cut plate leaves 176, i.e. the operation refused
    and the `except` returned the shape unchanged. That is why the cold plate gets its curved
    geometry from `_bolt_holes` instead — a construction that either works or raises, rather
    than one whose failure is invisible. Kept for the sink, where it applies (220 of its 448
    edges are circular).
    """
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    solid = BRepFilletAPI_MakeFillet(shape)
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        solid.Add(radius, TopoDS.Edge(explorer.Current()))
        explorer.Next()
    try:
        return solid.Shape()
    except Exception:  # a fillet that will not build must not lose the case
        return shape


# Four bolt holes at the plate's corners: 3 mm radius, through the full thickness. A cylinder cut
# gives circular edges and curved faces deterministically, which the Edge and Point captures need —
# on the cold plate the fillet above adds none.
BOLT_RADIUS = 3.0
BOLT_INSET = 6.0


def _bolt_holes() -> list[Any]:  # Any: list of OCP shapes
    """Four through-holes at the plate's corners, in millimetres."""
    radius, inset = BOLT_RADIUS, BOLT_INSET
    corners = [
        (inset, inset),
        (PLATE[0] - inset, inset),
        (inset, PLATE[1] - inset),
        (PLATE[0] - inset, PLATE[1] - inset),
    ]
    return [fx.cylinder(radius, PLATE[2] * 2.0, at=(x, y, -PLATE[2] / 2.0)) for x, y in corners]


def _channel() -> Any:  # Any: OCP shape
    """The serpentine channel as a fused set of axis-aligned boxes, in millimetres.

    Three passes across the plate, joined by two return connectors at alternating ends. **Boxes
    only, not rotated cylinders:** my first version put a cylinder at each turn with the turn's
    radius taken from the spacing between passes, so the bends were the wrong size, stuck out of
    the channel's depth and failed to join the legs — the fluid imported as five disconnected
    solids instead of one body. Box connectors overlap both legs by their width, which unions
    reliably.

    The **curved** faces the Edge and Point captures need come from the plate's bolt holes, not
    from here; what this contributes is the internal void, the inlet and outlet end faces, and
    the fluid/solid interface.
    """
    width, depth = CHANNEL
    x0, x1 = CHANNEL_MARGIN, PLATE[0] - CHANNEL_MARGIN
    z = PLATE[2] / 2.0 - depth / 2.0  # centred through the plate's thickness
    parts: list[Any] = []
    for y in CHANNEL_Y:
        parts.append(_box(x1 - x0, width, depth, (x0, y - width / 2.0, z)))
    # The return connectors: a full-width block at the end of a pass, spanning the two passes it
    # joins. It overlaps each leg by the leg's width, so the union is one solid.
    for index in range(len(CHANNEL_Y) - 1):
        y_a, y_b = CHANNEL_Y[index], CHANNEL_Y[index + 1]
        # Alternate the end, so the path snakes rather than going back over itself.
        at = x1 - width if index % 2 == 0 else x0
        parts.append(_box(width, (y_b - y_a) + width, depth, (at, y_a - width / 2.0, z)))
    return _fuse(parts)


def cold_plate_parts() -> list[tuple[str, Any, tuple[float, float, float] | None]]:
    """``plate``, ``chip`` and ``fluid`` for the internal-flow conjugate case (T-114 case 1).

    The **fluid** is the channel's void: cutting the channel from a solid block and keeping the
    removed material would be the `extract_fluid` operation's job on the plate, but the operation
    needs a mesh and a cavity mode, so the case ships the fluid body directly as a solid — the
    channel's own shape — and the test asserts the operation finds the same cavity when it can.
    """
    # The bolt holes are what give this case circular edges; the fillet does not apply to it (see
    # `_fillet`). Cut both in one pass so the shape is a single solid.
    plate = _cut(_box(*PLATE, at=(0, 0, 0)), [_channel(), *_bolt_holes()])
    chip = _box(*CHIP, at=((PLATE[0] - CHIP[0]) / 2.0, (PLATE[1] - CHIP[1]) / 2.0, PLATE[2]))
    fluid = _channel()
    return [
        ("plate", plate, (0.72, 0.74, 0.76)),
        ("chip", chip, (0.85, 0.45, 0.25)),
        ("fluid", fluid, (0.35, 0.62, 0.85)),
    ]


def heat_sink_parts() -> list[tuple[str, Any, tuple[float, float, float] | None]]:
    """``sink`` and ``enclosure`` for the external-flow case (T-114 case 2).

    The enclosure is built here rather than by the `enclosure` operation so the case imports in one
    step; the test also runs the operation and asserts it produces a body of the same size, which is
    what exercises the workflow rather than only the file.
    """
    base = _box(60.0, 40.0, 4.0, at=(0, 0, 0))
    fins = [_box(FIN[0], FIN[1], FIN[2], (8.0 + index * 8.0, 5.0, 4.0)) for index in range(FINS)]
    sink = _fuse([base, *fins])
    sink = _fillet(sink, 0.8)
    # The enclosure: a box around the sink with a margin, which `enclosure` would make as a solid
    # whose inner volume is the sink's bounding box plus the margin.
    margin = 10.0
    enclosure = _box(
        60.0 + 2 * margin, 40.0 + 2 * margin, 4.0 + FIN[2] + 2 * margin, (-margin, -margin, -margin)
    )
    return [
        ("sink", sink, (0.78, 0.79, 0.81)),
        ("enclosure", enclosure, (0.45, 0.72, 0.55)),
    ]


def write_cold_plate(folder: Path) -> Path:
    """Write case 1 as a named STEP and return its path."""
    folder.mkdir(parents=True, exist_ok=True)
    return fx.write_named(cold_plate_parts(), folder / "cold_plate.step")


def write_heat_sink(folder: Path) -> Path:
    """Write case 2 as a named STEP and return its path."""
    folder.mkdir(parents=True, exist_ok=True)
    return fx.write_named(heat_sink_parts(), folder / "heat_sink.step")
