"""The set-up commands' shared work (ADR-0022 decisions 2.8, 2.9, 3 and 4): reading regions and
boundaries from the tree, and :class:`Plan`, which builds one undo step against it.

A set-up command (``boundary.*``, ``region.*``, ``tag.delete`` with ``force``, and T-039's
``setup.defaults``) plans every model command first, against the tree as it will be once they
apply, then pushes them as one :class:`Step` and announces what changed. Planning first keeps the
command atomic: a refusal found while planning changes nothing, a model command that refuses
reverts the ones before it, and nothing is announced or journaled unless the whole step applied.

Where things live (contract: ``docs/engineering/setup-schema.md``)::

    /regions/<region>                    region (or a plain group from before ADR-0022: legacy)
      boundaries                         group, made with the region
        <boundary>                       boundary (or a plain group: legacy)
    /geometry/tags/<tag>                 tag: kind boundary (a boundary's faces), region (bodies)

Ids are stable for replay. A command records the ids it chose (``node_id``, ``tag_node_id``,
``region_node_id``) with ``ctx.record``; the ids of what a region brings with it derive from the
region's id: its tag (:func:`region_tag_id`) when the command names none, and its ``boundaries``
group (:func:`boundaries_id`). ``/geometry/tags`` takes the structural id ``tag.create`` gives it.

Size: the step builder, the readers of the set-up tree and the planning of regions and bodies
belong together; every set-up command plans through them, and T-039's ``setup.defaults`` will.

Import rule: ``stokes.model``, ``stokes.geometry.model`` and the condition catalogue (standard
library only) at module level; the tag commands' members internals (numpy) inside the functions
that need them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stokes.errors import StokesError, UnknownNode, ValidationError
from stokes.geometry.model import (
    BOUNDARIES_GROUP,
    BOUNDARY_TYPE,
    GEOMETRY_PATH,
    REGION_TYPE,
    REGIONS_PATH,
    SELECTION_PALETTE,
    TAG_TYPE,
    TAGS_GROUP,
    TAGS_PATH,
)
from stokes.model import AddNode, Composite, ConvertGroup, RemoveNode, RenameNode, SetParam
from stokes.model.ids import is_id, new_id, stable_id
from stokes.model.schema import NAME_RE
from stokes.physics.conditions.catalogue import INLET_COLOR, OUTLET_COLOR

if TYPE_CHECKING:
    from stokes.api.registry import CommandContext
    from stokes.geometry.selection import Member
    from stokes.model import Command, Node, Project

GROUP_TYPE = "group"
FLUID = "fluid"
REGION_COLORS: tuple[str, ...] = tuple(
    c for c in SELECTION_PALETTE if c not in (INLET_COLOR, OUTLET_COLOR)
)
"""The colours region tags take in turn: the tag palette without the inlet red and outlet blue."""


# ---------------------------------------------------------------- the undo step


@dataclass
class Step(Composite):
    """A composite that runs ``check`` once every part has applied and reverts itself when a
    part or the check refuses, on redo as on the first apply.

    A refusal from a :class:`SetParam` names the parameter (``details.parameter``) when the error
    does not already, so that a client can show it under the field (the value panel, T-044)."""

    check: Callable[[Project], None] | None = field(default=None, repr=False, compare=False)

    def apply(self, project: Project) -> Any:  # Any: JSON results of the parts
        results = []
        self.applied = 0
        try:
            for cmd in self.commands:
                results.append(cmd.apply(project))
                self.applied += 1
            if self.check is not None:
                self.check(project)
        except Exception as exc:
            failed = self.commands[self.applied] if self.applied < len(self.commands) else None
            for cmd in reversed(self.commands[: self.applied]):
                cmd.revert(project)
            self.applied = 0
            if isinstance(exc, StokesError) and isinstance(failed, SetParam):
                exc.details.setdefault("parameter", failed.param)
            raise
        return results


class Plan:
    """The model commands of one set-up command, planned against the tree as it will be once
    they apply, then pushed as one undo step (:meth:`push`) and announced.

    Names stay unique against the tree and the nodes planned so far (:meth:`unique`); ids of new
    nodes are chosen when they are planned, so later commands of the step can refer to them.
    Removals apply last, whenever they were planned: undo then puts the removed nodes back
    before it restores a reference to one of them (a partner the step cleared), which the model
    checks. A removed node's name is therefore still taken while the step plans."""

    def __init__(self, ctx: CommandContext, label: str = "") -> None:
        self.ctx = ctx
        self.project: Project = ctx.session.require_project()
        self.label = label
        self.commands: list[Command] = []
        self.removals: list[Command] = []
        self.created: list[str] = []
        self.tags_group: str | None = None
        self.new_tags = 0
        self._names: dict[str, set[str]] = {}
        self._freed: dict[str, set[str]] = {}
        self._gone: set[str] = set()
        self._paths: dict[str, str] = {}
        self._announce: dict[str, dict[str, None]] = {
            "tag.changed": {},
            "boundary.changed": {},
            "region.changed": {},
        }

    # ------------------------------------------------------------ names
    def taken(self, parent: str) -> set[str]:
        """The child names under ``parent`` (an id, of a node in the tree or planned) once the
        step has applied."""
        node = self.project.find(parent)
        names = {c.name for c in node.children} if node is not None else set()
        return (names - self._freed.get(parent, set())) | self._names.get(parent, set())

    def unique(self, base: str, *parents: str) -> str:
        """``base``, or ``base_2``, ``base_3`` ...: the first name free under every parent."""
        taken = set().union(*(self.taken(p) for p in parents))
        name, number = base, 2
        while name in taken:
            name, number = f"{base}_{number}", number + 1
        return name

    def free(self, name: str, parent: str, *, what: str = "name") -> str:
        """``name`` when it is a valid name not taken under ``parent``; else the refusal, which
        names the command parameter ``what`` it came from."""
        if not NAME_RE.match(name):
            raise ValidationError(
                f"invalid name {name!r}: letters, digits, underscores; no leading digit",
                details={"parameter": what, "name": name},
            )
        if name in self.taken(parent):
            where = self.project.find(parent)
            raise ValidationError(
                f"{where.path if where is not None else parent} already has {name!r}",
                details={"parameter": what, "name": name},
            )
        return name

    # ------------------------------------------------------------ commands
    def add(
        self,
        parent: str,
        node_type: str,
        name: str,
        params: dict[str, Any] | None = None,  # Any: JSON values
        node_id: str | None = None,
    ) -> str:
        """Plan a new node under ``parent`` (an id); returns its id."""
        chosen = node_id or new_id()
        self.commands.append(AddNode(parent, node_type, name, dict(params or {}), node_id=chosen))
        self._names.setdefault(parent, set()).add(name)
        self.created.append(chosen)
        if node_type == TAG_TYPE:
            self.new_tags += 1
        return chosen

    def set(self, node_id: str, param: str, value: Any) -> None:  # Any: JSON value
        self.commands.append(SetParam(node_id, param, value))

    def rename(self, node: Node, name: str) -> None:
        if node.parent is not None:
            self._freed.setdefault(node.parent.id, set()).add(node.name)
            self._names.setdefault(node.parent.id, set()).add(name)
        self.commands.append(RenameNode(node.id, name))

    def convert(self, node: Node, node_type: str, params: dict[str, Any]) -> None:  # Any: JSON
        self.commands.append(ConvertGroup(node.id, node_type, dict(params)))

    def remove(self, node: Node) -> None:
        """Plan the removal of ``node`` and its subtree (once, however often it is asked); it
        applies after every other command of the step."""
        if node.id in self._gone:
            return
        for inside in node.walk():
            self._gone.add(inside.id)
            self._paths[inside.id] = inside.path
        self.removals.append(RemoveNode(node.id))

    def gone(self, node_id: str) -> bool:
        """Whether the step removes the node (or a node above it)."""
        return node_id in self._gone

    # ------------------------------------------------------------ what is announced
    def tag_changed(self, node: Node | str) -> None:
        self._mark("tag.changed", node)

    def boundary_changed(self, node: Node | str) -> None:
        self._mark("boundary.changed", node)

    def region_changed(self, node: Node | str) -> None:
        self._mark("region.changed", node)

    def _mark(self, event: str, node: Node | str) -> None:
        if not isinstance(node, str):
            self._paths.setdefault(node.id, node.path)
        self._announce[event][node if isinstance(node, str) else node.id] = None

    # ------------------------------------------------------------ pushing
    def push(self, check: Callable[[Project], None] | None = None) -> bool:
        """Apply the planned commands as one undo step labelled :attr:`label`, then announce
        them: ``node.added``, ``node.changed`` (with ``param`` for a parameter) and
        ``node.removed`` per model command in order, then ``tag.changed``, ``boundary.changed``
        and ``region.changed`` once per node marked. False, and nothing done, when nothing was
        planned."""
        ordered = [*self.commands, *self.removals]
        if not ordered:
            return False
        step = Step(self.label, ordered, check=check)
        self.ctx.session.undo.push(step, self.project)
        emit = self.ctx.session.emit
        for cmd in ordered:
            match cmd:
                case AddNode(node_id=str(node_id)):
                    emit("node.added", node_id=node_id, path=self.path_of(node_id))
                case SetParam(node_id=str(node_id), param=param):
                    emit("node.changed", node_id=node_id, path=self.path_of(node_id), param=param)
                case RenameNode(node_id=str(node_id)) | ConvertGroup(node_id=str(node_id)):
                    emit("node.changed", node_id=node_id, path=self.path_of(node_id))
                case RemoveNode(node_id=str(node_id)):
                    emit("node.removed", node_id=node_id, path=cmd.node_path, parent=cmd.parent_id)
        for event, ids in self._announce.items():
            for node_id in ids:
                emit(event, node_id=node_id, path=self.path_of(node_id))
        return True

    def path_of(self, node_id: str) -> str:
        """The node's path now, or where it was when the step removed it."""
        node = self.project.find(node_id)
        return node.path if node is not None else self._paths.get(node_id, "")


