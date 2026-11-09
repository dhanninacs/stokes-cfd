"""The no-OpenFOAM-content gate (T-318): green on the tree, red on a planted file.

Every planted file is written by the test; the banner is assembled from pieces so this test file
does not itself carry it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent


def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_no_foam_content", ROOT / "tools" / "check_no_foam_content.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BANNER = "\\\\" + "      /   " + "F ield         | OpenFOAM"
HEADER = "FoamFile\n{\n    format ascii;\n}\n"


def test_the_repository_is_clean() -> None:
    assert _gate().scan() == []


def test_a_copied_dictionary_and_a_banner_are_caught(tmp_path: Path) -> None:
    gate = _gate()
    (tmp_path / "stokes" / "data").mkdir(parents=True)
    (tmp_path / "stokes" / "data" / "controlDict").write_text(HEADER + "endTime 1;\n")
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "notes.py").write_text(f"# {BANNER}\n")
    (tmp_path / "web" / "node_modules").mkdir(parents=True)
    (tmp_path / "web" / "node_modules" / "controlDict").write_text(HEADER)
    hits = sorted(gate.scan(tmp_path))
    assert hits == [
        "agent/notes.py: the OpenFOAM banner",
        "stokes/data/controlDict: an OpenFOAM dictionary header (FoamFile)",
    ]


def test_a_printer_may_write_the_header_from_code(tmp_path: Path) -> None:
    (tmp_path / "stokes").mkdir()
    (tmp_path / "stokes" / "printer.py").write_text(f'HEADER = """{HEADER}"""\n')
    assert _gate().scan(tmp_path) == []
