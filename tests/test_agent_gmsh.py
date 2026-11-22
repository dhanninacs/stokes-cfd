"""The agent's side of Gmsh (ADR-0043): the script grammar, the log parser, finding `gmsh.exe`.

A `.geo` script is a program - Gmsh's language can run shell commands and read or write any file -
so the agent holds a staged script to the statements the driver writes and nothing else. These
tests try the doors a hostile script would try.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stokes_agent.geo_lint import lint_geo
from stokes_agent.lint import lint_file
from stokes_agent.native_env import gmsh_tool
from stokes_agent.parsers.gmsh import GmshParser

# ------------------------------------------------------------------ the grammar


@pytest.mark.parametrize(
    "line",
    [
        'SystemCall "calc";',
        'NonBlockingSystemCall "calc";',
        'Shell "rm -rf /";',
        'OnelabRun "x";',
        'Include "evil.geo";',
        'Merge "/etc/passwd";',
        'Merge "../../outside.stl";',
        'Merge "../constant/geometry/a/b.stl";',
        'Merge "../system/controlDict";',
        'Save "../../evil.msh";',
        'Save "/tmp/x.msh";',
        'Print "x.png";',
        'Printf("x") > "/tmp/f";',
        'Printf("x") >> "f";',
        'Printf("%g", GetEnv("HOME"));',
        'x = GetEnv("HOME");',
        "x = Exp(1);",
        "x = StrCat(1);",
        'CreateDir "x";',
        "General.NumThreads = 4;",
        'Solver.Executable0 = "calc";',
        "Mesh.Algorithm = 1;",
        "x = #calc;",
        "x = 1; // trailing",
        "x = 1; /* block */",
        "Exit;",
        "x = $FOAM_ETC;",
        "x = a[1];",
        "Physical Surface(\"a\" + 'b') = {1};",
        'Physical Surface("a b") = {1};',
        "Field[1] = MathEval;",
        'Field[1].F = "1";',
        "Plugin(Crack).Run;",
        "x() = Point In BoundingBox{0, 0, 0, 1, 1, 1};",
        "ElseIf (1)",
        "EndFor",
        "If (1)",
    ],
)
def test_a_statement_the_driver_does_not_write_is_refused(line: str) -> None:
    assert lint_geo(line + "\n"), line


def test_a_control_character_is_refused_and_a_windows_line_end_is_not() -> None:
    assert lint_geo('x = 1;\rSystemCall "y";\n')
    assert lint_geo("x = 1;\x00\n")
    assert lint_geo("x = 1;\r\ny = 2;\r\n") == []


def test_the_topology_statement_is_allowed_and_only_bare() -> None:
    assert lint_geo("Coherence Mesh;\nCreateTopology;\n") == []
    assert lint_geo("CreateTopology{0, 1};\n")
    assert lint_geo('CreateTopology; Shell "x";\n')


def test_blocks_must_balance() -> None:
    assert lint_geo("For i In {0:2}\nx = i;\nEndFor\n") == []
    assert lint_geo("If (1)\nx = 1;\nElseIf (2)\nx = 2;\nEndIf\n") == []
    assert lint_geo("For i In {0:2}\nEndIf\n")
    assert lint_geo("If (1)\nEndFor\n")


def test_too_many_lines_is_refused_without_reading_them() -> None:
    from stokes_agent.geo_lint import MAX_LINES

    found = lint_geo("x = 1;\n" * (MAX_LINES + 1))
    assert len(found) == 1 and "more than" in found[0][2]


def test_a_staged_script_is_judged_by_the_grammar_not_the_dictionary_rules(tmp_path: Path) -> None:
    good = tmp_path / "good.geo"
    good.write_bytes(b'Merge "../constant/geometry/walls.stl";\nMesh 3;\n')
    assert lint_file("system/domain.geo", good) == []
    bad = tmp_path / "bad.geo"
    bad.write_bytes(b'x = 1;\nShell "calc";\n')
    found = lint_file("system/domain.geo", bad)
    assert [f.line for f in found] == [2]
    assert found[0].file == "system/domain.geo"


# ------------------------------------------------------------------ the parser

LOG = """Info    : Running 'gmsh -nopopup system/domain.geo -' [Gmsh 4.15.2, 1 node, max. 1 thread]
Info    : Reading 'system/domain.geo'...
\x1b[34mstokes layers 3\x1b[0m
Info    : Meshing 1D...
Info    : Meshing 2D...
Info    : Meshing 3D...
Warning : Could not orient normal of surface 115
Error   : PLC Error:  A segment and a facet intersect at point
Error   : Invalid boundary mesh (segment-facet intersection) on surface 91
Info    : 3929 nodes 10285 elements
Error   : ------------------------------
Error   : Mesh generation error summary
Error   :     1 warning
Error   :     2 errors
Error   : Check the full log for details
"""


def test_the_parser_keeps_gmshs_errors_and_the_layer_count() -> None:
    parser = GmshParser()
    assert parser.feed(LOG.splitlines()) == []
    report = parser.report()
    assert report["errors"] == [
        "PLC Error:  A segment and a facet intersect at point",
        "Invalid boundary mesh (segment-facet intersection) on surface 91",
    ]
    assert report["layers"] == 3
    assert report["warnings"] == 1
    assert report["stage"] == "3D"
    assert (report["nodes"], report["elements"]) == (3929, 10285)


def test_the_parser_reads_a_clean_run_and_never_raises() -> None:
    parser = GmshParser()
    parser.feed(["Info    : 839 nodes 4481 elements", "x" * 10_000, "\x1b[1mnoise", ""])
    report = parser.report()
    assert report["errors"] == [] and report["layers"] == 0
    assert report["nodes"] == 839


# ------------------------------------------------------------------ finding gmsh.exe


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "OF14-win"
    (root / "gmsh").mkdir(parents=True)
    (root / "gmsh" / "gmsh.exe").write_bytes(b"MZ")
    return root


def test_the_bundled_gmsh_is_found_beside_the_install(tmp_path: Path) -> None:
    root = _install(tmp_path)
    found = str((root / "gmsh" / "gmsh.exe").resolve())
    assert gmsh_tool(root, None) == found
    assert gmsh_tool(root, {"gmsh": str(root / "gmsh" / "gmsh.exe")}) == found


def test_a_handshake_path_outside_the_install_is_not_taken(tmp_path: Path) -> None:
    root = _install(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "gmsh.exe").write_bytes(b"MZ")
    (root / "gmsh" / "calc.exe").write_bytes(b"MZ")
    inside = str((root / "gmsh" / "gmsh.exe").resolve())
    # Outside the install, or not called gmsh.exe: the standard folder wins.
    assert gmsh_tool(root, {"gmsh": str(elsewhere / "gmsh.exe")}) == inside
    assert gmsh_tool(root, {"gmsh": str(root / "gmsh" / "calc.exe")}) == inside
    assert gmsh_tool(root, {"gmsh": "gmsh/gmsh.exe"}) == inside
    (root / "gmsh" / "gmsh.exe").unlink()
    assert gmsh_tool(root, {"gmsh": str(elsewhere / "gmsh.exe")}) is None
    assert gmsh_tool(tmp_path / "missing", None) is None