# ---------------------------------------------------------------- reading the set-up tree


def lookup(project: Project, ref: str, group: str) -> Node | None:
    """A node by id or path, or by bare name among the children of ``group`` (a path)."""
    if is_id(ref) or "/" in ref or "." in ref:
        return project.find(ref)
    parent = project.find(group)
    return parent.child(ref) if parent is not None else None


def regions_group(project: Project) -> Node:
    return project.get(REGIONS_PATH)


def is_legacy_region(node: Node) -> bool:
    """A plain group directly under ``/regions``: a region made before ADR-0022."""
    return node.type == GROUP_TYPE and node.parent is not None and node.parent.path == REGIONS_PATH


def region_nodes(project: Project) -> list[Node]:
    """The typed regions under ``/regions``, in tree order."""
    group = project.find(REGIONS_PATH)
    return [c for c in group.children if c.type == REGION_TYPE] if group is not None else []


def boundaries_group(region: Node) -> Node | None:
    """The region's ``boundaries`` group, when it has one."""
    found = region.child(BOUNDARIES_GROUP)
    return found if found is not None and found.type == GROUP_TYPE else None


def boundary_nodes(project: Project, region: Node | None = None) -> list[Node]:
    """The boundaries at ``/regions/<region>/boundaries/<name>`` (of one region, or of all),
    legacy plain groups included, in tree order."""
    group = project.find(REGIONS_PATH)
    holders = [region] if region is not None else list(group.children if group else ())
    found: list[Node] = []
    for holder in holders:
        if holder.type != REGION_TYPE and not is_legacy_region(holder):
            continue
        inner = boundaries_group(holder)
        if inner is not None:
            found.extend(c for c in inner.children if c.type in (BOUNDARY_TYPE, GROUP_TYPE))
    return found


