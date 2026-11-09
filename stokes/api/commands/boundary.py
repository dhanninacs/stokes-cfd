"""boundary.* commands (ADR-0022 decision 3): boundary conditions on tags.

A ``boundary`` node at ``/regions/<region>/boundaries/<name>`` carries a condition kind from the
catalogue (:mod:`stokes.physics.conditions.catalogue`), the values of that kind's parameters and
a reference to the boundary-kind tag whose faces it covers (contract:
``docs/engineering/setup-schema.md``).

``boundary.kinds`` returns the catalogue with each parameter's JSON schema, without a project.
``boundary.assign`` is the one atomic, replayable assign: onto a tag, an existing boundary
(re-kinded and re-valued: a second assign on a tag's boundary changes it, never duplicates it), a
boundary that is a plain group from before ADR-0022 (made typed in place, same id and name) or the
live pick as members (a new boundary-kind tag). It makes what is missing (the tags group, the tag,
the region with its tag and its ``boundaries`` group) and gives the tag the kind's colour, all in
one undo step. ``boundary.set`` changes the kind, values, tag, faces or name of a boundary in one
undo step; ``boundary.delete`` removes one, clearing partners that named it, and its tag on
request; ``boundary.list`` reports every boundary with its tag, primary value, findings and
derived status. Each command that changes the tree is one undo step and one journal record; the
ids it chose are recorded (``node_id``, ``tag_node_id``, ``region_node_id``) so a replay builds
the same tree.

Values are checked against the catalogue (builder notes of T-035): ``kind(id)`` refuses an unknown
kind and one not available yet; a value for a parameter the kind does not use, with the stored
values merged with the new ones, is refused; once the step has applied, each value given is read
back in SI and :func:`check_rule` refuses one that breaks its rule, which reverts the whole step.
Parameters left at their defaults are not checked here: a velocity inlet without a velocity is a
finding of ``setup.check`` (``setup.value_invalid``), not a refusal.

Events::

    node.added {node_id, path}           each node a command creates
    node.changed {node_id, path, param}  each parameter it sets (no param: a rename, a group typed)
    node.removed {node_id, path}         boundary.delete
    tag.changed {node_id, path}          a tag created, given faces or deleted
    boundary.changed {node_id, path}     once per boundary a command changes
    region.changed {node_id, path}       once per region a command creates or types

Errors: ``boundary.invalid_kind`` and ``boundary.invalid_value`` (:mod:`stokes.physics.conditions`,
``details.parameter`` names the value), ``tag.invalid`` (a target tag of another kind, members
that do not parse), ``model.referenced`` (``boundary.delete`` with ``delete_tag`` of a shared
tag), and ``validation``, ``model.unknown_node`` as everywhere.

Size: one module per command group, as the registry is organised: five commands, the checks of
kinds and values that ``boundary.set`` shares with ``boundary.assign``, and the list's reading of
tags, which the assign's result repeats.

Import rule: the condition catalogue, ``stokes.geometry.model`` and ``stokes.geometry.errors``
(standard library only) and the node registry at module level; the tag internals (numpy) inside
the handlers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.api.commands.setup_plan import (
    Plan,
    RegionRef,
    boundary_nodes,
    clear_references,
    default_region,
    find_region,
    is_legacy_boundary,
    lookup,
    members_text,
    plan_tag,
    plan_tags_group,
    ref_node,
    region_of,
    stored_members,
    use_region,
    users_of,
)
from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError, UnknownNode, ValidationError
from stokes.geometry.errors import SelectionError
from stokes.geometry.model import BOUNDARY_TYPE, TAG_TYPE, TAGS_PATH
from stokes.model import QuantityValue
from stokes.model.project import referenced_error
from stokes.model.schema import registry as node_types
from stokes.physics.conditions import InvalidKind, InvalidValue
from stokes.physics.conditions.catalogue import (
    BY_ID,
    KINDS,
    SWITCHES,
    ConditionSpec,
    ParamUse,
    check_rule,
)
from stokes.physics.conditions.catalogue import kind as catalogue_kind

if TYPE_CHECKING:
    from stokes.api.commands.selection import _Geometry
    from stokes.model import Node, Project

BOUNDARY = ParamDef(
    "boundary", "string", description="Boundary: id or path (/regions/fluid/boundaries/inlet)."
)
VALUES = ParamDef(
    "values",
    "object",
    required=False,
    description="Values of the kind's parameters, by name: quantity text ('2 [m/s]'), an SI "
    "number, an expression ('U_in * 1.5') or a JSON value; only parameters the kind uses with "
    "these values (boundary.kinds). null clears partner.",
)
NODE_ID = ParamDef(
    "node_id", "string", required=False, description="Journals: the boundary's id when made."
)
TARGET_HELP = (
    "What to assign to: a tag (id, path or name), a boundary (id or path; its kind and values "
    "change), a boundary that is a plain group from before typed boundaries (made typed, same id), "
    'or the live pick as members [{"part": <part id or path>, "faces": "all" or [ids and [first, '
    "last] ranges]}] (a new boundary-kind tag)."
)
KIND_LABELS = {"inlet": "inlet", "outlet": "outlet", "wall": "wall"}
"""The default names of new boundaries by family; a constraint or interface kind uses its id."""
NOT_VALUES = ("kind", "tag")
COMPONENTS = ("velocity_x", "velocity_y", "velocity_z")


# ---------------------------------------------------------------- the catalogue


def _kind_json(spec: ConditionSpec) -> dict[str, Any]:  # Any: JSON
    """A catalogue entry with the JSON schema of each parameter on ``boundary`` beside its use,
    so that a client renders a kind's fields without a second query."""
    boundary = node_types.get(BOUNDARY_TYPE)
    data = spec.to_json()
    for entry in data["params"]:
        entry["schema"] = boundary.param(entry["name"]).json_schema()
    return data


