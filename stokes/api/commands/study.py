"""study.* commands (ADR-0016): parametric studies over the project's parameters.

Definitions are nodes. ``study.create`` adds ``/studies/<name>`` with its ``variables``,
``responses``, ``objectives`` and ``constraints`` groups as one undo step; ``study.add_variable``,
``study.add_response``, ``study.set_objective`` and ``study.add_constraint`` add (or, for an
objective, change) one node each. All of them are undoable, and ``node.*`` and ``parameter.set``
work on these nodes as on any other.

The design table is not a node: ``study.generate``, ``study.add_point``, ``study.update`` and
``study.clear`` change the session's ``StudyStore`` (``session.extensions["study"]``) and the
files under ``<name>.stokes/studies/<study_id>/`` (``stokes.explore.study_table``). They are
journaled, and ``history.undo`` does not revert them (ADR-0016 point 9). The study's ``status``
parameter follows its table and is set outside the undo stack.

``study.update`` evaluates points with the ``function`` engine, the only engine until M3, and
returns when every point has finished. It runs as one batch with a cancel token and per-point
callbacks (``stokes.explore.evaluate``), so a run-queue evaluator can take the engine's place on a
worker thread without changing these commands or their events.

Events::

    node.added {node_id, path}, node.changed {node_id, path, param}
        definition nodes, and the study's status (param "status")
    study.generated {study, path, points}      a new table; points is its size
    study.point {study, index, status, outputs, error}
        one point changed: pending (added or cleared), running, done (outputs: SI values by
        response name) or failed (error says why)
    study.updated {study, path, evaluated, done, failed, pending, cancelled, status}
    study.cleared {study, path, points, removed}

Contract for clients: ``docs/engineering/study-schema.md``.

Size: about 860 lines in one module on purpose. The handlers are thin (the logic is in
``stokes.explore.definition``, ``authoring``, ``study_store``, ``engine_function`` and
``study_numerics``), but each declares its parameters, and the whole ``study.*`` surface in one
file is what the package imports and what a review compares with the contract.

Import rule: ``stokes.explore`` modules are imported inside the handlers (numpy only by those
that sample or sort), never when the registry loads; ``stokes.explore.model`` is already loaded
with the node types.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.explore.model import DIRECTIONS, DOMAINS, OPERATORS, SAMPLERS, STUDY_TYPES
from stokes.model import SetParam

if TYPE_CHECKING:
    from stokes.api.session import Session
    from stokes.explore.study_store import StudyStore
    from stokes.explore.study_table import PointRow, StudyTable, TableMeta
    from stokes.model import Node, Project

EXTENSION = "study"
_store_lock = threading.Lock()
_DIRECTION_ALIASES = {
    "min": "minimise",
    "minimize": "minimise",
    "max": "maximise",
    "maximize": "maximise",
}
_SUFFIX = {"<=": "max", ">=": "min", "==": "target"}

STUDY = ParamDef("study", "string", description="Study: id, path (/studies/sweep) or name.")
NODE_ID = ParamDef("node_id", "string", required=False, description="Reuse an id (journals).")
QUANTITY_HELP = "A number in SI units, quantity text such as '2 [m/s]', or an expression."
POINTS_HELP = '"all" (the default) or a list of point indices.'


# ---------------------------------------------------------------- helpers


def store_of(session: Session) -> StudyStore:
    """The session's study store, created on first use and kept in its extensions.

    It follows the project through the session's extension hooks (``project.new``, ``.open``,
    ``.save`` and ``.close`` call them); a Save as creates it when the project has studies
    (:func:`carry_on_save_as`), so their tables are carried whether or not a study command ran.
    """
    with _store_lock:
        found = session.extensions.get(EXTENSION)
        if found is not None:
            return cast("StudyStore", found)
        from stokes.explore.study_store import StudyStore

        service = StudyStore(session.project_path)
        session.extensions[EXTENSION] = service
    session.events.subscribe("history.changed", lambda e, d: _resync(session, service))
    return service


def carry_on_save_as(session: Session, project: Project) -> None:
    """``project.save`` to a new folder: make the study store when the project has studies.

    Their tables sit in the old folder, and in memory only once a study command has loaded
    them, so the store that carries them has to exist even when no such command has run. A
    project without studies has no tables, and its session pays no import for the study layer.
    """
    if any(node.type == "study" for node in project.nodes()):
        store_of(session)


def _resync(session: Session, service: StudyStore) -> None:
    """Undo and redo may bring a study node back with a stale status; its table decides."""
    project = session.project
    if project is None:
        return
    for node in project.nodes():
        if node.type == "study" and service.is_loaded(node.id):
            _set_status(session, project, node, service.status(node.id))


def _set_status(session: Session, project: Project, study: Node, status: str) -> None:
    if study.params.get("status") != status:
        project.set_param(study.id, "status", status)
        session.emit("node.changed", node_id=study.id, path=study.path, param="status")


def _sync(ctx: CommandContext, study: Node) -> str:
    status = store_of(ctx.session).status(study.id)
    _set_status(ctx.session, ctx.session.require_project(), study, status)
    return status


def _study(ctx: CommandContext, ref: str) -> tuple[Project, Node]:
    from stokes.explore.definition import find_study

    project = ctx.session.require_project()
    return project, find_study(project, ref)


def _meta(study: Node) -> TableMeta:
    """Table metadata carrying the columns the tree defines now."""
    from stokes.explore.definition import columns_of
    from stokes.explore.study_table import TableMeta

    variables, responses = columns_of(study)
    return TableMeta(type=str(study.params.get("type")), variables=variables, responses=responses)


def _indices(points: Any, what: str = "points") -> list[int] | None:  # Any: JSON value
    """``"all"`` (None) or a list of point indices."""
    if points is None or points == "all":
        return None
    if isinstance(points, list) and all(
        isinstance(p, int) and not isinstance(p, bool) for p in points
    ):
        return list(dict.fromkeys(points))
    raise ValidationError(f"{what} must be {POINTS_HELP}", details={"parameter": what})


def _add(
    ctx: CommandContext,
    study: Node,
    group: str,
    node_type: str,
    name: str,
    params: dict[str, Any],  # Any: JSON parameter values
    node_id: str | None,
) -> dict[str, Any]:  # Any: JSON node summary
    from stokes.explore.authoring import member_command

    cmd = member_command(study, group, node_type, name, params, node_id)
    result = ctx.session.undo.push(cmd, ctx.session.require_project())
    created: list[dict[str, Any]] = result if isinstance(result, list) else [result]
    for item in created:
        ctx.session.emit("node.added", node_id=item["id"], path=item["path"])
    summary = created[-1]
    ctx.record(name=name, node_id=summary["id"])
    return dict(summary)


def _taken(study: Node, name: str | None) -> None:
    from stokes.explore.authoring import taken_names
    from stokes.explore.definition import StudyDefinitionError

    if name is not None and name in taken_names(study):
        raise StudyDefinitionError(
            f"study {study.name} already has a variable or response named {name!r}",
            details={"name": name},
        )


def _point_event(ctx: CommandContext, study: Node, row: PointRow) -> None:
    ctx.session.emit(
        "study.point",
        study=study.id,
        index=row.index,
        status=row.status,
        outputs=dict(row.outputs),
        error=row.error,
    )


def _results(project: Project, study: Node, table: StudyTable, status: str) -> dict[str, Any]:
    from stokes.explore.definition import columns_of
    from stokes.explore.study_table import json_safe

    variables, responses = columns_of(study)
    meta = table.meta
    return {
        "study": study.id,
        "path": study.path,
        "name": study.name,
        "type": study.params.get("type"),
        "status": status,
        "sampler": meta.sampler,
        "seed": meta.seed,
        "design": json_safe(meta.design),
        "engine": meta.engine,
        "generated": meta.generated,
        "updated": meta.updated,
        "variables": [c.to_json(project.display_units) for c in variables],
        "responses": [c.to_json(project.display_units) for c in responses],
        "counts": table.counts(),
        "points": [r.to_json() for r in table.rows],
    }


# ---------------------------------------------------------------- definitions, undoable


@command(
    "study.create",
    params=(
        ParamDef("name", "string", description="Study name (letters, digits, underscores)."),
        ParamDef(
            "type",
            "string",
            required=False,
            default="sweep",
            description=f"One of {', '.join(STUDY_TYPES)}.",
        ),
        ParamDef("description", "string", required=False, default=""),
        ParamDef(
            "sampler",
            "string",
            required=False,
            description=f"One of {', '.join(SAMPLERS)}; default full_factorial for a sweep, "
            "latin_hypercube for a doe.",
        ),
        ParamDef("seed", "integer", required=False, description="Sampler seed (default 0)."),
        ParamDef(
            "count",
            "integer",
            required=False,
            description="Number of points, for the samplers that take one.",
        ),
        NODE_ID,
    ),
)
def study_create(
    ctx: CommandContext,
    name: str,
    type: str = "sweep",  # noqa: A002 - mirrors the study parameter
    description: str = "",
    sampler: str | None = None,
    seed: int | None = None,
    count: int | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Create a study under /studies with its variables, responses, objectives and constraints
    groups, as one undo step."""
    from stokes.explore.authoring import create_command, ensure_studies_group

    if type not in STUDY_TYPES:
        raise ValidationError(f"type must be one of {', '.join(STUDY_TYPES)}; got {type!r}")
    project = ctx.session.require_project()
    params: dict[str, Any] = {  # Any: JSON parameter values
        "type": type,
        "description": description,
        "sampler": sampler or ("latin_hypercube" if type == "doe" else "full_factorial"),
    }
    if seed is not None:
        params["seed"] = seed
    if count is not None:
        params["count"] = count
    group = ensure_studies_group(project)
    if group is not None:
        ctx.session.emit("node.added", node_id=group.id, path=group.path)
    results = ctx.session.undo.push(create_command(name, params, node_id), project)
    ctx.record(node_id=results[0]["id"])
    for item in results:
        ctx.session.emit("node.added", node_id=item["id"], path=item["path"])
    study = project.get(results[0]["id"])
    return {**study.summary(), "groups": {c.name: c.id for c in study.children}}