def is_legacy_boundary(node: Node) -> bool:
    """A plain group at ``/regions/<region>/boundaries/<name>``: a boundary made before
    ADR-0022."""
    holder = region_of(node)
    return node.type == GROUP_TYPE and holder is not None


def region_of(node: Node) -> Node | None:
    """The region (typed or legacy) a node at ``/regions/<region>/boundaries/<name>`` lives in."""
    group = node.parent
    holder = group.parent if group is not None else None
    if group is None or holder is None or group.name != BOUNDARIES_GROUP:
        return None
    if group.type != GROUP_TYPE:
        return None
    return holder if holder.type == REGION_TYPE or is_legacy_region(holder) else None


def ref_node(project: Project, node: Node, param: str, node_type: str) -> Node | None:
    """The node a ``ref`` parameter points at, when it exists and has the type it should."""
    target = node.params.get(param)
    found = project.find(target) if isinstance(target, str) and target else None
    return found if found is not None and found.type == node_type else None


def users_of(
    project: Project, node_types: Iterable[str] = (BOUNDARY_TYPE, REGION_TYPE)
) -> dict[str, list[Node]]:
    """Tag id to the nodes of ``node_types`` whose ``tag`` parameter names it, in tree order:
    one walk of the tree."""
    wanted = set(node_types)
    found: dict[str, list[Node]] = {}
    for node in project.nodes():
        if node.type in wanted:
            tag = node.params.get("tag")
            if isinstance(tag, str):
                found.setdefault(tag, []).append(node)
    return found


