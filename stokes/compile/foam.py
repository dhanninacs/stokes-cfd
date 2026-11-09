"""The OpenFOAM dictionary syntax tree and its canonical printer (ADR-0008, T-142).

## Why a tree and a printer, not templates

Every dictionary Stokes writes — ``blockMeshDict`` now, ``snappyHexMeshDict`` later, the solver's
dictionaries in M3 — comes from **one** printer over a small syntax tree. ``docs/05 §2`` and
ADR-0008
say so, and the reason is that OpenFOAM's dictionary language is not text: it has nests, lists of
dicts, dimensioned values and includes, and a template that gets one of them wrong produces a
file the
solver either rejects or, worse, silently reads as something else.

**No parser here.** Reading dictionaries is M3 work with ``lark``. This module only writes.

## What it knows and does not know

Nothing about meshes: there is no ``blockMesh`` vocabulary in this file. The blockMesh writer is
T-145a, and it builds trees out of these types. Anything that needs to know what a ``hex`` is
belongs
there.

## The two OF14 corrections the brief did not have

Both measured from the install's own tutorial files (`tests/fixtures/of14/`):

1. **`FoamFile` carries no `version`.** No dictionary under ``$FOAM_TUTORIALS/incompressibleFluid``
   has one. The brief asked whether to emit ``version 2.0;`` — the answer is no, and emitting it
   would
   make our output differ from every file the install ships.
2. **The scale keyword is `scale`, not `convertToMeters`.** The cavity's dict says ``scale
0.1;``. The brief's golden test described the value as ``convertToMeters 0.1``; the value is
right and the key is not. A writer emitting ``convertToMeters`` produces a dict OF14 ignores,
and the mesh comes out
   unscaled.

A third, found by running the output: **a list value ends with a semicolon and a dict value does
not** — ``vertices ( … );`` but ``FoamFile { … }``.

## The canonical form

Pinned by tests and by a golden file, because "canonical" has to mean one thing: no banner, ``\n``
only, four spaces per level, and exactly one trailing newline. A key shorter than 16 columns is
padded to 16; a longer one is followed by a single space. Floats print with ``repr``, the
shortest text that
round-trips.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

# A bare OpenFOAM word: what may appear unquoted as a key or a value.
WORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:<>,\-]*$")

# **A KEY is a wider thing than a word.** OpenFOAM's own dictionaries key entries by expression:
# `div(phi,U)`, `div((nuEff*dev2(T(grad(U)))))`, `"(U|k|epsilon|f|v2)"`, `".*"`. Measured from the
# steady tutorial's `fvSchemes` and `fvSolution`. The value regex stays narrow because a VALUE that
# looks like an expression is the thing the printer must refuse; a KEY that looks like one is the
# normal case. **`|` is allowed inside an unquoted key's parentheses** because the `fluid` solver's
# internal-energy equation names its pressure-work scheme `div(phi,(p|rho))` (OpenFOAM 14
# `tutorials/fluid/angledDuct`, ADR-0027 step 3); it still cannot end an entry or open a block.
KEY_RE = re.compile(
    '^(?:"[A-Za-z0-9_.*|()\\-]+"|[A-Za-z_][A-Za-z0-9_.:<>,\\-]*(\\([A-Za-z0-9_.:<>,|\\-()*]*\\))?)$'
)

# An `#include` path. Deliberately narrow: letters, digits, `_ . / $ -`. No quote, no semicolon,
# nothing
# that could end the directive or start a new one.
INCLUDE_RE = re.compile(r"^[A-Za-z0-9_./$-]+$")

# An `#includeFunc` call: a function name, then one parenthesised argument list. **One nested pair
# of parentheses is allowed**, because a patch average writes `fields=(p)`; anything deeper is not a
# shape OpenFOAM's case dicts use, and every extra nesting is another way to smuggle a character
# past a scan.
FUNC_CALL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\((?:[A-Za-z0-9_=,. ]|\([A-Za-z0-9_, ]*\))*\)$")

# The column a scalar key is padded to. OpenFOAM's own files use 16, and matching them is what makes
# our
# output diffable against theirs.
KEY_COLUMN = 16

# A raw value is one line of the user's own text; these would break the format it is pasted into.
_FORBIDDEN_RAW = (";", "{", "}", "\n", "//", "/*")


class FoamError(ValueError):
    """A value or key cannot be written as an OpenFOAM dictionary.
    Always a programming error here."""


@dataclass(frozen=True)
class Word:
    """A bare word: ``ascii``, ``wall``, ``Gauss``. Validated by `WORD_RE`."""

    text: str


@dataclass(frozen=True)
class Quoted:
    """Text printed in double quotes: ``"system"``. For a value a bare word cannot carry."""

    text: str


@dataclass(frozen=True)
class Raw:
    """Text printed verbatim: ``hex (0 1 2 3 4 5 6 7) (20 20 1) simpleGrading (1 1 1)``.

    This is the escape hatch for OpenFOAM's own composite values, whose grammar is not worth
    reimplementing for a printer. One line, and it may not contain ``;`` or braces — a raw value
    that
    spills into the surrounding structure would produce a file that means something else.
    """

    text: str


# `Tuple_` and `List_` carry a trailing underscore to avoid shadowing the builtins: `Tuple` and
# `List`
# are what a reader expects from `typing`, and these are neither. The names are the brief's decided
# design (T-142), so the lint is answered here rather than by renaming them.
@dataclass(frozen=True)
class Tuple_:  # noqa: N801
    """Items printed inline in parentheses: ``(1 1 1)``."""

    items: tuple[Value, ...]


@dataclass(frozen=True)
class List_:  # noqa: N801
    """Items printed as a block, one per line: ``(\n    ...\n)``.

    A `List_` of `Entry` items whose values are `Dict` is how a ``boundary`` block is written:
    each entry
    prints as ``name\\n{ ... }``.
    """

    items: tuple[Value | Entry, ...]


@dataclass(frozen=True)
class Dimensioned:
    """``name [0 1 -1 0 0 0 0] value`` — OpenFOAM's seven-exponent dimension set."""

    name: str
    dims: tuple[int, int, int, int, int, int, int]
    value: Value


