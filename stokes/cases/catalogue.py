"""The tutorial catalogue: every case under a folder, and whether this install can run it (T-304).

## Responsibility

`find_cases` lists the case folders under a root (a case is a directory holding
`system/controlDict`); `classify` gives one case its entry
`{id, category, path, application, solver, regions, recipe_kind, status, why}`; `catalogue` does
both with the filters `tutorial.catalogue` takes. The verdict compares what the case's files say
it needs (`stokes.cases.facts`) with what the host probe found (`Inventory`).

## Statuses, in the order they are decided

1. `unverified` - the controlDict cannot be read, the install was not probed, or no solver can be
   named from the files or, for a legacy case, from its nearest `*Foam` folder.
2. `coded` - the case compiles or runs code (`#codeStream`, `#calc`, `coded*`, `systemCall`,
   `#{ ... #}`); the `external` lint profile refuses it whatever the install has. Decided before
   the probe check because it does not depend on the install.
3. `missing_module` - a solver module the case names has no `lib<name>Solver` in the install.
4. `missing_library` - a `libs` entry, an `#includeEtc` file or an `#includeFunc` function is not
   in the install. Etc files count here because they are install content a case loads, like a
   library (assumption recorded in the T-304 report).
5. `missing_utility` - an application a step needs is not in `FOAM_APPBIN`.
6. `needs_recipe` - Allrun has a line that is not one `runApplication`/`runParallel` step, or
   there is no Allrun, no `blockMeshDict` and no mesh.
7. `ready` - none of the above.

## Read only

The root and every case are read, never written; symlinks are not followed; the walk is bounded
(`MAX_CASES`, `MAX_WALK_DEPTH`, `MAX_DIRS`).

Import rules: standard library, `stokes.cases.facts`. Headless.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stokes.cases.allrun import parse_allrun
from stokes.cases.facts import (
    MAX_ALLRUN_BYTES,
    MODULE_RUNNERS,
    CaseFacts,
    library_name,
    read_case,
    scan_dictionaries,
)
from stokes.cases.resources import resource_entries

STATUSES: tuple[str, ...] = (
    "ready",
    "missing_module",
    "missing_utility",
    "missing_library",
    "coded",
    "needs_recipe",
    "unverified",
)
MAX_CASES = 5000
MAX_WALK_DEPTH = 8
MAX_DIRS = 100_000


@dataclass(frozen=True)
class Inventory:
    """What an install can run, from the `host.probe` record (T-304)."""

    solver_modules: frozenset[str]
    utilities: frozenset[str]
    libraries: frozenset[str]
    etc_functions: frozenset[str]
    etc_case_dicts: frozenset[str]

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | None) -> Inventory | None:  # Any: JSON
        """The inventory in a probe record, or None when it has none (an older agent)."""
        if not record:
            return None

        def names(key: str) -> frozenset[str]:
            value = record.get(key)
            if not isinstance(value, list):
                return frozenset()
            return frozenset(item for item in value if isinstance(item, str))

        keys = ("solver_modules", "utilities", "libraries", "etc_functions", "etc_case_dicts")
        if not all(isinstance(record.get(key), list) for key in keys[:3]):
            return None
        libraries = frozenset(
            name for name in (library_name(item) for item in names("libraries")) if name
        )
        return cls(
            solver_modules=names("solver_modules"),
            utilities=names("utilities"),
            libraries=libraries,
            etc_functions=names("etc_functions"),
            etc_case_dicts=names("etc_case_dicts"),
        )


def _is_case(folder: Path) -> bool:
    control = folder / "system" / "controlDict"
    return control.is_file() and not control.is_symlink()


def find_cases(root: Path, limit: int = MAX_CASES) -> list[Path]:
    """Case folders under ``root`` (``root`` itself included), sorted, at most ``limit``.

    The walk does not enter a case (its time and region folders are not cases), hidden folders or
    symlinked folders, and stops after `MAX_DIRS` folders whatever it has found.
    """
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    visited = 0
    while stack and len(found) < limit and visited < MAX_DIRS:
        folder, depth = stack.pop()
        visited += 1
        if _is_case(folder):
            found.append(folder)
            continue
        if depth >= MAX_WALK_DEPTH:
            continue
        try:
            with os.scandir(folder) as entries:
                children = sorted(
                    Path(entry.path)
                    for entry in entries
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".")
                )
        except OSError:
            continue
        stack.extend((child, depth + 1) for child in reversed(children))
    return sorted(found)


def has_case(root: Path) -> bool:
    """Whether at least one case sits under ``root``."""
    return bool(find_cases(root, limit=1))


def _application(facts: CaseFacts) -> str | None:
    """The application the case runs; `foamRun`/`foamMultiRun` inferred from a solver entry."""
    if facts.application:
        return facts.application
    if facts.region_solvers:
        return "foamMultiRun"
    if facts.solver:
        return "foamRun"
    return None


def _modules(facts: CaseFacts, application: str | None) -> list[str]:
    """The solver modules the case loads, in a stable order."""
    if facts.region_solvers:
        return sorted(set(facts.region_solvers.values()))
    named = [facts.solver] if facts.solver else []
    if facts.allrun is not None:
        named += facts.allrun.solvers
    return sorted(set(named)) if application in MODULE_RUNNERS else []


def _utilities(facts: CaseFacts, application: str | None) -> list[str]:
    """The applications the recipe needs: Allrun's steps, or the default recipe's."""
    if facts.allrun is not None:
        needed = list(facts.allrun.utilities)
        if facts.allrun.runs_application and application is not None:
            needed.append(application)
        return list(dict.fromkeys(needed))
    needed = ["blockMesh"] if facts.has_block_mesh_dict and not facts.has_mesh else []
    return needed + ([application] if application is not None else [])


def _missing(needed: Iterable[str], have: frozenset[str]) -> list[str]:
    return [name for name in needed if name not in have]


def _verdict(  # noqa: PLR0911 - one return per status, in the documented order
    facts: CaseFacts, inventory: Inventory | None
) -> tuple[str, str]:
    """`(status, why)` for one case; the order is the module docstring's."""
    if not facts.readable:
        return "unverified", facts.problem
    if facts.coded:
        extra = f" (and {len(facts.coded) - 1} more)" if len(facts.coded) > 1 else ""
        return "coded", f"runtime code: {facts.coded[0]}{extra}"
    if inventory is None:
        return "unverified", "the install has not been probed, so what it can run is unknown"
    application = _application(facts)
    # A meshing-only case (its Allrun runs utilities and never the application) needs no solver.
    mesh_only = (
        facts.allrun is not None
        and bool(facts.allrun.utilities)
        and not facts.allrun.runs_application
    )
    if application is None and not mesh_only:
        return "unverified", "system/controlDict names no application, solver or regionSolvers"
    modules = _modules(facts, application)
    if application == "foamRun" and not modules:
        return "unverified", "foamRun with no solver in system/controlDict or Allrun"
    lacking = _missing(modules, inventory.solver_modules)
    if lacking:
        return "missing_module", f"solver module {', '.join(lacking)} is not in this install"
    lacking = _missing(sorted(facts.libraries), inventory.libraries)
    if lacking:
        return "missing_library", f"library {', '.join(lacking)} is not in this install"
    lacking = _missing(sorted(facts.etc_includes), inventory.etc_case_dicts)
    lacking += _missing(sorted(facts.etc_functions), inventory.etc_functions)
    if lacking:
        return "missing_library", f"etc file {', '.join(lacking)} is not in this install"
    lacking = _missing(_utilities(facts, application), inventory.utilities)
    if lacking:
        return "missing_utility", f"application {', '.join(lacking)} is not in this install"
    if facts.allrun is not None and facts.allrun.unmapped:
        more = len(facts.allrun.unmapped) - 1
        extra = f" (and {more} more)" if more else ""
        return (
            "needs_recipe",
            f"Allrun line not mapped to a step: {facts.allrun.unmapped[0]}{extra}",
        )
    if facts.allrun is None and not facts.has_block_mesh_dict and not facts.has_mesh:
        return "needs_recipe", "no Allrun, no system/blockMeshDict and no mesh"
    return "ready", ""


