"""The `external` lint profile: a case the user brought, checked against what the install has.

ADR-0029 decision 5 and its amendment to ADR-0024 §1.8. The current profile (`lint.lint_text`) is
for cases Stokes wrote, so it allows only what Stokes' own writers emit. An imported tutorial or a
user's own case uses more of OpenFOAM: `#includeEtc "caseDicts/…"`, any `#includeFunc` the install
ships, `libs` naming the install's own libraries, relative includes that climb out of `system/` but
stay in the case. This profile admits exactly those, **each checked against the probed install**,
and nothing that runs code.

## What does not change

Everything that refuses code execution is shared with the current profile and is not a parameter
(`lint.LintRules`): the comment stripper, the directive allow-list (`#codeStream`, `#calc`, `#eval`,
`#codeBlock`, `#sinclude`, `#includeIfPresent` stay refused), `coded*` values, `systemCall`, the `$`
macro allow-list, the quoted pattern-key rule, path-writing keys and the size and line limits.

## What it admits

* `#includeEtc "caseDicts/…"` when that exact relative path is in the probed `$FOAM_ETC` file list.
* `#includeFunc <name>` when ``name`` is a function the probe found under `$FOAM_ETC`; its arguments
  still may not name a path.
* `#include` of a relative path, or one rooted at `$FOAM_CASE`, that **resolves inside the job**;
  `..` is allowed while the resolved path stays in the job. The staged-surface and polyMesh
  reservations of the current profile still apply.
* `libs` / `functionObjectLibs` when every name is a plain `lib<name>[.so|.dll]` the probe listed.
* `#remove`, which deletes entries and reads nothing.

## Binary data

A file is passed unread only when **nothing can parse it as a dictionary**: it lives under
`constant/geometry/`, its name carries a surface extension (`.stl`, `.obj`, `.vtk`, …, optionally
`.gz`), and its first bytes match that format. Such a name has a `.` in it, which the `$` macro
grammar cannot spell, and `geometry` is a reserved include component, so no dictionary can reach
it. A `.gz` surface is still decompressed to the end, and refused past `MAX_SCAN_BYTES`, so a
bomb fails at lint time. Any other `.gz` is decompressed (bounded) and linted as the text OpenFOAM
would read, because OpenFOAM reads `name.gz` wherever it would read `name`. Every other binary
file is refused.

**The probed lists are parameters** (`ProbedInstall`), not imports from the probe, so the agent's
probe (T-304) supplies them and tests build them by hand.
"""

from __future__ import annotations

import gzip
import posixpath
import re
import zlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from stokes.errors import StokesError

from .lint import (
    ALLOWED_DIRECTIVES,
    MAX_SCAN_BYTES,
    MAX_STL_BYTES,
    POLYMESH_PREFIX,
    REFUSED_WORDS,
    Finding,
    LintRules,
    _is_trusted_polymesh,
    _job_files,
    _reserved_include,
    is_binary,
    lint_with_rules,
)

#: A name the probe may report and a dictionary may use: a module, a utility, an etc function.
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
#: A library as a dictionary names it: `libfoo`, `libfoo.so` or `libfoo.dll`, never a path.
_LIBRARY_RE = re.compile(r"^lib(?P<stem>[A-Za-z0-9_]+)(?:\.so|\.dll)?$")
#: One component of a relative path: a plain name, or `..` (judged after resolution).
_COMPONENT_RE = re.compile(r"^(?:\.\.|[A-Za-z0-9_][A-Za-z0-9_.\-]*)$")
#: An etc path as `#includeEtc` names it: plain components under `caseDicts/`, no `..`.
_ETC_RE = re.compile(r"^caseDicts(?:/[A-Za-z0-9_][A-Za-z0-9_.\-]*)+$")
#: `$FOAM_CASE/…` and `${FOAM_CASE}/…`: the job root, which is the case.
_FOAM_CASE_RE = re.compile(r"^\$(?:FOAM_CASE|\{FOAM_CASE\})/")

