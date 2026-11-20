"""The lint for a Gmsh job's two inputs: its script and the STEP it reads (ADR-0007 decision 5).

## Why the script needs a closed grammar, not a deny-list

A Gmsh `.geo` file is a program. Besides geometry and mesh options it has `SystemCall` and
`NonBlockingSystemCall` (a shell command), `Include` and `Merge` (any file, by absolute path),
`Save` and `Print` (write any file), `Plugin(...)` and `OnelabRun`. A deny-list of those would be a
bet that the language has no other door, so this goes the other way: **every line must fully match
one of the few shapes `stokes.mesh.drivers.gmsh.geo` writes**, numbers and names included, and
anything else is refused with its line number. A new shape in the driver needs a pattern here,
with its reason; the driver's tests check that what it writes passes this module.

The script must also say `Geometry.OCCImportLabels = 0;`: that keeps Gmsh on OpenCASCADE's plain
STEP reader, which reads the one file it is given, rather than the document reader, which can
follow references to other files.

## The STEP is data, checked as data

OpenCASCADE parses STEP; it has no directive that runs anything. What is checked is what a reader
of untrusted input owes the process: exactly one path (`constant/geometry/domain.step`), a size
limit, printable ASCII only (no NUL, no binary), and the ISO 10303-21 header and trailer, read
**as a stream** so a large part costs one block of memory.
"""

from __future__ import annotations

import re
from pathlib import Path

from .lint import Finding

#: The two paths this module owns, exactly.
GEO_PATH = "constant/geometry/mesh.geo"
STEP_PATH = "constant/geometry/domain.step"

#: The most a script may be. A 5,000-face part writes about 1.5 MB (four lines per face).
MAX_GEO_BYTES = 32 * 1024 * 1024
#: The longest script line: a physical group of every face of a large part on one line.
MAX_GEO_LINE = 1024 * 1024
#: The most a staged STEP may be, as for a staged STL (the owner's 500 MiB).
MAX_STEP_BYTES = 500 * 1024 * 1024
#: How much of the STEP is held at once.
STEP_BLOCK_BYTES = 1024 * 1024
#: The most findings one script reports.
MAX_FINDINGS = 32
#: Any byte that is not printable ASCII, tab or a line end.
_NOT_TEXT = re.compile(rb"[^\t\n\r\x20-\x7e]")

_NUM = r"-?\d{1,12}(?:\.\d{1,20})?(?:e[+-]?\d{1,3})?"
_INTS = r"\d{1,9}(?:, \d{1,9})*"
_NAME = r"[A-Za-z][A-Za-z0-9_]{0,63}"
_BOX = rf"Fabs\(bb\([0-5]\) - {_NUM}\) > {_NUM}"

#: Every line shape the driver writes, each matched against the WHOLE line.
GEO_LINES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"",
        r"// [ -~]{0,200}",
        r'SetFactory\("OpenCASCADE"\);',
        r"Geometry\.(?:OCCAutoFix|OCCImportLabels|OCCBoundsUseStl) = [01];",
        r'Geometry\.OCCTargetUnit = "M";',
        r'v\(\) = ShapeFromFile\("domain\.step"\);',
        r"s\(\) = Surface\{:\};",
        r"If \(#s\(\) != \d{1,9} \|\| #v\(\) != 1\)",
        r'  Error\("[A-Za-z0-9 .,;:()_\'-]{1,160}"\);',
        r"EndIf",
        r"bb\(\) = BoundingBox Surface\{\d{1,9}\};",
        rf"If \({_BOX}(?: \|\| {_BOX}){{0,5}}\)",
        rf'Physical Surface\("{_NAME}"\) = \{{{_INTS}\}};',
        rf'Physical Volume\("{_NAME}"\) = v\(\);',
        rf"MeshSize\{{ PointsOf\{{ Surface\{{{_INTS}\}}; \}} \}} = {_NUM};",
        r"Mesh\.(?:MeshSizeMax|MeshSizeMin|MeshSizeFromCurvature|Algorithm|Algorithm3D|Optimize"
        rf"|MshFileVersion|Binary) = {_NUM};",
    )
)

#: Lines the script must contain, each with its reason.
GEO_REQUIRED: dict[str, str] = {
    "Geometry.OCCImportLabels = 0;": "keeps Gmsh on the plain STEP reader (no external files)",
    'v() = ShapeFromFile("domain.step");': "the one file the script reads",
}


def lint_geo(rel: str, path: Path) -> list[Finding]:
    """Refuse a Gmsh script with any line outside the closed grammar, or without its required
    lines."""
    try:
        size = path.stat().st_size
        if size > MAX_GEO_BYTES:
            return [Finding(rel, 0, "", f"the script is {size} bytes, over {MAX_GEO_BYTES}")]
        raw = path.read_bytes()
    except OSError as exc:
        return [Finding(rel, 0, "", f"the file could not be read: {exc.strerror or exc}")]
    if _NOT_TEXT.search(raw):
        return [Finding(rel, 0, "", "a Gmsh script must be printable ASCII")]
    text = raw.decode("ascii")
    found: list[Finding] = []
    seen: set[str] = set()
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if len(line) > MAX_GEO_LINE:
            found.append(Finding(rel, number, line[:40], "a line longer than the limit"))
        elif not any(pattern.fullmatch(line) for pattern in GEO_LINES):
            found.append(
                Finding(rel, number, line[:80], "a line outside the Gmsh grammar Stokes writes")
            )
        else:
            seen.add(line)
        if len(found) >= MAX_FINDINGS:
            break
    for line, why in GEO_REQUIRED.items():
        if line not in seen:
            found.append(Finding(rel, 0, line, f"the script must contain this line: {why}"))
    return found


def lint_step(rel: str, path: Path) -> list[Finding]:
    """Refuse a STEP file that is too large, not printable ASCII, or not ISO 10303-21 text."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [Finding(rel, 0, "", f"the file could not be read: {exc.strerror or exc}")]
    if size > MAX_STEP_BYTES:
        return [Finding(rel, 0, "", f"the STEP is {size} bytes, over the {MAX_STEP_BYTES} limit")]
    head = b""
    tail = b""
    try:
        with path.open("rb") as handle:
            while block := handle.read(STEP_BLOCK_BYTES):
                if _NOT_TEXT.search(block):
                    return [Finding(rel, 0, "", "a STEP file must be printable ASCII")]
                if len(head) < 64:
                    head += block[: 64 - len(head)]
                tail = (tail + block)[-256:]
    except OSError as exc:
        return [Finding(rel, 0, "", f"the file could not be read: {exc.strerror or exc}")]
    found: list[Finding] = []
    if not head.lstrip().startswith(b"ISO-10303-21;"):
        found.append(Finding(rel, 1, head[:20].decode("ascii"), "not an ISO 10303-21 STEP file"))
    if b"END-ISO-10303-21;" not in tail:
        found.append(Finding(rel, 0, "", "the STEP file does not end with END-ISO-10303-21;"))
    return found


def lint_gmsh_input(rel: str, path: Path) -> list[Finding] | None:
    """The findings for a Gmsh script or STEP at its one path; None for any other file."""
    if rel == GEO_PATH:
        return lint_geo(rel, path)
    if rel == STEP_PATH:
        return lint_step(rel, path)
    return None