@command(
    "boundary.kinds",
    params=(
        ParamDef(
            "engine",
            "string",
            required=False,
            description="An engine id (solve.engines). Each kind then says whether that engine's "
            "case writer can express it. Defaults to the open project's engine; with neither, "
            "every kind counts as supported.",
        ),
    ),
    mutates=False,
    requires_project=False,
)
def boundary_kinds(
    ctx: CommandContext, engine: str | None = None
) -> list[dict[str, Any]]:  # Any: JSON
    """The condition kinds: family, label, tag colour, glyph, the solver fields each fixes, the
    physics its parameters need, whether it is available, and its parameters with their rules,
    their conditions (when) and their JSON schemas. ``supported`` and ``unsupported_reason`` say
    whether the engine can write the kind; a kind it cannot write is still listed, so a client
    can show it disabled rather than hide it."""
    from stokes.engines.catalogue import UNSUPPORTED_KIND_REASON

    supported = _engine_kinds(ctx, engine)
    rows = []
    for spec in KINDS:
        row = _kind_json(spec)
        ok = supported is None or spec.id in supported
        row["supported"] = ok
        row["unsupported_reason"] = None if ok else UNSUPPORTED_KIND_REASON
        rows.append(row)
    return rows


def _engine_kinds(ctx: CommandContext, engine: str | None) -> tuple[str, ...] | None:
    """The kinds the engine can express: the one asked for, else the open project's, else
    ``None`` (no filter). An unknown engine id is a ``solve.invalid`` refusal."""
    from stokes.engines.catalogue import engine as engine_spec
    from stokes.engines.catalogue import kinds_for
    from stokes.engines.model import SETTINGS_PATH

    if engine:
        return engine_spec(engine).kinds
    project = ctx.session.project
    settings = None if project is None else project.find(SETTINGS_PATH)
    if settings is None:
        return None
    return kinds_for(str(settings.params.get("engine") or ""))


# ---------------------------------------------------------------- kinds and values


def available(kind_id: str) -> ConditionSpec:
    """The catalogue entry of a kind a boundary can take now; ``boundary.invalid_kind`` for an
    unknown id and for a kind that arrives later (``interface``, M5)."""
    try:
        spec = catalogue_kind(kind_id)
    except InvalidKind as exc:
        exc.details.setdefault("parameter", "kind")
        raise
    if not spec.available:
        raise InvalidKind(
            f"{spec.label} arrives with {spec.arrives}; it cannot be chosen yet",
            details={"parameter": "kind", "kind": kind_id, "arrives": spec.arrives},
        )
    return spec


