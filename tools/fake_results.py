"""Synthetic results of the end-to-end duct, for the Results harness step and its captures (T-218).

OpenFOAM is not installed on the machine the harness runs on, and the real routes are on the Linux
host's tree, so the SERVER half of the results is played from here: this module builds the bodies
the routes would send for the demo project of ``tools/mesh_demo_project.py`` (a 500 x 20 x 20 mm
duct), and ``tools/fake_results.js`` serves them from the page.

Per ``.mailbox/plan/e2e`` (the T-216 contract as built):

* ``GET /api/results/field/{run}/{patch node id | slice}?field=`` is ``u32 header_len``, the JSON
  header ``{version, field, units, components, count, min, max}`` space padded to a multiple of 4,
  then ``f32[components x count]``: one value per vertex of the surface the route names, in the
  vertex order of that surface's own transport;
* ``GET /api/results/slice/{run}`` is the transport version 2 of a patch, and
  ``GET /api/results/slice-edges/{run}`` the edge codec; both are written here by the same writers
  the mesh routes use (``stokes.mesh.surfaces``);
* ``run.fields`` answers ``{run, time, fields: [{name, units, min, max}], slice, surfaces}``.

The fields are a plausible laminar duct: ``U_mag`` develops from a thin-boundary-layer inlet profile
to the parabolic-like profile of a square duct (zero on every wall vertex), ``p`` falls linearly
along x by the laminar pressure drop ``28.45 nu L Ub / D^2`` (kinematic, m2/s2). They say nothing
about any other case.

Any plane (the slices, and the Section view's cap): ``plane_response(path)`` answers the three
cut routes of ``.mailbox/plan/slices/CONTRACT.md`` --
``/api/results/slice/{run}?plane=ox,oy,oz,nx,ny,nz``, ``/slice-edges/{run}`` and
``/field/{run}/slice?plane=...&field=`` -- for an axis-aligned or oblique plane through the duct
box. The cut is a triangulated grid of the plane clipped to the box (a cell of about ``SIDE /
16``), the fields are the walls' own ``speed`` and ``pressure`` of its points (so a cap
continues the surface colours), and the field header range is the run's global one, as the
contract says. A plane that misses the box is the contract's 404 ``results.slice_empty``.
``tools/fake_results.js`` asks Python for these through ``__fakeResults.pending``.
"""

from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

LENGTH = 0.5
SIDE = 0.02
UB = 0.075
NU = 1e-5
#: Laminar square-duct pressure drop over the duct, kinematic (m2/s2).
DP = 28.45 * NU * LENGTH * UB / SIDE**2
RUN = "r_e2e"