#: Words refused anywhere: the current list without the library keywords, which this profile
#: checks by value instead (`_libs_findings`).
LIBRARY_KEYWORDS = ("libs", "functionobjectlibs")
EXTERNAL_WORDS: dict[str, str] = {
    word: why for word, why in REFUSED_WORDS.items() if word not in LIBRARY_KEYWORDS
}
#: `#remove` deletes entries and reads nothing; `#includeConstraint` names an fvConstraint the
#: install's `caseDicts` provides, checked like `#includeModel` (`lint._include_model_finding`).
EXTERNAL_DIRECTIVES: frozenset[str] = ALLOWED_DIRECTIVES | {"remove", "includeconstraint"}

GEOMETRY_ROOT = "constant/geometry/"
GZIP_MAGIC = b"\x1f\x8b"


def _ascii_stl(head: bytes) -> bool:
    return head.lstrip().lower().startswith(b"solid")


def _stlb(head: bytes) -> bool:
    return len(head) >= 84


def _text_head(head: bytes) -> bool:
    return b"\0" not in head


def _legacy_vtk(head: bytes) -> bool:
    return head.startswith(b"# vtk DataFile")


def _xml_vtk(head: bytes) -> bool:
    return head.lstrip().startswith((b"<?xml", b"<VTKFile"))


#: Surface formats passed unread under `constant/geometry/`, with the header each must have. A
#: binary STL's 80-byte header is free, so `.stl` also accepts the binary layout (`_binary_stl`).
DATA_HEADERS: dict[str, Callable[[bytes], bool]] = {
    ".stl": _ascii_stl,
    ".stlb": _stlb,
    ".obj": _text_head,
    ".vtk": _legacy_vtk,
    ".vtp": _xml_vtk,
}


@dataclass(frozen=True)
class ProbedInstall:
    """What the probed OpenFOAM install has, as the external profile needs it.

    ``etc_files`` are paths relative to `$FOAM_ETC` (`caseDicts/setConstraintTypes`);
    ``etc_functions`` are the function names under `caseDicts/functions` that `#includeFunc` and
    `-func` resolve; ``libraries`` are library stems (`fieldFunctionObjects` for
    `libfieldFunctionObjects.so`); ``solver_modules`` are names with a `lib<name>Solver` present.
    """

    solver_modules: frozenset[str] = frozenset()
    utilities: frozenset[str] = frozenset()
    libraries: frozenset[str] = frozenset()
    etc_functions: frozenset[str] = frozenset()
    etc_files: frozenset[str] = frozenset()

    @classmethod
    def from_lists(
        cls,
        *,
        solver_modules: Iterable[str] = (),
        utilities: Iterable[str] = (),
        libraries: Iterable[str] = (),
        etc_functions: Iterable[str] = (),
        etc_files: Iterable[str] = (),
    ) -> ProbedInstall:
        """Build from a probe record, **dropping anything that is not a plain name**.

        The record crosses the wire, so it is untrusted: a "module" called `../x` or a "library"
        called `/tmp/evil.so` must not become something the lint then allows.
        """
        stems = {_library_stem(name) for name in libraries if isinstance(name, str)}
        return cls(
            solver_modules=_plain(solver_modules),
            utilities=_plain(utilities),
            libraries=frozenset(stem for stem in stems if stem is not None),
            etc_functions=_plain(etc_functions),
            etc_files=frozenset(
                name for name in etc_files if isinstance(name, str) and _ETC_RE.match(name)
            ),
        )


def _plain(names: Iterable[str]) -> frozenset[str]:
    return frozenset(name for name in names if isinstance(name, str) and _NAME_RE.match(name))


def _library_stem(name: str) -> str | None:
    """`fieldFunctionObjects` for `libfieldFunctionObjects.so`, `.dll`, or bare; None otherwise.

    A probe may report a stem with no prefix; a dictionary must use the `lib` form.
    """
    match = _LIBRARY_RE.match(name)
    if match is not None:
        return match.group("stem")
    return name if _NAME_RE.match(name) else None


class ExternalUnsafeDict(StokesError):
    """A file of an external case is refused; details name the file, line and token."""

    code = "agent.unsafe_dict"