def uses_of(
    spec: ConditionSpec,
    stored: Mapping[str, Any],  # Any: the boundary's values as stored
    given: Mapping[str, Any],  # Any: the values a command sets
) -> dict[str, ParamUse]:
    """The use of each given value, with the stored values merged with the given ones (a switch
    such as ``direction`` given in the same call counts); ``boundary.invalid_value`` for a value
    the kind does not use with them."""
    for name in given:
        if name in NOT_VALUES:
            where = "kind" if name == "kind" else "target (boundary.assign) or tag (boundary.set)"
            raise InvalidValue(
                f"{name} is not a value: give it as the command's {where}",
                details={"parameter": name, "kind": spec.id},
            )
    merged = {**stored, **given}
    in_use = {use.name: use for use in spec.uses(merged)}
    for name in given:
        if name not in in_use:
            raise InvalidValue(
                _unused(spec, name, merged), details={"parameter": name, "kind": spec.id}
            )
    return {name: in_use[name] for name in given}


def _unused(spec: ConditionSpec, name: str, merged: Mapping[str, Any]) -> str:  # Any: values
    use = next((u for u in spec.params if u.name == name), None)
    what = f"a {spec.label[0].lower()}{spec.label[1:]}"
    if use is None:
        return f"{what} does not use {name}"
    needs = "; ".join(f"{s} {' or '.join(c)}" for s, c in use.when.items())
    has = ", ".join(f"{s} {merged.get(s, SWITCHES[s][0])}" for s in use.when)
    return f"{name} is used only with {needs}; this is {what} with {has}"


def _number(stored: Any) -> float | list[float] | None:  # Any: a stored parameter value
    """A stored value as :func:`check_rule` takes it (SI), or None for one it does not judge."""
    if isinstance(stored, QuantityValue):
        return stored.value
    if isinstance(stored, bool):
        return None
    if isinstance(stored, int | float):
        return float(stored)
    if isinstance(stored, list) and all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in stored
    ):
        return [float(v) for v in stored]
    return None


def rules_check(boundary_id: str, uses: Mapping[str, ParamUse]) -> Callable[[Project], None]:
    """The step's check: each value given, read back in SI once the step has applied (an
    expression evaluated), keeps its rule; ``boundary.invalid_value`` reverts the step."""

    def check(project: Project) -> None:
        node = project.get(boundary_id)
        for name, use in uses.items():
            value = _number(node.params.get(name))
            message = check_rule(use, value) if value is not None else None
            if message:
                raise InvalidValue(message, details={"parameter": name, "rule": use.rule})

    return check


def default_name(spec: ConditionSpec) -> str:
    """A new boundary's name before it is made unique: its family (inlet, outlet, wall), or the
    kind's id for a constraint or an interface (symmetry, cyclic, interface)."""
    return KIND_LABELS.get(spec.family, spec.id)


# ---------------------------------------------------------------- targets


def _target(project: Project, raw: Any) -> tuple[str, Node | None]:  # Any: JSON target
    """What ``target`` names: ``("members", None)``, or ``("tag" | "boundary" | "legacy",
    node)``."""
    if isinstance(raw, list):
        if not raw:
            raise ValidationError(
                "target is an empty list: pick faces, or give a tag or a boundary",
                details={"parameter": "target"},
            )
        return "members", None
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError(
            "target must be a tag or a boundary (id, path or tag name) or a list of members",
            details={"parameter": "target"},
        )
    node = lookup(project, raw, TAGS_PATH)
    if node is None:
        raise UnknownNode(
            f"no tag or boundary {raw!r}", details={"parameter": "target", "ref": raw}
        )
    if node.type == TAG_TYPE:
        return "tag", node
    if node.type == BOUNDARY_TYPE:
        return "boundary", node
    if is_legacy_boundary(node):
        return "legacy", node
    hint = (
        ": it is a plain group from before tags had a type; delete it and create a tag"
        if node.parent is not None and node.parent.path == TAGS_PATH
        else ""
    )
    raise ValidationError(
        f"{node.path} is a {node.type}, not a tag or a boundary{hint}",
        details={"parameter": "target", "node": node.path},
    )


