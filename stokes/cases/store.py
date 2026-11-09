"""The read-only, content-addressed copy of an imported case (ADR-0029 decision 3, plan T-307).

## Layout

    <project>.stokes/cases/<node id>/
        manifest.json   {version, snapshot, files: [{path, area, size, sha256}]}
        source/         the case as it will be staged: every file passed the external lint
        scripts/        Allrun, Allclean, ... kept as text to show; never staged, never run

`snapshot` is the sha256 of the sorted `area/path\\0sha256\\n` lines of the manifest, so equal
content gives an equal snapshot wherever it was imported from. The copy's files are made
read-only once it is complete. The journal records the snapshot, so a replay finds the copy in
the project and does not need the source (`has_snapshot`).

## How a copy is made

`collect` walks the source **without writing to it**: hidden entries, `processor*`,
`postProcessing`, `dynamicCode` and `log.*` are left out; a symbolic link that resolves inside
the case is copied as what it points to, one that escapes is a refusal. `stage` copies into a
private folder beside the final one, hashing as it goes, runs the caller's lint on **the copied
bytes** (so what is linted is what is kept), and only then renames the folder into place. A
lint finding discards the copy: a refused case never stages. The whole copy is capped at
`MAX_CASE_BYTES` (1 GiB, the agent's `MAX_JOB_BYTES`) and `MAX_CASE_FILES` files.

Import rules: standard library, `stokes.errors`, `stokes.log`, `stokes.cases.model`. Headless;
the lint is a parameter because this package may not import `stokes_agent`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stokes.cases.model import is_snapshot
from stokes.errors import StokesError
from stokes.log import fields, get_logger
from stokes.model.ids import is_id

log = get_logger(__name__)

CASES_FOLDER = "cases"
MANIFEST = "manifest.json"
MANIFEST_VERSION = 1
SOURCE_AREA = "source"
SCRIPTS_AREA = "scripts"
#: Files outside the folders a solver reads (`validation/`, `README`, helper scripts, plot files):
#: kept so the case is whole, never linted as dictionaries and never staged.
EXTRAS_AREA = "extras"
MAX_CASE_BYTES = 1024 * 1024 * 1024
MAX_CASE_FILES = 20_000
MAX_DEPTH = 16
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_PARSE_BYTES = 2 * 1024 * 1024
_TIME_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?")
_SOLVER_TOPS = frozenset({"system", "constant", "0.orig"})
_CHUNK = 1024 * 1024
_SKIP_DIRS = frozenset({"postProcessing", "dynamicCode"})
_PROCESSOR_RE = re.compile(r"^processor[0-9]+$|^processors[0-9]*$")
_SCRIPT_RE = re.compile(r"All[A-Za-z]*(?:[.\-][A-Za-z0-9]+)*")

#: A lint: ``(relative path, file) -> findings`` as JSON objects `{file, line, token, why}`.
Lint = Callable[[str, Path], list[dict[str, Any]]]


class CaseStoreError(StokesError):
    """The copy of a case cannot be made or read."""

    code = "case.store"


@dataclass(frozen=True)
class Entry:
    """One file to copy: its path in the copy, its area and where it is read from."""

    rel: str
    area: str
    path: Path


@dataclass
class Staged:
    """The result of `stage`: the snapshot, or the refusals that stopped it."""

    snapshot: str = ""
    files: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON
    refusals: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON
    #: The plain-arithmetic `#calc` resolved in the copy (ADR-0033), as `"<file>: <expression>"`.
    calc: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals and bool(self.snapshot)


def case_dir(project: Path, node_id: str) -> Path:
    """`<project>/cases/<node id>`; the id must be a node id, so it cannot name another path."""
    if not is_id(node_id):
        raise CaseStoreError(f"{node_id!r} is not a node id", details={"node_id": node_id})
    return project / CASES_FOLDER / node_id


def is_script(name: str) -> bool:
    """Whether a file name is one of the case's shell scripts (`Allrun`, `Allclean.pre`, ...)."""
    return _SCRIPT_RE.fullmatch(name) is not None