@dataclass(frozen=True)
class Include:
    """``#include "path"``."""

    path: str


@dataclass(frozen=True)
class FuncInclude:
    """``#includeFunc name(args)`` on one line, with an optional trailing comment.

    **Not `Include`.** `#include "path"` quotes a path; `#includeFunc` calls a function object that
    OpenFOAM resolves against ``$FOAM_ETC/caseDicts/functions/``. They are different directives with
    different argument shapes, and printing one as the other produces a file OpenFOAM refuses.

    ``call`` is validated by `FUNC_CALL_RE`, which allows **one** nested pair of parentheses
    because a
    patch average's field list is written ``fields=(p)``.
    """

    call: str
    comment: str = ""


@dataclass(frozen=True)
class Comment:
    """``// text``, on one line."""

    text: str


@dataclass(frozen=True)
class Entry:
    """``key value;``, or ``key { ... }`` when the value is a `Dict`."""

    key: str
    value: Value


@dataclass(frozen=True)
class Dict:
    """An ordered set of entries. **Order is the caller's**: nothing here sorts."""

    items: tuple[Entry | Include | FuncInclude | Comment, ...]


type Scalar = int | float | bool | str
type Value = Scalar | Word | Quoted | Raw | Tuple_ | List_ | Dimensioned | Dict

# Relative POSIX path -> file text. What a writer hands the case compiler.
type CaseTree = dict[str, str]


def _word(text: str, what: str) -> str:
    """Validate a bare word, naming what it was so a failure says which value is wrong."""
    if not WORD_RE.match(text):
        raise FoamError(f"{what} {text!r} is not a bare OpenFOAM word")
    return text


def _scalar(value: Scalar) -> str:
    """A scalar as OpenFOAM writes it.

    **`repr` for floats**, because it is the shortest text that reads back as the same number:
    ``0.002`` and ``1e-05`` round-trip, where ``%f`` would print ``0.000010`` and lose the
    exponent OpenFOAM's own files use. `nan` and `inf` are refused: OpenFOAM has no literal for
    them, so a file containing one is
    read as a word and fails far from here.
    """
    if isinstance(value, bool):
        # Before `int`, because `bool` is a subclass of it.
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FoamError(f"{value!r} cannot be written: OpenFOAM has no NaN or infinity literal")
        return repr(value)
    return _word(value, "the string scalar")


def _check_key(key: str) -> str:
    """The key validated by `KEY_RE` — **with `\\Z`, not `$`** (the M-504 security review's
    trailing-newline note: `re.match` with `$` accepts a trailing newline, and a key of
    `"wall\n"` would render a blank line into the dictionary). No padding: the caller decides
    the layout (the scalar branch pads, the Dict/List branches keep the bare key).
    """
    if not KEY_RE.fullmatch(key):
        raise FoamError(f"the key {key!r} is not an OpenFOAM dictionary key")
    return key


def _entry_key(key: str) -> str:
    """A key, padded to `KEY_COLUMN` or followed by one space when it is longer.

    **Validated by `KEY_RE`, not `WORD_RE`.** An OpenFOAM key is often an expression — `div(phi,U)`,
    `"(U|k|epsilon|f|v2)"` — and a value is not, which is why the two have separate
    patterns rather than
    one widened one. Measured: the steady tutorial's `fvSchemes` keys are all expressions.
    """
    return _check_key(key).ljust(KEY_COLUMN) if len(key) < KEY_COLUMN else _check_key(key) + " "


