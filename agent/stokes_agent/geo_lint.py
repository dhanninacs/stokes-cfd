"""The closed grammar a staged Gmsh script must fit, line by line (ADR-0043).

## Why a grammar and not a word list

A `.geo` file is a program. Gmsh's language can run a shell command (`SystemCall`, `Shell`),
start other programs (`OnelabRun`, the `Solver.*` options), read any file (`Include`, `Merge`) and
write one (`Save`, `Print`, `Printf(...) > "file"`, `CreateDir`). A list of refused words would be a
list of the ones somebody thought of. So, like `flags.py` for argv, the list here is **closed**:
every line must be one of the statement shapes the Gmsh driver (`stokes.mesh.drivers.gmsh`) writes,
and a line that is not is refused with its number.

## The rules that make the shapes safe

* **Files are pinned.** `Merge` names `../constant/geometry/<word>.stl` and `Save` names
  `../constant/domain.msh` - nothing else, relative to the script in `system/`, so inside the job.
* **A capitalised word is a command**, in Gmsh's language: every built-in (`SystemCall`, `Exp`,
  `GetEnv`, `StrCat`) starts with a capital. Inside an expression the only capitalised words
  allowed are `Pi`, `Abs`, `Surface` and `Curve`; a variable starts with a small letter.
* **A string appears only where the driver writes one**: the two file names, a physical group's
  name (a patch word), and the fixed text of `Printf`/`Error`, which may not contain a quote, so a
  `> "file"` redirect cannot follow it.
* **`#` is only the list-size operator** (`#name()`), so no OpenFOAM directive can hide in a
  script that some dictionary might include.
* No control characters, no `/*` block comments, no `;` or `[` outside the shapes that need them.

Standard library only; returns `(line, token, why)` triples that `lint.py` turns into findings.
"""

from __future__ import annotations

import re

#: The most lines a script may have. The driver writes about 60 per tag; 20,000 is far above a
#: real part and far below a size that would make the check itself slow.
MAX_LINES = 20_000

#: The options a script may set, by category, each to a number.
OPTIONS: frozenset[str] = frozenset(
    {
        "Mesh.MshFileVersion",
        "Mesh.Binary",
        "Mesh.Optimize",
        "Mesh.OptimizeNetgen",
        "Mesh.MeshSizeMax",
        "Mesh.MeshSizeMin",
        "Mesh.MeshSizeFromCurvature",
        "Mesh.MeshSizeFromPoints",
        "Mesh.MeshSizeExtendFromBoundary",
        "Geometry.ExtrudeReturnLateralEntities",
    }
)

#: The capitalised words an expression may contain.
EXPRESSION_WORDS: frozenset[str] = frozenset({"Pi", "Abs", "Surface", "Curve"})

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_IDENT = r"[a-z][A-Za-z0-9_]{0,63}"
#: An expression: identifiers, numbers, arithmetic, comparisons, list braces and ranges.
_EXPR = r"[A-Za-z0-9_ .+\-*/%#(){}:,<>=!&|]+"
_LHS = rf"{_IDENT}(?:\(\)|\({_EXPR}\))?"
_NAME = r"[A-Za-z][A-Za-z0-9_]{0,63}"
_TEXT = r"[A-Za-z0-9 _.,:%=+\-()']*"

