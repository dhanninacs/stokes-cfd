"""``setup.*`` commands (ADR-0022 decisions 5.1 to 5.4 and 10.1): the set-up check and the
default conditions of the two flow helpers.

``setup.check`` reads the project and the tag staleness the session knows, and answers with a
readiness state, the next thing to do, counts and the findings of the ADR's table. It never
writes to the tree, emits nothing and is not journaled (``mutates: false``); the front end calls
it after ``node.*``, ``tag.changed`` and ``boundary.changed``, the CLI and MCP directly.

``setup.defaults`` (T-039) turns a prepared body — an enclosure output (ADR-0021 6.2) or an
extracted fluid (6.1) — into a set-up of one region, one material and typed boundaries in ONE undo
step: the tag names the operation gave, which ``stokes.physics.conditions.templates`` maps to
condition kinds, become the boundaries; every face of the body no tag holds becomes a no-slip
wall; the region takes the material it is given, its own, the project's only fluid one or a new
one from a built-in template. One :class:`~stokes.api.commands.setup_plan.Plan` builds every model
command, so one undo takes the whole set-up back, and one journal record replays it: the ids it
chose travel back in ``node_ids``.

Contract for clients: ``docs/engineering/setup-schema.md`` (Checks and ``setup.defaults``).

Import rule: the standard library, the registry, ``stokes.geometry.model``, the check and the
template tables (standard library only) at module level; the geometry store (numpy, the meshes in
memory), the set-up planning, the tag internals and the material authoring load inside the
handlers, where they are needed.

Size: the check and the helper share this module because they are one stage of the product and the
helper answers what the check asks next; the helper's phases live in :class:`_Defaults` below, one
method each, because they read as one plan when they are read together.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import StokesError, UnknownNode, ValidationError
from stokes.geometry.errors import SelectionError
from stokes.geometry.model import (
    BOUNDARY_TYPE,
    DEFAULT_REGION,
    REGION_TYPE,
    TAG_TYPE,
    TAGS_PATH,
)
from stokes.physics.conditions.checks import check_setup
from stokes.physics.conditions.templates import TEMPLATES

if TYPE_CHECKING:
    from stokes.api.commands.selection import _Geometry
    from stokes.api.commands.setup_plan import RegionRef
    from stokes.geometry.selection import Member
    from stokes.model import Node, Project

FLUID = DEFAULT_REGION
"""The fluid kind of a region, and the name of the region the helper falls back to: one word."""
INTERNAL_TEMPLATE = "internal"
"""The template whose leftover ``caps`` faces become walls and one warning."""
WALLS = "walls"
"""The tag the faces no boundary-kind tag holds go to, in both templates."""
CAPS = "caps"
"""The tag an extraction gives its openings."""
VELOCITY_INLET = "velocity_inlet"
"""The kind ``velocity`` is written to, and the only kind this command sets one for."""
PART = "part"
"""The node type of an imported solid, a valid target of its own."""


def stale_tags(ctx: CommandContext) -> set[str]:
    """The ids of the tags whose members were picked at an earlier version of their part, as the
    session knows them (``tag.list``'s staleness, read with ``load=False``).

    A mesh that is not in memory has no current basis to compare with, so the CLI without meshes
    answers with nothing: staleness is then unknown, never wrongly "fresh", and ``setup.check``
    makes no ``setup.stale_tag`` finding.
    """
    from stokes.api.commands.selection import _Geometry
    from stokes.api.commands.setup_plan import stored_members

    project: Project = ctx.session.require_project()
    group = project.find(TAGS_PATH)
    if group is None:
        return set()
    try:
        geo = _Geometry(ctx)
    except StokesError:
        return set()
    found: set[str] = set()
    for tag in group.children:
        if tag.type != TAG_TYPE:
            continue
        try:
            members = stored_members(tag)
        except StokesError:
            continue
        if any(geo.stale(member, load=False) for member in members):
            found.add(tag.id)
    return found


@command("setup.check", mutates=False, requires_project=True)
def setup_check(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """Whether the set-up can go on: ``{state, next, counts, findings}``, errors first. States
    (decision 5.2): not_defined (no region holds a body), incomplete (an error), stale (a missing
    or stale tag), ready. The findings are the codes of the ADR table with their severity, a
    sentence, a suggestion, the nodes they are about and the fix where one exists."""
    project: Project = ctx.session.require_project()
    return check_setup(project, stale_tags(ctx)).to_json()


# ---------------------------------------------------------------- setup.defaults

TARGET = ParamDef(
    "target",
    "string",
    description="The prepared body: a part (id or path) or a region (id, path or name); a "
    "region stands for the parts its tag holds.",
)
TEMPLATE = ParamDef(
    "template",
    "string",
    description="external (after an enclosure) or internal (after a fluid extraction): which tag "
    "names become which conditions (docs/engineering/setup-schema.md).",
)
INLET = ParamDef(
    "inlet",
    "array",
    required=False,
    description="The inlet faces, in the tag commands' members form; a tag named inlet is made "
    "or set to them.",
)
OUTLET = ParamDef(
    "outlet",
    "array",
    required=False,
    description="As inlet, for the tag named outlet.",
)
MATERIAL = ParamDef(
    "material",
    "string",
    required=False,
    description="Material: id, path or name; default the region's own, the project's only fluid "
    "material, else a new air (external) or water (internal) from the built-in templates.",
)
VELOCITY = ParamDef(
    "velocity",
    "any",
    required=False,
    description="The velocity inlet's velocity: quantity text such as '2 [m/s]', or a number in "
    "SI; left out, setup.check asks for it.",
)
NODE_IDS = ParamDef(
    "node_ids",
    "object",
    required=False,
    description="Journals: the ids this command made, by name: region, material, and tags and "
    "boundaries keyed by the template's tag names.",
)


@command(
    "setup.defaults",
    params=(TARGET, TEMPLATE, INLET, OUTLET, MATERIAL, VELOCITY, NODE_IDS),
)
def setup_defaults(
    ctx: CommandContext,
    target: str,
    template: str,
    inlet: list[Any] | None = None,  # Any: JSON members
    outlet: list[Any] | None = None,  # Any: JSON members
    material: str | None = None,
    velocity: Any = None,  # Any: quantity text or a number in SI
    node_ids: dict[str, Any] | None = None,  # Any: the ids a replay recorded
) -> dict[str, Any]:  # Any: JSON
    """Give a prepared body the default conditions of one flow template, in one undo step: the
    tags named there become their kinds (inlet a velocity inlet, outlet a pressure outlet, the far
    field a slip wall, the rest no-slip walls), the picked inlet and outlet faces make or set their
    tags, every face of the body no tag holds becomes a ``walls`` wall, and the body's region (made
    when no region holds it) takes ``material``, the project's only fluid material or a new air
    (external) or water (internal). A boundary that already uses a qualifying tag is left alone.
    ``velocity`` is written to the velocity inlet's ``velocity``. The result names the region, the
    material, the boundaries with their tags, how many faces went to walls, what was skipped and
    what to know."""
    if template not in TEMPLATES:
        raise ValidationError(
            f"template must be one of {', '.join(TEMPLATES)}; got {template!r}",
            details={"parameter": "template"},
        )
    from stokes.api.commands.selection import _Geometry

    ids = _Ids.of(node_ids)
    geo = _Geometry(ctx)
    parts, holder = _target(geo, target)
    if holder is None:  # a part: the region that already holds it is the set-up's (decision 4.1)
        holder = _holding_region(geo.project, parts)
    picks = _picks(geo, parts, inlet, outlet)
    job = _Defaults(ctx, template, geo, ids, parts, holder)
    chosen = job.choose_material(material)
    region = job.choose_region(chosen)
    job.move_bodies(region)
    job.boundaries(region, picks, velocity)
    job.walls(region)
    job.warn_unwritten_velocity(velocity)
    job.plan.push(check=_check_all(job.checks))
    ctx.record(node_ids=ids.dump())
    return job.result(region, chosen)


# ---------------------------------------------------------------- the ids a replay reuses


@dataclass
class _Ids:
    """The ids a journal record carries, so that a replay builds the same tree: the region and the
    material, and by the tag name of the template the tags and the boundaries the command made."""

    region: str | None = None
    material: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    boundaries: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, raw: dict[str, Any] | None) -> _Ids:  # Any: the recorded JSON object
        """The recorded ids, checked: a client passes back the object this command recorded."""
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValidationError("node_ids must be an object", details={"parameter": "node_ids"})
        return cls(
            region=_one_id(raw.get("region"), "region"),
            material=_one_id(raw.get("material"), "material"),
            tags=_many_ids(raw.get("tags"), "tags"),
            boundaries=_many_ids(raw.get("boundaries"), "boundaries"),
        )

    def dump(self) -> dict[str, Any]:  # Any: JSON
        """What ``ctx.record`` journals: the nodes this call made, and nothing else."""
        data: dict[str, Any] = {}  # Any: JSON
        if self.region is not None:
            data["region"] = self.region
        if self.material is not None:
            data["material"] = self.material
        if self.tags:
            data["tags"] = dict(self.tags)
        if self.boundaries:
            data["boundaries"] = dict(self.boundaries)
        return data


def _one_id(value: Any, what: str) -> str | None:  # Any: recorded JSON
    """A recorded node id, or None; anything else is refused (the journal is untrusted input)."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValidationError(
            f"node_ids.{what} must be a node id", details={"parameter": "node_ids"}
        )
    return value


