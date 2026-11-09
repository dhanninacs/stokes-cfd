"""What one OpenFOAM case needs, read statically from its files (ADR-0029, plan T-304).

## Responsibility

`read_case` reports a case's application and solver (a module, a `regionSolvers` map, or a legacy
application), the libraries its `libs` entries load, the etc files its `#includeEtc` and
`#includeFunc` directives name, any runtime-compiled or shell code (`#codeStream`, `#calc`,
`coded*`, `systemCall`, `#{ ... #}`), and what its Allrun asks to run. `catalogue` turns those facts
into a status against a probed install.

## Why static, and what that misses

Nothing from a case is run, so a fact is what the files say: a library loaded by a function object
inside an `#include`d file is seen only when that file sits under `system/`, `constant/`, `0/` or
`0.orig/`, and an Allrun line this reader does not map is reported rather than guessed at. That is
the "limits of static classification" the plan accepts; `needs_recipe` and `unverified` say so.

Every file is bounded (`MAX_SCAN_BYTES`, `MAX_ALLRUN_BYTES`, `MAX_FILES`), symlinks and Windows
junctions are not followed, binary and compressed files are skipped, and a file the reader refuses
is skipped, not fatal.

Import rules: standard library, `stokes.compile.foam_read`. Headless.
"""

from __future__ import annotations

import itertools
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from stokes.cases.calc import resolve_calc
from stokes.compile.foam_read import (
    BlockNode,
    DirectiveNode,
    Document,
    EntryNode,
    FoamReadError,
    Kind,
    Token,
    parse,
    walk,
)

#: Larger files under the scanned folders are mesh or field data, never a dictionary that loads a
#: library; reading them would cost time and memory for nothing.
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_ALLRUN_BYTES = 256 * 1024
#: Files read per case. The largest OpenFOAM 14 tutorial has well under a hundred text files.
MAX_FILES = 400
#: How deep the scan goes below a scanned folder (regions add one level, `include/` another).
MAX_SCAN_DEPTH = 4
SCAN_DIRS: tuple[str, ...] = ("system", "constant", "0", "0.orig")
#: Folders that hold mesh, surface or boundary data rather than dictionaries.
SKIP_DIRS = frozenset(
    {"polyMesh", "triSurface", "geometry", "boundaryData", "extendedFeatureEdgeMesh"}
)
#: Directives that compile and run code inside OpenFOAM (the `external` lint refuses them).
CODED_DIRECTIVES = frozenset({"codeStream", "calc", "eval"})
#: Applications that take a solver module rather than being the solver.
MODULE_RUNNERS = frozenset({"foamRun", "foamMultiRun"})
LIBRARY_SUFFIXES: tuple[str, ...] = (".so", ".dll", ".dylib")
#: Allrun lines that carry no step: the working-directory guard, sourcing RunFunctions, the
#: application lookup and the test-mode exit.
_GUARD_LINES = frozenset(
    {
        "cd ${0%/*} || exit 1",
        'cd "${0%/*}" || exit 1',
        "application=$(getApplication)",
        'isTest "$@" && exit 0',
    }
)
#: A file holding none of these bytes has no fact `_collect` looks for, so it is not parsed. Most
#: field files are skipped this way: parsing the 260 OpenFOAM 14 tutorials' files takes seconds.
_MARKERS: tuple[bytes, ...] = (b"libs", b"#", b"coded", b"systemCall")
_RUNNERS = frozenset({"runApplication", "runParallel"})
_TRAILING_COMMENT = re.compile(r"\s+#.*$")
#: RunFunctions options that take a value.
_VALUED_FLAGS = frozenset({"-s", "-suffix", "-np"})
#: Characters that make a line a shell construct rather than one argv step.
_SHELL_CHARS = frozenset(";|&<>`")


def library_name(text: str) -> str | None:
    """`"libfoo.so"`, `libfoo.dll`, `foo` -> `foo`: one spelling for a case's `libs` and the probe.

    A path keeps only its last part. Empty or nonsense input gives None.
    """
    name = text.strip().strip('"').replace("\\", "/").rsplit("/", 1)[-1]
    for suffix in LIBRARY_SUFFIXES:
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    if name.startswith("lib"):
        name = name[3:]
    return name or None