def _scan_resources(case: Path, root: Path, application: str | None, facts: CaseFacts) -> None:
    """Scan the `resources/` files the case's Allrun copies in, as the import will lint them.

    Without this a tutorial whose own files are clean but whose Allrun copies a coded dictionary
    from `resources/` (compressibleVoF's angledDuct: `#codeStream` in its blockMeshDict) was listed
    `ready` and then refused when opened. A resource that cannot be found is the import's to report.
    """
    allrun = case / "Allrun"
    try:
        if allrun.stat().st_size > MAX_ALLRUN_BYTES:
            return
        text = allrun.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return
    if "resources" not in text:
        return
    recipe = parse_allrun(text, application)
    entries, _missing_resources = resource_entries(root, recipe.resources)
    scan_dictionaries(((f"{entry.rel} (from resources)", entry.path) for entry in entries), facts)


def classify(case: Path, root: Path, inventory: Inventory | None) -> dict[str, Any]:  # Any: JSON
    """The catalogue entry for one case folder under ``root``."""
    facts = read_case(case)
    relative = case.relative_to(root)
    parts = relative.parts
    if facts.readable and _application(facts) is None:
        # OpenFOAM 14's legacy tutorials name no application in controlDict: the folder does
        # (`legacy/incompressible/icoFoam/cavity/cavity`). The nearest `*Foam` folder is taken.
        named = [part for part in parts[:-1] if part.endswith("Foam") and len(part) > 4]
        facts.application = named[-1] if named else None
    application = _application(facts) if facts.readable else None
    solver: str | dict[str, str] | None
    if facts.region_solvers:
        solver = dict(sorted(facts.region_solvers.items()))
    elif application in MODULE_RUNNERS:
        modules = _modules(facts, application)
        solver = modules[0] if modules else None
    else:
        solver = application
    if facts.readable and facts.allrun is not None:
        _scan_resources(case, root, application, facts)
    status, why = _verdict(facts, inventory)
    return {
        "id": relative.as_posix() if parts else ".",
        "category": parts[0] if len(parts) > 1 else ".",
        "path": str(case),
        "application": application,
        "solver": solver,
        "regions": sorted(facts.region_solvers) or None,
        "recipe_kind": "allrun" if facts.allrun is not None else "default",
        "status": status,
        "why": why,
    }