def refuse(findings: list[Finding]) -> None:
    """Raise `agent.unsafe_dict` for the first finding, in the shape `jobs.py` uses."""
    if not findings:
        return
    first = findings[0]
    where = f"{first.file}:{first.line}" if first.line else first.file
    raise ExternalUnsafeDict(
        f"{where}: {first.token} {first.why}",
        details={
            "code": "agent.unsafe_dict",
            "file": first.file,
            "line": first.line,
            "token": first.token,
            "findings": [item.to_json() for item in findings[:20]],
        },
    )


def unsafe_job_path(target: str, including: str = "") -> str | None:
    """Why ``target`` (relative to ``including``'s folder) may not be read, or None when it may.

    The external `#include` rule, and the rule for a `-dict` value. **Resolved before judged**:
    `..` is allowed while the result stays inside the job; an absolute path, a drive, `~`, a
    backslash and any `$` other than a leading `$FOAM_CASE/` are refused.
    """
    base = posixpath.dirname(including)
    rooted = _FOAM_CASE_RE.match(target)
    if rooted is not None:
        target = target[rooted.end() :]
        base = ""
    if "$" in target or "~" in target or "\\" in target:
        return "the path is expanded or escaped, so it is not a path inside the job"
    if target.startswith("/") or re.match(r"^[A-Za-z]:", target):
        return "an absolute path is outside the job"
    parts = target.split("/")
    if not target or not all(_COMPONENT_RE.match(part) for part in parts):
        return "the path is not a relative path of plain names"
    resolved = posixpath.normpath(posixpath.join(base, target))
    if resolved == ".." or resolved.startswith("../") or resolved == ".":
        return "the path leaves the job folder"
    if resolved.endswith((".part", ".tmp")):
        return "the file is a partial upload"
    return _reserved_include(resolved)


def _include_etc(install: ProbedInstall, target: str) -> str | None:
    if _ETC_RE.match(target) is None:
        return "only a plain path under caseDicts/ may be included from the OpenFOAM etc folder"
    if target not in install.etc_files:
        return "the file is not in this OpenFOAM install's etc folder"
    return None


def _include_func(install: ProbedInstall, name: str) -> str | None:
    if _NAME_RE.match(name) is None or name not in install.etc_functions:
        return "the function is not one this OpenFOAM install provides"
    return None


_LIBRARY_KEYWORD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(libs|functionObjectLibs)(?![A-Za-z0-9_])", re.I
)
_LIBRARY_VALUE_RE = re.compile(r"[ \t\r\n]*(?:\((?P<list>[^()]{0,4096})\)|(?P<one>[^\s;(){}]+))")
_STATEMENT_END_RE = re.compile(r"\s*(?:[;}]|\Z)")
_LIBRARY_ITEM_RE = re.compile(r'"(?P<quoted>[^"\\\n]*)"|(?P<bare>[^\s"]+)')


def _libs_findings(
    install: ProbedInstall, path: str, clean: str, out: list[tuple[int, Finding]]
) -> None:
    """`libs` and `functionObjectLibs`: every name a plain library the probe listed.

    **Every occurrence of the word is judged**, wherever it sits: a `libs` that is not followed by
    a value this check can read is refused, rather than assumed to be something else.
    """
    for match in _LIBRARY_KEYWORD_RE.finditer(clean):
        start = match.end()
        # **A quoted plain key**, `"libs" (…);`, is the same keyword to OpenFOAM: the value starts
        # after the closing quote. Only a quote pair hugging the word is skipped, so the names
        # themselves are judged exactly as for the bare key.
        if clean[match.start() - 1 : match.start()] == '"' and clean[start : start + 1] == '"':
            start += 1
        value = _LIBRARY_VALUE_RE.match(clean, start)
        word = match.group()
        # **The value must end the statement**: `libs (a) (b);` or `libs a b;` would leave a second
        # value this check never read, whatever OpenFOAM makes of it.
        if value is None or not _STATEMENT_END_RE.match(clean, value.end()):
            out.append((match.start(), Finding(path, 0, word, "the library list is unreadable")))
            continue
        text = value.group("list") if value.group("list") is not None else value.group("one")
        items = [m.group("quoted") or m.group("bare") for m in _LIBRARY_ITEM_RE.finditer(text)]
        if not items:
            out.append((match.start(), Finding(path, 0, word, "the library list is empty")))
        for item in items:
            stem_match = _LIBRARY_RE.match(item or "")
            if stem_match is None:
                why = "a library must be a plain lib<name>.so, not a path or an expansion"
            elif stem_match.group("stem") not in install.libraries:
                why = "the library is not one this OpenFOAM install provides"
            else:
                continue
            out.append((match.start(), Finding(path, 0, f"{word} {item}"[:120], why)))


