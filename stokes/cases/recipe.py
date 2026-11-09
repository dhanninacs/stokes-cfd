"""Recipes: the argv steps that run an external case (ADR-0029 decision 6, plan T-306).

## Responsibility

The recipe's types (`Step`, `Action`, `Resource`, `Unparsed`, `Recipe`) and what is decided
without reading an Allrun: `default_recipe` is the recipe of a case with no Allrun (`blockMesh`
when `system/blockMeshDict` exists and no mesh ships, then the application), `application_of`
names the application, `stage_of` a step's stage, and `check_recipe` judges every step's argv
with a caller-supplied check (the agent's external flag allow-list,
`stokes_agent.flags_external`, wired in by the `case.*` commands because this package may not
import the agent), moving a refused step to `refused` with the reason. The Allrun parser is
`stokes.cases.allrun`.

Import rules: standard library and `stokes.cases.facts`. Headless; never `stokes_agent`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from stokes.cases.facts import CaseFacts

#: A step check: argv -> why the agent would refuse it, or None.
ArgvCheck = Callable[[Sequence[str]], "str | None"]

STAGES: tuple[str, ...] = ("mesh", "init", "solve", "post")

_MESH = frozenset(
    {
        "blockMesh",
        "snappyHexMesh",
        "surfaceFeatures",
        "extrudeMesh",
        "extrudeToRegionMesh",
        "refineMesh",
        "subsetMesh",
        "splitMeshRegions",
        "createBaffles",
        "createPatch",
        "splitBaffles",
        "mirrorMesh",
        "stitchMesh",
        "renumberMesh",
        "collapseEdges",
        "transformPoints",
        "mergeMeshes",
        "zeroDimensionalMesh",
        "createZones",
        "createNonConformalCouples",
        "topoSet",
        "checkMesh",
    }
)
_POST = frozenset({"reconstructPar", "foamPostProcess"})
_SOLVE = frozenset({"foamRun", "foamMultiRun"})


@dataclass(frozen=True)
class Step:
    """One program to run: `argv[0]` is the utility; `folder` is the sub-case it runs in."""

    name: str
    argv: tuple[str, ...]
    parallel: bool = False
    ranks: int | None = None
    region: str | None = None
    stage: str = "solve"
    folder: str = ""
    line: int | None = None

    @property
    def utility(self) -> str:
        return self.argv[0]

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "name": self.name,
            "utility": self.utility,
            "argv": list(self.argv),
            "parallel": self.parallel,
            "ranks": self.ranks,
            "region": self.region,
            "stage": self.stage,
            "folder": self.folder,
            "line": self.line,
        }


@dataclass(frozen=True)
class Action:
    """A file operation Allrun does before a step: `copy`, `mkdir` or `remove`, inside the case."""

    op: str
    target: str
    source: str | None = None
    line: int | None = None

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {"op": self.op, "source": self.source, "target": self.target, "line": self.line}


@dataclass(frozen=True)
class Resource:
    """A file from the tutorials' `resources/` folder the case needs at ``target``.

    ``source`` is relative to the tutorials root (`resources/geometry/x.stl.gz`); ``target`` is
    relative to the case and ends with `/` when it names a folder.
    """

    source: str
    target: str
    line: int | None = None

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {"source": self.source, "target": self.target, "line": self.line}


@dataclass(frozen=True)
class Unparsed:
    """A line the recipe does not run, and why."""

    line: int
    text: str
    why: str

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {"line": self.line, "text": self.text, "why": self.why}


@dataclass
class Recipe:
    """The steps of one case, where they came from, and what was left out."""

    origin: str  # "allrun" or "default"
    steps: list[Step] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    unparsed: list[Unparsed] = field(default_factory=list)
    refused: list[Unparsed] = field(default_factory=list)
    checked: bool = False

    @property
    def kind(self) -> str:
        """`complete` when every line became something and no step was refused."""
        return "partial" if self.unparsed or self.refused or not self.steps else "complete"

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "origin": self.origin,
            "kind": self.kind,
            "checked": self.checked,
            "steps": [step.to_json() for step in self.steps],
            "actions": [action.to_json() for action in self.actions],
            "resources": [item.to_json() for item in self.resources],
            "unparsed": [item.to_json() for item in self.unparsed],
            "refused": [item.to_json() for item in self.refused],
        }


def stage_of(utility: str, application: str | None) -> str:
    """Which stage a utility belongs to: `mesh`, `init`, `solve` or `post`."""
    if utility in _MESH:
        return "mesh"
    if utility in _POST:
        return "post"
    if utility in _SOLVE or utility == application or utility.endswith("Foam"):
        return "solve"
    return "init"


def default_recipe(application: str | None, *, block_mesh: bool, has_mesh: bool) -> Recipe:
    """The recipe of a case with no Allrun: `[blockMesh?, application]`."""
    recipe = Recipe("default")
    if block_mesh and not has_mesh:
        recipe.steps.append(Step("blockMesh", ("blockMesh",), stage="mesh"))
    if application:
        recipe.steps.append(Step(application, (application,), stage="solve"))
    else:
        recipe.unparsed.append(
            Unparsed(0, "", "system/controlDict names no application, solver or regionSolvers")
        )
    return recipe


def application_of(facts: CaseFacts, parts: Sequence[str] = ()) -> str | None:
    """The application a case runs: the controlDict's, `foamRun`/`foamMultiRun` from a solver
    entry, else the nearest `*Foam` folder of the case's id (a legacy tutorial)."""
    if facts.application:
        return facts.application
    if facts.region_solvers:
        return "foamMultiRun"
    if facts.solver:
        return "foamRun"
    named = [part for part in parts[:-1] if part.endswith("Foam") and len(part) > 4]
    return named[-1] if named else None


def check_recipe(recipe: Recipe, check: ArgvCheck) -> Recipe:
    """Judge every step with ``check``; a refused step moves to `refused` with the reason."""
    kept: list[Step] = []
    for step in recipe.steps:
        why = check(step.argv)
        if why is None:
            kept.append(step)
        else:
            recipe.refused.append(Unparsed(step.line or 0, " ".join(step.argv), why))
    recipe.steps = kept
    recipe.checked = True
    return recipe
