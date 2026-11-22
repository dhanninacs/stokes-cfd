"""The agent's half of a multi-region case (ADR-0042): the split's log, the merge of the regions'
fields onto the whole mesh, the allow-list and the mesh-file rule."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from stokes_agent import jobs
from stokes_agent.flags import ALLOWED_FLAGS
from stokes_agent.foamascii import FoamReadError
from stokes_agent.lint import is_mesh_path
from stokes_agent.parsers import PARSERS
from stokes_agent.parsers.splitregions import parse_split_regions
from stokes_agent.regions import merge_regions

#: The two tables as Foundation 14 printed them for a gyroid core (2026-11-20), with the lines
#: around them.
SPLIT_LOG = """
Creating single patch per inter-region interface

Region  nCells  cellZone
------  ------  --------
   hot   24960       hot
  cold   24960      cold
 solid   78080    <none>

Writing region index per cell as a labelList to "constant/cellToRegion"

    Interface  Owner Region  Neighbour Region  nFaces
-------------  ------------  ----------------  ------
cold_to_solid          cold             solid   22848
 hot_to_solid           hot             solid   22848

Adding interface patches
"""


def test_the_split_log_gives_the_regions_in_index_order_and_the_interfaces() -> None:
    found = parse_split_regions(SPLIT_LOG)
    assert found["regions"] == [
        {"name": "hot", "cells": 24960},
        {"name": "cold", "cells": 24960},
        {"name": "solid", "cells": 78080},
    ]
    assert found["interfaces"] == [
        {"name": "cold_to_solid", "owner": "cold", "neighbour": "solid", "faces": 22848},
        {"name": "hot_to_solid", "owner": "hot", "neighbour": "solid", "faces": 22848},
    ]


def test_a_log_without_the_tables_gives_empty_lists_and_the_parser_is_registered() -> None:
    assert parse_split_regions("FOAM FATAL ERROR\nno zones\n") == {"regions": [], "interfaces": []}
    parser = PARSERS["splitMeshRegions"]()
    assert parser.feed(SPLIT_LOG.splitlines()) == []
    assert len(parser.report()["interfaces"]) == 2


def test_the_new_programs_are_allowed_with_pinned_arguments() -> None:
    assert {"topoSet", "splitMeshRegions", "foamMultiRun"} <= jobs.ALLOWED_UTILITIES
    assert ALLOWED_FLAGS["foamMultiRun"] == {}
    region = ALLOWED_FLAGS["splitMeshRegions"]["-defaultRegion"].pattern
    assert region is not None
    import re

    assert re.match(region, "wall") and not re.match(region, "../wall")


@pytest.mark.parametrize(
    ("path", "mesh"),
    [
        ("constant/polyMesh/points", True),
        ("constant/hot/polyMesh/owner", True),
        ("constant/cellToRegion", True),
        ("constant/hot/physicalProperties", False),
        ("constant/a/b/polyMesh/points", False),
        ("system/hot/polyMesh/points", False),
    ],
)
def test_a_region_mesh_is_a_mesh_file_by_its_place(path: str, mesh: bool) -> None:
    assert is_mesh_path(path) is mesh


HEAD = "FoamFile\n{\n    format ascii;\n    class X;\n    object x;\n}\n"


def _labels(path: Path, values: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{HEAD}\n{len(values)}\n(\n" + "\n".join(map(str, values)) + "\n)\n")


def _field(path: Path, kind: str, values: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{HEAD}\ndimensions [0 0 0 1 0 0 0];\ninternalField {values};\n")
    del kind


def _case(root: Path) -> Path:
    """Four cells in a row: fluid `a` holds cells 0 and 2, the solid `s` cells 1 and 3."""
    case = root / "case"
    _labels(case / "constant" / "cellToRegion", [0, 1, 0, 1])
    # Three internal faces (owners 0, 1, 2) and two boundary faces (owners 0 and 3).
    _labels(case / "constant" / "polyMesh" / "owner", [0, 1, 2, 0, 3])
    (case / "constant" / "polyMesh" / "boundary").write_text(
        f"{HEAD}\n2\n(\n    left\n    {{\n        type patch;\n        nFaces 1;\n"
        "        startFace 3;\n    }\n    right\n    {\n        type wall;\n"
        "        nFaces 1;\n        startFace 4;\n    }\n)\n"
    )
    _field(case / "5" / "a" / "T", "scalar", "nonuniform List<scalar> 2 ( 350 340 )")
    _field(case / "5" / "s" / "T", "scalar", "uniform 320")
    _field(case / "5" / "a" / "U", "vector", "nonuniform List<vector> 2 ( (1 0 0) (2 0 0) )")
    _field(case / "5" / "a" / "p", "scalar", "nonuniform List<scalar> 2 ( 10 20 )")
    return case


def test_the_merge_places_each_regions_cells_on_the_whole_mesh(tmp_path: Path) -> None:
    from stokes_agent.regions import _internal

    case = _case(tmp_path)
    message = merge_regions(case, ["a", "s"])
    assert message == "time 5: T U p on 4 cells"
    assert _internal(case / "5" / "T", 1, 4).tolist() == [350.0, 320.0, 340.0, 320.0]
    # A solid has no velocity (zero) and no pressure (the lowest fluid value).
    assert _internal(case / "5" / "U", 3, 4)[:, 0].tolist() == [1.0, 0.0, 2.0, 0.0]
    assert _internal(case / "5" / "p", 1, 4).tolist() == [10.0, 10.0, 20.0, 10.0]
    text = (case / "5" / "T").read_text()
    # Each patch carries the value of the cell behind it: cell 0 on the left, cell 3 on the right.
    assert "left" in text and "right" in text
    left = text[text.index("left") : text.index("right")]
    assert "350" in left and "calculated" in left
    assert not (case / "5" / "p_rgh").exists()


def test_the_merge_refuses_a_region_whose_cell_count_differs(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _field(case / "5" / "a" / "T", "scalar", "nonuniform List<scalar> 3 ( 350 340 330 )")
    with pytest.raises(FoamReadError):
        merge_regions(case, ["a", "s"])


def test_the_merge_refuses_names_that_are_not_regions(tmp_path: Path) -> None:
    case = _case(tmp_path)
    for bad in (["../a", "s"], [], ["a"]):
        with pytest.raises(FoamReadError):
            merge_regions(case, bad)
    assert np.isfinite(1.0)