def external_rules(install: ProbedInstall) -> LintRules:
    """The `external` profile's rules, bound to one probed install."""

    def include_etc(target: str) -> str | None:
        return _include_etc(install, target)

    def include_func(name: str) -> str | None:
        return _include_func(install, name)

    def libraries(path: str, clean: str, out: list[tuple[int, Finding]]) -> None:
        _libs_findings(install, path, clean, out)

    return LintRules(
        directives=EXTERNAL_DIRECTIVES,
        include_etc=include_etc,
        include_func=include_func,
        include=unsafe_job_path,
        words=EXTERNAL_WORDS,
        further=libraries,
    )


def lint_text_external(
    path: str, text: str, install: ProbedInstall, *, polymesh: bool = False
) -> list[Finding]:
    """Every reason ``text`` may not be staged as ``path`` of an external case."""
    return lint_with_rules(path, text, polymesh=polymesh, rules=external_rules(install))


def data_kind(rel: str) -> str | None:
    """The surface extension that lets ``rel`` pass unread, or None.

    Only under `constant/geometry/`, only a listed extension (optionally `.gz`), and only with a
    stem before it, so the name always has a `.` the `$` macro grammar cannot spell.
    """
    if not rel.startswith(GEOMETRY_ROOT):
        return None
    name = rel.rsplit("/", 1)[-1].lower()
    inner = name[:-3] if name.endswith(".gz") else name
    for ext in DATA_HEADERS:
        if inner.endswith(ext) and len(inner) > len(ext):
            return ext
    return None


def _binary_stl(head: bytes, size: int) -> bool:
    if len(head) < 84:
        return False
    count = int.from_bytes(head[80:84], "little")
    return size == 84 + 50 * count


def _gunzip_bounded(path: Path, cap: int) -> tuple[bytes, int]:
    """The first 4 KiB of ``path`` decompressed, and its decompressed size counted to ``cap + 1``.

    Stops reading once the count passes ``cap``, so a bomb costs at most ``cap`` bytes of work.
    """
    head = b""
    total = 0
    with gzip.open(path, "rb") as stream:
        while total <= cap:
            chunk = stream.read(min(1 << 20, cap + 1 - total))
            if not chunk:
                break
            if len(head) < 4096:
                head += chunk[: 4096 - len(head)]
            total += len(chunk)
    return head, total