def _skipped(name: str, is_dir: bool) -> bool:
    if name.startswith("."):
        return True
    if is_dir:
        return name in _SKIP_DIRS or _PROCESSOR_RE.match(name) is not None
    return name.startswith("log.")


def _refusal(rel: str, why: str) -> dict[str, Any]:  # Any: JSON
    return {"file": rel, "line": 0, "token": "", "why": why}


def area_of(rel: str, name: str) -> str:
    """Where a copied file goes: `scripts` (Allrun and friends), `source` (what a solver reads:
    `system/`, `constant/`, a time folder, `0.orig/`), or `extras` (everything else)."""
    if is_script(name):
        return SCRIPTS_AREA
    top = rel.split("/", 1)[0]
    if "/" in rel and (top in _SOLVER_TOPS or _TIME_RE.fullmatch(top)):
        return SOURCE_AREA
    return EXTRAS_AREA


def collect(case: Path) -> tuple[list[Entry], list[dict[str, Any]]]:  # Any: JSON
    """The files of ``case`` to copy, and refusals (an escaping link, too many files).

    ``case`` must already be resolved. Nothing is written.
    """
    entries: list[Entry] = []
    refusals: list[dict[str, Any]] = []
    seen: set[Path] = {case}
    stack: list[tuple[Path, str, int]] = [(case, "", 0)]
    while stack:
        folder, prefix, depth = stack.pop()
        try:
            children = sorted(os.scandir(folder), key=lambda item: item.name, reverse=True)
        except OSError as exc:
            refusals.append(_refusal(prefix or ".", f"the folder could not be read: {exc}"))
            continue
        for child in children:
            rel = f"{prefix}{child.name}"
            path = Path(child.path)
            # **A Windows junction is a link too**, and `is_symlink` does not see it: without
            # this check the walk would descend into whatever folder the junction names.
            if child.is_symlink() or child.is_junction():
                try:
                    path = path.resolve(strict=True)
                except (OSError, RuntimeError):
                    refusals.append(_refusal(rel, "a symbolic link that leads nowhere"))
                    continue
                if not path.is_relative_to(case):
                    refusals.append(_refusal(rel, "a symbolic link that leaves the case"))
                    continue
            is_dir = path.is_dir()
            if _skipped(child.name, is_dir):
                continue
            if is_dir:
                if path in seen or depth >= MAX_DEPTH:
                    continue
                seen.add(path)
                stack.append((path, rel + "/", depth + 1))
            elif path.is_file():
                area = area_of(rel, child.name)
                entries.append(Entry(rel, area, path))
                if len(entries) > MAX_CASE_FILES:
                    refusals.append(_refusal(".", f"more than {MAX_CASE_FILES} files"))
                    return entries, refusals
    entries.sort(key=lambda entry: entry.rel)
    return entries, refusals


def snapshot_of(files: Iterable[dict[str, Any]]) -> str:  # Any: JSON
    """The snapshot hash of manifest file rows."""
    digest = hashlib.sha256()
    for row in sorted(files, key=lambda item: (item["area"], item["path"])):
        digest.update(f"{row['area']}/{row['path']}\0{row['sha256']}\n".encode())
    return digest.hexdigest()


def _copy(source: Path, target: Path, budget: int) -> tuple[int, str]:
    """Copy ``source`` to a new file ``target``; `(size, sha256)`. Refuses past ``budget``."""
    digest = hashlib.sha256()
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(_CHUNK):
            size += len(chunk)
            if size > budget:
                raise CaseStoreError(
                    f"the case is larger than {MAX_CASE_BYTES} bytes", code="case.too_large"
                )
            digest.update(chunk)
            writer.write(chunk)
    return size, digest.hexdigest()