def boundary_tag(tag: Node, what: str = "target") -> Node:
    """``tag`` when it is a boundary-kind tag; ``tag.invalid`` otherwise: a boundary covers
    faces, and a region or plain tag means something else."""
    kind = tag.params.get("kind")
    if kind != "boundary":
        raise SelectionError(
            f"{tag.path} is a {kind} tag; a boundary takes a boundary tag (set the tag's kind to "
            "boundary, or pick the faces)",
            details={"parameter": what, "tag": tag.path, "kind": kind},
        )
    return tag


def picked(ctx: CommandContext, raw: Any) -> str:  # Any: JSON members
    """Members in the tag commands' input form, parsed and stamped as ``tag.create`` does: the
    stored text of a new tag."""
    from stokes.api.commands.selection import _Geometry, _given, _stamp

    geo = _Geometry(ctx)
    chosen = _given(geo, raw)
    if not chosen:
        raise SelectionError("the members hold no faces", details={"parameter": "target"})
    return members_text(_stamp(geo, chosen, {m.part for m in chosen}))


def recolour(plan: Plan, tag: Node | None, color: str) -> bool:
    """Plan the tag's colour; True when it changes."""
    if tag is None or tag.params.get("color") == color:
        return False
    plan.set(tag.id, "color", color)
    return True


def tag_color(text: str) -> str:
    from stokes.api.commands.selection import _color

    try:
        return _color(text)
    except ValidationError as exc:
        exc.details["parameter"] = "color"
        raise


@dataclass(frozen=True)
class Assigned:
    """What an assign planned: the boundary, its tag, its region, the values' uses."""

    id: str
    name: str
    tag: str | None
    region: RegionRef
    uses: dict[str, ParamUse]
    recoloured: bool


def _onto_new(
    plan: Plan,
    spec: ConditionSpec,
    tag: Node | None,
    raw: Any,  # Any: JSON members when tag is None
    given: Mapping[str, Any],  # Any: JSON values
    name: str | None,
    color: str,
    ids: tuple[str | None, str | None, str | None],
    region: str | None,
) -> Assigned:
    """A new boundary on an unused tag, or on a new tag made from members."""
    node_id, tag_node_id, region_node_id = ids
    uses = uses_of(spec, {}, given)
    holder = default_region(plan, region, region_node_id)
    recoloured = False
    if tag is None:
        text = picked(plan.ctx, raw)
        tags = plan_tags_group(plan)
        bname = (
            plan.free(name, holder.boundaries)
            if name is not None
            else plan.unique(default_name(spec), holder.boundaries, tags)
        )
        tname = bname if bname not in plan.taken(tags) else plan.unique(bname, tags)
        tag_id = plan_tag(plan, tname, "boundary", text, color, tag_node_id)
    else:
        tag_id = tag.id
        bname = (
            plan.free(name, holder.boundaries)
            if name is not None
            else plan.unique(tag.name, holder.boundaries)
        )
        recoloured = recolour(plan, tag, color)
    params = {"kind": spec.id, "tag": tag_id}
    boundary_id = plan.add(holder.boundaries, BOUNDARY_TYPE, bname, params, node_id)
    for pname, value in given.items():
        plan.set(boundary_id, pname, value)
    return Assigned(boundary_id, bname, tag_id, holder, uses, recoloured)


def _legacy_tag(plan: Plan, node: Node, color: str, tag_node_id: str | None) -> tuple[str, bool]:
    """The tag of a plain-group boundary made typed: the boundary-kind tag of the same name that
    no boundary uses (pre-ADR boundaries were keyed on that name), else a new empty one."""
    same = lookup(plan.project, node.name, TAGS_PATH)
    if same is not None and same.type == TAG_TYPE and same.params.get("kind") == "boundary":
        used = users_of(plan.project, (BOUNDARY_TYPE,)).get(same.id, [])
        if not used:
            return same.id, recolour(plan, same, color)
    tags = plan_tags_group(plan)
    return plan_tag(plan, plan.unique(node.name, tags), "boundary", "[]", color, tag_node_id), False


