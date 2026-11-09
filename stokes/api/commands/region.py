"""region.* commands (ADR-0022 decisions 2.9 and 4): regions of bodies and their material.

A ``region`` node at ``/regions/<name>`` gives the bodies of a region-kind tag a kind (fluid or
solid) and a material; it holds a ``boundaries`` group for its boundaries. Bodies reach a
material only through a region (decision 4.1): one material per region, and one region per body,
so a body put in a region leaves any other (a region left empty stays; ``setup.check`` warns).
Contract: ``docs/engineering/setup-schema.md``.

``region.create`` makes a region with its tag and its ``boundaries`` group (a plain group of that
name from before ADR-0022 is typed in place); ``region.assign`` gives a material to a region, or
to bodies: bodies already in a region of that material stay, the others move to the region for
the material (one that has it; else the only region of the material's kind without one, which
takes it; else a new region named after the material); ``region.set`` changes a region's
material, kind, bodies or name; ``region.delete`` removes a region with its boundaries, and its
tag on request; ``region.list`` reports every region. A material of the other kind is refused
(``materials.kind_mismatch``, decision 4.2). Each command that changes the tree is one undo step
and one journal record, with the ids it chose recorded (``node_id``, ``tag_node_id``).

Events::

    node.added, node.changed, node.removed   per model command, as everywhere
    tag.changed {node_id, path}              a region tag made, given bodies or deleted
    region.changed {node_id, path}           once per region a command changes
    boundary.changed {node_id, path}         region.delete: each boundary it removes

Errors: ``materials.kind_mismatch``, ``tag.invalid`` (a target tag that is not a region tag, faces
that are not whole bodies), ``model.referenced``, ``validation``, ``model.unknown_node``.

Size: one module per command group, as the registry is organised: five commands, the choice of
the region for a material, and the list.

Import rule: as :mod:`stokes.api.commands.boundary`; the tag internals load in the handlers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stokes.api.commands.setup_plan import (
    FLUID,
    Plan,
    RegionRef,
    bodies_of,
    boundary_nodes,
    clear_references,
    existing_region,
    find_region,
    is_legacy_region,
    lookup,
    plan_convert_region,
    plan_leave_regions,
    plan_move_bodies,
    plan_new_region,
    ref_node,
    region_nodes,
    regions_group,
    stored_members,
    users_of,
    whole_bodies,
)
from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError, UnknownNode, ValidationError
from stokes.geometry.errors import SelectionError
from stokes.geometry.model import (
    BOUNDARY_TYPE,
    REGION_KINDS,
    REGION_TYPE,
    REGIONS_PATH,
    TAG_TYPE,
    TAGS_PATH,
)
from stokes.model.project import referenced_error
from stokes.physics.materials.errors import KindMismatch
from stokes.physics.materials.model import MATERIALS_PATH

if TYPE_CHECKING:
    from stokes.api.commands.selection import _Geometry
    from stokes.geometry.selection import Member
    from stokes.model import Node, Project

MATERIAL_TYPE = "material"
REGION = ParamDef("region", "string", description="Region: id, path or name.")
NODE_ID = ParamDef(
    "node_id", "string", required=False, description="Journals: the region's id when made."
)
TAG_NODE_ID = ParamDef(
    "tag_node_id", "string", required=False, description="Journals: a new region tag's id."
)
BODIES_HELP = (
    'Bodies: part or group ids or paths, or members [{"part": <id or path>, "faces": "all"}] '
    "(the live pick of bodies); a group stands for every part under it."
)


# ---------------------------------------------------------------- lookups


def material_node(project: Project, ref: str) -> Node:
    """A material by id, path or name."""
    node = lookup(project, ref, MATERIALS_PATH)
    if node is None or node.type != MATERIAL_TYPE:
        raise UnknownNode(f"no material {ref!r}", details={"parameter": "material", "ref": ref})
    return node


def material_kind(material: Node) -> str:
    return str(material.params.get("kind") or FLUID)


def check_kind(kind: str, material: Node, region: str) -> None:
    """``materials.kind_mismatch`` when a material of the other kind would fill a region."""
    found = material_kind(material)
    if found != kind:
        raise KindMismatch(
            f"{material.name} is a {found} material and region {region} is {kind}: a region "
            "takes a material of its own kind",
            details={"parameter": "material", "material": material.path, "kind": kind},
        )


def typed_region(project: Project, ref: str) -> Node:
    """A ``region`` node; a plain group from before typed regions is refused with the way on."""
    node = find_region(project, ref)
    if node is None:
        raise UnknownNode(f"no region {ref!r}", details={"parameter": "region", "ref": ref})
    if node.type != REGION_TYPE:
        raise ValidationError(
            f"{node.path} is a plain group from before typed regions: region.create with its "
            "name, or boundary.assign into it, makes it one",
            details={"parameter": "region", "node": node.path},
        )
    return node


def parts_of(ctx: CommandContext, raw: Any, what: str) -> list[str]:  # Any: JSON bodies
    """Part ids from a command's bodies: references (a group stands for its parts) or members
    with ``faces: "all"``; faces of a body are refused (a region holds whole bodies)."""
    from stokes.api.commands.selection import _Geometry, _given

    if not isinstance(raw, list):
        raise ValidationError(f"{what} must be a list of bodies", details={"parameter": what})
    # An explicit `faces: "all"`, not a bare `{"part": item}`: a member that names nothing is
    # malformed (the owner's answer 4, via Claude 2026-10-12), and this function's own docstring
    # already said "members with `faces: \"all\"`". It was building the form the parser now refuses.
    items = [{"part": item, "faces": "all"} if isinstance(item, str) else item for item in raw]
    members = _given(_Geometry(ctx), items)
    faces = [m.part for m in members if not m.whole]
    if faces:
        raise SelectionError(
            f'a region holds whole bodies: give faces "all" for {faces[0]}, or its part id',
            details={"parameter": what, "part": faces[0]},
        )
    return [m.part for m in members]


def _target(
    ctx: CommandContext, project: Project, raw: Any
) -> tuple[Node | None, list[str]]:  # Any
    """``region.assign``'s target: ``(region, [])`` for a region or a region tag a region uses,
    ``(None, part ids)`` for bodies (a list, a part, a group or an unused region tag)."""
    if isinstance(raw, list):
        return None, parts_of(ctx, raw, "target")
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError(
            "target must be a region, a region tag or bodies", details={"parameter": "target"}
        )
    node = lookup(project, raw, REGIONS_PATH) or lookup(project, raw, TAGS_PATH)
    if node is None:
        raise UnknownNode(f"no region, tag or body {raw!r}", details={"parameter": "target"})
    if node.type == REGION_TYPE or is_legacy_region(node):
        return node, []
    if node.type == TAG_TYPE:
        if node.params.get("kind") != "region":
            raise SelectionError(
                f"{node.path} is a {node.params.get('kind')} tag; give a region tag, a region or "
                "bodies",
                details={"parameter": "target", "tag": node.path},
            )
        used = users_of(project, (REGION_TYPE,)).get(node.id, [])
        if len(used) > 1:
            raise ValidationError(
                f"{node.path} is the tag of {len(used)} regions: give the region as target",
                details={"parameter": "target", "regions": [r.id for r in used]},
            )
        return (used[0], []) if used else (None, bodies_of(node))
    return None, parts_of(ctx, [raw], "target")


# ---------------------------------------------------------------- create, assign, set, delete


@command(
    "region.create",
    params=(
        ParamDef("name", "string", description="Region name (letters, digits, underscores)."),
        ParamDef(
            "kind",
            "string",
            required=False,
            description="fluid or solid; default the material's kind, else fluid.",
        ),
        ParamDef(
            "bodies",
            "array",
            required=False,
            description=BODIES_HELP + " They leave any other region.",
        ),
        ParamDef("material", "string", required=False, description="Material: id, path or name."),
        NODE_ID,
        TAG_NODE_ID,
    ),
)
def region_create(
    ctx: CommandContext,
    name: str,
    kind: str | None = None,
    bodies: list[Any] | None = None,  # Any: JSON bodies
    material: str | None = None,
    node_id: str | None = None,
    tag_node_id: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Create a region at /regions/<name> with its tag (kind region, the bodies) and its
    boundaries group, as one undo step; a plain group of that name from before typed regions
    becomes the region, keeping its id and children."""
    plan = Plan(ctx, f"create region {name}")
    project = plan.project
    found = material_node(project, material) if material is not None else None
    chosen = kind or (material_kind(found) if found is not None else FLUID)
    if chosen not in REGION_KINDS:
        raise ValidationError(
            f"kind must be one of {', '.join(REGION_KINDS)}; got {chosen!r}",
            details={"parameter": "kind"},
        )
    if found is not None:
        check_kind(chosen, found, name)
    parts = parts_of(ctx, bodies, "bodies") if bodies else []
    text = whole_bodies(parts)
    material_id = found.id if found is not None else None
    group = regions_group(project)
    current = group.child(name)
    if current is not None and is_legacy_region(current):
        ref = plan_convert_region(
            plan, current, chosen, material=material_id, bodies=text, tag_id=tag_node_id
        )
    else:
        plan.free(name, group.id)
        ref = plan_new_region(
            plan,
            name,
            chosen,
            material=material_id,
            bodies=text,
            node_id=node_id,
            tag_id=tag_node_id,
        )
    plan_move_bodies(plan, parts, ref)
    plan.push()
    ctx.record(node_id=ref.id, tag_node_id=ref.tag)
    return Lister(ctx).entry(project.get(ref.id))