def _size_sha(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def _resolve_calc(target: Path) -> list[str]:
    """Rewrite ``target`` (a copied text file) with its plain-arithmetic `#calc` resolved.

    Returns the expressions that were replaced; an unreadable, binary or `#calc`-free file is left
    alone. Only the project's copy is written, never the source case.
    """
    from stokes.cases.calc import resolve_calc

    try:
        raw = target.read_bytes()
    except OSError:
        return []
    if b"#calc" not in raw:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []
    out = resolve_calc(text)
    if not out.resolved:
        return []
    target.write_bytes(out.text.encode("utf-8"))
    return out.resolved


def remove_tree(folder: Path) -> None:
    """Delete ``folder`` although its files are read-only. **Never follows a link out.**"""

    def writable(function: Callable[..., Any], path: str, _exc: BaseException) -> None:
        with contextlib.suppress(OSError):
            Path(path).chmod(stat.S_IWRITE | stat.S_IREAD)
            function(path)

    if folder.is_symlink():
        folder.unlink()
    elif folder.exists():
        shutil.rmtree(folder, onexc=writable)


def _read_only(folder: Path) -> None:
    for root, _dirs, files in os.walk(folder):
        for name in files:
            with contextlib.suppress(OSError):
                (Path(root) / name).chmod(stat.S_IREAD)


def stage(
    project: Path,
    node_id: str,
    entries: list[Entry],
    lint: Lint,
    derived: dict[str, Any] | None = None,  # Any: JSON
) -> Staged:
    """Copy ``entries`` into the project as node ``node_id``'s snapshot, or refuse.

    Each `source` file is linted after it is copied; any finding discards the whole copy and the
    findings come back as refusals. An earlier copy for the same id is replaced. ``derived`` (the
    recipe and solver read from the source) is kept in the manifest, so a replay that finds the
    copy needs neither the source nor its Allrun.
    """
    home = case_dir(project, node_id)
    incoming = home.parent / f".incoming-{node_id}-{secrets.token_hex(4)}"
    staged = Staged()
    budget = MAX_CASE_BYTES
    try:
        incoming.mkdir(parents=True)
        for entry in entries:
            target = incoming / entry.area / entry.rel
            size, sha = _copy(entry.path, target, budget)
            budget -= size
            staged.files.append(
                {"path": entry.rel, "area": entry.area, "size": size, "sha256": sha}
            )
        # **Every file is copied before any is linted**, so a case-local function file
        # (`system/wallsSurface`) is there when the file that names it is checked. Plain-arithmetic
        # `#calc` in the copy is resolved first (ADR-0033); one it cannot evaluate is left for the
        # lint, which refuses it as before.
        for row in staged.files:
            if row["area"] != SOURCE_AREA:
                continue
            target = incoming / row["area"] / row["path"]
            resolved = _resolve_calc(target)
            if resolved:
                row["size"], row["sha256"] = _size_sha(target)
                staged.calc.extend(f"{row['path']}: {expr}" for expr in resolved)
            staged.refusals.extend(lint(str(row["path"]), target))
        if not staged.files:
            staged.refusals.append(_refusal(".", "the case holds no files"))
        if staged.refusals:
            return staged
        staged.snapshot = snapshot_of(staged.files)
        manifest = {
            "version": MANIFEST_VERSION,
            "snapshot": staged.snapshot,
            "files": staged.files,
            "derived": {**(derived or {}), **({"calc": staged.calc} if staged.calc else {})},
        }
        (incoming / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        remove_tree(home)
        incoming.replace(home)
        _read_only(home)
        return staged
    except OSError as exc:
        raise CaseStoreError(
            f"the case could not be copied into the project: {exc}",
            details={"node_id": node_id},
        ) from exc
    finally:
        if incoming.exists():
            remove_tree(incoming)


def read_manifest(project: Path, node_id: str) -> dict[str, Any] | None:  # Any: JSON
    """The manifest of node ``node_id``'s copy, or None when there is none or it is damaged."""
    path = case_dir(project, node_id) / MANIFEST
    try:
        if path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("a case manifest could not be read", extra=fields(error=str(exc)[:200]))
        return None
    if (
        not isinstance(data, dict)
        or data.get("version") != MANIFEST_VERSION
        or not is_snapshot(data.get("snapshot"))
        or not isinstance(data.get("files"), list)
    ):
        return None
    return data


def _kind(rel: str, path: Path) -> tuple[str, bool]:
    """`(dict | field | mesh | binary, parsed)` for one file of a copy's `source` area."""
    from stokes.compile.foam_read import FoamReadError, parse

    parts = rel.split("/")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_PARSE_BYTES + 1)
    except OSError:
        return "binary", False
    if b"\x00" in data[:8192] or data[:2] == b"\x1f\x8b":
        return "binary", False
    if "polyMesh" in parts[:-1]:
        kind = "mesh"
    elif _TIME_RE.fullmatch(parts[0]) or parts[0] == "0.orig":
        kind = "field"
    else:
        kind = "dict"
    if len(data) > MAX_PARSE_BYTES:
        return kind, False
    try:
        parse(data)
    except FoamReadError:
        return kind, False
    return kind, True


def list_files(project: Path, node_id: str) -> list[dict[str, Any]] | None:  # Any: JSON
    """Every file of node ``node_id``'s copy: `{path, area, kind, size, parsed}`; None if absent.

    Scripts are `kind: script` and never parsed: they are shell, not dictionaries.
    """
    manifest = read_manifest(project, node_id)
    if manifest is None:
        return None
    home = case_dir(project, node_id)
    out: list[dict[str, Any]] = []
    for row in manifest["files"]:
        if not isinstance(row, dict) or row.get("area") not in (
            SOURCE_AREA,
            SCRIPTS_AREA,
            EXTRAS_AREA,
        ):
            continue
        rel = str(row.get("path", ""))
        if row["area"] == SCRIPTS_AREA:
            kind, parsed = "script", False
        elif row["area"] == EXTRAS_AREA:
            kind, parsed = "extra", False
        else:
            kind, parsed = _kind(rel, home / SOURCE_AREA / rel)
        out.append(
            {
                "path": rel,
                "area": row["area"],
                "kind": kind,
                "size": row.get("size"),
                "parsed": parsed,
            }
        )
    return out


def source_rows(project: Path, node_id: str) -> list[dict[str, Any]] | None:  # Any: JSON
    """The manifest rows of the copy's `source` area (`{path, area, size, sha256}`), or None."""
    manifest = read_manifest(project, node_id)
    if manifest is None:
        return None
    return [
        row
        for row in manifest["files"]
        if isinstance(row, dict)
        and row.get("area") == SOURCE_AREA
        and isinstance(row.get("path"), str)
    ]


def read_source(project: Path, node_id: str, rel: str, limit: int) -> bytes:
    """The bytes of one `source` file of the copy, by its manifest path, at most ``limit`` bytes.

    **Only a path the manifest lists is read**, so a caller's text can never name another file;
    the file must also be what the manifest says (size and hash), so a copy edited on disk is a
    `case.store` refusal rather than a silent change to what the case stages.
    """
    rows = source_rows(project, node_id)
    row = next((item for item in rows or [] if item["path"] == rel), None)
    if row is None:
        raise CaseStoreError(
            f"the case has no file {rel[:80]}", code="case.file_missing", details={"file": rel[:80]}
        )
    if not isinstance(row.get("size"), int) or row["size"] > limit:
        raise CaseStoreError(
            f"{rel} is larger than {limit} bytes", code="case.file_too_large", details={"file": rel}
        )
    path = case_dir(project, node_id) / SOURCE_AREA / rel
    try:
        if path.is_symlink():
            raise CaseStoreError(f"{rel} is a link", details={"file": rel})
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise CaseStoreError(f"{rel} could not be read: {exc}", details={"file": rel}) from exc
    if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row.get("sha256"):
        raise CaseStoreError(
            f"{rel} differs from the copy that was imported", details={"file": rel}
        )
    return data


def has_snapshot(project: Path, node_id: str, snapshot: str) -> bool:
    """Whether the project holds node ``node_id``'s copy with exactly ``snapshot``."""
    manifest = read_manifest(project, node_id)
    if manifest is None or manifest["snapshot"] != snapshot:
        return False
    try:
        rows = [row for row in manifest["files"] if isinstance(row, dict)]
        if snapshot_of(rows) != snapshot:
            return False
        home = case_dir(project, node_id)
        return all((home / row["area"] / row["path"]).is_file() for row in rows)
    except (KeyError, TypeError):
        return False