#: Each statement shape, matched against the whole stripped line. The groups are the expressions
#: the word rules then check.
_SHAPES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        rf"(?P<option>(?:Mesh|Geometry)\.[A-Za-z0-9]+) = {_NUMBER};",
        rf'Merge "\.\./constant/geometry/{_NAME}\.stl";',
        r'Save "\.\./constant/domain\.msh";',
        r"Coherence Mesh;",
        r"CreateGeometry;",
        # Boundary curves between the merged tags, before classifying or in place of it.
        r"CreateTopology;",
        r"Delete Model;",
        r"Mesh 3;",
        r"EndFor",
        r"EndIf",
        rf"ClassifySurfaces\{{{_NUMBER}, [01], [01], (?:Pi|{_NUMBER})\}};",
        rf"For {_IDENT} In \{{(?P<e1>{_EXPR})\}}",
        rf"(?:If|ElseIf) \((?P<e1>{_EXPR})\)",
        rf'(?:Printf|Error)\("{_TEXT}"(?:, (?P<e1>{_EXPR}))?\);',
        rf"(?P<l1>{_LHS}) = Boundary\{{(?:Surface|Curve)\{{(?P<e1>{_EXPR})\}};\}};",
        rf"(?P<l1>{_LHS}) = Extrude\{{Surface\{{(?P<e1>{_EXPR})\}}; "
        rf"Layers\{{(?P<e2>{_EXPR})\}}; Recombine;\}};",
        rf"(?:Curve Loop|Plane Surface|Surface Loop|Volume)\((?P<e1>{_EXPR})\) = (?P<e2>{_EXPR});",
        rf'Physical (?:Surface|Volume)\("{_NAME}"\) = (?P<e1>{_EXPR});',
        r"Field\[\d{1,4}\] = (?:Distance|Threshold|Min);",
        r"Field\[\d{1,4}\]\.(?:SurfacesList|Sampling|InField|SizeMin|SizeMax|DistMin|DistMax"
        rf"|FieldsList) = (?P<e1>{_EXPR});",
        r"Background Field = \d{1,4};",
        rf"(?P<l1>{_LHS}) (?:=|\+=) (?P<e1>{_EXPR});",
    )
)

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_HASH = re.compile(r"#")
_LIST_SIZE = re.compile(rf"#{_IDENT}\(\)")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_OPENS = {"For": "EndFor", "If": "EndIf"}


def lint_geo(text: str) -> list[tuple[int, str, str]]:
    """Every reason ``text`` may not be run as a Gmsh script, as `(line, token, why)`."""
    found: list[tuple[int, str, str]] = []
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        return [(0, "", f"the script has more than {MAX_LINES} lines")]
    blocks: list[tuple[str, int]] = []
    for number, read in enumerate(lines, start=1):
        # A Windows line end is one; a carriage return anywhere else is a control character.
        raw = read.removesuffix("\r")
        line = raw.strip(" \t")
        if not line:
            continue
        if _CONTROL.search(raw):
            found.append((number, "", "a control character in a Gmsh script"))
            continue
        if line.startswith("//"):
            continue
        problem = _line_problem(line)
        if problem is not None:
            found.append((number, line[:80], problem))
            continue
        word = line.split(" ", 1)[0].split("(", 1)[0]
        if word in _OPENS:
            blocks.append((word, number))
        elif word in ("EndFor", "EndIf"):
            if not blocks or _OPENS[blocks[-1][0]] != word:
                found.append((number, word, f"{word} closes no open block"))
            else:
                blocks.pop()
        elif word == "ElseIf" and (not blocks or blocks[-1][0] != "If"):
            found.append((number, word, "ElseIf outside an If block"))
    for word, number in blocks:
        found.append((number, word, f"this {word} is never closed"))
    return found


def _line_problem(line: str) -> str | None:
    """Why one non-comment line is refused, or None when it is one of the shapes."""
    if "/*" in line or "*/" in line or "//" in line:
        return "a comment inside a statement; only whole-line // comments are allowed"
    for shape in _SHAPES:
        match = shape.fullmatch(line)
        if match is None:
            continue
        groups = match.groupdict()
        option = groups.get("option")
        if option is not None and option not in OPTIONS:
            return f"the option {option} is not one the Gmsh driver sets"
        for key in ("l1", "e1", "e2"):
            expression = groups.get(key)
            if expression is not None:
                problem = _expression_problem(expression)
                if problem is not None:
                    return problem
        return None
    return "not a statement the Gmsh driver writes"


def _expression_problem(expression: str) -> str | None:
    """Why an expression is refused: a command word, or a `#` that is not a list size."""
    for word in _WORD.findall(expression):
        if (word[0].isupper() or word[0] == "_") and word not in EXPRESSION_WORDS:
            return f"{word!r} is not allowed in an expression"
    if len(_HASH.findall(expression)) != len(_LIST_SIZE.findall(expression)):
        return "a '#' that is not a list size (#name())"
    return None
