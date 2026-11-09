"""The synthetic mesh surfaces of `tools/mesh_demo_project.py` (T-211): right counts, real format.

The tool exists so the viewport's mesh look can be captured without OpenFOAM, so what it writes
must be what a real run would: the cell faces of the block, edged and triangulated by the agent's
writer, and readable by the desktop's own reader (`stokes.mesh.surfaces`).
"""

from __future__ import annotations

import numpy as np
import pytest

from stokes.mesh import surfaces
from stokes_agent.extract import pack_artifact
from tools.mesh_demo_project import patch_surface, quality_report

BBOX = (0.0, 0.0, 0.0, 0.4, 0.2, 0.2)
COUNTS = (4, 2, 2)


def test_one_side_is_a_grid_of_cell_faces() -> None:
    inlet = patch_surface("inlet", "patch", ["xmin"], BBOX, COUNTS)
    # 2 x 2 cell faces, two triangles each, and the grid's own edges: 3 * 2 + 2 * 3 = 12.
    assert inlet["faces"] == 4
    assert inlet["triangles"].shape == (8, 3)
    assert inlet["edges"].shape == (12, 2)
    assert inlet["points"].shape == (9, 3)
    assert np.allclose(inlet["points"][:, 0], 0.0)


def test_sides_of_one_patch_share_their_corner_points() -> None:
    walls = patch_surface("walls", "wall", ["ymin", "ymax", "zmin", "zmax"], BBOX, COUNTS)
    # 4 x 2 faces on each of the four sides.
    assert walls["faces"] == 32
    # The sides' points are merged where they meet: the box's long corner lines belong to two sides.
    lattice = 5 * 3 * 3
    interior, cap_centres = 3, 2
    assert walls["points"].shape[0] == lattice - interior - cap_centres
    unique = np.unique(walls["points"], axis=0)
    assert unique.shape[0] == walls["points"].shape[0]
    # The distinct coordinates per axis are the lines of the block: cells + 1.
    assert [len(np.unique(np.round(walls["points"][:, a], 7))) for a in range(3)] == [5, 3, 3]


def test_the_agents_header_accepts_the_patches_and_every_index_names_a_point() -> None:
    patches = [
        patch_surface("inlet", "patch", ["xmin"], BBOX, COUNTS),
        patch_surface("walls", "wall", ["ymin", "ymax", "zmin", "zmax"], BBOX, COUNTS),
    ]
    data, _ = pack_artifact(patches)
    header = surfaces.read_header(data)
    assert [p["faces"] for p in header["patches"]] == [4, 32]
    for index in range(2):
        positions, triangles, edges = surfaces.patch_arrays(data, header, index)
        assert triangles.max() < len(positions) and edges.max() < len(positions)
    # The edge codec the viewport reads: six floats per segment, one per unique cell edge.
    segments = surfaces.unpack_edges(surfaces.patch_edges(data, header, 0))
    assert segments.shape == (12, 6)


@pytest.mark.parametrize(
    ("counts", "internal"), [((4, 2, 2), 3 * 4 + 4 * 1 * 2 + 4 * 2 * 1), ((1, 1, 1), 0)]
)
def test_the_quality_figures_count_the_faces_of_a_block(
    counts: tuple[int, int, int], internal: int
) -> None:
    report = quality_report(counts, boundary_faces=10)
    assert report["internal_faces"] == internal
    assert report["faces"] == internal + 10
    assert report["cells"] == counts[0] * counts[1] * counts[2]
    assert report["mesh_ok"] is True
