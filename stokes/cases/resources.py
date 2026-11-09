"""Files a case takes from the tutorials' shared `resources/` folder (ADR-0029 decision 3, T-306).

An Allrun copies or links shared files into the case (`cp $FOAM_TUTORIALS/resources/geometry/
x.stl.gz constant/geometry/`). The recipe parser records each as a `Resource`; `resource_entries`
turns them into files to copy into the snapshot at the place Allrun would put them, so the
imported case needs no install at run time. A resource is read from inside the tutorials root
only: its path is plain components with no `..`, it is resolved, and a result outside the
root's `resources/` folder (a link that escapes) is a refusal.

A single filename wildcard in a resource copy is expanded here, with a bounded
number of plain file names; it is never passed to a shell.

Import rules: standard library, `stokes.cases.recipe`, `stokes.cases.store`,
`stokes.compile.foam_read`. Headless.
"""

from __future__ import annotations

import itertools
import posixpath
import re
from pathlib import Path
from typing import Any

from stokes.cases.recipe import Resource
from stokes.cases.store import MAX_CASE_FILES, MAX_DEPTH, MAX_PARSE_BYTES, SOURCE_AREA, Entry
from stokes.compile.foam_read import DirectiveNode, FoamReadError, Token, parse, walk


def _refusal(rel: str, why: str) -> dict[str, Any]:  # Any: JSON
    return {"file": rel, "line": 0, "token": "", "why": why}


def _target(resource: Resource, source: Path) -> str:
    """Where the resource lands in the case: a folder target takes the source's name."""
    if not resource.target or resource.target.endswith("/"):
        return posixpath.join(resource.target.rstrip("/"), source.name)
    return resource.target


def _walk(folder: Path, prefix: str, base: Path) -> tuple[list[Entry], list[dict[str, Any]]]:
    """The files of a resource folder, links refused, depth bounded."""
    entries: list[Entry] = []
    refusals: list[dict[str, Any]] = []
    stack = [(folder, prefix, 0)]
    while stack:
        current, rel, depth = stack.pop()
        for child in sorted(current.iterdir()):
            name = f"{rel}/{child.name}"
            if child.is_symlink() or not child.resolve().is_relative_to(base):
                refusals.append(_refusal(name, "a symbolic link in a resource folder"))
            elif child.is_dir() and depth < MAX_DEPTH:
                stack.append((child, name, depth + 1))
            elif child.is_file():
                entries.append(Entry(name, SOURCE_AREA, child))
    return entries, refusals


def _mesh_dictionary_includes(
    source: Path, target: str, base: Path
) -> tuple[list[Entry], list[dict[str, Any]]]:
    """Copy literal local includes alongside a shared blockMesh dictionary."""
    entries: list[Entry] = []
    refusals: list[dict[str, Any]] = []
    pending = [(source, target, 0)]
    seen = {(source, target)}
    while pending:
        current, rel, depth = pending.pop()
        try:
            with current.open("rb") as handle:
                data = handle.read(MAX_PARSE_BYTES + 1)
            if len(data) > MAX_PARSE_BYTES:
                raise ValueError("dictionary exceeds the parse limit")
            document = parse(data)
        except (OSError, FoamReadError, ValueError) as exc:
            refusals.append(_refusal(rel, f"cannot inspect resource dictionary includes: {exc}"))
            continue
        for node in walk(document):
            if not isinstance(node, DirectiveNode) or node.name not in {
                "include",
                "includeIfPresent",
            }:
                continue
            argument = node.argument
            name = argument.text.strip('"') if isinstance(argument, Token) else ""
            parts = name.split("/")
            if not re.fullmatch(r"[A-Za-z0-9_./-]+", name) or any(
                p in {"", ".", ".."} for p in parts
            ):
                refusals.append(_refusal(rel, "resource dictionary has a non-local include"))
                continue
            try:
                included = (current.parent / name).resolve(strict=True)
            except (OSError, RuntimeError):
                if node.name == "include":
                    refusals.append(_refusal(rel, f"resource include {name} is missing"))
                continue
            if not included.is_relative_to(base) or not included.is_file():
                refusals.append(_refusal(rel, f"resource include {name} leads out of resources/"))
                continue
            destination = posixpath.join(posixpath.dirname(rel), name)
            key = (included, destination)
            if key in seen:
                continue
            if depth >= MAX_DEPTH or len(seen) >= MAX_CASE_FILES:
                refusals.append(_refusal(rel, "resource include limit exceeded"))
                continue
            seen.add(key)
            entries.append(Entry(destination, SOURCE_AREA, included))
            pending.append((included, destination, depth + 1))
    return entries, refusals