# ---------------------------------------------------------------- tags


def plan_tags_group(plan: Plan) -> str:
    """The id of ``/geometry/tags``, planning the group when the project lacks it (as
    ``tag.create`` does, with the same structural id)."""
    if plan.tags_group is not None:
        return plan.tags_group
    group = plan.project.find(TAGS_PATH)
    if group is not None:
        if group.type != GROUP_TYPE:
            raise ValidationError(f"{TAGS_PATH} is a {group.type}, not a group")
        plan.tags_group = group.id
        return group.id
    wanted = stable_id("g", TAGS_PATH)
    taken = plan.project.find(wanted) is not None
    plan.tags_group = plan.add(
        plan.project.get(GEOMETRY_PATH).id,
        GROUP_TYPE,
        TAGS_GROUP,
        node_id=None if taken else wanted,
    )
    return plan.tags_group


def next_color(plan: Plan) -> str:
    """A new region tag's colour: the tag palette's next, by the number of tags as
    ``tag.create`` picks it, past red and blue, which mark inlets and outlets (``web/DESIGN.md``
    reserves them; the condition kinds give them to boundary tags)."""
    group = plan.project.find(TAGS_PATH)
    count = sum(1 for c in group.children if c.type == TAG_TYPE) if group is not None else 0
    return REGION_COLORS[(count + plan.new_tags) % len(REGION_COLORS)]


def plan_tag(
    plan: Plan, name: str, kind: str, members: str, color: str, node_id: str | None = None
) -> str:
    """A new tag under ``/geometry/tags``; ``members`` is the stored text. Returns its id."""
    group = plan_tags_group(plan)
    params = {"members": members, "kind": kind, "color": color, "visible": True}
    tag_id = plan.add(group, TAG_TYPE, name, params, node_id)
    plan.tag_changed(tag_id)
    return tag_id


def stored_members(tag: Node) -> tuple[Member, ...]:
    """A tag's members as the tag commands read them (``tag.invalid`` when malformed)."""
    from stokes.api.commands.selection import _members

    return _members(tag)


def members_text(members: Sequence[Member]) -> str:
    from stokes.geometry.selection import dump_members

    return dump_members(members)


# ---------------------------------------------------------------- regions


@dataclass(frozen=True)
class RegionRef:
    """A region as a set-up command plans it: in the tree, converted from a plain group, or new.
    ``tag`` is its tag's id (None when the reference is gone) and ``boundaries`` the id of its
    ``boundaries`` group, which the plan adds when the region lacks one."""

    id: str
    name: str
    kind: str
    tag: str | None
    boundaries: str
    created: bool = False
    converted: bool = False


def region_tag_id(region_id: str) -> str:
    """The id of the tag a new region brings when the command names none."""
    return stable_id("n", f"{region_id}/tag")


def boundaries_id(region_id: str) -> str:
    """The id of a region's ``boundaries`` group when a command adds it."""
    return stable_id("g", f"{region_id}/{BOUNDARIES_GROUP}")


def _plan_boundaries_group(plan: Plan, region: Node | None, region_id: str) -> str:
    existing = region.child(BOUNDARIES_GROUP) if region is not None else None
    if existing is None:
        return plan.add(region_id, GROUP_TYPE, BOUNDARIES_GROUP, node_id=boundaries_id(region_id))
    if existing.type != GROUP_TYPE:
        raise ValidationError(
            f"{existing.path} is a {existing.type}, not the region's boundaries group",
            details={"node": existing.path},
        )
    return existing.id