@dataclass
class AllrunFacts:
    """What an Allrun asks for, as far as a line reader can tell."""

    #: Utilities named by `runApplication`/`runParallel`, in order, without duplicates.
    utilities: list[str] = field(default_factory=list)
    #: Whether a step runs the controlDict's application (`$(getApplication)`, `$application`).
    runs_application: bool = False
    #: Modules named by a `-solver` argument.
    solvers: list[str] = field(default_factory=list)
    #: Lines that are not a guard and not a single `runApplication`/`runParallel` step.
    unmapped: list[str] = field(default_factory=list)


@dataclass
class CaseFacts:
    """What one case's files say it needs."""

    readable: bool = True
    #: Why the controlDict could not be read, when `readable` is false.
    problem: str = ""
    application: str | None = None
    solver: str | None = None
    region_solvers: dict[str, str] = field(default_factory=dict)
    libraries: set[str] = field(default_factory=set)
    etc_functions: set[str] = field(default_factory=set)
    etc_includes: set[str] = field(default_factory=set)
    #: `file: construct` for each runtime-code construct found.
    coded: list[str] = field(default_factory=list)
    allrun: AllrunFacts | None = None
    has_block_mesh_dict: bool = False
    has_mesh: bool = False


def _word(node: object) -> str | None:
    """A word or quoted-string value's text, unquoted; None for anything else."""
    if isinstance(node, Token) and node.kind in (Kind.WORD, Kind.STRING):
        return node.text.strip('"')
    return None


def _key(node: EntryNode | BlockNode) -> str:
    return node.key.text.strip('"')


def _is_link(path: Path) -> bool:
    """Whether ``path`` is a symbolic link or a Windows junction.

    **A junction is a link too**, and `is_symlink` does not see it: the case copy
    (`stokes.cases.store.collect`) checks both, and so does every read here, or a junction inside a
    case would make the reader descend into whatever folder it names.
    """
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def _read_bounded(path: Path, limit: int) -> bytes | None:
    """The file's bytes, or None when it is a link, too large, binary, compressed or unreadable."""
    try:
        if _is_link(path) or not path.is_file() or path.stat().st_size > limit:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192] or data[:2] == b"\x1f\x8b":
        return None
    return data


def _dictionary_files(case: Path) -> list[Path]:
    """Candidate dictionaries under the scanned folders, sorted, bounded; links not followed."""
    found: list[Path] = []
    for top in SCAN_DIRS:
        stack: list[tuple[Path, int]] = [(case / top, 0)]
        while stack and len(found) < MAX_FILES:
            folder, depth = stack.pop()
            if _is_link(folder) or not folder.is_dir():
                continue
            try:
                entries = sorted(folder.iterdir(), reverse=True)
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") or _is_link(entry):
                    continue
                if entry.is_dir():
                    if depth < MAX_SCAN_DEPTH and entry.name not in SKIP_DIRS:
                        stack.append((entry, depth + 1))
                elif entry.suffix.lower() not in {".gz", ".stl", ".obj", ".vtk", ".emesh"}:
                    found.append(entry)
    return sorted(found[:MAX_FILES])


def _control(doc: Document, facts: CaseFacts) -> None:
    """Application, solver and regionSolvers from the controlDict's top level."""
    for item in doc.items:
        if isinstance(item, EntryNode) and item.values:
            key = _key(item)
            if key == "application":
                facts.application = _word(item.values[0])
            elif key == "solver":
                facts.solver = _word(item.values[0])
        elif isinstance(item, BlockNode) and _key(item) == "regionSolvers":
            for region in item.body.items:
                if isinstance(region, EntryNode) and region.values:
                    module = _word(region.values[0])
                    if module:
                        facts.region_solvers[_key(region)] = module


def _collect(doc: Document, where: str, facts: CaseFacts) -> None:
    """Libraries, etc references and runtime code anywhere in one dictionary."""
    for node in walk(doc):
        if isinstance(node, EntryNode):
            key = _key(node)
            if key == "libs":
                for part in node.values:
                    for leaf in walk(part):
                        text = _word(leaf)
                        name = library_name(text) if text else None
                        if name:
                            facts.libraries.add(name)
            elif key == "type" and node.values:
                kind = _word(node.values[0]) or ""
                if kind.startswith("coded") or kind == "systemCall":
                    facts.coded.append(f"{where}: type {kind}")
        elif isinstance(node, DirectiveNode):
            argument = node.argument
            text = argument.text.strip().strip('"') if isinstance(argument, Token) else ""
            if node.name in CODED_DIRECTIVES:
                facts.coded.append(f"{where}: #{node.name}")
            elif node.name == "includeEtc" and text:
                facts.etc_includes.add(text)
            elif node.name in {"includeFunc", "includeFunction"} and text:
                facts.etc_functions.add(text.split("(", 1)[0].strip())
        elif isinstance(node, Token) and node.kind is Kind.VERBATIM:
            facts.coded.append(f"{where}: #{{ ... #}}")


