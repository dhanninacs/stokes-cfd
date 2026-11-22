"""A fluid zone keeps only the cells its openings reach (`stokes_agent.zones`)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from stokes_agent import zones

HEADER = "FoamFile\n{\n    format      ascii;\n    class       labelList;\n}\n\n"


def _labels(values: list[int]) -> str:
    return HEADER + f"{len(values)}\n(\n" + "\n".join(map(str, values)) + "\n)\n"


def _case(tmp_path: Path) -> Path:
    """Eight cells in a row, 0-7. Zone `hot` is cells 0-2 and the pocket 5-6 (no face to 0-2).

    Internal faces join each cell to the next (7 faces). Boundary: `hot_in` is a face of cell 0,
    `wall` a face of cell 6.
    """
    mesh = tmp_path / "constant" / "polyMesh"
    mesh.mkdir(parents=True)
    owner = [*range(7), 0, 6]
    neighbour = list(range(1, 8))
    (mesh / "owner").write_text(_labels(owner), encoding="utf-8")
    (mesh / "neighbour").write_text(_labels(neighbour), encoding="utf-8")
    (mesh / "boundary").write_text(
        HEADER.replace("labelList", "polyBoundaryMesh")
        + "2\n(\n    hot_in\n    {\n        type patch;\n        nFaces 1;\n"
        + "        startFace 7;\n    }\n"
        + "    wall\n    {\n        type wall;\n        nFaces 1;\n"
        + "        startFace 8;\n    }\n)\n",
        encoding="utf-8",
    )
    cells = [0, 1, 2, 5, 6]
    (mesh / "cellZones").write_text(
        HEADER.replace("labelList", "cellZoneList")
        + "1\n(\nhot\n{\ncellLabels      List<label> \n5\n(\n"
        + "\n".join(map(str, cells))
        + "\n)\n;\n}\n)\n",
        encoding="utf-8",
    )
    return tmp_path


def test_a_pocket_no_opening_reaches_leaves_the_zone(tmp_path: Path) -> None:
    case = _case(tmp_path)
    report = zones.prune_zones(case, {"hot": ["hot_in"]})
    assert "2 pieces, 1 kept, 2 cells" in report
    _head, found, _tail = zones._zones(case / "constant" / "polyMesh" / "cellZones")
    assert found[0][0] == "hot"
    assert list(found[0][1]) == [0, 1, 2]
    # Again: nothing left to prune, and the file is read back as written.
    assert "1 pieces, 1 kept, 0 cells" in zones.prune_zones(case, {"hot": ["hot_in"]})


def test_a_zone_no_named_patch_reaches_keeps_its_largest_piece(tmp_path: Path) -> None:
    case = _case(tmp_path)
    report = zones.prune_zones(case, {"hot": ["no_such_patch"]})
    assert "kept the largest" in report
    _head, found, _tail = zones._zones(case / "constant" / "polyMesh" / "cellZones")
    assert list(found[0][1]) == [0, 1, 2]


def test_pieces_are_face_connected_within_the_zone_only() -> None:
    owner = np.array([0, 1, 2, 3])
    neighbour = np.array([1, 2, 3, 4])
    # Cells 0, 1 and 3, 4: cell 2 is outside the zone, so the zone is two pieces.
    piece = zones.components(np.array([0, 1, 3, 4]), owner, neighbour, 5)
    assert piece[0] == piece[1] and piece[2] == piece[3] and piece[0] != piece[2]
