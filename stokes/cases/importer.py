"""Import one external case into a project (ADR-0029 decisions 1, 3 and 6, plan T-307).

## Responsibility

`tutorial_source` and `folder_source` turn what the user named into a resolved case folder,
refusing anything that is not one; `check_apart` refuses a case inside the project (or a project
inside the case); `import_case` derives the recipe and the solver, collects the files and the
resources, and has `stokes.cases.store` make the read-only snapshot. The `case.*` commands own
the model change; this module never touches the tree.

## Paths

A tutorial id is a relative path of plain names (`incompressibleFluid/cavity`): no `..`, no
drive, no backslash. It is joined to the tutorials root, resolved, and must stay under the
resolved root; a link that leads out is `case.source_escape`. A folder must be an absolute path to
an existing directory holding `system/controlDict`. The source is only ever read.

Import rules: standard library, `stokes.errors`, the `stokes.cases` modules. Headless; the lint
and the argv check come in as parameters (`stokes.cases.store.Lint`, `recipe.ArgvCheck`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stokes.cases.allrun import case_recipe
from stokes.cases.facts import MODULE_RUNNERS, CaseFacts
from stokes.cases.recipe import ArgvCheck, Recipe, check_recipe
from stokes.cases.resources import resource_entries
from stokes.cases.store import Entry, Lint, collect, has_snapshot, read_manifest, stage
from stokes.errors import ValidationError

MAX_ID_LENGTH = 512
_PART_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*")
_NAME_CHARS_RE = re.compile(r"[^A-Za-z0-9_]")
MAX_NAME = 64


class CaseSourceError(ValidationError):
    """The case named cannot be imported from where it was named."""

    code = "case.source_invalid"


@dataclass(frozen=True)
class Source:
    """A resolved case folder and how the user named it."""

    kind: str  # "install" or "folder"
    text: str  # the tutorial id or the folder as given
    case: Path
    root: Path | None = None  # the tutorials folder, for resources

    def to_json(self) -> dict[str, str]:
        key = "tutorial" if self.kind == "install" else "path"
        return {"kind": self.kind, key: self.text}

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.text.split("/")) if self.kind == "install" else (self.case.name,)


@dataclass
class Imported:
    """What `import_case` made: the snapshot and the derived fields, or the refusals."""

    status: str
    snapshot: str = ""
    recipe: Recipe | None = None
    solver: str | dict[str, str] | None = None
    regions: list[str] | None = None
    refusals: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON


def _is_case(folder: Path) -> bool:
    control = folder / "system" / "controlDict"
    return folder.is_dir() and control.is_file()


def tutorial_source(root: Path, tutorial: str) -> Source:
    """The case ``tutorial`` under the tutorials folder ``root``, resolved and contained."""
    parts = tutorial.split("/")
    if (
        not tutorial
        or len(tutorial) > MAX_ID_LENGTH
        or not all(_PART_RE.fullmatch(part) for part in parts)
        or any(part in {".", ".."} for part in parts)
    ):
        raise CaseSourceError(
            f"{tutorial!r} is not a tutorial id like 'incompressibleFluid/cavity'",
            details={"tutorial": tutorial[:200]},
        )
    base = root.resolve()
    try:
        case = (base / tutorial).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CaseSourceError(
            f"the tutorial {tutorial} is not in {base}",
            code="case.source_missing",
            details={"tutorial": tutorial},
        ) from exc
    if not case.is_relative_to(base):
        raise CaseSourceError(
            f"the tutorial {tutorial} leads out of the tutorials folder",
            code="case.source_escape",
            details={"tutorial": tutorial},
        )
    if not _is_case(case):
        raise CaseSourceError(
            f"{tutorial} is not a case (no system/controlDict)",
            code="case.source_missing",
            details={"tutorial": tutorial},
        )
    return Source("install", tutorial, case, base)


def folder_source(path: str) -> Source:
    """The case folder ``path``: absolute, existing, holding `system/controlDict`."""
    if not path or len(path) > 4096 or "\x00" in path:
        raise CaseSourceError("give the case folder as a path")
    raw = Path(path)
    if not raw.is_absolute():
        raise CaseSourceError(
            f"{path!r} is not an absolute path", details={"path": path}, code="case.source_relative"
        )
    try:
        case = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CaseSourceError(
            f"{path} does not exist", code="case.source_missing", details={"path": path}
        ) from exc
    if not _is_case(case):
        raise CaseSourceError(
            f"{path} is not a case (no system/controlDict)",
            code="case.source_missing",
            details={"path": path},
        )
    return Source("folder", str(case), case)


def check_apart(case: Path, project: Path) -> None:
    """Refuse a case inside the project, or a project inside the case (it would copy itself)."""
    home = project.resolve()
    if case.is_relative_to(home) or home.is_relative_to(case):
        raise CaseSourceError(
            f"{case} overlaps the project folder {home}; import a case from outside it",
            code="case.source_in_project",
            details={"path": str(case)},
        )


def node_name(folder: str, taken: set[str]) -> str:
    """A tree name from a case's folder name: letters, digits, underscores, unique."""
    stem = _NAME_CHARS_RE.sub("_", folder)[:MAX_NAME].strip("_") or "case"
    if stem[0].isdigit():
        stem = f"case_{stem}"[:MAX_NAME]
    name, number = stem, 2
    while name in taken:
        suffix = f"_{number}"
        name = stem[: MAX_NAME - len(suffix)] + suffix
        number += 1
    return name