def _onto_existing(
    plan: Plan,
    node: Node,
    spec: ConditionSpec,
    given: Mapping[str, Any],  # Any: JSON values
    name: str | None,
    color: str,
    region: str | None,
    tag_node_id: str | None,
) -> Assigned:
    """A boundary in the tree, typed or a plain group, takes the kind and values (a re-kind)."""
    project = plan.project
    holder_node = region_of(node)
    if holder_node is None:
        raise ValidationError(
            f"{node.path} does not live at /regions/<region>/boundaries/<name>",
            details={"parameter": "target", "node": node.path},
        )
    if region is not None and find_region(project, region) is not holder_node:
        raise ValidationError(
            f"{node.path} lives in {holder_node.path}; a boundary does not move between regions",
            details={"parameter": "region", "region": holder_node.path},
        )
    holder = use_region(plan, holder_node)
    if node.type == BOUNDARY_TYPE:
        uses = uses_of(spec, node.params, given)
        if node.params.get("kind") != spec.id:
            plan.set(node.id, "kind", spec.id)
        tag = ref_node(project, node, "tag", TAG_TYPE)
        recoloured = recolour(plan, tag, color)
        tag_id = tag.id if tag is not None else None
    else:
        uses = uses_of(spec, {}, given)
        tag_id, recoloured = _legacy_tag(plan, node, color, tag_node_id)
        plan.convert(node, BOUNDARY_TYPE, {"kind": spec.id, "tag": tag_id})
    for pname, value in given.items():
        plan.set(node.id, pname, value)
    bname = node.name
    if name is not None and name != node.name and node.parent is not None:
        plan.rename(node, plan.free(name, node.parent.id))
        bname = name
    return Assigned(node.id, bname, tag_id, holder, uses, recoloured)