def plan_new_region(
    plan: Plan,
    name: str,
    kind: str,
    *,
    material: str | None = None,
    bodies: str = "[]",
    node_id: str | None = None,
    tag_id: str | None = None,
) -> RegionRef:
    """A new region at ``/regions/<name>`` with its tag (kind region, the region's name made
    unique among the tags, ``bodies`` as its members) and its ``boundaries`` group."""
    regions = regions_group(plan.project)
    region_id = node_id or new_id()
    tag_name = plan.unique(name, plan_tags_group(plan))
    tag = plan_tag(
        plan, tag_name, "region", bodies, next_color(plan), tag_id or region_tag_id(region_id)
    )
    params: dict[str, Any] = {"kind": kind, "tag": tag}  # Any: JSON values
    if material is not None:
        params["material"] = material
    plan.add(regions.id, REGION_TYPE, name, params, node_id=region_id)
    group = _plan_boundaries_group(plan, None, region_id)
    plan.region_changed(region_id)
    return RegionRef(region_id, name, kind, tag, group, created=True)


def plan_convert_region(
    plan: Plan,
    group: Node,
    kind: str,
    *,
    material: str | None = None,
    bodies: str = "[]",
    tag_id: str | None = None,
) -> RegionRef:
    """A plain group at ``/regions/<name>`` becomes a typed region in place (same id, name and
    children; ADR-0022 decision 2.9), with a new tag as :func:`plan_new_region` makes one."""
    tag_name = plan.unique(group.name, plan_tags_group(plan))
    tag = plan_tag(
        plan, tag_name, "region", bodies, next_color(plan), tag_id or region_tag_id(group.id)
    )
    params: dict[str, Any] = {"kind": kind, "tag": tag}  # Any: JSON values
    if material is not None:
        params["material"] = material
    plan.convert(group, REGION_TYPE, params)
    boundaries = _plan_boundaries_group(plan, group, group.id)
    plan.region_changed(group.id)
    return RegionRef(group.id, group.name, kind, tag, boundaries, converted=True)


def existing_region(plan: Plan, region: Node) -> RegionRef:
    """A typed region in the tree, with its ``boundaries`` group planned when it lacks one."""
    tag = ref_node(plan.project, region, "tag", TAG_TYPE)
    boundaries = _plan_boundaries_group(plan, region, region.id)
    return RegionRef(
        region.id,
        region.name,
        str(region.params.get("kind") or FLUID),
        tag.id if tag is not None else None,
        boundaries,
    )


def find_region(project: Project, ref: str) -> Node | None:
    """A region (or a legacy plain group under ``/regions``) by id, path or name; None when there
    is none by that reference; ``validation`` when the reference names another node."""
    node = lookup(project, ref, REGIONS_PATH)
    if node is None:
        return None
    if node.type == REGION_TYPE or is_legacy_region(node):
        return node
    raise ValidationError(
        f"{node.path} is a {node.type}, not a region", details={"parameter": "region"}
    )


def use_region(plan: Plan, node: Node, kind: str = FLUID) -> RegionRef:
    """A region in the tree as a target: typed as it is, or a legacy plain group converted (it
    takes ``kind``)."""
    if node.type == REGION_TYPE:
        return existing_region(plan, node)
    return plan_convert_region(plan, node, kind)