def solver_of(facts: CaseFacts, recipe: Recipe) -> str | dict[str, str] | None:
    """The case's solver: its `regionSolvers` map, its module, or its legacy application."""
    if facts.region_solvers:
        return dict(sorted(facts.region_solvers.items()))
    if facts.solver:
        return facts.solver
    for step in recipe.steps:
        if step.utility in MODULE_RUNNERS and "-solver" in step.argv[:-1]:
            return step.argv[step.argv.index("-solver") + 1]
    application = facts.application
    return application if application not in MODULE_RUNNERS else None


def _status(recipe: Recipe) -> str:
    return status_of({"checked": recipe.checked, "kind": recipe.kind})


def _merge(case: list[Entry], extra: list[Entry]) -> list[Entry]:
    """The case's files with the resources laid over them, as Allrun's `cp` would."""
    merged = {(entry.area, entry.rel): entry for entry in case}
    merged.update({(entry.area, entry.rel): entry for entry in extra})
    return sorted(merged.values(), key=lambda entry: (entry.area, entry.rel))


def import_case(
    project: Path,
    node_id: str,
    source: Source,
    lint: Lint,
    check: ArgvCheck | None,
) -> Imported:
    """Copy ``source`` into ``project`` as node ``node_id``'s snapshot, or say why not.

    ``check`` is None when the install has not been probed: the recipe is then `unverified`.
    """
    check_apart(source.case, project)
    recipe, facts = case_recipe(source.case, source.parts)
    if check is not None:
        check_recipe(recipe, check)
    entries, refusals = collect(source.case)
    extra, missing = resource_entries(source.root, recipe.resources)
    refusals += missing
    out = Imported(
        status="refused",
        recipe=recipe,
        solver=solver_of(facts, recipe),
        regions=sorted(facts.region_solvers) or None,
        refusals=refusals,
    )
    if refusals:
        return out
    derived = {"recipe": recipe.to_json(), "solver": out.solver, "regions": out.regions}
    staged = stage(project, node_id, _merge(entries, extra), lint, derived)
    if not staged.ok:
        out.refusals = staged.refusals
        return out
    out.snapshot = staged.snapshot
    out.status = _status(recipe)
    return out


def status_of(recipe: dict[str, Any]) -> str:  # Any: JSON
    """A stored recipe's status: `ready`, `needs_recipe` or `unverified` (not checked)."""
    if not recipe.get("checked"):
        return "unverified"
    return "ready" if recipe.get("kind") == "complete" else "needs_recipe"


def reuse(project: Path, node_id: str, snapshot: str) -> dict[str, Any] | None:  # Any: JSON
    """The derived fields of a copy already in the project with ``snapshot``, or None.

    A journal replay comes here first: the copy, not the source, is what the record names.
    """
    if not has_snapshot(project, node_id, snapshot):
        return None
    manifest = read_manifest(project, node_id) or {}
    derived = manifest.get("derived")
    if not isinstance(derived, dict) or not isinstance(derived.get("recipe"), dict):
        return None
    return derived