def _many_ids(value: Any, what: str) -> dict[str, str]:  # Any: recorded JSON
    """Recorded ids by name, or an empty mapping."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(found, str) and found for name, found in value.items()
    ):
        raise ValidationError(
            f"node_ids.{what} must map names to node ids", details={"parameter": "node_ids"}
        )
    return {str(name): str(found) for name, found in value.items()}


# ---------------------------------------------------------------- the target and its faces


def _target(geo: _Geometry, target: str) -> tuple[list[str], Node | None]:
    """The parts the set-up is for, and the region node when the target is a region.

    A bare name that is no node of its own is looked up among the regions, as the region commands
    do; anything that is neither a part nor a region is refused.
    """
    from stokes.api.commands.setup_plan import bodies_of, find_region, is_legacy_region, ref_node

    project = geo.project
    if not target.strip():
        raise ValidationError(
            "target must be a part or a region (id, path or name)", details={"parameter": "target"}
        )
    node = project.find(target)
    if node is None and "/" not in target and "." not in target:
        node = find_region(project, target)
    if node is None:
        raise UnknownNode(
            f"no part or region {target!r}", details={"parameter": "target", "ref": target}
        )
    if node.type == PART:
        return [node.id], None
    if node.type == REGION_TYPE or is_legacy_region(node):
        parts = list(dict.fromkeys(bodies_of(ref_node(project, node, "tag", TAG_TYPE))))
        if not parts:
            raise ValidationError(f"{node.path} holds no bodies", details={"parameter": "target"})
        return parts, node
    raise ValidationError(
        f"{node.path} is a {node.type}, not a part or a region", details={"parameter": "target"}
    )


def _picks(
    geo: _Geometry, parts: Sequence[str], inlet: list[Any] | None, outlet: list[Any] | None
) -> dict[str, tuple[Member, ...]]:
    """The inlet and outlet faces, parsed and stamped as the tag commands stamp them; faces that
    are not the target's are refused (a cap of another body is not this flow's opening)."""
    from stokes.api.commands.selection import _given, _stamp

    target = set(parts)
    found: dict[str, tuple[Member, ...]] = {}
    for name, raw in (("inlet", inlet), ("outlet", outlet)):
        if raw is None:
            continue
        try:
            chosen = _given(geo, raw)
        except SelectionError as exc:
            exc.details.setdefault("parameter", name)
            raise
        if not chosen:
            raise SelectionError(f"{name} holds no faces", details={"parameter": name})
        stamped = _stamp(geo, chosen, {m.part for m in chosen})
        strangers = sorted({m.part for m in stamped if m.part not in target})
        if strangers:
            raise ValidationError(
                f"the {name} faces are not on the target: {geo.path(strangers[0])} is not one of "
                "its parts",
                details={"parameter": name, "part": strangers[0]},
            )
        found[name] = stamped
    return found


# ---------------------------------------------------------------- the tags of the tree


def _boundary_tags(project: Project) -> list[Node]:
    """Every boundary-kind tag under ``/geometry/tags``, in tree order: a tag of another kind
    holds bodies or a working set, not a patch."""
    group = project.find(TAGS_PATH)
    if group is None:
        return []
    return [c for c in group.children if c.type == TAG_TYPE and c.params.get("kind") == "boundary"]


def _named_tag(project: Project, name: str) -> Node | None:
    """The boundary-kind tag named exactly ``name``, when the project has one: the name is how an
    operation's tags qualify (decision 10.1)."""
    group = project.find(TAGS_PATH)
    node = group.child(name) if group is not None else None
    if node is None or node.type != TAG_TYPE or node.params.get("kind") != "boundary":
        return None
    return node