def _render_value(value: Value, indent: int) -> str:  # noqa: PLR0911 - one branch per value type
    """One value, with no trailing newline. `Dict` and `List_` are the two that span lines.

    A dispatcher over the value union: one `return` per type is what makes "what can a value be"
    readable
    in one place, and folding them into a table would move that knowledge away from the printer.
    """
    if isinstance(value, Dict):
        return _render_dict(value, indent)
    if isinstance(value, Entry):
        # Only reachable from a `List_`, which handles it; kept so the type checker sees the case.
        raise FoamError("an Entry cannot be a value outside a list")
    if isinstance(value, Word):
        return _word(value.text, "the word")
    if isinstance(value, Quoted):
        if '"' in value.text or "\n" in value.text:
            raise FoamError(f"a quoted value may not contain a quote or a newline: {value.text!r}")
        return f'"{value.text}"'
    if isinstance(value, Raw):
        for bad in _FORBIDDEN_RAW:
            if bad in value.text:
                raise FoamError(f"a raw value may not contain {bad!r}: {value.text!r}")
        return value.text
    if isinstance(value, Tuple_):
        inner = " ".join(_render_value(item, indent) for item in value.items)
        return f"({inner})"
    if isinstance(value, List_):
        return _render_list(value, indent)
    if isinstance(value, Dimensioned):
        dims = " ".join(str(d) for d in value.dims)
        return f"{value.name} [{dims}] {_render_value(value.value, indent)}"
    return _scalar(value)


def _render_list(value: List_, indent: int) -> str:
    """A list as a block: ``(`` on the key's own column, one item per line four deeper,
    ``)`` closing.

    An `Entry` item prints as a named sub-dictionary — ``name`` on its own line, then its braces
    — which
    is how a ``boundary`` block is written.
    """
    pad = " " * (indent + 4)
    # **The opening parenthesis is indented too.** It was written as a bare `"("`, so a list nested
    # in a
    # dict put its `(` at column 0 while its items and its `)` sat at the right depth — the file
    # still
    # *parsed*, which is why this is the kind of bug a golden test exists to catch.
    close = " " * indent
    lines: list[str] = [f"{close}("]
    for item in value.items:
        if isinstance(item, Entry):
            # **Rendered at the list's item depth and used verbatim.** `_render_entry` indents the
            # key
            # and passes the same depth to a nested block, so the block's braces and everything
            # inside it
            # land at the right columns without a second shift. Shifting a body rendered at depth 0
            # was
            # the bug: the inner block's own nested list had already been placed at column 0, and
            # adding
            # `pad` to every line moved the braces but left that list one level too shallow.
            lines.extend(_render_entry(item, indent + 4).splitlines())
        elif isinstance(item, (Dict, List_)):
            # **A multi-line value indents itself.** `_render_dict` puts its `{`, its body and its
            # `}` relative to the indent it is given, so it is rendered at the item column and used
            # verbatim - prefixing it with `pad` as well pushed the opener one level right while its
            # body stayed put, which is the misalignment a bare `Dict` in a list used to show
            # (`features ( { file ...; level ...; } )`).
            lines.append(_render_value(item, indent + 4))
        else:
            # A single-line value has no indent of its own, so the list supplies one.
            lines.append(f"{pad}{_render_value(item, indent + 4)}")
    lines.append(f"{close})")
    return "\n".join(lines)


def _render_entry(entry: Entry, indent: int) -> str:
    """One entry, indented by ``indent``.

    **A `Dict` or a `List_` value puts the key on its own line**; everything else is ``key
    value;``. That is OpenFOAM's own layout for ``vertices``, ``blocks`` and ``boundary``, and
    it is why the golden file
    can be diffed against a tutorial.
    """
    pad = " " * indent
    # **The key is checked on EVERY branch** (the M-504 security review's F1): the first draft
    # validated only the scalar branch, so a Dict- or List-valued entry's key — a tag name in the
    # layers block — reached the dictionary unchecked. A key from a loaded `project.json` carries
    # no validation of its own (the load checks no names), and the review measured `$FOAM_CASE`,
    # `"(.*)"` and `a;b` passing the lint as keys.
    # **Checked, not padded**: the Dict/List branches keep the bare key (their body starts on
    # the next line, so padding is meaningless there and would move every golden), and the
    # scalar branch keeps `_entry_key`'s padding.
    _check_key(entry.key)
    if isinstance(entry.value, List_):
        # **A LIST value ends with a semicolon; a DICT value does not.** Measured against the
        # install:
        # the cavity's `vertices`, `blocks` and `boundary` all end `);`, while `FoamFile { … }` has
        # no
        # semicolon after its brace. OpenFOAM is strict about this, and omitting it produced a file
        # it
        # rejects — the golden test caught it before the real utility had to.
        body = _render_value(entry.value, indent)
        return f"{pad}{entry.key}\n{body};"
    if isinstance(entry.value, Dict):
        body = _render_value(entry.value, indent)
        return f"{pad}{entry.key}\n{body}"
    return f"{pad}{_entry_key(entry.key)}{_render_value(entry.value, indent)};"


