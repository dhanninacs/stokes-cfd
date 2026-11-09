"""Running an imported OpenFOAM case (ADR-0029 decision 6, plan T-309).

## What runs

`plan_case` turns a `foam_case` node into an agent job: **every file of the copy's `source`
area**, the node's overrides applied by the pure writer (`stokes.compile.of14.external`), and the
recipe's steps as argv lists. The job is created under the agent's `external` profile, so the agent
judges every file and every step against its own probe of the install; nothing here is trusted by
the agent, and nothing here runs a shell.

## What the recipe's file actions become

An Allrun's `cp -r 0.orig 0`, `mkdir` and `rm -rf` before its first step are applied to the staged
files here, in the order the Allrun had them. An action that comes after a step would act on a file
that step wrote, which staging cannot reproduce, so it becomes a file-action step of the job: the
agent runs it in its place in the order (`jobs._run_action`), inside the job folder only.

## Cores

The solve runs on the solver settings' `cores` (`_parallel_mode`): a recipe that decomposes
itself keeps its decomposePar and reconstructPar and runs its parallel steps on that many MPI
ranks; a serial recipe with one `foamRun` solve is decomposed around it by Stokes; anything else,
and a mesh-only run, stays on one rank, said in `adapted`. `system/decomposeParDict` is written for
the core count, so `numberOfSubdomains` always matches the ranks.

## Files the agent will not stage

A file outside `system/`, `constant/`, a time directory or `0.orig/` (a tutorial's `README`, a
`validation/` script folder) is listed in `skipped` and not sent: the agent refuses any other top
folder, and none of these is read by a solver.

Import rules: standard library, `stokes.cases`, `stokes.compile`, `stokes.run`; headless.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.errors import ValidationError
from stokes.log import get_logger
from stokes.run import staging
from stokes.run.executors import FoamRunExecutor
from stokes.run.records import EXTERNAL_ENGINE

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stokes.model import Node
    from stokes.run.records import RunRecord

log = get_logger(__name__)

#: The engine id of an imported case's runs.
ENGINE = EXTERNAL_ENGINE
#: The agent profile its jobs are created under.
PROFILE = "external"
#: The utilities whose log the residual parser reads: a solver writes residual blocks.
SOLVERS = frozenset(
    {
        "foamRun",
        "foamMultiRun",
        "icoFoam",
        "porousSimpleFoam",
        "rhoPorousSimpleFoam",
        "mhdFoam",
        "electrostaticFoam",
        "laplacianFoam",
        "shallowWaterFoam",
        "adjointShapeOptimisationFoam",
        "financialFoam",
        "dsmcFoam",
        "mdFoam",
        "mdEquilibrationFoam",
    }
)
#: Dropped from a serial run: they split and join a parallel one.
PARALLEL_ONLY = frozenset({"decomposePar", "reconstructPar"})
#: The top folders the agent's external profile stages.
_TOP_RE = re.compile(r"^(?:system|constant|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|0\.orig)$")
_STEP_RE = re.compile(r"[^A-Za-z0-9_]")
#: A step's time cap: the agent's own maximum, because a tutorial may be hours long.
STEP_TIMEOUT_S = 86400
#: A file action is a copy or a removal: it finishes in seconds, and the cap stops a stuck one.
FILE_ACTION_TIMEOUT_S = 600


class ExternalRunInvalid(ValidationError):
    """An imported case cannot be run as it stands."""

    code = "run.external_invalid"


@dataclass
class CasePlan:
    """What one run of an imported case sends and runs, and what it changed or left out."""

    files: dict[str, bytes] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    adapted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def basis(node: Node, cores: int = 1) -> str:
    """The run basis of an imported case: the same copy, edits, recipe and cores are one run."""
    from stokes.compile.of14.external import WRITER_VERSION

    params = node.params
    body: dict[str, Any] = {
        "engine": ENGINE,
        "writer": WRITER_VERSION,
        "snapshot": params.get("snapshot"),
        "overrides": params.get("overrides") or [],
        "recipe": params.get("recipe") or {},
    }
    if cores > 1:
        # Only above one, so a run made before cores counted keeps its basis.
        body["cores"] = cores
    if params.get("file_edits"):
        # Only when there are some, so a run made before text edits existed keeps its basis.
        body["file_edits"] = params["file_edits"]
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "ext:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def engine_lacks(node: Node, record: dict[str, Any]) -> str | None:  # Any: probe record
    """Why the probed install cannot run this case's solver, or None.

    Each solver the case names (a module, each region's module, or a legacy application) must be
    a module the probe found (`lib<name>Solver`) or, for a legacy application, a utility it found.
    **Only a probe that lists modules can refuse**: an agent that predates the inventory says
    nothing either way, and the run then finds out for itself.
    """
    modules = record.get("solver_modules")
    utilities = record.get("utilities")
    if not isinstance(modules, list) or not isinstance(utilities, list):
        return None
    solver = node.params.get("solver")
    names = list(solver.values()) if isinstance(solver, dict) else [solver] if solver else []
    missing = sorted({str(n) for n in names if n not in modules and n not in utilities})
    if not missing:
        return None
    return (
        f"this OpenFOAM install has no {', '.join(missing)} solver, so {node.path} cannot run "
        "here; it runs on an install that has it"
    )


def _apply_action(files: dict[str, bytes], action: dict[str, Any]) -> bool:
    """Apply one recipe file action to the staged files; False when it names nothing staged."""
    op = action.get("op")
    source = str(action.get("source") or "").strip("/")
    target = str(action.get("target") or "").strip("/")
    if op == "mkdir":
        return True
    if op == "remove" and target:
        doomed = [name for name in files if name == target or name.startswith(target + "/")]
        for name in doomed:
            del files[name]
        return True
    if op == "copy" and source and target:
        moved = {
            target + name[len(source) :]: data
            for name, data in files.items()
            if name == source or name.startswith(source + "/")
        }
        files.update(moved)
        return bool(moved)
    return False


def _step_name(index: int, utility: str) -> str:
    """A step name the agent accepts: a letter, then up to 31 letters, digits or underscores."""
    return f"s{index}_{_STEP_RE.sub('_', utility)}"[:32]


def _end_time(files: dict[str, bytes]) -> float:
    """The controlDict's `endTime`, for the parser's progress; 0 when it cannot be read."""
    from stokes.compile.of14.external import entry_text
    from stokes.errors import StokesError

    data = files.get("system/controlDict")
    if data is None:
        return 0.0
    try:
        text = entry_text(data, ("endTime",))
        return float(text) if text else 0.0
    except (StokesError, ValueError):
        return 0.0


def _stage_files(project_dir: Path, node: Node, plan: CasePlan) -> None:
    """Every stageable file of the copy, with the node's overrides written over it."""
    from stokes.cases.overrides import staged
    from stokes.cases.store import MAX_CASE_BYTES, read_source, source_rows

    rows = source_rows(project_dir, node.id)
    if rows is None:
        raise ExternalRunInvalid(
            f"the copy of {node.path} is not in the project", code="case.source_missing"
        )
    budget = MAX_CASE_BYTES
    for row in rows:
        name = str(row["path"])
        if "/" not in name or not _TOP_RE.match(name.split("/", maxsplit=1)[0]):
            plan.skipped.append(f"{name}: not in a folder a solver reads")
            continue
        size = int(row.get("size") or 0)
        budget -= size
        if budget < 0:
            raise ExternalRunInvalid(f"{node.path} is too large to stage", code="case.too_large")
        plan.files[name] = read_source(project_dir, node.id, name, size)
    plan.files.update(staged(project_dir, node.id, node.params.get("overrides") or []))
    # A saved text edit is the whole file (case.file_write linted it when it was saved).
    for item in node.params.get("file_edits") or []:
        if isinstance(item, dict) and str(item.get("file")) in plan.files:
            plan.files[str(item["file"])] = str(item.get("text", "")).encode("utf-8")


def _apply_actions(actions: list[Any], first_line: int, plan: CasePlan) -> list[dict[str, Any]]:
    """Apply the recipe's file actions that come before its first step; return the later ones.

    An action with no line (a recipe from before line numbers) counts as before the first step.
    """
    later: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        line = int(action.get("line") or 0)
        if first_line and line > first_line:
            later.append(action)
            continue
        if not _apply_action(plan.files, action):
            names = action.get("source") or action.get("target") or ""
            plan.skipped.append(f"line {line}: {action.get('op')} {names} (names no staged file)")
    return later


#: The file actions the agent runs as a step, and the path keys each takes.
_FILE_ACTION_PATHS = {"mkdir": ("target",), "remove": ("target",), "copy": ("source", "target")}


def _file_action_step(action: dict[str, Any], index: int, plan: CasePlan) -> None:
    """A recipe file action after a step, as an agent step; listed in `skipped` when it is not one.

    Only a path in a folder a solver reads is sent: the agent refuses any other top folder anyway,
    and refusing it here keeps the job from failing at create.
    """
    op = str(action.get("op") or "")
    line = int(action.get("line") or 0)
    keys = _FILE_ACTION_PATHS.get(op)
    paths = {key: str(action.get(key) or "").strip("/") for key in ("source", "target")}
    what = f"line {line}: {op} {paths['source'] or paths['target']}"
    if keys is None or any(not paths[key] for key in keys):
        plan.skipped.append(f"{what} (not a file action this version runs)")
        return
    if any(not _TOP_RE.match(paths[key].split("/", maxsplit=1)[0]) for key in keys):
        plan.skipped.append(f"{what} (outside the folders a solver reads)")
        return
    plan.steps.append(
        {
            "name": f"a{index}_{op}"[:32],
            "argv": [],
            "timeout_s": FILE_ACTION_TIMEOUT_S,
            "action": {"op": op, **{key: paths[key] for key in keys}},
        }
    )


#: The utilities the agent runs over MPI for an imported case (its `EXTERNAL_MPI`).
MPI_UTILITIES = frozenset({"foamRun", "foamMultiRun", "snappyHexMesh", "checkMesh"})


def _parallel_mode(  # noqa: PLR0911 - one return per rule, in the docstring's order
    steps: list[dict[str, Any]], cores: int, regions: bool
) -> tuple[str, str]:
    """How a run on ``cores`` goes: `(mode, why)`; mode `recipe`, `solver` or `serial`.

    * `recipe` - the Allrun decomposes and runs steps in parallel itself: its decomposePar and
      reconstructPar stay and each parallel step runs on ``cores`` MPI ranks.
    * `solver` - a serial recipe with one `foamRun` solve and one region: Stokes decomposes
      before it, runs it on ``cores`` ranks and reconstructs after it.
    * `serial` - everything on one rank (one core, or a recipe neither rule covers); ``why`` says
      which, for `adapted`.
    """
    if cores < 2:
        return "serial", ""
    utilities = [str(step.get("utility") or "") for step in steps]
    parallel = [str(step.get("utility") or "") for step in steps if step.get("parallel")]
    if "decomposePar" in utilities or parallel:
        unsupported = sorted({u for u in parallel if u not in MPI_UTILITIES})
        if unsupported:
            return "serial", f"{', '.join(unsupported)} cannot run over MPI here"
        if "reconstructParMesh" in utilities:
            return "serial", "reconstructParMesh is not run by this version"
        return "recipe", ""
    solvers = [u for u in utilities if u in SOLVERS]
    if regions:
        return "serial", "a multi-region case is not decomposed by this version"
    if solvers != ["foamRun"]:
        return "serial", "only a single foamRun solve is decomposed by this version"
    return "solver", ""


def _build_steps(
    steps: list[dict[str, Any]],
    later: list[dict[str, Any]],
    plan: CasePlan,
    *,
    cores: int = 1,
    regions: bool = False,
) -> None:  # Any: JSON
    """The agent's steps in the recipe's order: programs and file actions, by source line.

    The residual parser goes on the solver steps. With ``cores`` above one the solve runs over MPI
    when `_parallel_mode` allows it, with a `decomposeParDict` for that many subdomains.
    """
    mode, why = _parallel_mode(steps, cores, regions)
    if why:
        plan.adapted.append(f"runs on one core: {why}")
    if mode != "serial":
        from stokes.compile.parallel import decompose_par_dict

        plan.files["system/decomposeParDict"] = decompose_par_dict(cores).encode("utf-8")
        plan.adapted.append(f"decomposeParDict written for {cores} cores")
    end_time = _end_time(plan.files)
    ordered: list[tuple[int, int, dict[str, Any]]] = [
        (int(step.get("line") or 0), 0, step) for step in steps
    ] + [(int(action.get("line") or 0), 1, action) for action in later]
    for _, kind, item in sorted(ordered, key=lambda entry: (entry[0], entry[1])):
        if kind == 1:
            _file_action_step(item, len(plan.steps) + 1, plan)
            continue
        utility = str(item.get("utility") or "")
        argv = [str(arg) for arg in item.get("argv") or []]
        if not utility or not argv:
            continue
        if utility in PARALLEL_ONLY and mode != "recipe":
            plan.adapted.append(f"{utility} left out: this run is on one core")
            continue
        if item.get("parallel") and mode != "recipe":
            plan.adapted.append(f"{utility} runs on one core instead of in parallel")
        ranks = 1
        if (mode == "recipe" and item.get("parallel")) or (mode == "solver" and utility in SOLVERS):
            ranks = cores
        if mode == "solver" and ranks > 1:
            plan.steps.append(_plain_step(len(plan.steps) + 1, ["decomposePar", "-force"]))
        built: dict[str, Any] = {
            "name": _step_name(len(plan.steps) + 1, utility),
            "argv": argv,
            "timeout_s": STEP_TIMEOUT_S,
        }
        if ranks > 1:
            built["ranks"] = ranks
        if utility in SOLVERS:
            built["parse"] = "foamRun"
            built["parse_params"] = {"end_time": end_time}
        plan.steps.append(built)
        if mode == "solver" and ranks > 1:
            plan.steps.append(_plain_step(len(plan.steps) + 1, ["reconstructPar"]))


def _plain_step(index: int, argv: list[str]) -> dict[str, Any]:  # Any: JSON
    """A serial program step Stokes adds around a decomposed solve."""
    return {"name": _step_name(index, argv[0]), "argv": argv, "timeout_s": STEP_TIMEOUT_S}


#: The recipe stage `mesh_only` keeps: the steps that build the mesh (blockMesh, snappyHexMesh,
#: createZones, ...), as `stokes.cases.recipe` classifies them.
MESH_STAGE = "mesh"


def plan_case(
    project_dir: Path, node: Node, *, mesh_only: bool = False, cores: int = 1
) -> CasePlan:
    """The files and steps of one run of imported case ``node``. Raises `ExternalRunInvalid`.

    With ``mesh_only`` only the recipe's mesh-stage steps run, and the file actions written
    between them: what the Mesh step's Generate does for a case that has no CAD, so its mesh can
    be built and looked at before anything is solved. ``cores`` is the solve's MPI rank count
    (`_parallel_mode`); a mesh-only run is serial.
    """
    recipe = node.params.get("recipe") or {}
    steps = [step for step in recipe.get("steps") or [] if isinstance(step, dict)]
    if mesh_only:
        steps = [step for step in steps if step.get("stage") == MESH_STAGE]
        if not steps:
            raise ExternalRunInvalid(
                f"{node.path} has no mesh steps: its mesh comes with the case "
                "(constant/polyMesh), so there is nothing to generate",
                code="run.external_no_mesh_steps",
            )
    if not steps:
        raise ExternalRunInvalid(f"{node.path} has no steps to run", code="run.external_no_steps")
    if any(step.get("folder") for step in steps):
        raise ExternalRunInvalid(
            f"{node.path} runs steps in sub-cases, which this version cannot stage",
            code="run.external_subcase",
        )
    plan = CasePlan()
    _stage_files(project_dir, node, plan)
    first_line = min(int(step.get("line") or 0) for step in steps)
    later = _apply_actions(list(recipe.get("actions") or []), first_line, plan)
    if mesh_only:
        last_line = max(int(step.get("line") or 0) for step in steps)
        later = [action for action in later if int(action.get("line") or 0) <= last_line]
    regions = bool(node.params.get("regions"))
    _build_steps(steps, later, plan, cores=1 if mesh_only else cores, regions=regions)
    if not plan.steps:
        raise ExternalRunInvalid(f"{node.path} has no steps to run", code="run.external_no_steps")
    return plan


#: Libraries a case needs that the native Windows build's solver modules do not load on their own:
#: the file whose presence says the case needs it, and the library's file name as `libs` takes it.
WINDOWS_LIBS: tuple[tuple[str, str], ...] = (
    ("system/fvConstraints", "libfvConstraints.dll"),
    ("constant/fvModels", "libfvModels.dll"),
)
#: The generic patch fields, for a run that decomposes or reconstructs (`add_windows_libs`).
GENERIC_FIELDS_LIB = "libgenericFvFields.dll"
_LIBS_ENTRY = re.compile(rb"^\s*libs\s*\(([^)]*)\)\s*;", re.MULTILINE)


def add_windows_libs(plan: CasePlan, runner: Any) -> None:
    """On a Windows (FoamWin) host, name the constraint and model libraries in `controlDict`.

    **Measured in the tutorial sweep (2026-11-01):** `squareBend` and `channel395` died at once
    with `Unknown constraint type limitPressure … Table of fvConstraints is empty`. On the MinGW
    build the solver module links `libfvConstraints` but the DLL is never loaded, so its types
    never register; with `libs ("libfvConstraints.dll")` in the controlDict the same case ran.
    Only a library the probe lists is added (the agent's lint refuses one the install lacks, and
    FoamWin ships no `libfvModels.dll` at the time of writing), and only when the case has the
    file that needs it. Off Windows nothing changes. A probe that cannot be read changes nothing.
    """
    try:
        record = runner.call("host.probe", timeout=30.0)
    except Exception:
        return
    if not isinstance(record, dict) or record.get("system") != "Windows":
        return
    have = {str(name) for name in record.get("libraries") or []}
    wanted = [lib for path, lib in WINDOWS_LIBS if path in plan.files and lib[3:-4] in have]
    # **decomposePar and reconstructPar read every field in a time folder**, including ones the
    # solve never uses (pitzDaily's `0/v2` with `v2WallFunction`): measured on FoamWin, decomposePar
    # stopped at "Unknown patchField type v2WallFunction". OpenFOAM reads a type it does not know
    # through the generic patch fields, whose library the Windows build does not load on its own.
    splits = any(str((step.get("argv") or [""])[0]) in PARALLEL_ONLY for step in plan.steps)
    if splits and GENERIC_FIELDS_LIB[3:-4] in have:
        wanted.append(GENERIC_FIELDS_LIB)
    control = plan.files.get("system/controlDict")
    if not wanted or control is None:
        return
    present = _LIBS_ENTRY.search(control)
    if present is not None:
        names = present.group(1).decode("utf-8", errors="ignore").split()
        missing = [lib for lib in wanted if lib not in {n.strip('"') for n in names}]
        if not missing:
            return
        entry = (
            b"libs ("
            + b" ".join(name.encode() for name in [*names, *(f'"{m}"' for m in missing)])
            + b");"
        )
        plan.files["system/controlDict"] = (
            control[: present.start()] + entry + control[present.end() :]
        )
    else:
        plan.files["system/controlDict"] = (
            control.rstrip(b"\r\n")
            + b"\n\nlibs ("
            + b" ".join(f'"{lib}"'.encode() for lib in wanted)
            + b");\n"
        )
    plan.adapted.append(f"libs added for the Windows build: {', '.join(wanted)}")


class ExternalCaseExecutor(FoamRunExecutor):
    """The executor of `of14.external` runs: an imported case staged whole, as an external job.

    Collection, events and re-attach are the parent's: a job is a job once it runs. Its monitor
    files are not read (the parent's monitor paths are those of Stokes' own writer), so `monitors`
    stays empty until T-311 reads a case's `postProcessing/` generically.
    """

    name = ENGINE

    def start_case(
        self,
        *,
        run: RunRecord,
        node: Node,
        project_dir: Path,
        runner: Any,
        engine: str | None,
        mesh_only: bool = False,
        cores: int = 1,
    ) -> dict[str, Any]:
        """Plan, stage under the `external` profile, subscribe and start one run."""
        plan = plan_case(project_dir, node, mesh_only=mesh_only, cores=cores)
        add_windows_libs(plan, runner)
        created = staging.stage(
            runner.call,
            dict(plan.files),
            steps=plan.steps,
            install=engine,
            label=run.id,
            profile=PROFILE,
            timeout=300.0,
        )
        job = str(created["job"])
        self._store.bind_job(run.id, job)
        none: Any = None  # the parent's monitor snapshot: an imported case has none
        runner.subscribe(lambda name, data: self._on_event(run.id, job, name, data, none))
        runner.call("job.start", job=job)
        return {
            "job": job,
            "steps": [step["name"] for step in plan.steps],
            "files": len(plan.files),
            "adapted": plan.adapted,
            "skipped": plan.skipped,
        }