def _resource_glob(
    root: Path, base: Path, resource: Resource, where: str
) -> tuple[list[Entry], list[dict[str, Any]]]:
    """Expand a bounded filename pattern under the shared resources directory."""
    pattern = Path(resource.source)
    if (
        not resource.target.endswith("/")
        or pattern.is_absolute()
        or pattern.parts[:1] != ("resources",)
        or any(part in {".", ".."} or "*" in part for part in pattern.parts[:-1])
        or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\*[A-Za-z0-9_.-]*", pattern.name)
    ):
        return [], [_refusal(where, "the resource glob is not a plain file pattern")]
    entries: list[Entry] = []
    try:
        folder = (root / pattern.parent).resolve(strict=True)
        if not folder.is_relative_to(base) or not folder.is_dir():
            raise OSError("resource glob folder leaves resources/")
        matches = sorted(itertools.islice(folder.glob(pattern.name), MAX_CASE_FILES + 1))
        if not matches or len(matches) > MAX_CASE_FILES:
            raise OSError("resource glob matched no files or too many files")
        for match in matches:
            resolved = match.resolve(strict=True)
            if (
                not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", match.name)
                or not resolved.is_relative_to(base)
                or not resolved.is_file()
            ):
                raise OSError("resource glob matched a non-file or an escaping link")
            entries.append(
                Entry(
                    posixpath.join(resource.target.rstrip("/"), match.name), SOURCE_AREA, resolved
                )
            )
    except (OSError, RuntimeError) as exc:
        return [], [_refusal(where, f"cannot copy resource glob: {exc}")]
    return entries, []


def resource_entries(
    root: Path | None, resources: list[Resource]
) -> tuple[list[Entry], list[dict[str, Any]]]:  # Any: JSON
    """The files ``resources`` bring into the case, read from ``root`` (the tutorials folder).

    With no root (a case imported from a folder) every resource is a refusal: the file it needs
    is not known.
    """
    entries: list[Entry] = []
    refusals: list[dict[str, Any]] = []
    for resource in resources:
        where = f"Allrun:{resource.line}" if resource.line else "Allrun"
        if root is None:
            refusals.append(
                _refusal(where, f"{resource.source} needs the tutorials folder's resources")
            )
            continue
        try:
            base = (root / "resources").resolve()
            tutorial_root = root.resolve()
        except (OSError, RuntimeError):
            refusals.append(_refusal(where, "the resources folder could not be resolved"))
            continue
        if not base.is_relative_to(tutorial_root):
            refusals.append(_refusal(where, "the resources folder leads out of tutorials/"))
            continue
        if "*" in resource.source:
            found, why = _resource_glob(root, base, resource, where)
            entries += found
            refusals += why
            continue
        try:
            source = (root / resource.source).resolve(strict=True)
        except (OSError, RuntimeError):
            refusals.append(_refusal(where, f"{resource.source} is not in the tutorials folder"))
            continue
        if not source.is_relative_to(base):
            refusals.append(_refusal(where, f"{resource.source} leads out of resources/"))
            continue
        target = _target(resource, source)
        if source.is_dir():
            found, why = _walk(source, target, base)
            entries += found
            refusals += why
        else:
            entries.append(Entry(target, SOURCE_AREA, source))
            if resource.source.startswith("resources/blockMesh/"):
                found, why = _mesh_dictionary_includes(source, target, base)
                entries += found
                refusals += why
    return entries, refusals