@command(
    "study.add_variable",
    params=(
        STUDY,
        ParamDef(
            "parameter",
            "string",
            description="The quantity to vary: U_in, /physics/parameters/U_in or "
            "regions.fluid.inlet.velocity.",
        ),
        ParamDef(
            "domain",
            "string",
            required=False,
            default="values",
            description=f"One of {', '.join(DOMAINS)}.",
        ),
        ParamDef(
            "values",
            "array",
            required=False,
            description="Levels (values, categorical): SI numbers or quantity text.",
        ),
        ParamDef(
            "labels",
            "array",
            required=False,
            description="Category labels, one per value (categorical).",
        ),
        ParamDef("min", "any", required=False, description="Lower bound. " + QUANTITY_HELP),
        ParamDef("max", "any", required=False, description="Upper bound. " + QUANTITY_HELP),
        ParamDef(
            "count",
            "integer",
            required=False,
            description="Levels of a range, or of a distribution in a full factorial.",
        ),
        ParamDef(
            "step", "any", required=False, description="Range level spacing. " + QUANTITY_HELP
        ),
        ParamDef("mean", "any", required=False, description="Normal mean. " + QUANTITY_HELP),
        ParamDef("std", "any", required=False, description="Normal deviation. " + QUANTITY_HELP),
        ParamDef("name", "string", required=False, description="Default: the parameter's name."),
        NODE_ID,
    ),
)
def study_add_variable(
    ctx: CommandContext,
    study: str,
    parameter: str,
    domain: str = "values",
    values: list[Any] | None = None,  # Any: numbers or quantity text, checked below
    labels: list[Any] | None = None,  # Any: strings, checked below
    min: Any = None,  # noqa: A002 - Any: a quantity in any accepted form
    max: Any = None,  # noqa: A002 - Any: a quantity in any accepted form
    count: int | None = None,
    step: Any = None,  # Any: a quantity in any accepted form
    mean: Any = None,  # Any: a quantity in any accepted form
    std: Any = None,  # Any: a quantity in any accepted form
    name: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Vary a parameter or node quantity: a value list, a range, a distribution or categories.
    The variable takes the quantity's dimension; its bounds keep any expression."""
    from stokes.explore import authoring as a
    from stokes.explore import definition as d

    project, node = _study(ctx, study)
    target, pname = d.resolve_target(project, parameter)
    dim = d.quantity_dimension(project, target, pname)
    for other in d.members(node, "variables", "variable"):
        try:
            same = d.resolve_target(project, str(other.params.get("parameter") or ""))
        except d.StudyDefinitionError:
            continue
        if same[0] is target and same[1] == pname:
            raise d.StudyDefinitionError(
                f"{parameter} is already varied by {other.name}", details={"variable": other.path}
            )
    if labels is not None and not all(isinstance(x, str) and "," not in x for x in labels):
        raise ValidationError("labels must be strings without commas")
    names = [str(x).strip() for x in labels or []]
    levels = a.si_values(project, values or [], dim)
    raw = {"min": min, "max": max, "step": step, "mean": mean, "std": std}
    si = {k: a.si_value(project, v, dim, k) for k, v in raw.items() if v is not None}
    a.check_domain(
        domain,
        values=levels,
        labels=names,
        low=si.get("min", 0.0),
        high=si.get("max", 0.0),
        count=count or 0,
        step=si.get("step", 0.0),
        std=si.get("std", 0.0),
    )
    params: dict[str, Any] = {"parameter": parameter, "dimension": dim.name, "domain": domain}
    if levels:
        params["values"] = levels
    if names:
        params["labels"] = ", ".join(names)
    if count is not None:
        params["count"] = count
    # Every quantity explicitly, so each is stored in the variable's dimension (a default would
    # be filed before the dimension is set).
    params.update({k: 0.0 if v is None else v for k, v in raw.items()})
    _taken(node, name)
    default = target.name if target.type == "parameter" else f"{target.name}_{pname}"
    final = name or a.unique_name(a.identifier(default, "variable"), a.taken_names(node))
    return _add(ctx, node, "variables", "variable", final, params, node_id)


@command(
    "study.add_response",
    params=(
        STUDY,
        ParamDef(
            "expression",
            "string",
            description="What each point records: an expression over parameters, e.g. dp_dyn.",
        ),
        ParamDef("label", "string", required=False, default="", description="Display name."),
        ParamDef(
            "dimension",
            "string",
            required=False,
            description="Dimension of the result; inferred from the expression when omitted.",
        ),
        ParamDef(
            "name",
            "string",
            required=False,
            description="Default: the referenced name for a bare reference, else from the label.",
        ),
        NODE_ID,
    ),
)
def study_add_response(
    ctx: CommandContext,
    study: str,
    expression: str,
    label: str = "",
    dimension: str | None = None,
    name: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Record a scalar for every design point; the expression is checked against the base case
    now, unit-checked against the dimension when one is given."""
    from stokes.explore import authoring as a
    from stokes.units import dimension as named_dimension

    project, node = _study(ctx, study)
    text = expression.strip()
    if not text:
        raise ValidationError("a response needs an expression")
    if dimension is not None:
        dim = named_dimension(dimension)
        a.evaluate_text(text, project.resolve, dim)
    else:
        dim = a.dimension_of(a.evaluate_text(text, project.resolve), f"response {text!r}")
    _taken(node, name)
    if name is None:
        if a.is_reference(text):
            default = text.split(".")[-1]
        else:
            default = a.identifier(label, "response") if label else "response"
        name = a.unique_name(default, a.taken_names(node))
    params = {"expression": text, "dimension": dim.name, "label": label}
    return _add(ctx, node, "responses", "response", name, params, node_id)


@command(
    "study.set_objective",
    params=(
        STUDY,
        ParamDef("response", "string", description="A response of the study: name, id or path."),
        ParamDef(
            "direction",
            "string",
            required=False,
            default="minimise",
            description=f"One of {', '.join(DIRECTIONS)} (min and max also accepted).",
        ),
        ParamDef("name", "string", required=False, description="Default: the response's name."),
        NODE_ID,
    ),
)
def study_set_objective(
    ctx: CommandContext,
    study: str,
    response: str,
    direction: str = "minimise",
    name: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Minimise or maximise a response; an objective on the same response changes direction."""
    from stokes.explore import authoring as a
    from stokes.explore import definition as d

    project, node = _study(ctx, study)
    direction = _DIRECTION_ALIASES.get(direction.lower(), direction.lower())
    if direction not in DIRECTIONS:
        raise ValidationError(f"direction must be one of {', '.join(DIRECTIONS)}")
    holder = node.child("responses")
    target = holder.child(response) if holder is not None else None
    if target is None:
        target = project.find(response)
    if target is None or target.type != "response" or target.parent is not holder:
        raise d.StudyDefinitionError(
            f"study {node.name} has no response {response!r}", details={"response": response}
        )
    for objective in d.members(node, "objectives", "objective"):
        if objective.params.get("response") == target.id:
            cmd = SetParam(objective.id, "direction", direction)
            result = ctx.session.undo.push(cmd, project)
            ctx.session.emit(
                "node.changed", node_id=objective.id, path=objective.path, param="direction"
            )
            return dict(result)
    taken = {c.name for c in a.members_any(node, "objectives")}
    final = name or a.unique_name(target.name, taken)
    params = {"response": target.id, "direction": direction}
    return _add(ctx, node, "objectives", "objective", final, params, node_id)


@command(
    "study.add_constraint",
    params=(
        STUDY,
        ParamDef(
            "expression",
            "string",
            description="Over the study's responses by name and the parameters, e.g. dp_dyn.",
        ),
        ParamDef("operator", "string", description=f"One of {', '.join(OPERATORS)}."),
        ParamDef("limit", "any", description="The bound. " + QUANTITY_HELP),
        ParamDef(
            "dimension",
            "string",
            required=False,
            description="Dimension of the expression; inferred when omitted.",
        ),
        ParamDef("name", "string", required=False, description="Default: e.g. dp_dyn_max."),
        NODE_ID,
    ),
)
def study_add_constraint(
    ctx: CommandContext,
    study: str,
    expression: str,
    operator: str,
    limit: Any,  # Any: a quantity in any accepted form
    dimension: str | None = None,
    name: str | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    """A condition every feasible point satisfies: expression operator limit."""
    from stokes.explore import authoring as a
    from stokes.units import dimension as named_dimension

    project, node = _study(ctx, study)
    if operator not in OPERATORS:
        raise ValidationError(f"operator must be one of {', '.join(OPERATORS)}; got {operator!r}")
    text = expression.strip()
    if not text:
        raise ValidationError("a constraint needs an expression")
    resolver = a.response_resolver(project, node)
    if dimension is not None:
        dim = named_dimension(dimension)
        a.evaluate_text(text, resolver, dim)
    else:
        dim = a.dimension_of(a.evaluate_text(text, resolver), f"constraint {text!r}")
    a.si_value(project, limit, dim, "limit")
    taken = {c.name for c in a.members_any(node, "constraints")}
    if name is None:
        base = (
            f"{text.split('.')[-1]}_{_SUFFIX[operator]}" if a.is_reference(text) else "constraint"
        )
        name = a.unique_name(base, taken)
    params = {"expression": text, "dimension": dim.name, "operator": operator, "limit": limit}
    return _add(ctx, node, "constraints", "constraint", name, params, node_id)


# ---------------------------------------------------------------- the design table, journaled


@command("study.generate", params=(STUDY,))
def study_generate(ctx: CommandContext, study: str) -> dict[str, Any]:
    """Fill the design table: the full factorial for a sweep, the sampler for a doe (seeded).
    Replaces the table; not undoable."""
    from stokes.explore.definition import read_definition
    from stokes.explore.study_numerics import design_points
    from stokes.explore.study_table import MAX_POINTS, PointRow, utc_now

    project, node = _study(ctx, study)
    store = store_of(ctx.session)
    definition = read_definition(project, node, base=False)
    points, design = design_points(definition, max_points=MAX_POINTS)
    meta = _meta(node)
    meta.sampler = str(design.get("sampler") or definition.sampler)
    seed = design.get("seed")
    meta.seed = seed if isinstance(seed, int) else None
    meta.count = len(points)
    meta.design = dict(design)
    meta.generated = utc_now()
    store.replace(node.id, meta, [PointRow(i, p) for i, p in enumerate(points)])
    status = _sync(ctx, node)
    ctx.session.emit("study.generated", study=node.id, path=node.path, points=len(points))
    return {
        "study": node.id,
        "path": node.path,
        "points": len(points),
        "sampler": meta.sampler,
        "seed": meta.seed,
        "status": status,
    }


@command(
    "study.add_point",
    params=(
        STUDY,
        ParamDef(
            "inputs",
            "object",
            required=False,
            description="Values by variable name (SI numbers or quantity text); a variable left "
            "out takes the base case's value.",
        ),
    ),
)
def study_add_point(
    ctx: CommandContext, study: str, inputs: dict[str, Any] | None = None
) -> dict[str, Any]:  # Any: JSON
    """Add a design point by hand (a hand-edited sweep); it starts pending."""
    from stokes.explore import authoring as a
    from stokes.explore import definition as d
    from stokes.units import dimension as named_dimension
    from stokes.units import to_si

    project, node = _study(ctx, study)
    definition = d.read_definition(project, node, base=False)
    if not definition.variables:
        raise d.StudyDefinitionError(f"study {node.name} has no variables")
    given = dict(inputs or {})
    unknown = sorted(set(given) - {v.name for v in definition.variables})
    if unknown:
        raise ValidationError(f"study {node.name} has no variables {unknown}")
    values: dict[str, float] = {}
    for variable in definition.variables:
        dim = named_dimension(variable.dimension)
        if variable.name in given:
            values[variable.name] = a.si_value(project, given[variable.name], dim, variable.name)
        else:
            base = project.value_of(variable.target_id, variable.target_param)
            values[variable.name] = to_si(base, dim)
    row = store_of(ctx.session).add_point(node.id, values, _meta(node))
    _sync(ctx, node)
    _point_event(ctx, node, row)
    return row.to_json()


@command(
    "study.update",
    params=(
        STUDY,
        ParamDef("points", "any", required=False, default="all", description=POINTS_HELP),
    ),
)
def study_update(
    ctx: CommandContext, study: str, points: Any = "all"
) -> dict[str, Any]:  # Any: JSON
    """Evaluate points with the function engine: with "all", every point not yet done (or
    lacking a response); streams study.point events, then study.updated."""
    from stokes.explore.definition import StudyDefinitionError, read_definition
    from stokes.explore.engine_function import FunctionEngine
    from stokes.explore.evaluate import PointRequest, PointResult

    selection = _indices(points)
    project, node = _study(ctx, study)
    store = store_of(ctx.session)
    definition = read_definition(project, node)
    if not definition.responses:
        raise StudyDefinitionError(
            f"study {node.name} has no responses; add one with study.add_response"
        )
    rows = store.select(node.id, selection, [r.name for r in definition.responses])
    token = store.begin(node.id)
    engine = FunctionEngine()
    finished: list[dict[str, Any]] = []  # Any: JSON rows

    def started(index: int) -> None:
        store.start_point(node.id, index)
        ctx.session.emit(
            "study.point", study=node.id, index=index, status="running", outputs={}, error=None
        )

    def ended(result: PointResult) -> None:
        row = store.record(node.id, result)
        finished.append(row.to_json())
        _point_event(ctx, node, row)

    try:
        _sync(ctx, node)
        engine.evaluate(
            definition,
            [PointRequest(r.index, r.inputs) for r in rows],
            cancel=token,
            on_start=started,
            on_result=ended,
        )
    finally:
        table = store.end(node.id, _meta(node), engine.name)
        status = _sync(ctx, node)
        summary = {
            "study": node.id,
            "path": node.path,
            "evaluated": len(finished),
            "done": sum(1 for r in finished if r["status"] == "done"),
            "failed": sum(1 for r in finished if r["status"] == "failed"),
            "pending": table.counts()["pending"],
            "cancelled": token.cancelled,
            "status": status,
        }
        ctx.session.emit("study.updated", **summary)
    return {**summary, "points": finished}


@command(
    "study.cancel",
    params=(
        ParamDef(
            "study", "string", required=False, description="Study to stop; default: every one."
        ),
    ),
    mutates=False,
)
def study_cancel(ctx: CommandContext, study: str | None = None) -> dict[str, Any]:
    """Stop running updates after their current point; the points not reached stay pending."""
    store = store_of(ctx.session)
    if study is None:
        return {"cancelled": store.cancel()}
    _, node = _study(ctx, study)
    return {"cancelled": store.cancel(node.id)}


@command(
    "study.clear",
    params=(
        STUDY,
        ParamDef("points", "any", required=False, description=POINTS_HELP),
        ParamDef(
            "remove",
            "boolean",
            required=False,
            default=False,
            description="Remove the points instead of resetting them to pending.",
        ),
    ),
)
def study_clear(
    ctx: CommandContext, study: str, points: Any = None, remove: bool = False
) -> dict[str, Any]:  # Any: JSON
    """Reset points to pending, dropping their outputs (or remove them); not undoable."""
    selection = _indices(points)
    _, node = _study(ctx, study)
    touched = store_of(ctx.session).clear(node.id, selection, remove=remove, meta=_meta(node))
    status = _sync(ctx, node)
    ctx.session.emit("study.cleared", study=node.id, path=node.path, points=touched, removed=remove)
    return {
        "study": node.id,
        "path": node.path,
        "points": touched,
        "removed": remove,
        "status": status,
    }


# ---------------------------------------------------------------- queries


@command(
    "study.results",
    params=(
        STUDY,
        ParamDef("format", "string", required=False, default="json", description="json or csv."),
    ),
    mutates=False,
)
def study_results(
    ctx: CommandContext,
    study: str,
    format: str = "json",  # noqa: A002 - the command's parameter name
) -> dict[str, Any]:
    """The design table: columns with units and every point (json), or CSV text (csv)."""
    from stokes.explore.definition import columns_of
    from stokes.explore.study_table import csv_text

    if format not in ("json", "csv"):
        raise ValidationError(f"format must be json or csv; got {format!r}")
    project, node = _study(ctx, study)
    store = store_of(ctx.session)
    table = store.snapshot(node.id)
    if format == "csv":
        variables, responses = columns_of(node)
        text = csv_text(table.rows, variables, responses)
        return {"study": node.id, "path": node.path, "format": "csv", "text": text}
    return _results(project, node, table, store.status(node.id))


@command("study.pareto", params=(STUDY,), mutates=False)
def study_pareto(ctx: CommandContext, study: str) -> dict[str, Any]:
    """The Pareto front of the evaluated points under the study's objectives and constraints:
    every evaluated point's rank, crowding distance and feasibility."""
    from stokes.explore.definition import read_definition
    from stokes.explore.engine_function import constraint_values
    from stokes.explore.evaluate import PointRequest
    from stokes.explore.study_numerics import pareto

    project, node = _study(ctx, study)
    probe = read_definition(project, node, base=False)
    definition = read_definition(project, node) if probe.constraints else probe
    table = store_of(ctx.session).snapshot(node.id)
    done = [r for r in table.rows if r.status == "done"]
    values = (
        constraint_values(definition, [(PointRequest(r.index, r.inputs), r.outputs) for r in done])
        if definition.constraints
        else {}
    )
    return {"study": node.id, "path": node.path, **pareto(definition, table.rows, values)}


@command(
    "study.export",
    params=(
        STUDY,
        ParamDef("path", "string", description="File to write, ending in .csv or .json."),
        ParamDef(
            "format",
            "string",
            required=False,
            description="csv or json; default: from the file's suffix.",
        ),
    ),
    mutates=False,
)
def study_export(
    ctx: CommandContext,
    study: str,
    path: str,
    format: str | None = None,  # noqa: A002 - mirrors study.results
) -> dict[str, Any]:
    """Write the design table to a file of the user's choosing (CSV in SI units, or the JSON of
    study.results)."""
    from stokes.explore.study_table import write_text

    target = Path(path).expanduser().resolve()
    suffix = target.suffix.lower().lstrip(".")
    chosen = format or suffix
    if chosen not in ("csv", "json") or suffix != chosen:
        raise ValidationError(
            f"export to a .csv or .json file whose suffix matches the format; got {target.name}",
            details={"path": str(target)},
        )
    if target.exists() and not target.is_file():
        raise ValidationError(f"{target} exists and is not a file", details={"path": str(target)})
    if not target.parent.is_dir():
        raise ValidationError(f"no folder {target.parent}", details={"path": str(target)})
    folder = ctx.session.project_path
    if folder is not None and target.is_relative_to(folder.resolve()):
        # The project folder belongs to the model and the store (project.json, the tables).
        raise ValidationError(
            f"export outside the project folder {folder}", details={"path": str(target)}
        )
    reply = study_results(ctx, study, chosen)
    if chosen == "csv":
        text = str(reply["text"])
    else:
        text = json.dumps(reply, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    write_text(target, text)
    return {"study": reply["study"], "file": str(target), "format": chosen}