def _region_for(
    plan: Plan,
    material: Node,
    name: str | None,
    movers: list[str],
    ids: tuple[str | None, str | None],
) -> RegionRef:
    """The region bodies of ``material`` go to (ADR-0022 decision 4.1): ``name`` when given
    (made when missing; it must not hold another material); else a region that has the material
    (the one named after it first); else the only region of the material's kind without a
    material, which takes it; else a new region named after the material."""
    project = plan.project
    kind = material_kind(material)
    text = whole_bodies(movers)
    node_id, tag_id = ids
    regions = regions_group(project)
    if name is not None:
        node = find_region(project, name)
        if node is None:
            plan.free(name, regions.id)
            return plan_new_region(
                plan, name, kind, material=material.id, bodies=text, node_id=node_id, tag_id=tag_id
            )
        if is_legacy_region(node):
            return plan_convert_region(
                plan, node, kind, material=material.id, bodies=text, tag_id=tag_id
            )
        return _adopt(plan, node, material, strict=True)
    holders = [r for r in region_nodes(project) if r.params.get("material") == material.id]
    if holders:
        return existing_region(
            plan, next((r for r in holders if r.name == material.name), holders[0])
        )
    free = [
        r
        for r in region_nodes(project)
        if r.params.get("material") is None and r.params.get("kind") == kind
    ]
    if len(free) == 1:
        return _adopt(plan, free[0], material, strict=False)
    return plan_new_region(
        plan,
        plan.unique(material.name, regions.id),
        kind,
        material=material.id,
        bodies=text,
        node_id=node_id,
        tag_id=tag_id,
    )


