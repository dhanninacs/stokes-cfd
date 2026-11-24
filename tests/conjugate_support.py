"""Synthetic conjugate bodies and the measures the snapshot tests take of them (ADR-0042).

Two square water channels through an aluminium block, as gridded numpy surfaces handed to the
geometry store with its public ``put`` (`tests.selection_support`), so the multi-region snapshot
builder runs on them without OCP or the Rust mesher. The block's walls in the tunnels are gridded
differently from the channels' walls, as two independently tessellated bodies are: no triangle
centre of one repeats a centre of the other, so only the probe can find the shared walls.

Sizes are millimetres here and metres in the meshes. Each channel is 2 mm square and 24 mm long,
the block 20 x 10 x 6 mm, so a channel is 2 mm proud of the block at each end and its four walls
inside the block (8 mm x 20 mm) are 160 mm2 of interface.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from stokes.api import Session
from tests.selection_support import add_part, mesh_from_corners, new_session, part

MM = 1e-3
BASE = 0.5  # mm, the base cell
CHANNELS = {"hot": (-3.5, -1.5), "cold": (1.5, 3.5)}  # y extent, mm; z is -1 to 1
INTERFACE_AREA = 160.0  # mm2 per channel


def rectangle(
    axis: int,
    at: float,
    outward: float,
    u: tuple[float, float, int],
    v: tuple[float, float, int],
    holes: Sequence[tuple[float, float, float, float]] = (),
) -> np.ndarray[Any, Any]:
    """A plane rectangle normal to ``axis`` at ``at``, cut into ``u[2]`` by ``v[2]`` cells of two
    triangles each, wound so the normal points ``outward`` along the axis. ``u`` and ``v`` are the
    other two axes in order. Cells whose centre lies in a ``(u0, u1, v0, v1)`` hole are left out.
    Returns (T, 3, 3) corners in millimetres."""
    ua, va = [a for a in range(3) if a != axis]
    us = np.linspace(u[0], u[1], u[2] + 1)
    vs = np.linspace(v[0], v[1], v[2] + 1)
    out: list[np.ndarray[Any, Any]] = []
    for i in range(u[2]):
        for j in range(v[2]):
            cu, cv = 0.5 * (us[i] + us[i + 1]), 0.5 * (vs[j] + vs[j + 1])
            if any(h[0] < cu < h[1] and h[2] < cv < h[3] for h in holes):
                continue
            quad = []
            for a, b in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
                point = np.zeros(3)
                point[axis], point[ua], point[va] = at, us[a], vs[b]
                quad.append(point)
            for tri in ((0, 1, 2), (0, 2, 3)):
                corners = np.array([quad[k] for k in tri])
                normal = np.cross(corners[1] - corners[0], corners[2] - corners[0])
                if normal[axis] * outward < 0.0:
                    corners = corners[[0, 2, 1]]
                out.append(corners)
    return np.array(out)


def channel(y0: float, y1: float, grid: tuple[int, int] = (48, 4)) -> list[np.ndarray[Any, Any]]:
    """A channel's six faces, x from -12 to 12 and z from -1 to 1, its walls ``grid`` cells along
    x and across (by default a 0.5 mm grid)."""
    along, across = grid
    x = (-12.0, 12.0, along)
    return [
        rectangle(1, y0, -1.0, x, (-1.0, 1.0, across)),
        rectangle(1, y1, 1.0, x, (-1.0, 1.0, across)),
        rectangle(2, -1.0, -1.0, x, (y0, y1, across)),
        rectangle(2, 1.0, 1.0, x, (y0, y1, across)),
        rectangle(0, -12.0, -1.0, (y0, y1, across), (-1.0, 1.0, across)),
        rectangle(0, 12.0, 1.0, (y0, y1, across), (-1.0, 1.0, across)),
    ]


def cube() -> list[np.ndarray[Any, Any]]:
    """A 2 mm cube clear of the block (x 30 to 32): a second solid that touches nothing."""
    span = (-1.0, 1.0, 4)
    return [
        rectangle(0, 30.0, -1.0, span, span),
        rectangle(0, 32.0, 1.0, span, span),
        rectangle(1, -1.0, -1.0, (30.0, 32.0, 4), span),
        rectangle(1, 1.0, 1.0, (30.0, 32.0, 4), span),
        rectangle(2, -1.0, -1.0, (30.0, 32.0, 4), span),
        rectangle(2, 1.0, 1.0, (30.0, 32.0, 4), span),
    ]


#: The plate `plate` lays on the block's top (y = 5 mm), y extent in mm.
PLATE = (5.0, 7.0)


def plate() -> list[np.ndarray[Any, Any]]:
    """A 2 mm plate covering the block's top (y = 5 mm), its face on the block gridded 0.4 by
    0.5 mm (the block's top is 0.5 mm square): a second solid sharing a wall with the block."""
    y0, y1 = PLATE
    x = (-10.0, 10.0, 50)
    return [
        rectangle(1, y0, -1.0, x, (-3.0, 3.0, 12)),
        rectangle(1, y1, 1.0, (-10.0, 10.0, 40), (-3.0, 3.0, 12)),
        rectangle(0, -10.0, -1.0, (y0, y1, 4), (-3.0, 3.0, 12)),
        rectangle(0, 10.0, 1.0, (y0, y1, 4), (-3.0, 3.0, 12)),
        rectangle(2, -3.0, -1.0, (-10.0, 10.0, 40), (y0, y1, 4)),
        rectangle(2, 3.0, 1.0, (-10.0, 10.0, 40), (y0, y1, 4)),
    ]


def outer_skin(second: str | None = None) -> np.ndarray[Any, Any]:
    """The true outer skin of the bodies `two_channel_session` makes, in mm: the block's sides
    less the mouths (and less its top under a plate), each channel's 2 mm stubs and end caps, and
    the second solid's outside."""
    faces = block()[:6]
    if second == "plate":
        faces = faces[:3] + faces[4:] + plate()[1:]
    elif second == "cube":
        faces = faces + cube()
    for y0, y1 in CHANNELS.values():
        for xr in ((-12.0, -10.0, 4), (10.0, 12.0, 4)):
            faces += [
                rectangle(1, y0, -1.0, xr, (-1.0, 1.0, 4)),
                rectangle(1, y1, 1.0, xr, (-1.0, 1.0, 4)),
                rectangle(2, -1.0, -1.0, xr, (y0, y1, 4)),
                rectangle(2, 1.0, 1.0, xr, (y0, y1, 4)),
            ]
        faces += channel(y0, y1)[4:]
    return np.concatenate(faces)


def block(
    pocket: tuple[int, int] | None = None, tunnel: tuple[int, int] = (48, 4)
) -> list[np.ndarray[Any, Any]]:
    """The block less the two channels: its outer sides on a 0.5 mm grid with the four mouths
    cut out, and each tunnel's four walls ``tunnel`` cells along x and across (by default 48 by 4,
    0.417 mm by 0.5 mm: no edge under the base cell, so no collar on the block's side).

    ``pocket`` is the cell ``(along x, across z)`` of the hot tunnel's floor (y = -3.5 mm) sunk
    0.3 mm into the block: the hot channel's wall over it is clear of the block by more than the
    probe reaches, so the probe finds no neighbour there and leaves an island of outer skin."""
    mouths = [(y0, y1, -1.0, 1.0) for y0, y1 in CHANNELS.values()]
    x = (-10.0, 10.0, 40)
    faces = [
        rectangle(0, -10.0, -1.0, (-5.0, 5.0, 20), (-3.0, 3.0, 12), mouths),
        rectangle(0, 10.0, 1.0, (-5.0, 5.0, 20), (-3.0, 3.0, 12), mouths),
        rectangle(1, -5.0, -1.0, x, (-3.0, 3.0, 12)),
        rectangle(1, 5.0, 1.0, x, (-3.0, 3.0, 12)),
        rectangle(2, -3.0, -1.0, x, (-5.0, 5.0, 20)),
        rectangle(2, 3.0, 1.0, x, (-5.0, 5.0, 20)),
    ]
    tunnel_x = (-10.0, 10.0, tunnel[0])
    across = tunnel[1]
    hole: list[tuple[float, float, float, float]] = []
    if pocket is not None:
        xs = np.linspace(-10.0, 10.0, tunnel[0] + 1)
        zs = np.linspace(-1.0, 1.0, across + 1)
        hole = [(xs[pocket[0]], xs[pocket[0] + 1], zs[pocket[1]], zs[pocket[1] + 1])]
    for name, (y0, y1) in CHANNELS.items():
        faces += [
            rectangle(1, y0, 1.0, tunnel_x, (-1.0, 1.0, across), hole if name == "hot" else ()),
            rectangle(1, y1, -1.0, tunnel_x, (-1.0, 1.0, across)),
            rectangle(2, -1.0, 1.0, tunnel_x, (y0, y1, across)),
            rectangle(2, 1.0, -1.0, tunnel_x, (y0, y1, across)),
        ]
    if hole:
        x0, x1, z0, z1 = hole[0]
        y0 = CHANNELS["hot"][0]
        bottom = y0 - 0.3
        faces += [
            rectangle(1, bottom, 1.0, (x0, x1, 1), (z0, z1, 1)),
            rectangle(0, x0, 1.0, (bottom, y0, 1), (z0, z1, 1)),
            rectangle(0, x1, -1.0, (bottom, y0, 1), (z0, z1, 1)),
            rectangle(2, z0, 1.0, (x0, x1, 1), (bottom, y0, 1)),
            rectangle(2, z1, -1.0, (x0, x1, 1), (bottom, y0, 1)),
        ]
    return faces


def pocket_box(cell: tuple[int, int], tunnel: tuple[int, int] = (48, 4)) -> tuple[float, ...]:
    """The pocket `block` sinks for ``cell``, as ``(x0, x1, z0, z1)`` in mm."""
    xs = np.linspace(-10.0, 10.0, tunnel[0] + 1)
    zs = np.linspace(-1.0, 1.0, tunnel[1] + 1)
    return (xs[cell[0]], xs[cell[0] + 1], zs[cell[1]], zs[cell[1] + 1])


def on_hot_wall(corners: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    """Which triangles (mm) lie on the hot channel's walls inside the block."""
    y0, y1 = CHANNELS["hot"]
    centre = corners.mean(axis=1)
    inside = np.abs(centre[:, 0]) < 10.0
    flat_y = np.isclose(corners[:, :, 1], y0).all(axis=1) | np.isclose(corners[:, :, 1], y1).all(
        axis=1
    )
    flat_z = np.isclose(np.abs(corners[:, :, 2]), 1.0).all(axis=1) & (
        np.ptp(corners[:, :, 2], axis=1) < 1e-9
    )
    return np.asarray(inside & (flat_y | flat_z))


def two_channel_session(
    tmp_path: Path,
    *,
    flip_hot: Any = None,
    flip_block: Any = None,
    pocket: tuple[int, int] | None = None,
    tunnel: tuple[int, int] = (48, 4),
    grid: tuple[int, int] = (48, 4),
    second: str | None = None,
    second_first: bool = False,
) -> Session:
    """A session holding the two channels and the block as three regions, meshed snappy at a
    0.5 mm base cell. ``flip_hot`` and ``flip_block`` choose triangles (a function of the body's
    corners in mm, returning a mask) to wind the wrong way, as the display mesh sometimes has;
    ``pocket`` and ``tunnel`` are `block`'s, ``grid`` the channels'. ``second`` adds a second
    solid, ``"plate"`` (`plate`) or ``"cube"`` (`cube`), as the solid region made before the
    block when ``second_first`` and after it otherwise: the last solid is the region no zone
    surface bounds, so with a second solid last the block is a zone."""
    session = new_session(tmp_path)
    ids: dict[str, str] = {}
    bodies = {name: channel(*span, grid) for name, span in CHANNELS.items()}
    bodies["block"] = block(pocket, tunnel)
    if second is not None:
        bodies["second"] = plate() if second == "plate" else cube()
    flips = {"hot": flip_hot, "block": flip_block}
    for name, faces in bodies.items():
        corners = np.concatenate(faces)
        face_ids = np.concatenate([np.full(len(f), k) for k, f in enumerate(faces)])
        chooser = flips.get(name)
        if chooser is not None:
            wrong = chooser(corners)
            assert wrong.any(), name
            corners[wrong] = corners[wrong][:, [0, 2, 1]]
        mesh = mesh_from_corners(corners * MM, face_ids, name=name)
        ids[name] = add_part(session, name, part(mesh))
    session.execute("material.add_from_library", fluid="water_liquid", name="water")
    session.execute("material.add_from_library", fluid="aluminium", name="aluminium")
    for name in CHANNELS:
        session.execute(
            "region.create", name=name, kind="fluid", bodies=[ids[name]], material="water"
        )
    solids = ["block"] + (["second"] if second is not None else [])
    for name in reversed(solids) if second_first else solids:
        session.execute(
            "region.create", name=name, kind="solid", bodies=[ids[name]], material="aluminium"
        )
    session.execute(
        "mesh.configure",
        mesher="snappy",
        values={"domain": "part", "domain_part": ids["hot"], "base_size": f"{BASE} [mm]"},
    )
    return session


# ------------------------------------------------------------------------------- the measures


def stl_triangles(text: str) -> np.ndarray[Any, Any]:
    """The (T, 3, 3) corners of an ASCII STL."""
    found = [line.split()[1:] for line in text.splitlines() if line.strip().startswith("vertex")]
    return np.asarray(found, dtype="float64").reshape(-1, 3, 3)


def area(corners: np.ndarray[Any, Any]) -> float:
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    return float(0.5 * np.linalg.norm(cross, axis=1).sum())


def _point_triangle_distance(
    p: np.ndarray[Any, Any],
    a: np.ndarray[Any, Any],
    b: np.ndarray[Any, Any],
    c: np.ndarray[Any, Any],
) -> np.ndarray[Any, Any]:
    """The distance from each point to its triangle, row by row (closest point by region)."""
    ab, ac, ap = b - a, c - a, p - a

    def dot(x: np.ndarray[Any, Any], y: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
        return np.asarray(np.einsum("ij,ij->i", x, y))

    d1, d2 = dot(ab, ap), dot(ac, ap)
    bp = p - b
    d3, d4 = dot(ab, bp), dot(ac, bp)
    cp = p - c
    d5, d6 = dot(ab, cp), dot(ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    tiny = 1e-300
    denom = va + vb + vc
    v = vb / np.where(np.abs(denom) > tiny, denom, tiny)
    w = vc / np.where(np.abs(denom) > tiny, denom, tiny)
    closest = a + v[:, None] * ab + w[:, None] * ac  # inside the face
    # The edges and corners, each where its region test holds (Ericson, Real-Time Collision
    # Detection, 5.1.5), applied from the most general to the most particular.
    e_bc = (va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0)
    t = (d4 - d3) / np.where((d4 - d3) + (d5 - d6) > tiny, (d4 - d3) + (d5 - d6), tiny)
    closest[e_bc] = (b + t[:, None] * (c - b))[e_bc]
    e_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    t = d2 / np.where(d2 - d6 > tiny, d2 - d6, tiny)
    closest[e_ac] = (a + t[:, None] * ac)[e_ac]
    e_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    t = d1 / np.where(d1 - d3 > tiny, d1 - d3, tiny)
    closest[e_ab] = (a + t[:, None] * ab)[e_ab]
    closest[(d6 >= 0) & (d5 <= d6)] = c[(d6 >= 0) & (d5 <= d6)]
    closest[(d3 >= 0) & (d4 <= d3)] = b[(d3 >= 0) & (d4 <= d3)]
    closest[(d1 <= 0) & (d2 <= 0)] = a[(d1 <= 0) & (d2 <= 0)]
    return np.asarray(np.linalg.norm(p - closest, axis=1))


def area_on(corners: np.ndarray[Any, Any], surface: np.ndarray[Any, Any], gap: float) -> float:
    """The area of ``corners`` lying within ``gap`` of ``surface``, whatever either's triangles.

    Each triangle is sampled at seven points (its centre, and halfway from the centre to each
    corner and to each edge's middle) and counts the share of them within ``gap`` of a triangle of
    ``surface``: so a copy of the surface triangulated differently, or overlapping it in part, is
    measured by its area, where matching triangle centres finds only exact copies."""
    from scipy.spatial import cKDTree

    if len(corners) == 0 or len(surface) == 0:
        return 0.0
    centre = corners.mean(axis=1)
    middles = 0.5 * (corners + np.roll(corners, -1, axis=1))
    samples = np.concatenate(
        [centre[:, None], 0.5 * (centre[:, None] + corners), 0.5 * (centre[:, None] + middles)],
        axis=1,
    )  # (T, 7, 3)
    points = samples.reshape(-1, 3)
    s_centre = surface.mean(axis=1)
    reach = float(np.linalg.norm(surface - s_centre[:, None], axis=2).max()) + gap
    candidates = cKDTree(s_centre).query_ball_point(points, reach)
    near = np.zeros(len(points), dtype=bool)
    rows = np.repeat(np.arange(len(points)), [len(c) for c in candidates])
    if rows.size:
        cols = np.concatenate([np.asarray(c, dtype="int64") for c in candidates])
        d = _point_triangle_distance(
            points[rows], surface[cols, 0], surface[cols, 1], surface[cols, 2]
        )
        near[rows[d < gap]] = True
    share = near.reshape(len(corners), 7).mean(axis=1)
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    return float((0.5 * np.linalg.norm(cross, axis=1) * share).sum())