@command(
    "boundary.assign",
    params=(
        ParamDef("target", "any", description=TARGET_HELP),
        ParamDef("kind", "string", description="A condition kind id (boundary.kinds)."),
        VALUES,
        ParamDef(
            "name",
            "string",
            required=False,
            description="The boundary's name (and a new tag's); default: the tag's name, or for "
            "members the kind's family (inlet, outlet, wall) or id, made unique.",
        ),
        ParamDef(
            "region",
            "string",
            required=False,
            description="Region: id, path or name; a name no region has makes a fluid region. "
            "Default: the project's only fluid region, else /regions/fluid (made, or typed when "
            "it is a plain group).",
        ),
        ParamDef(
            "color",
            "string",
            required=False,
            description="The tag's colour as #rrggbb; default the kind's colour.",
        ),
        NODE_ID,
        ParamDef("tag_node_id", "string", required=False, description="Journals: a new tag's id."),
        ParamDef(
            "region_node_id", "string", required=False, description="Journals: a new region's id."
        ),
    ),
)
def boundary_assign(
    ctx: CommandContext,
    target: Any,  # Any: a reference or JSON members, checked by hand
    kind: str,
    values: dict[str, Any] | None = None,  # Any: JSON values
    name: str | None = None,
    region: str | None = None,
    color: str | None = None,
    node_id: str | None = None,
    tag_node_id: str | None = None,
    region_node_id: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Assign a condition kind and its values to a tag, a boundary, a plain-group boundary from
    before typed ones, or picked faces, as one undo step: makes the tags group, the tag, the
    region (with its tag and boundaries group) and the boundary when missing, and gives the tag
    the kind's colour unless color is given. A second assign on a tag that has a boundary
    changes that boundary."""
    spec = available(kind)
    paint = tag_color(color) if color is not None else spec.color
    given = dict(values or {})
    plan = Plan(ctx)
    form, node = _target(plan.project, target)
    if form == "tag" and node is not None:
        used = users_of(plan.project, (BOUNDARY_TYPE,)).get(boundary_tag(node).id, [])
        if len(used) > 1:
            raise ValidationError(
                f"{node.path} is the tag of {len(used)} boundaries "
                f"({', '.join(b.path for b in used)}): give the boundary as target",
                details={"parameter": "target", "boundaries": [b.id for b in used]},
            )
        if used:
            form, node = "boundary", used[0]
    if node is not None and form != "tag":
        done = _onto_existing(plan, node, spec, given, name, paint, region, tag_node_id)
    else:
        ids = (node_id, tag_node_id, region_node_id)
        done = _onto_new(plan, spec, node, target, given, name, paint, ids, region)
    plan.label = f"assign {spec.label.lower()} to {done.name}"
    plan.boundary_changed(done.id)
    plan.push(check=rules_check(done.id, done.uses))
    ctx.record(node_id=done.id, tag_node_id=done.tag, region_node_id=done.region.id)
    project = plan.project
    tag = project.find(done.tag) if done.tag else None
    holder = project.get(done.region.id)
    return {
        "boundary": Lister(ctx).entry(project.get(done.id)),
        "tag": _brief(tag, "color", "kind"),
        "region": _brief(holder, "kind"),
        "created": list(plan.created),
        "recoloured": done.recoloured,
    }


def _brief(node: Node | None, *params: str) -> dict[str, Any] | None:  # Any: JSON
    if node is None:
        return None
    return {
        "id": node.id,
        "path": node.path,
        "name": node.name,
        **{p: node.params.get(p) for p in params},
    }


# ---------------------------------------------------------------- set and delete


def typed_boundary(project: Project, ref: str) -> Node:
    """A ``boundary`` node by id or path; a plain-group boundary is refused with the way on."""
    node = project.get(ref)
    if node.type == BOUNDARY_TYPE:
        return node
    hint = (
        "; it is a plain group from before typed boundaries: boundary.assign on it makes it one"
        if is_legacy_boundary(node)
        else ""
    )
    raise ValidationError(
        f"{node.path} is a {node.type}, not a boundary{hint}",
        details={"parameter": "boundary", "node": node.path},
    )


def _new_faces(plan: Plan, tag: Node, raw: Any) -> None:  # Any: JSON members
    """``tag.set`` inside the step: the tag's members become ``raw``, stamped as the tag commands
    stamp them."""
    text = picked(plan.ctx, raw) if raw else members_text(())
    if text != tag.params.get("members"):
        plan.set(tag.id, "members", text)
        plan.tag_changed(tag)


@command(
    "boundary.set",
    params=(
        BOUNDARY,
        ParamDef("kind", "string", required=False, description="A new condition kind."),
        VALUES,
        ParamDef(
            "tag",
            "string",
            required=False,
            description="Another boundary-kind tag (id, path or name) for the boundary's faces.",
        ),
        ParamDef(
            "members",
            "array",
            required=False,
            description="New faces for the boundary's tag (after tag, when both are given), in "
            "the tag commands' members form: tag.set inside the step.",
        ),
        ParamDef("name", "string", required=False, description="A new name."),
        ParamDef(
            "keep_color",
            "boolean",
            required=False,
            default=False,
            description="Keep the tag's colour when the kind or the tag changes; by default the "
            "tag takes the kind's colour.",
        ),
    ),
)
def boundary_set(
    ctx: CommandContext,
    boundary: str,
    kind: str | None = None,
    values: dict[str, Any] | None = None,  # Any: JSON values
    tag: str | None = None,
    members: list[Any] | None = None,  # Any: JSON members
    name: str | None = None,
    keep_color: bool = False,
) -> dict[str, Any]:  # Any: JSON
    """Change a boundary's kind, values, tag, faces or name in one undo step. A new kind or tag
    gives the tag the kind's colour unless keep_color; values for parameters the kind does not
    use are refused. parameter.set still sets any one parameter."""
    plan = Plan(ctx)
    project = plan.project
    node = typed_boundary(project, boundary)
    stored = str(node.params.get("kind"))
    spec = available(kind) if kind is not None else catalogue_kind(stored)
    given = dict(values or {})
    uses = uses_of(spec, node.params, given)
    moved = False
    if spec.id != stored:
        plan.set(node.id, "kind", spec.id)
    final = ref_node(project, node, "tag", TAG_TYPE)
    if tag is not None:
        chosen = lookup(project, tag, TAGS_PATH)
        if chosen is None or chosen.type != TAG_TYPE:
            raise UnknownNode(f"no tag {tag!r}", details={"parameter": "tag", "ref": tag})
        boundary_tag(chosen, "tag")
        moved = final is None or chosen.id != final.id
        if moved:
            plan.set(node.id, "tag", chosen.id)
        final = chosen
    if members is not None:
        if final is None:
            raise ValidationError(
                f"{node.path} has no tag to give the faces to: give tag too",
                details={"parameter": "members"},
            )
        _new_faces(plan, final, members)
    recoloured = (
        (spec.id != stored or moved) and not keep_color and recolour(plan, final, spec.color)
    )
    for pname, value in given.items():
        plan.set(node.id, pname, value)
    if name is not None and name != node.name and node.parent is not None:
        plan.rename(node, plan.free(name, node.parent.id))
    plan.label = f"edit boundary {name or node.name}"
    plan.boundary_changed(node)
    changed = plan.push(check=rules_check(node.id, uses))
    return {**Lister(ctx).entry(node), "changed": changed, "recoloured": bool(recoloured)}


@command(
    "boundary.delete",
    params=(
        BOUNDARY,
        ParamDef(
            "delete_tag",
            "boolean",
            required=False,
            default=False,
            description="Delete the boundary's tag too; refused while another node uses it.",
        ),
    ),
)
def boundary_delete(ctx: CommandContext, boundary: str, delete_tag: bool = False) -> dict[str, Any]:
    """Delete a boundary (a plain-group one too) in one undo step: partners that named it are
    cleared, and with delete_tag its tag goes as well."""
    plan = Plan(ctx)
    project = plan.project
    node = project.get(boundary)
    if node.type != BOUNDARY_TYPE and not is_legacy_boundary(node):
        raise ValidationError(
            f"{node.path} is a {node.type}, not a boundary", details={"parameter": "boundary"}
        )
    path = node.path
    tag = ref_node(project, node, "tag", TAG_TYPE) if node.type == BOUNDARY_TYPE else None
    plan.remove(node)
    plan.boundary_changed(node)
    clear_references(plan, [node])
    if delete_tag and tag is not None:
        others = [(n, p) for n, p in project.referrers(tag.id) if not plan.gone(n.id)]
        if others:
            raise referenced_error(
                tag, others, hint="delete_tag deletes only a tag nothing else uses"
            )
        plan.remove(tag)
        plan.tag_changed(tag)
    plan.label = f"delete boundary {node.name}"
    plan.push()
    return {
        "id": node.id,
        "path": path,
        "name": node.name,
        "deleted": True,
        "tag_deleted": bool(delete_tag and tag is not None),
    }


# ---------------------------------------------------------------- list


class Lister:
    """What ``boundary.list`` reads about the tags once per call: their faces, staleness (for
    meshes in memory) and which boundary tags share faces."""

    def __init__(self, ctx: CommandContext) -> None:
        from stokes.api.commands.selection import _Geometry, tag_overlaps

        self.geo: _Geometry = _Geometry(ctx)
        self.project: Project = self.geo.project
        self.overlapping = {t for o in tag_overlaps(self.geo) for t in o["tags"]}

    def entry(self, node: Node) -> dict[str, Any]:  # Any: JSON
        """A boundary as ``boundary.list`` lists it (a plain-group one as a legacy entry)."""
        holder = region_of(node)
        base: dict[str, Any] = {  # Any: JSON
            "id": node.id,
            "path": node.path,
            "name": node.name,
            "region": _brief(holder),
        }
        if node.type != BOUNDARY_TYPE:
            return {**base, "legacy": True}
        kind_id = str(node.params.get("kind"))
        spec = BY_ID.get(kind_id)
        warnings: list[str] = []
        if spec is None or not spec.available:
            warnings.append("boundary.invalid_kind")
        tag = ref_node(self.project, node, "tag", TAG_TYPE)
        info = self._tag(node, tag, warnings)
        if spec is not None:
            warnings.extend(_value_codes(self.project, node, spec))
        status = (
            "failed"
            if info["missing"] or "boundary.invalid_kind" in warnings
            else "stale"
            if info["stale"]
            else "warning"
            if warnings
            else "ok"
        )
        return {
            **base,
            "kind": kind_id,
            "label": spec.label if spec is not None else kind_id,
            "family": spec.family if spec is not None else None,
            "tag": info,
            "primary": _primary(self.project, node, spec),
            "warnings": warnings,
            "status": status,
        }

    def _tag(self, node: Node, tag: Node | None, warnings: list[str]) -> dict[str, Any]:
        from stokes.api.commands.selection import _size

        if tag is None:
            warnings.append("setup.tag_missing")
            return {
                "id": node.params.get("tag"),
                "name": None,
                "path": None,
                "color": None,
                "visible": None,
                "parts": 0,
                "faces": None,
                "stale": False,
                "missing": True,
            }
        try:
            members = stored_members(tag)
        except StokesError:
            members = ()
        faces = _size(self.geo, members)
        stale = any(self.geo.stale(m, load=False) for m in members)
        if not members or faces == 0:
            warnings.append("setup.empty_tag")
        if stale:
            warnings.append("setup.stale_tag")
        if tag.id in self.overlapping:
            warnings.append("setup.overlap")
        return {
            "id": tag.id,
            "name": tag.name,
            "path": tag.path,
            "color": tag.params.get("color"),
            "visible": tag.params.get("visible"),
            "parts": len(members),
            "faces": faces,
            "stale": stale,
            "missing": False,
        }


def _value_codes(project: Project, node: Node, spec: ConditionSpec) -> list[str]:
    """The boundary's own value findings: a value in use that breaks its rule (three zero
    velocity components included), and a partner that is missing or does not name it back."""
    codes: list[str] = []
    used = spec.uses(node.params)
    names = {u.name for u in used}
    broken = False
    for use in used:
        value = _number(node.params.get(use.name))
        broken = broken or (value is not None and check_rule(use, value) is not None)
    if set(COMPONENTS) <= names:
        broken = broken or all(_number(node.params.get(c)) == 0.0 for c in COMPONENTS)
    if broken:
        codes.append("setup.value_invalid")
    if "partner" in names:
        partner = ref_node(project, node, "partner", BOUNDARY_TYPE)
        if partner is None or partner.params.get("partner") != node.id:
            codes.append("setup.partner_missing")
    return codes


def _primary(
    project: Project, node: Node, spec: ConditionSpec | None
) -> dict[str, Any] | None:  # Any: JSON
    """The value a row shows: the first required parameter in use, else the first quantity,
    else the first switch; ``display`` is in the project's display units."""
    if spec is None:
        return None
    boundary = node_types.get(BOUNDARY_TYPE)
    used = spec.uses(node.params)
    pick = (
        next((u for u in used if u.required), None)
        or next((u for u in used if boundary.param(u.name).kind == "quantity"), None)
        or next((u for u in used if boundary.param(u.name).kind == "enum"), None)
    )
    if pick is None:
        return None
    raw = node.params.get(pick.name)
    if isinstance(raw, QuantityValue):
        from stokes.units import format_quantity

        dim = project.param_dimension(node, boundary.param(pick.name))
        q = project.value_of(node.id, pick.name)
        display = format_quantity(q, dim.unit_for(project.display_units) or None)
        return {"name": pick.name, "value": raw.value, "display": display}
    if boundary.param(pick.name).kind == "ref":
        target = project.find(raw) if isinstance(raw, str) else None
        return {"name": pick.name, "value": raw, "display": target.name if target else "none"}
    return {"name": pick.name, "value": raw, "display": str(raw)}


@command(
    "boundary.list",
    params=(
        ParamDef(
            "region",
            "string",
            required=False,
            description="Only this region's boundaries (id, path or name).",
        ),
    ),
    mutates=False,
)
def boundary_list(ctx: CommandContext, region: str | None = None) -> list[dict[str, Any]]:
    """Every boundary at /regions/<region>/boundaries with its region, kind, tag (colour, faces,
    stale, missing), primary value, findings and derived status; plain groups from before typed
    boundaries as legacy entries."""
    lister = Lister(ctx)
    holder = None
    if region is not None:
        holder = find_region(lister.project, region)
        if holder is None:
            raise UnknownNode(f"no region {region!r}", details={"parameter": "region"})
    return [lister.entry(n) for n in boundary_nodes(lister.project, holder)]