def _holding_region(project: Project, parts: Sequence[str]) -> Node | None:
    """The first fluid region whose tag holds one of the parts: a body reaches a material only
    through a region (decision 4.1), so that region is the set-up's."""
    from stokes.api.commands.setup_plan import bodies_of, ref_node, region_nodes

    wanted = set(parts)
    for region in region_nodes(project):
        if region.params.get("kind") != FLUID:
            continue
        tag = ref_node(project, region, "tag", TAG_TYPE)
        if tag is not None and wanted & set(bodies_of(tag)):
            return region
    return None


def _region_kind(holder: Node | None) -> str:
    """The kind of the region the set-up goes in; a region the tree lacks, and a plain group from
    before typed regions, counts as fluid, which is what it is made as."""
    if holder is None or holder.type != REGION_TYPE:
        return FLUID
    return str(holder.params.get("kind") or FLUID)


def _region_name(holder: Node | None) -> str:
    return holder.name if holder is not None else FLUID


def _faces_of(members: Sequence[Member]) -> int:
    """How many faces the members hold (an empty face list is never stored)."""
    return sum(int(m.size(None) or 0) for m in members)


def _cap_warning(faces: int) -> str:
    """The sentence the result carries for the cap faces that became walls (decision 10.1)."""
    if faces == 1:
        return (
            "1 cap face of the extraction is in neither the inlet nor the outlet: it became a wall"
        )
    return (
        f"{faces} cap faces of the extraction are in neither the inlet nor the outlet: they "
        "became walls"
    )