def _data_finding(rel: str, path: Path, ext: str) -> Finding | None:
    """Why a data file is refused (size, or a header that does not match its name), or None."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError as exc:
        return Finding(rel, 0, "", f"the file could not be read: {exc.strerror or exc}")
    if size > MAX_STL_BYTES:
        why: str | None = f"the file is {size} bytes, over the {MAX_STL_BYTES} limit"
    elif rel.lower().endswith(".gz"):
        why = _gzip_data_why(path, head, ext)
    elif not (DATA_HEADERS[ext](head) or (ext == ".stl" and _binary_stl(head, size))):
        why = _HEADER_WHY.format(ext=ext)
    else:
        why = None
    return None if why is None else Finding(rel, 0, "", why)


_HEADER_WHY = "the file is named {ext} but its header is not that format"


def _gzip_data_why(path: Path, head: bytes, ext: str) -> str | None:
    """Why a gzipped surface is refused, or None.

    **Decompressed to the end, under the cap** that bounds decompressed text: reading only the head
    let a small gzip that expands without limit (a bomb) pass the lint and reach snappy, which
    decompresses all of it.
    """
    if not head.startswith(GZIP_MAGIC):
        return "named .gz but not gzip data"
    try:
        inner, expanded = _gunzip_bounded(path, MAX_SCAN_BYTES)
    except (OSError, EOFError, zlib.error):
        return "the gzip data could not be read"
    if expanded > MAX_SCAN_BYTES:
        return f"the gzip data expands past the {MAX_SCAN_BYTES}-byte limit"
    if DATA_HEADERS[ext](inner) or (ext == ".stl" and _binary_stl(inner, expanded)):
        return None
    return _HEADER_WHY.format(ext=ext)


def _read_text(rel: str, path: Path) -> tuple[str | None, Finding | None]:
    """The text OpenFOAM would parse from ``path`` (gunzipped), or the reason there is none."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SCAN_BYTES + 1)
        if raw.startswith(GZIP_MAGIC) and rel.lower().endswith(".gz"):
            with gzip.open(path, "rb") as stream:
                raw = stream.read(MAX_SCAN_BYTES + 1)
    except (OSError, EOFError, zlib.error) as exc:
        return None, Finding(rel, 0, "", f"the file could not be read: {exc}")
    if len(raw) > MAX_SCAN_BYTES:
        return None, Finding(rel, 0, "", f"the file is over the {MAX_SCAN_BYTES}-byte scan limit")
    if is_binary(raw):
        head = raw[:4096]
        why = (
            "a binary-format OpenFOAM file; the external profile reads ASCII dictionaries only"
            if b"FoamFile" in head and re.search(rb"format\s+binary", head)
            else "a binary file that is not a known surface format under constant/geometry/"
        )
        return None, Finding(rel, 0, "", why)
    return raw.decode("utf-8"), None


def case_functions(case_root: Path) -> frozenset[str]:
    """The function files a case ships in its own `system/` folder (`system/wallsSurface`).

    `#includeFunc name` reads the case's `system/<name>` before the install's, so a tutorial that
    brings its own function file is not asking for anything the install lacks. Each such file is
    a case file and is linted like every other one; only its NAME is accepted here. Plain names
    only, no links, files directly in `system/`.
    """
    folder = case_root / "system"
    try:
        entries = list(folder.iterdir())
    except OSError:
        return frozenset()
    return frozenset(
        item.name
        for item in entries
        if _NAME_RE.match(item.name) is not None and item.is_file() and not item.is_symlink()
    )


def with_case_functions(install: ProbedInstall, names: frozenset[str]) -> ProbedInstall:
    """``install`` with a case's own function files accepted by `#includeFunc`."""
    if not names:
        return install
    from dataclasses import replace

    return replace(install, etc_functions=install.etc_functions | names)


def _case_root(rel: str, path: Path) -> Path | None:
    """The folder ``rel`` is relative to, when ``path`` really ends with it."""
    parts = PurePosixPath(rel).parts
    if not parts or tuple(path.parts[-len(parts) :]) != parts:
        return None
    return path.parents[len(parts) - 1]


def lint_file_external(rel: str, path: Path, install: ProbedInstall) -> list[Finding]:
    """Lint one file of an external case: data passes by name and header, text is linted.

    The case's own `system/<name>` function files count as functions it may `#includeFunc`.
    """
    if path.is_symlink():
        return [Finding(rel, 0, "", "a symbolic link in a job folder")]
    ext = data_kind(rel)
    if ext is not None:
        found = _data_finding(rel, path, ext)
        return [] if found is None else [found]
    text, why = _read_text(rel, path)
    if text is None:
        return [why] if why is not None else []
    root = _case_root(rel, path)
    if root is not None:
        install = with_case_functions(install, case_functions(root))
    return lint_text_external(rel, text, install, polymesh=rel.startswith(POLYMESH_PREFIX))


def lint_job_external(
    job_dir: Path,
    install: ProbedInstall,
    rel_paths: list[str] | None = None,
    trusted_polymesh: Mapping[str, int] | None = None,
) -> list[Finding]:
    """`lint.lint_job` under the external profile: every file, links refused, own files skipped."""
    found: list[Finding] = []
    trusted = trusted_polymesh or {}
    for rel, path in _job_files(job_dir, rel_paths, found):
        if _is_trusted_polymesh(rel, path, trusted):
            continue
        found.extend(lint_file_external(rel, path, install))
    return found