def field_body(
    field: str,
    units: str,
    values: np.ndarray,
    components: int = 1,
    version: int = 1,
    limits: tuple[float, float] | None = None,
) -> bytes:
    """One field on one surface, as the field route sends it.

    ``limits`` is the ``(min, max)`` the header carries instead of the surface's own: the cut routes
    send the run's global range, so one colour scale serves every slice and every wall.
    """
    data = np.ascontiguousarray(values, dtype="<f4")
    mags = data if components == 1 else np.linalg.norm(data.reshape(-1, components), axis=1)
    head = json.dumps(
        {
            "version": version,
            "field": field,
            "units": units,
            "components": components,
            "count": int(data.size // components),
            "min": limits[0] if limits else float(mags.min()) if mags.size else 0.0,
            "max": limits[1] if limits else float(mags.max()) if mags.size else 0.0,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    head += b" " * ((-len(head)) % 4)
    return struct.pack("<I", len(head)) + head + data.tobytes()


def speed(points: np.ndarray) -> np.ndarray:
    """``|U|`` at points of the duct: a developing profile, zero on every wall."""
    x, y, z = points[:, 0].astype("float64"), points[:, 1], points[:, 2]
    eta = 2.0 * y / SIDE - 1.0
    zeta = 2.0 * z / SIDE - 1.0
    developed = (UB / (4.0 / 9.0)) * np.clip(1 - eta**2, 0, None) * np.clip(1 - zeta**2, 0, None)
    wall_distance = np.minimum.reduce([y, SIDE - y, z, SIDE - z])
    inlet = UB * np.clip(wall_distance / 0.0015, 0, 1)
    blend = np.exp(-x / 0.08)
    return (blend * inlet + (1 - blend) * developed).astype("float32")


def pressure(points: np.ndarray) -> np.ndarray:
    """``p / rho``: a linear drop along x to zero at the outlet."""
    return (DP * (1.0 - points[:, 0] / LENGTH)).astype("float32")


def _slice_surface() -> dict[str, Any]:
    """The mid-plane z = SIDE / 2 as a 100 x 10 grid of quads (as ``mesh_demo_project``)."""
    from stokes_agent.extract import triangulate_fan, unique_edges
    from tools.mesh_demo_project import _face_grid

    points, quads = _face_grid((0.0, 0.0, SIDE / 2), (LENGTH, 0.0, 0.0), (0.0, SIDE, 0.0), 100, 10)
    offsets = np.arange(0, quads.size + 1, 4)
    connectivity = quads.reshape(-1)
    return {
        "name": "slice",
        "patch_type": "patch",
        "points": points.astype("float32"),
        "triangles": triangulate_fan(offsets, connectivity),
        "edges": unique_edges(offsets, connectivity),
        "faces": int(quads.shape[0]),
    }


def build(session: Any, run: str = RUN) -> dict[str, Any]:
    """The routes' bodies (base64, by path and query) and the ``run.fields`` reply of the mesh.

    Raises ``LookupError`` when the project has no finished mesh result.
    """
    from stokes.mesh.service import MeshService
    from stokes.mesh.surfaces import patch_arrays, patch_edges, patch_transport, read_header
    from stokes_agent.extract import pack_artifact

    project = session.require_project()
    results = project.find("/mesh/results")
    result = results.children[0] if results is not None and results.children else None
    if result is None:
        raise LookupError("the project has no mesh result (see tools/mesh_demo_project.py)")
    service = MeshService.of(session)
    data = service._surface_path(str(result.id)).read_bytes()
    header = read_header(data)
    index = {str(p["name"]): i for i, p in enumerate(header["patches"])}

    routes: dict[str, bytes] = {}
    lo = {"U_mag": np.inf, "p": np.inf}
    hi = {"U_mag": -np.inf, "p": -np.inf}

    def note(name: str, values: np.ndarray) -> None:
        lo[name] = min(lo[name], float(values.min()))
        hi[name] = max(hi[name], float(values.max()))

    nodes: dict[str, str] = {}
    for patch in result.children:
        positions, _, _ = patch_arrays(data, header, index[str(patch.name)])
        pts = np.asarray(positions)
        u, p = speed(pts), pressure(pts)
        note("U_mag", u)
        note("p", p)
        nodes[str(patch.name)] = str(patch.id)
        base = f"/api/results/field/{run}/{patch.id}"
        routes[f"{base}?field=U_mag"] = field_body("U_mag", "m/s", u)
        routes[f"{base}?field=p"] = field_body("p", "m2/s2", p)

    surface = _slice_surface()
    slice_data, slice_header = pack_artifact([surface])
    slice_head = read_header(slice_data)
    spts = np.asarray(surface["points"])
    # The mid-plane carries its own pressure profile (a quadratic drop, in the same range as the
    # walls' linear one), so its cut face reads differently from the walls in a capture and in a
    # pixel check.
    su = speed(spts)
    sp = (DP * (1.0 - spts[:, 0] / LENGTH) ** 2).astype("float32")
    note("U_mag", su)
    note("p", sp)
    routes[f"/api/results/field/{run}/slice?field=U_mag"] = field_body("U_mag", "m/s", su)
    routes[f"/api/results/field/{run}/slice?field=p"] = field_body("p", "m2/s2", sp)
    routes[f"/api/results/slice/{run}"] = patch_transport(
        slice_data, slice_head, 0, (0.6, 0.64, 0.68)
    )
    routes[f"/api/results/slice-edges/{run}"] = patch_edges(slice_data, slice_head, 0)
    assert slice_header["patches"][0]["points"] == len(spts)

    _RANGES.update({"U_mag": (lo["U_mag"], hi["U_mag"]), "p": (lo["p"], hi["p"])})
    _CUTS.clear()

    fields = [
        {"name": "U", "units": "m/s", "min": lo["U_mag"], "max": hi["U_mag"]},
        {"name": "U_mag", "units": "m/s", "min": lo["U_mag"], "max": hi["U_mag"]},
        {"name": "p", "units": "m2/s2", "min": lo["p"], "max": hi["p"]},
    ]
    return {
        "routes": {path: base64.b64encode(body).decode("ascii") for path, body in routes.items()},
        "reply": {"run": run, "time": 400, "fields": fields, "slice": True, "surfaces": "ok"},
        "nodes": nodes,
        "dp": DP,
        "ranges": {"U_mag": [lo["U_mag"], hi["U_mag"]], "p": [lo["p"], hi["p"]]},
    }


# ------------------------------------------------------------------------------------- any plane
#: The global range of each field (set by ``build``), carried by every cut's field header.
_RANGES: dict[str, tuple[float, float]] = {}
#: Cell size of the cut's grid.
CELL = SIDE / 16.0
#: The most cells one cut has: a long oblique plane gets a coarser grid, not a slow response.
MAX_CELLS = 9000
#: A cut smaller than this (m2) is a plane that misses the duct.
MIN_AREA = 1e-10
_BOX_MIN = np.array([0.0, 0.0, 0.0])
_BOX_MAX = np.array([LENGTH, SIDE, SIDE])


def parse_plane(text: str) -> tuple[np.ndarray, np.ndarray] | None:
    """``ox,oy,oz,nx,ny,nz`` as an origin and a unit normal; None for what the contract refuses.

    The contract's 400 ``results.plane``: not six numbers, a number that is not finite, no normal.
    """
    parts = text.split(",")
    if len(parts) != 6:
        return None
    try:
        numbers = np.array([float(p) for p in parts])
    except ValueError:
        return None
    if not np.all(np.isfinite(numbers)):
        return None
    normal = numbers[3:]
    length = float(np.linalg.norm(normal))
    if not length > 0:
        return None
    return numbers[:3], normal / length


def _clip_half_space(
    polygon: list[np.ndarray], axis: int, bound: float, keep_above: bool
) -> list[np.ndarray]:
    """Sutherland-Hodgman against ``x[axis] >= bound`` (or ``<=``), a hair inclusive."""
    eps = 1e-9
    sign = 1.0 if keep_above else -1.0

    def inside(p: np.ndarray) -> bool:
        return sign * (p[axis] - bound) >= -eps

    out: list[np.ndarray] = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        if inside(current):
            if not inside(previous):
                out.append(_edge_at(previous, current, axis, bound))
            out.append(current)
        elif inside(previous):
            out.append(_edge_at(previous, current, axis, bound))
    return out


def _edge_at(a: np.ndarray, b: np.ndarray, axis: int, bound: float) -> np.ndarray:
    span = b[axis] - a[axis]
    t = 0.0 if span == 0 else (bound - a[axis]) / span
    return a + (b - a) * min(1.0, max(0.0, t))


def _basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``u, v`` in the plane with ``u x v = normal``; along the world axes for an axis plane."""
    axis = int(np.argmax(np.abs(normal)))
    if abs(abs(normal[axis]) - 1.0) < 1e-9:
        u = np.zeros(3)
        u[(axis + 1) % 3] = 1.0
    else:
        weakest = np.zeros(3)
        weakest[int(np.argmin(np.abs(normal)))] = 1.0
        u = np.cross(normal, weakest)
        u /= np.linalg.norm(u)
    return u, np.cross(normal, u)


def _clip_cell(
    cell: list[tuple[float, float]], polygon: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """The convex ``cell`` clipped to the convex counter-clockwise ``polygon`` (plane coords)."""
    out = cell
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        ex, ey = b[0] - a[0], b[1] - a[1]

        def side(
            p: tuple[float, float], ex: float = ex, ey: float = ey, a: tuple[float, float] = a
        ) -> float:
            return ex * (p[1] - a[1]) - ey * (p[0] - a[0])

        clipped: list[tuple[float, float]] = []
        for j, current in enumerate(out):
            previous = out[j - 1]
            sc, sp = side(current), side(previous)
            if sc >= -1e-12:
                if sp < -1e-12:
                    t = sp / (sp - sc)
                    clipped.append(
                        (
                            previous[0] + (current[0] - previous[0]) * t,
                            previous[1] + (current[1] - previous[1]) * t,
                        )
                    )
                clipped.append(current)
            elif sp >= -1e-12:
                t = sp / (sp - sc)
                clipped.append(
                    (
                        previous[0] + (current[0] - previous[0]) * t,
                        previous[1] + (current[1] - previous[1]) * t,
                    )
                )
        out = clipped
        if len(out) < 3:
            return []
    return out


def cut_duct(origin: np.ndarray, normal: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """The plane through the duct box as ``(points (N, 3) float32, triangles (T, 3) uint32)``.

    None when the plane misses the box. The plane is clipped to the box (a convex polygon),
    laid with a grid of ``CELL`` along its own axes, and each cell clipped to the polygon
    and fanned: the cut has the box's outline exactly and neighbouring cells share their
    vertices (no hanging nodes), as a real cut of a hex mesh would.
    """
    u, v = _basis(normal)
    centre = (_BOX_MIN + _BOX_MAX) / 2.0
    anchor = centre - normal * float(np.dot(centre - origin, normal))
    big = float(np.linalg.norm(_BOX_MAX - _BOX_MIN))
    polygon3 = [anchor + (su * u + sv * v) * big for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    for axis in range(3):
        polygon3 = _clip_half_space(polygon3, axis, float(_BOX_MIN[axis]), True)
        if len(polygon3) >= 1:
            polygon3 = _clip_half_space(polygon3, axis, float(_BOX_MAX[axis]), False)
        if len(polygon3) < 3:
            return None
    flat = [(float(np.dot(p - origin, u)), float(np.dot(p - origin, v))) for p in polygon3]
    area = 0.5 * sum(
        flat[i][0] * flat[(i + 1) % len(flat)][1] - flat[(i + 1) % len(flat)][0] * flat[i][1]
        for i in range(len(flat))
    )
    if abs(area) < MIN_AREA:
        return None
    if area < 0:
        flat.reverse()
    lo = np.array([min(p[0] for p in flat), min(p[1] for p in flat)])
    hi = np.array([max(p[0] for p in flat), max(p[1] for p in flat)])
    step = max(CELL, float(np.sqrt((hi[0] - lo[0]) * (hi[1] - lo[1]) / MAX_CELLS)))
    nu = max(1, int(np.ceil((hi[0] - lo[0]) / step - 1e-9)))
    nv = max(1, int(np.ceil((hi[1] - lo[1]) / step - 1e-9)))
    du, dv = (hi[0] - lo[0]) / nu, (hi[1] - lo[1]) / nv

    index: dict[tuple[int, int], int] = {}
    points: list[np.ndarray] = []

    def vertex(a: float, b: float) -> int:
        key = (round(a * 1e9), round(b * 1e9))
        found = index.get(key)
        if found is None:
            found = len(points)
            index[key] = found
            points.append(origin + a * u + b * v)
        return found

    triangles: list[tuple[int, int, int]] = []
    for i in range(nu):
        for j in range(nv):
            x0, x1 = lo[0] + i * du, lo[0] + (i + 1) * du
            y0, y1 = lo[1] + j * dv, lo[1] + (j + 1) * dv
            cell = _clip_cell([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], flat)
            if len(cell) < 3:
                continue
            ids = [vertex(a, b) for a, b in cell]
            for k in range(1, len(ids) - 1):
                if len({ids[0], ids[k], ids[k + 1]}) == 3:
                    triangles.append((ids[0], ids[k], ids[k + 1]))
    if not triangles:
        return None
    return np.array(points, dtype="float32"), np.array(triangles, dtype="uint32")


def _cut_bodies(query: str) -> dict[str, bytes] | None:
    """The three bodies of the cut at ``query`` (None: the plane misses the duct)."""
    from stokes.mesh.surfaces import patch_edges, patch_transport, read_header
    from stokes_agent.extract import pack_artifact

    plane = parse_plane(query)
    assert plane is not None
    cut = cut_duct(*plane)
    if cut is None:
        return None
    points, triangles = cut
    sides = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    edges = np.unique(np.sort(sides, axis=1), axis=0).astype("uint32")
    surface = {
        "name": "slice",
        "patch_type": "patch",
        "points": points,
        "triangles": triangles,
        "edges": edges,
        "faces": int(triangles.shape[0]),
    }
    data, _ = pack_artifact([surface])
    head = read_header(data)
    bodies = {
        "surface": patch_transport(data, head, 0, (0.6, 0.64, 0.68)),
        "edges": patch_edges(data, head, 0),
    }
    u, p = speed(points), pressure(points)
    bodies["U_mag"] = field_body("U_mag", "m/s", u, limits=_RANGES.get("U_mag"))
    bodies["p"] = field_body("p", "m2/s2", p, limits=_RANGES.get("p"))
    return bodies


_CUTS: dict[str, dict[str, bytes] | None] = {}


def plane_response(path: str) -> tuple[int, bytes | None, str]:
    """What the server answers for a cut route with a ``plane`` query: ``(status, body, refusal)``.

    ``path`` is the route with its query, as the page asked for it. 404
    ``results.slice_empty`` for a plane that misses the duct, 400 ``results.plane`` for a
    plane that is not one, 400 for an unknown field; a route that is not a cut route is a
    plain 404. Cuts are kept by plane, as the contract's cache does.
    """
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(path)
    query = parse_qs(parts.query)
    pieces = parts.path.strip("/").split("/")
    kind = pieces[2] if len(pieces) > 2 else ""
    is_cut = kind in ("slice", "slice-edges") or (kind == "field" and pieces[4:] == ["slice"])
    if pieces[:2] != ["api", "results"] or "plane" not in query or not is_cut:
        return 404, None, '{"detail":"unknown run or patch"}'
    text = query["plane"][0]
    if parse_plane(text) is None:
        return 400, None, '{"detail":{"code":"results.plane","message":"the plane is not valid"}}'
    if text not in _CUTS:
        _CUTS[text] = _cut_bodies(text)
    bodies = _CUTS[text]
    if bodies is None:
        empty = '{"detail":{"code":"results.slice_empty","message":"the plane misses the model"}}'
        return 404, None, empty
    name = {"slice": "surface", "slice-edges": "edges"}.get(kind, query.get("field", [""])[0])
    if name not in bodies:
        return 400, None, '{"detail":"unknown field"}'
    return 200, bodies[name], ""