def _adopt(plan: Plan, region: Node, material: Node, *, strict: bool) -> RegionRef:
    """``region`` takes ``material`` when it has none; one that holds another material is
    refused when it was named (``strict``)."""
    check_kind(str(region.params.get("kind")), material, region.name)
    current = region.params.get("material")
    if current is None:
        plan.set(region.id, "material", material.id)
        plan.region_changed(region)
    elif current != material.id and strict:
        other = plan.project.find(current)
        raise ValidationError(
            f"region {region.name} holds {other.name if other else current}; region.set changes "
            "a region's material",
            details={"parameter": "name", "region": region.path},
        )
    return existing_region(plan, region)


@command(
    "region.assign",
    params=(
        ParamDef("material", "string", description="Material: id, path or name."),
        ParamDef(
            "target",
            "any",
            description="A region or a region tag (id, path or name): it takes the material; or "
            "bodies: " + BODIES_HELP,
        ),
        ParamDef(
            "name",
            "string",
            required=False,
            description="For bodies: the region to put them in (made when missing). Default: a "
            "region that has the material, else the only region of its kind without one, else a "
            "new region named after the material.",
        ),
        NODE_ID,
        TAG_NODE_ID,
    ),
)
def region_assign(
    ctx: CommandContext,
    material: str,
    target: Any,  # Any: a reference or a JSON list of bodies, checked by hand
    name: str | None = None,
    node_id: str | None = None,
    tag_node_id: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Assign a material, as one undo step, to a region (it takes the material) or to bodies:
    bodies already in a region of that material stay, the others move to the region for the
    material, made when none fits; a region left empty stays. A material of the other kind than
    its region is refused (materials.kind_mismatch)."""
    plan = Plan(ctx)
    project = plan.project
    found = material_node(project, material)
    region, parts = _target(ctx, project, target)
    held: tuple[str, str | None]
    moved: list[str] = []
    if region is not None:
        ref = _onto_region(plan, region, found)
        held = (ref.id, ref.tag)
        plan.label = f"assign {found.name} to {ref.name}"
    else:
        held, moved = _onto_bodies(plan, found, parts, name, (node_id, tag_node_id))
        count = len(dict.fromkeys(parts))
        plan.label = f"assign {found.name} to {count} bod{'y' if count == 1 else 'ies'}"
    plan.push()
    ctx.record(node_id=held[0], tag_node_id=held[1])
    return {
        "region": Lister(ctx).entry(project.get(held[0])),
        "bodies": list(dict.fromkeys(parts)),
        "moved": moved,
        "created": list(plan.created),
    }


def _onto_region(plan: Plan, region: Node, material: Node) -> RegionRef:
    """A region as the target: it takes the material (a plain-group region is typed with it)."""
    if region.type != REGION_TYPE:
        return plan_convert_region(plan, region, material_kind(material), material=material.id)
    ref = _adopt(plan, region, material, strict=False)
    if region.params.get("material") not in (None, material.id):
        plan.set(region.id, "material", material.id)
        plan.region_changed(region)
    return ref


def _onto_bodies(
    plan: Plan,
    material: Node,
    parts: list[str],
    name: str | None,
    ids: tuple[str | None, str | None],
) -> tuple[tuple[str, str | None], list[str]]:
    """Bodies as the target: those already in a region of the material stay, the others move
    to the region for it. Returns the (region id, tag id) the bodies are in, and those that
    moved."""
    project = plan.project
    if not parts:
        raise ValidationError("the target holds no bodies", details={"parameter": "target"})
    holders = [r for r in region_nodes(project) if r.params.get("material") == material.id]
    staying = {p for r in holders for p in bodies_of(ref_node(project, r, "tag", TAG_TYPE))}
    movers = [p for p in dict.fromkeys(parts) if p not in staying]
    if not movers:  # nothing moves and nothing is made: the region that holds the first body
        holder = next(
            r for r in holders if parts[0] in bodies_of(ref_node(project, r, "tag", TAG_TYPE))
        )
        return (holder.id, holder.params.get("tag")), []
    ref = _region_for(plan, material, name, movers, ids)
    return (ref.id, ref.tag), plan_move_bodies(plan, movers, ref)


@command(
    "region.set",
    params=(
        REGION,
        ParamDef("material", "string", required=False, description="Material: id, path or name."),
        ParamDef("kind", "string", required=False, description="fluid or solid."),
        ParamDef(
            "members",
            "array",
            required=False,
            description="The region's bodies from now on: " + BODIES_HELP + " They leave any "
            "other region.",
        ),
        ParamDef("name", "string", required=False, description="A new name."),
    ),
)
def region_set(
    ctx: CommandContext,
    region: str,
    material: str | None = None,
    kind: str | None = None,
    members: list[Any] | None = None,  # Any: JSON bodies
    name: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Change a region's material, kind, bodies (tag.set on its tag; the bodies leave any other
    region) or name in one undo step. A material of the other kind is refused
    (materials.kind_mismatch). parameter.set with null clears the material."""
    plan = Plan(ctx)
    project = plan.project
    node = typed_region(project, region)
    final_kind = kind if kind is not None else str(node.params.get("kind"))
    if final_kind not in REGION_KINDS:
        raise ValidationError(
            f"kind must be one of {', '.join(REGION_KINDS)}; got {final_kind!r}",
            details={"parameter": "kind"},
        )
    chosen = (
        material_node(project, material)
        if material is not None
        else ref_node(project, node, "material", MATERIAL_TYPE)
    )
    if chosen is not None:
        check_kind(final_kind, chosen, node.name)
    if final_kind != node.params.get("kind"):
        plan.set(node.id, "kind", final_kind)
    if chosen is not None and material is not None and chosen.id != node.params.get("material"):
        plan.set(node.id, "material", chosen.id)
    if members is not None:
        _new_bodies(ctx, plan, node, members)
    if name is not None and name != node.name and node.parent is not None:
        plan.rename(node, plan.free(name, node.parent.id))
    plan.label = f"edit region {name or node.name}"
    plan.region_changed(node)
    changed = plan.push()
    return {**Lister(ctx).entry(node), "changed": changed}


def _new_bodies(ctx: CommandContext, plan: Plan, node: Node, raw: list[Any]) -> None:  # Any
    tag = ref_node(plan.project, node, "tag", TAG_TYPE)
    if tag is None:
        raise ValidationError(
            f"{node.path} has no tag to hold its bodies", details={"parameter": "members"}
        )
    parts = parts_of(ctx, raw, "members") if raw else []
    text = whole_bodies(parts)
    if text != tag.params.get("members"):
        plan.set(tag.id, "members", text)
        plan.tag_changed(tag)
    plan_leave_regions(plan, parts, node.id, tag.id)


@command(
    "region.delete",
    params=(
        REGION,
        ParamDef(
            "delete_tag",
            "boolean",
            required=False,
            default=False,
            description="Delete the region's tag too; refused while another node uses it.",
        ),
    ),
)
def region_delete(ctx: CommandContext, region: str, delete_tag: bool = False) -> dict[str, Any]:
    """Delete a region with its boundaries (a plain-group region too) in one undo step: partners
    that named one of its boundaries are cleared, and with delete_tag the region tag goes too."""
    plan = Plan(ctx)
    project = plan.project
    node = find_region(project, region)
    if node is None:
        raise UnknownNode(f"no region {region!r}", details={"parameter": "region", "ref": region})
    path = node.path
    tag = ref_node(project, node, "tag", TAG_TYPE) if node.type == REGION_TYPE else None
    inner = [b for b in boundary_nodes(project, node) if b.type == BOUNDARY_TYPE]
    plan.remove(node)
    plan.region_changed(node)
    for boundary in inner:
        plan.boundary_changed(boundary)
    clear_references(plan, [node])
    if delete_tag and tag is not None:
        others = [(n, p) for n, p in project.referrers(tag.id) if not plan.gone(n.id)]
        if others:
            raise referenced_error(
                tag, others, hint="delete_tag deletes only a tag nothing else uses"
            )
        plan.remove(tag)
        plan.tag_changed(tag)
    plan.label = f"delete region {node.name}"
    plan.push()
    return {
        "id": node.id,
        "path": path,
        "name": node.name,
        "deleted": True,
        "boundaries": [b.id for b in inner],
        "tag_deleted": bool(delete_tag and tag is not None),
    }


# ---------------------------------------------------------------- list


class Lister:
    """What ``region.list`` reads about tags once per call: staleness of meshes in memory, and
    face counts for the faces no boundary of a region covers."""

    def __init__(self, ctx: CommandContext) -> None:
        from stokes.api.commands.selection import _Geometry

        self.geo: _Geometry = _Geometry(ctx)
        self.project: Project = self.geo.project

    def entry(self, node: Node) -> dict[str, Any]:  # Any: JSON
        """A region as ``region.list`` lists it."""
        project = self.project
        tag = ref_node(project, node, "tag", TAG_TYPE)
        try:
            bodies = stored_members(tag) if tag is not None else ()
        except StokesError:
            bodies = ()
        material = ref_node(project, node, MATERIAL_TYPE, MATERIAL_TYPE)
        held = node.params.get(MATERIAL_TYPE)
        kind = str(node.params.get("kind"))
        boundaries = boundary_nodes(project, node)
        stale = any(self.geo.stale(m, load=False) for m in bodies)
        warnings = _codes(tag, bodies, held, material, kind, stale)
        status = "failed" if tag is None else "stale" if stale else "warning" if warnings else "ok"
        return {
            "id": node.id,
            "path": node.path,
            "name": node.name,
            "kind": kind,
            "material": None
            if held is None
            else {
                "id": held,
                "name": material.name if material is not None else None,
                "kind": material.params.get("kind") if material is not None else None,
                "missing": material is None,
            },
            "tag": None
            if tag is None
            else {
                "id": tag.id,
                "name": tag.name,
                "path": tag.path,
                "color": tag.params.get("color"),
                "visible": tag.params.get("visible"),
            },
            "bodies": [m.part for m in bodies],
            "boundaries": [b.id for b in boundaries],
            "uncovered_faces": self.uncovered(bodies, boundaries),
            "stale": stale,
            "warnings": warnings,
            "status": status,
        }

    def uncovered(self, bodies: tuple[Member, ...], boundaries: list[Node]) -> int | None:
        """Faces of the region's bodies that no boundary tag of the region holds (they become
        no-slip walls when the case is compiled); None while a body's face count is unknown.
        Range arithmetic, never expanded to face ids."""
        from stokes.geometry.selection import merge

        whole: set[str] = set()
        covered: dict[str, list[tuple[int, int]]] = {}
        for boundary in boundaries:
            tag = ref_node(self.project, boundary, "tag", TAG_TYPE)
            try:
                members = stored_members(tag) if tag is not None else ()
            except StokesError:
                continue
            for member in members:
                if member.faces is None:
                    whole.add(member.part)
                else:
                    covered.setdefault(member.part, []).extend(member.faces)
        total = 0
        for body in bodies:
            if body.part in whole:
                continue
            count = self.geo.count(body.part)
            if count is None:
                return None
            ranges = merge(covered.get(body.part, ()))
            total += count - sum(
                min(last, count - 1) - first + 1 for first, last in ranges if first < count
            )
        return total


def _codes(
    tag: Node | None,
    bodies: tuple[Member, ...],
    held: Any,  # Any: the stored material reference
    material: Node | None,
    kind: str,
    stale: bool,
) -> list[str]:
    """The region's own findings, as ``setup.check`` names them."""
    codes: list[str] = []
    if tag is None:
        codes.append("setup.tag_missing")
    elif not bodies:
        codes.append("setup.empty_region" if held is not None else "setup.empty_tag")
    if held is None or material is None:
        codes.append("setup.no_material")
    elif material_kind(material) != kind:
        codes.append("setup.material_kind")
    if stale:
        codes.append("setup.stale_tag")
    return codes


@command("region.list", mutates=False)
def region_list(ctx: CommandContext) -> list[dict[str, Any]]:  # Any: JSON
    """Every region with its kind, material, tag, bodies, boundaries, the faces of its bodies no
    boundary covers, staleness, findings and derived status; plain groups from before typed
    regions as legacy entries."""
    lister = Lister(ctx)
    items: list[dict[str, Any]] = []  # Any: JSON
    for node in regions_group(lister.project).children:
        if node.type == REGION_TYPE:
            items.append(lister.entry(node))
        elif is_legacy_region(node):
            items.append({"id": node.id, "path": node.path, "name": node.name, "legacy": True})
    return items