def read_allrun(path: Path) -> AllrunFacts:
    """The steps an Allrun names, never running it. Unmapped lines are kept for the reason."""
    facts = AllrunFacts()
    data = _read_bounded(path, MAX_ALLRUN_BYTES)
    if data is None:
        facts.unmapped.append("(Allrun could not be read)")
        return facts
    lines: list[str] = []
    pending = ""
    for raw in data.decode("latin-1").splitlines():
        line = pending + raw.strip()
        if line.endswith("\\"):
            pending = line[:-1] + " "
            continue
        pending = ""
        lines.append(line)
    for raw_line in lines:
        # A trailing ` # comment` is not part of the step; runs of blanks are one separator.
        line = " ".join(_TRAILING_COMMENT.sub("", raw_line).split())
        if not line or line.startswith("#") or line in _GUARD_LINES:
            continue
        words = line.split()
        if words[0] in {".", "source"} and len(words) == 2 and "RunFunctions" in words[1]:
            continue
        if words[0] in _RUNNERS and not (_SHELL_CHARS & set(line)) and _step(line, facts):
            continue
        facts.unmapped.append(line[:200])
    return facts


def _step(line: str, facts: AllrunFacts) -> bool:
    """Record one `runApplication`/`runParallel` line; False when it is not a single step."""
    try:
        argv = shlex.split(line, posix=True)
    except ValueError:
        return False
    index = 1
    while index < len(argv) and argv[index].startswith("-"):
        index += 2 if argv[index] in _VALUED_FLAGS else 1
    if index >= len(argv):
        return False
    tool, rest = argv[index], argv[index + 1 :]
    if tool.startswith("$"):
        if tool not in {"$(getApplication)", "$application", "${application}"}:
            return False
        facts.runs_application = True
    elif tool not in facts.utilities:
        facts.utilities.append(tool)
    for flag, value in itertools.pairwise(rest):
        if flag == "-solver" and not value.startswith("$") and value not in facts.solvers:
            facts.solvers.append(value)
    return True


def scan_dictionaries(files: Iterable[tuple[str, Path]], facts: CaseFacts) -> None:
    """Add the libraries, etc references and runtime code of ``files`` (`(where, path)`).

    The catalogue also runs this over the `resources/` files an Allrun copies into the case: the
    import lints those too, so a `#codeStream` there refuses the import as one in the case does.
    """
    for where, path in files:
        body = _read_bounded(path, MAX_SCAN_BYTES)
        if body is None or not any(marker in body for marker in _MARKERS):
            continue
        if b"#calc" in body:
            # The import replaces every `#calc` it can evaluate before the lint (ADR-0033), so only
            # the ones left make the case coded.
            body = resolve_calc(body.decode("latin-1")).text.encode("latin-1")
        try:
            doc = parse(body)
        except FoamReadError:
            continue
        _collect(doc, where, facts)


def read_case(case: Path) -> CaseFacts:
    """Everything `catalogue` needs about one case folder (one holding `system/controlDict`)."""
    facts = CaseFacts()
    facts.has_block_mesh_dict = (case / "system" / "blockMeshDict").is_file()
    facts.has_mesh = (case / "constant" / "polyMesh" / "faces").exists() or (
        case / "constant" / "polyMesh" / "faces.gz"
    ).exists()
    control = case / "system" / "controlDict"
    data = None if _is_link(control.parent) else _read_bounded(control, MAX_SCAN_BYTES)
    if data is None:
        facts.readable, facts.problem = False, "system/controlDict could not be read"
    else:
        try:
            _control(parse(data), facts)
        except FoamReadError as exc:
            facts.readable, facts.problem = False, f"system/controlDict: {exc.message}"
    files = _dictionary_files(case)
    scan_dictionaries(((path.relative_to(case).as_posix(), path) for path in files), facts)
    # `#includeFunc name` finds a file the case ships in a `system/` folder before the install's
    # etc one, so only the names the case does not ship need the install.
    facts.etc_functions -= {path.name for path in files if path.parent.name == "system"}
    allrun = case / "Allrun"
    if allrun.is_file() and not _is_link(allrun):
        facts.allrun = read_allrun(allrun)
    return facts
