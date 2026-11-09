"""Probes of the STEP reader that must run in a process of their own.

OCCT reports some faults as an access violation, which ends the process it happens in with no
Python exception. ``tests/test_geometry_step.py`` runs these as
``python -m tests.step_probe <probe> [path]``, so a fault fails that one test with an exit code
instead of taking pytest down. A probe prints one ``RESULT <json>`` line: OCCT writes its own
progress text to the same stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from tests import geometry_fixtures as fx


def read(path: str) -> dict[str, Any]:
    """``read_step`` on a file: name, face count and colour of each solid."""
    from stokes.geometry.step import read_step

    model = read_step(Path(path))
    return {"solids": [[s.name, s.faces, s.color] for s in model.solids]}


def named_sub_labels() -> dict[str, Any]:
    """Solids whose sub-shape labels carry a name and a colour, walked in memory.

    The STEP writer drops those names, so this is the only way to see the reader use one.
    """
    _document, part = fx.subsolid_document()  # the document must outlive the label
    return {"bodies": _bodies(part)}


def nameless_labels() -> dict[str, Any]:
    """A part and two solid sub-labels with no TDataStd_Name at all, read through the reader."""
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    from stokes.geometry.step import _label_name

    XCAFDoc_ShapeTool.SetAutoNaming_s(False)  # process-wide: why this runs in its own process
    _document, part = fx.subsolid_document(named=False)
    return {"part_name": _label_name(part), "bodies": _bodies(part)}


def _bodies(part: Any) -> list[list[Any]]:  # Any: OCP TDF_Label
    from OCP.TopLoc import TopLoc_Location

    from stokes.geometry.step import _Body, _Context, _walk

    found: list[_Body] = []
    _walk(part, TopLoc_Location(), _Context(), found, 0)
    return [[body.names[0], body.color, body.kind] for body in found]


def main(argv: list[str]) -> int:
    probes = {
        "read": read,
        "named_sub_labels": named_sub_labels,
        "nameless_labels": nameless_labels,
    }
    result = probes[argv[0]](*argv[1:])
    sys.stdout.write("RESULT " + json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
