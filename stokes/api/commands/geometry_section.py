"""`geometry.section`: the solid a section plane cuts through each shown modeller body.

The viewer fills a cut body with a stencil cap, which is right for a closed mesh (an imported CAD
body's tessellation) and wrong for a modeller body whose display mesh is open (a lattice: the
mesher drops folded triangles). For those the section is computed from the body's own field
(`stokes.geometry.section`), so the plate of a gyroid exchanger cuts as a plate with channels
through it rather than a hollow. Read-only; a cut is a fraction of a second, and the last few are
kept.
"""

from __future__ import annotations

import base64
import json
import threading
from collections import OrderedDict
from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError

#: Cuts kept: a slider drag revisits the last few planes, and each holds a few hundred kB.
KEEP = 24
_held: OrderedDict[str, dict[str, Any]] = OrderedDict()
_lock = threading.Lock()


def _b64(array: Any) -> str:
    return base64.b64encode(bytes(array.tobytes())).decode("ascii")


@command(
    "geometry.section",
    params=(
        ParamDef("axis", "string", description="The plane's normal: 'x', 'y' or 'z'."),
        ParamDef(
            "offset",
            "any",
            description="Where along the axis, as a length: '2 [mm]', or a bare number in metres.",
        ),
    ),
    mutates=False,
)
def geometry_section(ctx: CommandContext, axis: str, offset: Any) -> dict[str, Any]:
    """The section of every shown body made with the modeller, at the plane normal to `axis`.

    `{"cuts": [{"part", "positions", "triangles"}]}`: the part's node id and its filled section as
    base64 of little-endian float32 (x, y, z per point, metres) and uint32 (three points per
    triangle). A body the plane misses, a hidden one and an imported one have no entry (an imported
    body's tessellation is closed, and the viewer caps it itself).
    """
    from stokes.geometry.errors import OperationInvalid
    from stokes.geometry.operations.implicit import body_of
    from stokes.geometry.opplan import implicit_recipe
    from stokes.geometry.section import AXES, cut_body
    from stokes.units import dimension, parse_quantity, to_si

    if axis not in AXES:
        raise ValidationError("axis must be 'x', 'y' or 'z'", code="geometry.section_invalid")
    if isinstance(offset, str):
        length = dimension("length")
        offset = to_si(parse_quantity(offset, length), length)
    if isinstance(offset, bool) or not isinstance(offset, int | float):
        raise ValidationError("offset must be a length", code="geometry.section_invalid")
    project = ctx.session.require_project()
    cuts: list[dict[str, Any]] = []
    for node in project.root.walk():
        if node.type != "part" or node.params.get("family") != "implicit":
            continue
        if node.params.get("visible") is False:
            continue
        try:
            recipe = implicit_recipe(project, node)
        except OperationInvalid:
            continue
        key = json.dumps([node.id, recipe, axis, round(float(offset), 9)], sort_keys=True)
        with _lock:
            found = _held.get(key)
            if found is not None:
                _held.move_to_end(key)
        if found is None:
            positions, triangles = cut_body(body_of(recipe), axis, float(offset))
            found = {
                "part": str(node.id),
                "positions": _b64(positions.astype("<f4")),
                "triangles": _b64(triangles.astype("<u4")),
            }
            with _lock:
                _held[key] = found
                while len(_held) > KEEP:
                    _held.popitem(last=False)
        if found["triangles"]:
            cuts.append(found)
    return {"axis": axis, "offset": float(offset), "cuts": cuts}