def _matches(entry: Mapping[str, Any], text: str) -> bool:  # Any: JSON
    needle = text.casefold()
    solver = entry["solver"]
    words = [entry["id"], entry["application"] or "", entry["status"]]
    words += list(solver.values()) if isinstance(solver, dict) else [solver or ""]
    return any(needle in str(word).casefold() for word in words)


def classified(root: Path, inventory: Inventory | None) -> list[dict[str, Any]]:  # Any: JSON
    """Every case under ``root``, classified, unfiltered: one walk serves every filter."""
    return [classify(case, root, inventory) for case in find_cases(root)]


def select(
    entries: list[dict[str, Any]],  # Any: JSON
    category: str | None = None,
    text: str | None = None,
) -> list[dict[str, Any]]:  # Any: JSON
    """The entries in ``category`` (a top-level folder) whose words contain ``text``."""
    return [
        entry
        for entry in entries
        if (not category or entry["category"] == category) and (not text or _matches(entry, text))
    ]


def catalogue(
    root: Path,
    inventory: Inventory | None,
    category: str | None = None,
    text: str | None = None,
) -> list[dict[str, Any]]:  # Any: JSON
    """Every case under ``root``, classified, filtered by category and free text."""
    return select(classified(root, inventory), category, text)


def counts(entries: Iterable[Mapping[str, Any]]) -> dict[str, int]:  # Any: JSON
    """How many entries have each status, every status present."""
    out = dict.fromkeys(STATUSES, 0)
    for entry in entries:
        out[entry["status"]] = out.get(entry["status"], 0) + 1
    return out