def _render_dict(node: Dict, indent: int) -> str:
    """A braced block. **The caller's order is kept**: two equal trees render identically,
    and a tree
    with its entries in another order renders in that order."""
    pad = " " * indent
    lines = [f"{pad}{{"]
    for item in node.items:
        if isinstance(item, Comment):
            if "\n" in item.text or "*/" in item.text:
                raise FoamError(f"a comment must be one line without '*/': {item.text!r}")
            lines.append(f"{pad}    // {item.text}")
        elif isinstance(item, Include):
            if not INCLUDE_RE.match(item.path):
                raise FoamError(f"the include path {item.path!r} is not a plain relative path")
            lines.append(f'{pad}    #include "{item.path}"')
        elif isinstance(item, FuncInclude):
            lines.append(render_func_include(item, len(pad) + 4))
        else:
            lines.extend(_render_entry(item, indent + 4).splitlines())
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def render_func_include(node: FuncInclude, indent: int = 0) -> str:
    """``#includeFunc call`` on one line, with the comment after it when there is one.

    The call is validated here rather than at construction because the node is a plain record: a
    caller
    builds a tree and the printer decides whether it can be written, which is the same rule
    every other
    value follows.
    """
    if not FUNC_CALL_RE.match(node.call):
        raise FoamError(f"{node.call!r} is not an #includeFunc call")
    if node.comment and ("\n" in node.comment or "*/" in node.comment):
        raise FoamError(f"a comment must be one line without '*/': {node.comment!r}")
    pad = " " * indent
    tail = f"  // {node.comment}" if node.comment else ""
    return f"{pad}#includeFunc {node.call}{tail}"


def header(cls: str, obj: str, location: str | None = None) -> Entry:
    """The ``FoamFile`` entry: ``format``, ``class``, an optional ``location``, and ``object``.

    **No `version`.** Measured against the install: no dictionary under
    ``$FOAM_TUTORIALS/incompressibleFluid`` carries one, so emitting it would make our files
    differ from
    every file OpenFOAM ships. The brief asked the question; this is the answer with its evidence.
    """
    items: list[Entry] = [Entry("format", Word("ascii")), Entry("class", Word(cls))]
    if location is not None:
        items.append(Entry("location", Quoted(location)))
    items.append(Entry("object", Word(obj)))
    return Entry("FoamFile", Dict(tuple(items)))


def render(node: Dict) -> str:
    """The canonical text of a dictionary: no banner, ``\\n`` only, one trailing newline."""
    return _render_dict(node, 0) + "\n"


def render_file(
    obj: str,
    body: Dict,
    cls: str = "dictionary",
    location: str | None = None,
) -> str:
    """A whole dictionary file: the `header` entry, then the body, with blank lines between
    the entries
    that follow it.

    The blank line is what OpenFOAM's own files do between a header and the body and between
    top-level blocks, and it is the difference between a file that diffs cleanly against theirs
    and one that does
    not.
    """
    first = _render_entry(header(cls, obj, location), 0)
    rest = [_render_item(item, 0) for item in body.items]
    return "\n\n".join([first, *rest]) + "\n"


def _render_item(item: Entry | Include | FuncInclude | Comment, indent: int) -> str:
    """A top-level include or comment, indented like an entry."""
    pad = " " * indent
    if isinstance(item, Comment):
        if "\n" in item.text or "*/" in item.text:
            raise FoamError(f"a comment must be one line without '*/': {item.text!r}")
        return f"{pad}// {item.text}"
    if isinstance(item, Include):
        if not INCLUDE_RE.match(item.path):
            raise FoamError(f"the include path {item.path!r} is not a plain relative path")
        return f'{pad}#include "{item.path}"'
    if isinstance(item, FuncInclude):
        return render_func_include(item, indent)
    return _render_entry(item, indent)


def as_tree(files: dict[str, str]) -> CaseTree:
    """A `CaseTree` from relative paths to text, refusing a path that would escape its root.

    A writer hands back paths like ``system/blockMeshDict``; this is where a mistake in one of them
    becomes an error rather than a file written outside the case directory.
    """
    out: CaseTree = {}
    for path, text in files.items():
        if path.startswith("/") or ".." in path.split("/") or not path:
            raise FoamError(f"a case path must be relative and must not climb: {path!r}")
        out[path] = text
    return out


def explain(value: Any) -> str:
    """A short description of a value, for an error message that says what was given."""
    return type(value).__name__
