"""`mesh.section`: the finished volume mesh where a section plane cuts it, for the Mesh stage.

The viewer's section clips the boundary surfaces; on the Mesh stage it also draws what the plane
passes through: each cut cell's outline, filled by region (ADR-0042's regions in a conjugate case).
The cut is made by the mesh's host (`stokes.mesh.section`); the first one of a large mesh reads it,
so the command answers `cutting` at once and the caller asks again.
"""

from __future__ import annotations

from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError


@command(
    "mesh.section",
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
def mesh_section(ctx: CommandContext, axis: str, offset: Any) -> dict[str, Any]:
    """The current mesh's cells at the plane normal to `axis` at `offset`.

    `{"state": "cutting"}` while the host cuts (ask again), `{"state": "failed", "message"}`, or
    `{"state": "ready", cells, names, positions, triangles, edges, regions}` with the arrays as
    base64 of little-endian float32 (x, y, z per point, metres), uint32 (three points per triangle,
    a fan per cut cell), uint32 (two points per cell edge) and uint8 (each triangle's region, an
    index into `names`; `names` is empty for a mesh of one region), and `kinds`, each region's kind
    (`fluid`, `solid`) where the project has a region of that name.
    """
    from stokes.mesh.section import AXES, MeshSections
    from stokes.mesh.service import MeshService
    from stokes.run.hosts import HostRegistry
    from stokes.units import dimension, parse_quantity, to_si

    if axis not in AXES:
        raise ValidationError("axis must be 'x', 'y' or 'z'", code="mesh.section_invalid")
    if isinstance(offset, str):
        length = dimension("length")
        offset = to_si(parse_quantity(offset, length), length)
    if isinstance(offset, bool) or not isinstance(offset, int | float):
        raise ValidationError("offset must be a length", code="mesh.section_invalid")
    status = MeshService.of(ctx.session).status()
    job = str(status.get("job") or "")
    if not job or status.get("state") != "done":
        raise ValidationError(
            "there is no finished mesh to cut; generate the mesh first", code="mesh.not_built"
        )
    runner = HostRegistry.of(ctx.session).runner(MeshService.of(ctx.session)._host_of())
    answer = MeshSections.of(ctx.session).section(runner, job, axis, float(offset))
    if answer.get("state") == "ready":
        answer = {**answer, "kinds": _kinds(ctx, answer.get("names") or [])}
    return answer


def _kinds(ctx: CommandContext, names: list[str]) -> list[str]:
    """Each mesh region's kind (`fluid`, `solid`), named as the mesh named it, else ""."""
    from stokes.mesh.conjugate import region_bodies

    project = ctx.session.project
    found: dict[str, str] = {}
    if project is not None:
        found = {str(name): str(kind) for _node, kind, _parts, name in region_bodies(project)}
    return [found.get(name, "") for name in names]