def default_region(plan: Plan, ref: str | None, node_id: str | None) -> RegionRef:
    """The region a new boundary goes to (ADR-0022 decision 2.9): ``ref`` (created, kind fluid,
    when no region has that name); else the project's only fluid region; else a plain group
    ``/regions/fluid`` converted; else a new region ``fluid``. Several fluid regions and no
    ``ref`` is refused: the command cannot choose."""
    project = plan.project
    if ref is not None:
        node = find_region(project, ref)
        if node is not None:
            return use_region(plan, node)
        if is_id(ref) or "/" in ref or "." in ref:
            raise UnknownNode(f"no region {ref!r}", details={"parameter": "region", "ref": ref})
        name = plan.free(ref, regions_group(project).id, what="region")
        return plan_new_region(plan, name, FLUID, node_id=node_id)
    fluids = [r for r in region_nodes(project) if r.params.get("kind") == FLUID]
    if len(fluids) == 1:
        return existing_region(plan, fluids[0])
    if fluids:
        names = ", ".join(r.name for r in fluids)
        raise ValidationError(
            f"the project has {len(fluids)} fluid regions ({names}): name one with region",
            details={"parameter": "region", "regions": [r.id for r in fluids]},
        )
    legacy = project.find(f"{REGIONS_PATH}/{FLUID}")
    if legacy is not None and is_legacy_region(legacy):
        return plan_convert_region(plan, legacy, FLUID)
    name = plan.unique(FLUID, regions_group(project).id)
    return plan_new_region(plan, name, FLUID, node_id=node_id)


# ---------------------------------------------------------------- bodies


def bodies_of(tag: Node | None) -> list[str]:
    """The part ids a region tag holds (any member counts as its whole part)."""
    if tag is None:
        return []
    try:
        return [m.part for m in stored_members(tag)]
    except StokesError:  # malformed members hold no body the commands can move
        return []


def clear_references(
    plan: Plan, removed: Sequence[Node], hint: str = "remove or change them first"
) -> None:
    """Inside the step, clear the optional references into the removed subtrees from nodes the
    step keeps (a boundary's ``partner`` naming a removed boundary becomes null); a required one
    refuses the removal (``model.referenced``). Plan every removal before calling this."""
    from stokes.model.project import referenced_error

    project = plan.project
    for node in removed:
        for referrer, param in project.referrers(node.id, subtree=True):
            if plan.gone(referrer.id):
                continue
            if project.registry.get(referrer.type).param(param).required:
                raise referenced_error(node, [(referrer, param)], hint=hint)
            plan.set(referrer.id, param, None)
            if referrer.type == BOUNDARY_TYPE:
                plan.boundary_changed(referrer)


def whole_bodies(parts: Iterable[str]) -> str:
    """The stored members text of a region tag holding ``parts`` whole."""
    from stokes.geometry.selection import Member

    return members_text([Member(part, None) for part in dict.fromkeys(parts)])


def plan_leave_regions(
    plan: Plan, parts: Iterable[str], region_id: str, tag_id: str | None
) -> None:
    """``parts`` leave every typed region but ``region_id`` (``tag.remove`` on each region tag
    that holds one, inside the step); a region left empty stays (``setup.empty_region``)."""
    from stokes.geometry.selection import Member, difference

    project = plan.project
    leaving = [Member(part, None) for part in dict.fromkeys(parts)]
    seen: set[str] = {tag_id} if tag_id is not None else set()
    for region in region_nodes(project):
        tag = ref_node(project, region, "tag", TAG_TYPE)
        if region.id == region_id or tag is None or tag.id in seen:
            continue
        seen.add(tag.id)
        members = stored_members(tag)
        # Whole bodies leave whole: the difference never needs a face count.
        kept = difference(members, leaving, lambda part: None)
        if len(kept) != len(members):
            plan.set(tag.id, "members", members_text(kept))
            plan.tag_changed(tag)
            plan.region_changed(region)


def plan_move_bodies(plan: Plan, parts: Sequence[str], into: RegionRef) -> list[str]:
    """Move ``parts`` (whole bodies) into the region ``into`` (ADR-0022 decision 4.1): out of
    every other region (:func:`plan_leave_regions`) and into ``into``'s tag (``tag.add``), through
    the tag internals. Returns the parts that joined it. A region the plan makes or types holds
    its bodies already: its tag was planned with them."""
    from stokes.geometry.selection import Member, union

    wanted = list(dict.fromkeys(parts))
    plan_leave_regions(plan, wanted, into.id, into.tag)
    target = plan.project.find(into.tag) if into.tag is not None else None
    if into.created or into.converted or target is None:
        return wanted
    members = stored_members(target)
    have = {m.part for m in members if m.whole}
    joining = [p for p in wanted if p not in have]
    if joining:
        merged = union(members, [Member(p, None) for p in joining])
        plan.set(target.id, "members", members_text(merged))
        plan.tag_changed(target)
        plan.region_changed(into.id)
    return joining