def _check_all(checks: Sequence[Callable[[Project], None]]) -> Callable[[Project], None]:
    """The one check ``Plan.push`` takes: every check of the step, in the order it was planned
    (the values of each boundary once evaluated, and the material's properties)."""

    def check(project: Project) -> None:
        for one in checks:
            one(project)

    return check


def _brief(node: Node, *params: str) -> dict[str, Any]:  # Any: JSON
    """A node as this command's result names it: its id, path, name and the given parameters."""
    return {
        "id": node.id,
        "path": node.path,
        "name": node.name,
        **{name: node.params.get(name) for name in params},
    }


# ---------------------------------------------------------------- one call


class _Defaults:
    """One ``setup.defaults`` call: what it reads once, the one :class:`Plan` it builds, and what
    the result reads. One instance is one execution, and nothing is kept between calls.

    The phases run in the order the plan needs them (the material decides what the region is made
    with, the region what the boundaries hang under), and every refusal is raised while planning,
    so a command that fails leaves the tree exactly as it was.
    """

    def __init__(
        self,
        ctx: CommandContext,
        template: str,
        geo: _Geometry,
        ids: _Ids,
        parts: Sequence[str],
        holder: Node | None,
    ) -> None:
        from stokes.api.commands.setup_plan import Plan

        self.ctx = ctx
        self.template = template
        self.geo = geo
        self.ids = ids
        self.parts = list(dict.fromkeys(parts))
        self.holder = holder
        self.plan = Plan(ctx, f"add default conditions ({template})")
        self.rows: list[dict[str, Any]] = []  # Any: JSON
        self.skipped: list[str] = []
        self.warnings: list[str] = []
        self.checks: list[Callable[[Project], None]] = []
        self.walls_faces = 0
        self.material_created = False
        # Whether velocity reached a boundary: the result warns when it did not.
        self.written = False
        self._held = self._tree_members()
        self._named: dict[str, tuple[Member, ...]] = {}
        # The cap faces the extraction's tag gave up that no inlet or outlet named.
        self._caps_left: tuple[Member, ...] = ()
        self._users = _users(self.plan.project)

    # ------------------------------------------------------------ what the tree holds now
    def _tree_members(self) -> dict[str, tuple[Member, ...]]:
        """What every boundary-kind tag holds now, as it will hold it once the step applies. A tag
        whose text was set by hand counts as empty, as the set-up check reads it: ``tag.invalid``
        is reported where the text is written, not here."""
        from stokes.api.commands.setup_plan import stored_members

        held: dict[str, tuple[Member, ...]] = {}
        for tag in _boundary_tags(self.plan.project):
            try:
                held[tag.id] = stored_members(tag)
            except StokesError:
                held[tag.id] = ()
        return held

    # ------------------------------------------------------------ the material
    def choose_material(self, material: str | None) -> str:
        """The material of the region (decision 4.3): the one given, the region's own when it has
        one, the project's only material of that kind, else a new one from the built-in template —
        ``gas``/``air`` for an external flow, ``liquid``/``water`` for an internal one, which the
        result marks ``material_created``."""
        from stokes.api.commands.region import (
            MATERIAL_TYPE,
            check_kind,
            material_kind,
            material_node,
        )

        project = self.plan.project
        kind = _region_kind(self.holder)
        where = _region_name(self.holder)
        if material is not None:
            found = material_node(project, material)
            check_kind(kind, found, where)
            return found.id
        held = (
            _ref(project, self.holder, "material", MATERIAL_TYPE)
            if self.holder is not None
            else None
        )
        if held is not None and material_kind(held) == kind:
            return held.id
        same = [
            node
            for node in project.nodes()
            if node.type == MATERIAL_TYPE and material_kind(node) == kind
        ]
        if len(same) == 1:
            return same[0].id
        return self._make_material()

    def _make_material(self) -> str:
        """A new fluid material and its properties, from the template's built-in record.

        Its model commands join this plan, so the material is part of the same undo step; its
        check joins the step's check, so a property the catalogue cannot resolve reverts it. The
        materials group is structural and is added outside the step when an old project lacks it,
        as ``material.create`` does."""
        from stokes.model.ids import new_id
        from stokes.physics.materials import authoring
        from stokes.physics.materials.model import MATERIALS_PATH

        project = self.plan.project
        added = authoring.ensure_materials_group(project)
        if added is not None:
            self.ctx.session.emit("node.added", node_id=added.id, path=added.path)
        group = added or project.get(MATERIALS_PATH)
        template = "liquid" if self.template == INTERNAL_TEMPLATE else "gas"
        name = self.plan.unique("water" if template == "liquid" else "air", group.id)
        spec = authoring.template(template)
        material = self.ids.material or new_id()
        self.ids.material = material
        params: dict[str, Any] = {  # Any: JSON parameter values
            "kind": FLUID,
            "source": "user",
            "provider": "none",
            "description": "",
            **spec["state"],
        }
        command = authoring.create_command(
            name,
            FLUID,
            params,
            spec["properties"],
            node_id=material,
            material_meta={"template": {"name": template, "record": spec["record"]}},
        )
        self.plan.commands.extend(command.commands)
        self.checks.append(authoring.check_all(material))
        self.material_created = True
        return material

    # ------------------------------------------------------------ the region
    def choose_region(self, material: str) -> RegionRef:
        """The region the set-up goes in (decision 10.1): the target region, or the fluid region
        that already holds the target; else ``/regions/fluid`` (a plain group of that name is
        typed); else a new fluid region named ``fluid``. A region made here is made with the
        bodies and the material, so it holds both from the first apply."""
        from stokes.api.commands.setup_plan import (
            existing_region,
            find_region,
            plan_convert_region,
            plan_new_region,
            regions_group,
            use_region,
            whole_bodies,
        )

        project = self.plan.project
        bodies = whole_bodies(self.parts)
        if self.holder is not None:
            ref = use_region(self.plan, self.holder, FLUID)
        else:
            node = find_region(project, FLUID)
            if node is None or (node.type == REGION_TYPE and _region_kind(node) != FLUID):
                ref = plan_new_region(
                    self.plan,
                    self.plan.unique(FLUID, regions_group(project).id),
                    FLUID,
                    material=material,
                    bodies=bodies,
                    node_id=self.ids.region,
                )
            elif node.type == REGION_TYPE:
                ref = existing_region(self.plan, node)
            else:
                ref = plan_convert_region(self.plan, node, FLUID, material=material, bodies=bodies)
        self.ids.region = ref.id
        keeps = not (ref.created or ref.converted)  # a region made here holds the material already
        if keeps and project.get(ref.id).params.get("material") != material:
            self.plan.set(ref.id, "material", material)
            self.plan.region_changed(ref.id)
        return ref

    def move_bodies(self, region: RegionRef) -> None:
        """The target's parts are in that region and in no other (decision 4.1): a part that came
        from another region leaves its tag, a part the new region was made with is already in."""
        from stokes.api.commands.setup_plan import plan_move_bodies

        plan_move_bodies(self.plan, self.parts, region)

    # ------------------------------------------------------------ the boundaries
    def boundaries(
        self,
        region: RegionRef,
        picks: Mapping[str, tuple[Member, ...]],
        velocity: Any,  # Any: quantity text or a number in SI
    ) -> None:
        """Plan the template's boundaries in its order (decision 10.1): a tag that qualifies — its
        name is in the table and it holds a face of the target — becomes its condition, and a tag a
        boundary already uses is left alone and listed in ``skipped``."""
        target = set(self.parts)
        for name, (kind, values) in TEMPLATES[self.template].items():
            if name == WALLS:
                continue  # the walls tag collects the faces no tag holds, after the others
            tag = _named_tag(self.plan.project, name)
            held = self._held.get(tag.id, ()) if tag is not None else ()
            used = self._users.get(tag.id, []) if tag is not None else []
            if used:
                self.skipped.append(used[0].name)
                self._named[name] = held
                continue
            given = picks.get(name)
            members = given if given is not None else held
            if not any(m.part in target for m in members):
                continue  # no such tag, or a tag that holds no face of the target
            self._named[name] = members
            self.plan_boundary(
                region, name, tag, members, kind, values, velocity, write=given is not None
            )
        self.take_caps()

    def take_caps(self) -> None:
        """Give the extraction's openings to the boundaries: the target's faces leave the
        operation's ``caps`` tag, which the inlet and outlet tags, and the walls, now hold.

        The ``caps`` tag is what the extraction left so that the user can name the openings
        (ADR-0021 6.1), and a face in two boundary tags is refused when the case is compiled
        (T-033's ``tag.overlap``): the named caps and the leftover ones alike leave it, so the
        set-up the helper makes compiles. The tag's other members — another body's caps — stay.
        What it gave up is remembered for the warning about the caps no boundary named."""
        if self.template != INTERNAL_TEMPLATE:
            return
        from stokes.api.commands.setup_plan import members_text
        from stokes.geometry.selection import Member, difference

        tag = _named_tag(self.plan.project, CAPS)
        if tag is None or self._users.get(tag.id):
            return  # no openings tag, or one a boundary already uses: it is left alone
        held = self._held.get(tag.id, ())
        left = difference(held, [Member(part, None) for part in self.parts], self.geo.count)
        if len(left) == len(held):
            return  # nothing of the target is in it
        self.plan.set(tag.id, "members", members_text(left))
        self.plan.tag_changed(tag)
        self._held[tag.id] = left
        named = _union(self._named.get("inlet", ()), self._named.get("outlet", ()))
        self._caps_left = difference(held, named, self.geo.count)

    def plan_boundary(
        self,
        region: RegionRef,
        name: str,
        tag: Node | None,
        members: tuple[Member, ...],
        kind: str,
        values: Mapping[str, str],
        velocity: Any,  # Any: quantity text or a number in SI
        *,
        write: bool,
    ) -> None:
        """Plan one boundary of ``kind`` over the tag ``name``: a new tag holding ``members`` when
        there is none, the given faces when ``write``, the kind's colour, and the boundary with its
        values — the model commands ``boundary.assign`` plans through ``boundary._onto_new``.

        ``_onto_new`` is not called itself: it takes its region from ``default_region``, which
        reads the tree and cannot see a region this step makes, and the walls phase extends an
        existing tag rather than replacing its faces."""
        from stokes.api.commands.boundary import available, recolour, rules_check, uses_of
        from stokes.api.commands.setup_plan import members_text, plan_tag, plan_tags_group
        from stokes.model.ids import new_id

        plan = self.plan
        spec = available(kind)
        assigned: dict[str, Any] = dict(values)  # Any: JSON values
        if spec.id == VELOCITY_INLET and velocity is not None:
            assigned["velocity"] = velocity
            self.written = True
        uses = uses_of(spec, {}, assigned)
        if tag is None:
            tags = plan_tags_group(plan)
            tag_name = name if name not in plan.taken(tags) else plan.unique(name, tags)
            tag_id = plan_tag(
                plan,
                tag_name,
                "boundary",
                members_text(members),
                spec.color,
                self.ids.tags.get(name),
            )
            self.ids.tags[name] = tag_id
        else:
            tag_name, tag_id = tag.name, tag.id
            if write:
                text = members_text(members)
                if text != tag.params.get("members"):
                    plan.set(tag.id, "members", text)
                    plan.tag_changed(tag)
            recolour(plan, tag, spec.color)
        self._held[tag_id] = members
        boundary = self.ids.boundaries.get(name) or new_id()
        self.ids.boundaries[name] = boundary
        boundary_name = plan.unique(tag_name, region.boundaries)
        plan.add(
            region.boundaries,
            BOUNDARY_TYPE,
            boundary_name,
            {"kind": spec.id, "tag": tag_id},
            boundary,
        )
        for pname, value in assigned.items():
            plan.set(boundary, pname, value)
        plan.boundary_changed(boundary)
        self.checks.append(rules_check(boundary, uses))
        self.rows.append({"name": boundary_name, "kind": spec.id, "tag": tag_id})

    def walls(self, region: RegionRef) -> None:
        """The faces of the target that no boundary-kind tag holds become the ``walls`` tag's (the
        cap faces the extraction's tag gave up are among them), with a warning saying how many of
        them were caps neither the inlet nor the outlet named (decision 10.1). The tag is made or
        extended; a boundary that already uses it is left alone."""
        kind, values = TEMPLATES[self.template][WALLS]
        tag = _named_tag(self.plan.project, WALLS)
        existing = self._held.get(tag.id, ()) if tag is not None else ()
        additions = self._leftover()
        walled = _union(existing, additions)
        if not any(m.part in set(self.parts) for m in walled):
            return  # nothing of the target is uncovered: no walls tag is made
        self.walls_faces = _faces_of(_difference(additions, existing, self.geo))
        became = _difference(self._caps_left, existing, self.geo)
        if became:
            self.warnings.append(_cap_warning(_faces_of(became)))
        used = self._users.get(tag.id, []) if tag is not None else []
        if used:
            self.skipped.append(used[0].name)
            if tag is not None:
                self._extend(tag, walled)
            return
        self.plan_boundary(region, WALLS, tag, walled, kind, values, None, write=True)

    def _extend(self, tag: Node, members: tuple[Member, ...]) -> None:
        """Give an existing tag the faces it gains, inside the step."""
        from stokes.api.commands.setup_plan import members_text

        text = members_text(members)
        if text != tag.params.get("members"):
            self.plan.set(tag.id, "members", text)
            self.plan.tag_changed(tag)
            self._held[tag.id] = members

    def _leftover(self) -> tuple[Member, ...]:
        """The target's faces that no boundary-kind tag holds, by range arithmetic: a whole part
        another tag holds has none, and a part whose face count is unknown cannot be judged."""
        from stokes.geometry.selection import Member, merge, subtract

        target = set(self.parts)
        covered = [m for held in self._held.values() for m in held if m.part in target]
        whole = {m.part for m in covered if m.faces is None}
        found: list[Member] = []
        for part in self.parts:
            if part in whole:
                continue
            count = self.geo.count(part)
            if count is None:
                raise SelectionError(
                    f"{self.geo.path(part)} has no face count yet: wait for its import, or give "
                    "its faces a tag by hand",
                    details={"part": part},
                )
            ranges = merge(r for m in covered if m.part == part for r in (m.faces or ()))
            rest = subtract(((0, count - 1),), ranges)
            if rest:
                found.append(Member(part, rest))
        return tuple(found)

    def warn_unwritten_velocity(self, velocity: Any) -> None:  # Any: quantity text or SI number
        """A velocity that reached no boundary is worth saying: the target has no inlet, or its
        inlet already has a boundary, and the result would otherwise read as if it were given."""
        if velocity is not None and not self.written:
            self.warnings.append(
                "velocity was given but no velocity inlet was made: the target has no inlet, or "
                "its inlet already has a boundary (boundary.set changes it)"
            )

    # ------------------------------------------------------------ the answer
    def result(self, region: RegionRef, material: str) -> dict[str, Any]:  # Any: JSON
        """What the command answers: the region, the material, the boundaries it planned with
        their tags, the faces it gave to walls, what it left alone and what to know."""
        project = self.plan.project
        return {
            "region": _brief(project.get(region.id), "kind"),
            "material": _brief(project.get(material), "kind"),
            "material_created": self.material_created,
            "boundaries": list(self.rows),
            "walls_faces": self.walls_faces,
            "skipped": list(self.skipped),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------- the helpers that read the plan


def _users(project: Project) -> dict[str, list[Node]]:
    """The boundaries that use each tag, by tag id, in one walk of the tree."""
    from stokes.api.commands.setup_plan import users_of

    return users_of(project, (BOUNDARY_TYPE,))


def _ref(project: Project, node: Node, param: str, node_type: str) -> Node | None:
    from stokes.api.commands.setup_plan import ref_node

    return ref_node(project, node, param, node_type)


def _union(have: Sequence[Member], add: Sequence[Member]) -> tuple[Member, ...]:
    from stokes.geometry.selection import union

    return union(have, add)


def _difference(
    have: Sequence[Member], drop: Sequence[Member], geo: _Geometry
) -> tuple[Member, ...]:
    """``have`` without the faces of ``drop``; a whole part needs its face count, which the
    geometry store has (the node's ``faces``, else its mesh)."""
    from stokes.geometry.selection import difference

    return difference(have, drop, geo.count)
