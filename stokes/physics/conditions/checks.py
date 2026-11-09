"""The set-up check: readiness states and findings (ADR-0022 decisions 5.1 to 5.4, 2.7, 2.10).

:func:`check_setup` reads a project and the tag staleness the session knows, and returns a
:class:`CheckResult`: the readiness ``state``, the ``next`` thing to do, the ``counts`` and every
finding of the table in ``docs/engineering/setup-schema.md`` (Checks) with its stable code, its
severity, a sentence, a suggestion, the nodes it is about and, where one exists, the ``fix`` that
mends it. It never writes to the tree: ``setup.check`` (``stokes/api/commands/setup.py``) gathers
staleness and calls this, the front end calls that command, and ``stokes setup check`` prints it.

**States**, in precedence: ``not_defined`` when no ``region`` node's tag holds a body;
``incomplete`` when an error is left; ``stale`` when ``setup.tag_missing`` (an error) or
``setup.stale_tag`` (a warning) is what is wrong and nothing else; ``ready`` otherwise, warnings
and information allowed. The ADR table makes a missing tag an error, and yet it must not read as
"incomplete": a tree someone edited by hand is mended by putting the tags right, which is what
``stale`` says, so ``setup.tag_missing`` is the one error that does not make the state
``incomplete``. ``next`` is the first error's suggestion, else the first warning's, else
"Set-up complete".

Findings come out errors, then warnings, then information, and inside a severity in tree order
(the order :meth:`Project.nodes` yields them; a finding about the whole project comes first).

**Findings**, one function each, are the sixteen rows of the ADR table (:data:`CODES`).
``setup.needs_physics`` is information until ``/physics/models`` exists (decision 2.7). The check
makes no finding the table gives no code for: an unknown ``kind`` (``boundary.list`` reports
``boundary.invalid_kind``) and a tag of the wrong kind stay where they are reported. One row
excludes another where they would say the same thing twice: a region whose tag is gone gets
``setup.tag_missing`` alone, not also ``setup.empty_tag`` or ``setup.empty_region``, because the
missing tag is what has to be mended first.

**Cost** is a walk of the tree and range arithmetic over the members of the tags in use: the
faces of a region's bodies that no boundary tag holds, and the faces two boundary tags share, are
counted from the stored ``[first, last]`` ranges and never expanded to face ids (4,500 parts).

Import rule: the standard library, ``stokes.errors``, ``stokes.geometry.model``,
``stokes.model.QuantityValue`` and the condition catalogue at module level; the set-up readers
(``stokes.api.commands.setup_plan``, ``region``) and the tag internals (numpy) inside the
functions that need them. Importing those at the top would re-enter ``stokes.api.commands`` while
that package is still importing the command this module serves. No Qt, no OCP, no numpy at module
level.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.errors import StokesError
from stokes.geometry.model import BOUNDARY_TYPE, REGIONS_PATH, TAG_TYPE, TAGS_PATH
from stokes.model import QuantityValue
from stokes.physics.conditions.catalogue import BY_ID, ConditionSpec, check_rule

if TYPE_CHECKING:
    from stokes.geometry.selection import Member
    from stokes.model import Node, Project

SEVERITIES: tuple[str, ...] = ("error", "warning", "info")
"""The severities of the ADR table, in the order the findings come out."""

STATES: tuple[str, ...] = ("not_defined", "incomplete", "stale", "ready")
"""The readiness states of decision 5.2, in precedence."""

COMPLETE = "Set-up complete"
"""What ``next`` says when no error and no warning is left."""

CODES: tuple[str, ...] = (
    "setup.no_region",
    "setup.no_material",
    "setup.material_kind",
    "setup.no_inlet",
    "setup.no_outlet",
    "setup.no_pressure",
    "setup.value_invalid",
    "setup.tag_missing",
    "setup.empty_tag",
    "setup.stale_tag",
    "setup.overlap",
    "setup.uncovered_faces",
    "setup.partner_missing",
    "setup.needs_physics",
    "setup.legacy",
    "setup.empty_region",
    # **Added after the ADR's sixteen, and appended rather than inserted**: `report.add` DROPS a
    # code
    # it does not know, so a finding with an unlisted code exists and is never shown - which is how
    # this one first failed its test with a list that simply lacked it. Appended, because the ADR's
    # table order is what the first sixteen are in and a new code has no place in that table.
    "setup.excluded_body",
    # **Appended with the energy equation** (ADR-0027 step a): an inlet's temperature is a silent
    # 300 K until the user sets it, which is a warning once energy is on, not a refusal.
    "setup.no_inlet_temperature",
    # **Appended with the engine-aware kinds**: a kind the solver in `/solver/settings` cannot
    # write is an error here, before `solve.unsupported_kind` refuses it at run time.
    "setup.kind_unsupported",
)
"""The codes of ADR-0022 decision 5.3 (sixteen), plus the ones added since.

**A code that is not here is silently dropped**, which is the failure this list invites: the finding
is built, `report.add` ignores it, and a test sees a list without it rather than an error.
"""

COMPONENTS: tuple[str, ...] = ("velocity_x", "velocity_y", "velocity_z")
"""The components a velocity inlet reads with ``direction: components`` (as in ``boundary.py``):
each may be zero, all three may not."""

PHYSICS_TEXT: dict[str, str] = {
    "energy": "the energy equation",
    "turbulence": "the turbulence models",
}
"""What each physics model reads as, in the sentence of ``setup.needs_physics``."""


# ---------------------------------------------------------------- the answer


@dataclass(frozen=True)
class Finding:
    """One row of the check's answer: a stable code, its severity and what to do about it."""

    code: str
    severity: str
    message: str
    suggestion: str | None = None
    nodes: tuple[str, ...] = ()
    fix: dict[str, Any] | None = None  # Any: JSON, {command, params} or {action, node}

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
            "nodes": list(self.nodes),
            "fix": self.fix,
        }


@dataclass(frozen=True)
class CheckResult:
    """What ``setup.check`` returns: the state, the next thing to do, the counts, the findings."""

    state: str
    next_step: str
    counts: dict[str, int | None]
    findings: tuple[Finding, ...]

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "state": self.state,
            "next": self.next_step,
            "counts": dict(self.counts),
            "findings": [finding.to_json() for finding in self.findings],
        }


class _Report:
    """The findings of one run, sorted errors first and, inside a severity, in tree order."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.findings: list[Finding] = []
        self.order = {node.id: index for index, node in enumerate(project.nodes())}

    def add(
        self,
        code: str,
        severity: str,
        message: str,
        *,
        suggestion: str | None = None,
        nodes: Sequence[str] = (),
        fix: dict[str, Any] | None = None,  # Any: JSON
    ) -> None:
        self.findings.append(Finding(code, severity, message, suggestion, tuple(nodes), fix))

    def sorted(self) -> tuple[Finding, ...]:
        rank = {severity: index for index, severity in enumerate(SEVERITIES)}
        return tuple(sorted(self.findings, key=lambda one: (rank[one.severity], self._place(one))))

    def _place(self, finding: Finding) -> int:
        """Where the finding's first node sits in the tree; a finding about the project as a
        whole (no nodes) comes first, one about a node outside the tree last."""
        if not finding.nodes:
            return -1
        return self.order.get(finding.nodes[0], len(self.order))


def _edit(node_id: str) -> dict[str, Any]:  # Any: JSON
    return {"action": "condition.edit", "node": node_id}


def _add() -> dict[str, Any]:  # Any: JSON
    return {"action": "condition.add"}


def _rematch(tag_id: str) -> dict[str, Any]:  # Any: JSON
    return {"command": "tag.rematch", "params": {"tag": tag_id}}


# ---------------------------------------------------------------- what the check reads


def _tag_of(project: Project, node: Node) -> Node | None:
    from stokes.api.commands.setup_plan import ref_node

    return ref_node(project, node, "tag", TAG_TYPE)


def _members(project: Project, tag: Node | None) -> tuple[Member, ...]:
    """What a tag holds, read as the tag commands read it; nothing when the tag is gone or its
    text was set by hand (``tag.invalid`` is reported where the text is written, not here)."""
    from stokes.api.commands.setup_plan import stored_members

    if tag is None:
        return ()
    try:
        return stored_members(tag)
    except StokesError:
        return ()


def _faces(project: Project, part: str) -> int | None:
    """The part's ``faces`` parameter: the only face count the project itself holds. A mesh in
    the session's store would cost the session, and this check is pure."""
    node = project.find(part)
    if node is None or node.type != "part":
        return None
    faces = node.params.get("faces")
    if isinstance(faces, int) and not isinstance(faces, bool) and faces > 0:
        return faces
    return None


def _size(project: Project, members: Sequence[Member]) -> int | None:
    """How many faces the members hold; None while one part's face count is unknown."""
    total = 0
    for member in members:
        found = member.size(_faces(project, member.part))
        if found is None:
            return None
        total += found
    return total


def _si(stored: Any) -> float | list[float] | None:  # Any: a stored parameter value
    """The value as :func:`check_rule` takes it (SI), or None for one it does not judge."""
    if isinstance(stored, QuantityValue):
        return stored.value
    if isinstance(stored, bool):
        return None
    if isinstance(stored, int | float):
        return float(stored)
    if isinstance(stored, list) and all(
        isinstance(value, int | float) and not isinstance(value, bool) for value in stored
    ):
        return [float(value) for value in stored]
    return None


def _intersection(a: Sequence[tuple[int, int]], b: Sequence[tuple[int, int]]) -> int:
    """Faces in both range lists (inclusive, sorted, merged), by range arithmetic alone."""
    total = 0
    i = j = 0
    while i < len(a) and j < len(b):
        low, high = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if low <= high:
            total += high - low + 1
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _shared(project: Project, part: str, first: Member, second: Member) -> int | None:
    """Faces of one part in both members; None when both hold the whole part and its face count
    is not known (as ``tag_overlaps`` reads them)."""
    if first.faces is None and second.faces is None:
        return _faces(project, part)
    if first.faces is None or second.faces is None:
        return (second if first.faces is None else first).size(None)
    return _intersection(first.faces, second.faces)


def _where(project: Project, part: str) -> str:
    node = project.find(part)
    return node.path if node is not None else part


class _Region:
    """One ``region`` node and what its findings read once: its tag, the bodies it holds, its
    material and the boundaries under it."""

    def __init__(self, project: Project, node: Node) -> None:
        from stokes.api.commands.region import MATERIAL_TYPE, material_kind
        from stokes.api.commands.setup_plan import boundary_nodes, ref_node

        self.project = project
        self.node = node
        self.kind = str(node.params.get("kind"))
        self.tag = ref_node(project, node, "tag", TAG_TYPE)
        self.bodies = _members(project, self.tag)
        self.held = node.params.get(MATERIAL_TYPE)
        self.material = ref_node(project, node, "material", MATERIAL_TYPE)
        self.material_kind = material_kind(self.material) if self.material is not None else None
        self.boundaries = [
            found for found in boundary_nodes(project, node) if found.type == BOUNDARY_TYPE
        ]

    def specs(self) -> list[ConditionSpec]:
        """The catalogue entries of its boundaries' kinds, an unknown kind left out."""
        return [
            spec
            for spec in (
                BY_ID.get(str(boundary.params.get("kind"))) for boundary in self.boundaries
            )
            if spec is not None
        ]


# ---------------------------------------------------------------- one function per finding


def _no_region(report: _Report) -> None:
    """Decision 5.3: no ``region`` node's tag holds a body, so there is no set-up yet."""
    report.add(
        "setup.no_region",
        "error",
        "no region holds a body yet",
        suggestion="pick the bodies and choose them a material in Material…",
        fix={"action": "material.assign"},
    )


def _excluded_bodies(project: Project, regions: list[_Region]) -> list[tuple[Node, Node]]:
    """Bodies a region holds that are left OUT of the simulation (T-221), as ``(region, part)``.

    **A contradiction worth reporting rather than an error**: a user who leaves a body out and
    has not noticed that a region still contains it will get a mesh whose region has a hole -
    right about what it was told and wrong about what was meant. The fix is one of two clicks,
    so saying which is the useful part.
    """
    found: list[tuple[Node, Node]] = []
    for region in regions:
        # **`region.bodies` holds `Member` objects, not ids** - measured:
        # `Member(part='n_4d5…', faces=None, …)`. Passing each member to `project.find` returns
        # None,
        # so the finding never fired. My first fix went into a SECOND copy of this function that a
        # previous edit had left behind, which is why the tests kept failing after it looked
        # repaired.
        for member in region.bodies:
            part = project.find(member.part)
            if part is None or part.type != "part":
                continue
            if part.params.get("simulate", True) is False:
                found.append((region.node, part))
    return found


def _region_structure(report: _Report, region: _Region, stale: Collection[str]) -> None:
    """The four findings about a region itself: its tag, its bodies, its material and the kind
    the material and the region agree on; and the tag that went stale under it."""
    node = region.node
    if region.tag is None:
        report.add(
            "setup.tag_missing",
            "error",
            f"{node.path} names a tag that is gone",
            suggestion="create the region's tag again, or delete the region",
            nodes=(node.id,),
        )
    elif not region.bodies:
        if region.held is None:
            report.add(
                "setup.empty_tag",
                "warning",
                f"{node.path} holds no bodies",
                suggestion="give it bodies, or delete the region",
                nodes=(node.id,),
            )
        else:
            report.add(
                "setup.empty_region",
                "warning",
                f"{node.path} has a material but no bodies",
                suggestion="give it bodies with region.set, or delete the region",
                nodes=(node.id,),
            )
    if region.held is None or region.material is None:
        report.add(
            "setup.no_material",
            "error",
            f"{node.path} has no material",
            suggestion="choose the region's material in Material…",
            nodes=(node.id,),
        )
    elif region.material_kind is not None and region.material_kind != region.kind:
        report.add(
            "setup.material_kind",
            "error",
            f"{node.path} is {region.kind}, but {region.material.path} is {region.material_kind}",
            suggestion="run region.set to give the region its material's kind",
            nodes=(node.id,),
            fix={
                "command": "region.set",
                "params": {"region": node.id, "kind": region.material_kind},
            },
        )
    if region.tag is not None and region.tag.id in stale:
        report.add(
            "setup.stale_tag",
            "warning",
            f"the bodies of {node.path} were picked at an earlier version of their part",
            suggestion="run tag.rematch",
            nodes=(node.id,),
            fix=_rematch(region.tag.id),
        )


def _flow(report: _Report, region: _Region) -> None:
    """The three findings a fluid region with bodies gets from its boundaries: one of the inlet
    family, one of the outlet family, and one that fixes the pressure (``p`` in ``fields``)."""
    node = region.node
    specs = region.specs()
    families = {spec.family for spec in specs}
    if "inlet" not in families:
        report.add(
            "setup.no_inlet",
            "error",
            f"{node.path} has no inlet",
            suggestion="add an inlet boundary",
            nodes=(node.id,),
            fix=_add(),
        )
    if "outlet" not in families:
        report.add(
            "setup.no_outlet",
            "error",
            f"{node.path} has no outlet",
            suggestion="add an outlet boundary",
            nodes=(node.id,),
            fix=_add(),
        )
    if not any("p" in spec.fields for spec in specs):
        report.add(
            "setup.no_pressure",
            "warning",
            f"no boundary of {node.path} fixes the pressure",
            suggestion="add a pressure outlet",
            nodes=(node.id,),
            fix=_add(),
        )


def _uncovered(region: _Region) -> int | None:
    """The faces of the region's bodies that no boundary tag of the region holds (they become
    no-slip walls when the case is compiled); None while a body's face count is unknown. Range
    arithmetic, never expanded to face ids."""
    from stokes.api.commands.setup_plan import ref_node
    from stokes.geometry.selection import merge

    project = region.project
    whole: set[str] = set()
    covered: dict[str, list[tuple[int, int]]] = {}
    for boundary in region.boundaries:  # a plain group holds no tag and so no faces
        for member in _members(project, ref_node(project, boundary, "tag", TAG_TYPE)):
            if member.faces is None:
                whole.add(member.part)
            else:
                covered.setdefault(member.part, []).extend(member.faces)
    total = 0
    for body in region.bodies:
        if body.part in whole:
            continue
        count = _faces(project, body.part)
        if count is None:
            return None
        ranges = merge(covered.get(body.part, ()))
        total += count - sum(
            min(last, count - 1) - first + 1 for first, last in ranges if first < count
        )
    return total


def _uncovered_finding(report: _Report, region: _Region, faces: int | None) -> None:
    if faces is None or faces == 0:
        return
    report.add(
        "setup.uncovered_faces",
        "info",
        f"{faces} faces of {region.node.path} are in no boundary tag",
        suggestion="pick them and add a wall; untagged faces of a fluid region become no-slip "
        "walls at compile",
        nodes=(region.node.id,),
        fix=_add(),
    )


def _values(report: _Report, node: Node, spec: ConditionSpec) -> None:
    """``setup.value_invalid``: every parameter the kind uses with these values, judged on its
    SI value by ``check_rule``, and the three velocity components that may not all be zero. One
    finding for the boundary, naming it, the parameters and the rules."""
    used = spec.uses(node.params)
    broken: list[str] = []
    for use in used:
        value = _si(node.params.get(use.name))
        message = check_rule(use, value) if value is not None else None
        if message is not None:
            broken.append(message)
    names = {use.name for use in used}
    if set(COMPONENTS) <= names and all(_si(node.params.get(c)) == 0.0 for c in COMPONENTS):
        broken.append(f"{' and '.join(COMPONENTS)} are all zero")
    if broken:
        report.add(
            "setup.value_invalid",
            "error",
            f"{node.path}: {'; '.join(broken)}",
            suggestion="give it values the rule accepts, in the value panel",
            nodes=(node.id,),
            fix=_edit(node.id),
        )


def _partner(report: _Report, project: Project, node: Node, spec: ConditionSpec) -> None:
    """``setup.partner_missing``: a cyclic or interface boundary whose partner is gone or does
    not name it back."""
    if "partner" not in {use.name for use in spec.uses(node.params)}:
        return
    from stokes.api.commands.setup_plan import ref_node

    partner = ref_node(project, node, "partner", BOUNDARY_TYPE)
    if partner is None or partner.params.get("partner") != node.id:
        report.add(
            "setup.partner_missing",
            "error",
            f"{node.path} has no partner that names it back",
            suggestion="give it its partner in the value panel",
            nodes=(node.id,),
            fix=_edit(node.id),
        )


def _energy_solved(project: Any) -> bool:
    """Whether `/solver/settings` solves the energy equation (ADR-0027 decision 2)."""
    from stokes.engines.model import SETTINGS_PATH

    settings = project.find(SETTINGS_PATH)
    return settings is not None and bool(settings.params.get("energy"))


def _turbulence_solved(project: Any) -> bool:
    """Whether `/solver/settings` names a turbulence model other than laminar (ADR-0028)."""
    from stokes.engines.model import SETTINGS_PATH

    settings = project.find(SETTINGS_PATH)
    if settings is None:
        return False
    return str(settings.params.get("turbulence") or "laminar") != "laminar"


#: The catalogue's default temperature for a boundary, in kelvin (``quantity`` in catalogue.py).
DEFAULT_TEMPERATURE = 300.0


def _is_default_temperature(node: Node) -> bool:
    """Whether the boundary's ``temperature`` still holds the catalogue default."""
    value = node.params.get("temperature")
    return value is None or getattr(value, "value", value) == DEFAULT_TEMPERATURE


def _physics(report: _Report, node: Node, spec: ConditionSpec) -> None:
    """``setup.needs_physics`` (decision 2.7): a parameter in use that needs energy or
    turbulence, information until ``/physics/models`` exists. One finding per boundary."""
    used = {use.name for use in spec.uses(node.params)}
    # **The energy row is dropped once the solver solves energy** (ADR-0027): the finding says
    # "choose it in Physics", which is no longer the open question when the solver setting is on.
    energy = _energy_solved(report.project)
    # **The turbulence row goes the same way once a turbulence model is chosen** (ADR-0028). It
    # was written before the Flow model existed and never learnt to stop, so a case solving
    # k-omega SST still told every inlet to "choose the turbulence models in Physics", which read
    # as turbulence not being connected at all.
    turbulence = _turbulence_solved(report.project)
    solved = {"energy": energy, "turbulence": turbulence}
    wanted = [
        PHYSICS_TEXT.get(physics, physics)
        for physics, names in spec.needs.items()
        if set(names) & used and not solved.get(physics, False)
    ]
    # **The default 300 K is not a chosen inflow temperature**: with energy on, a boundary that
    # reads the temperature and still holds the default is named, because the solve would run at a
    # temperature nobody set (ADR-0027 decision 4). Equal to the default is the best signal.
    if energy and "temperature" in used and _is_default_temperature(node):
        report.add(
            "setup.no_inlet_temperature",
            "warning",
            f"{node.name} still has the default temperature of 300 K; set the temperature it "
            "should have",
            suggestion="set the temperature in the value panel",
            nodes=(node.id,),
            fix=_edit(node.id),
        )
    if not wanted:
        return
    report.add(
        "setup.needs_physics",
        "info",
        # **The boundary's NAME and what to do** (claude-win2's package-two verdict, leftover 2):
        # it read *"/regions/fluid/boundaries/inlet uses the energy equation and the turbulence
        # models; set the physics models in M3"* - a raw path where a name belongs, and a
        # milestone code that means nothing outside this repository. The Flow model is the control
        # a user reaches, and it is under Physics, where the row they clicked also lives.
        f"{node.name} needs {' and '.join(wanted)}: choose them in Physics → Flow model",
        suggestion="choose the flow model in the Physics stage",
        nodes=(node.id,),
    )


def _engine_kind(report: _Report, node: Node, spec: ConditionSpec) -> None:
    """``setup.kind_unsupported``: a kind the solver in ``/solver/settings`` cannot write. Said
    here so the user hears before the solve refuses it (``solve.unsupported_kind``). A kind that
    is not available at all is ``boundary.invalid_kind``'s business, and no solver settings means
    no engine to judge against."""
    from stokes.engines.catalogue import kinds_for
    from stokes.engines.model import SETTINGS_PATH

    settings = report.project.find(SETTINGS_PATH)
    if settings is None or not spec.available:
        return
    if spec.id in kinds_for(str(settings.params.get("engine") or "")):
        return
    report.add(
        "setup.kind_unsupported",
        "error",
        f"{node.name} is a {spec.label.lower()}, which the solver cannot write yet",
        suggestion="choose another condition kind in the value panel",
        nodes=(node.id,),
        fix=_edit(node.id),
    )


def _boundary(report: _Report, node: Node, stale: Collection[str]) -> None:
    """The findings about one boundary: the tag it names, its values, its partner and the
    physics its parameters need."""
    project = report.project
    tag = _tag_of(project, node)
    if tag is None:
        report.add(
            "setup.tag_missing",
            "error",
            f"{node.path} names a tag that is gone",
            suggestion="pick its faces again and give them to the boundary (Replace faces…)",
            nodes=(node.id,),
            fix=_edit(node.id),
        )
    else:
        members = _members(project, tag)
        if not members or _size(project, members) == 0:
            report.add(
                "setup.empty_tag",
                "warning",
                f"the tag of {node.path} holds no faces",
                suggestion="pick faces for it, or delete the boundary",
                nodes=(node.id,),
                fix=_edit(node.id),
            )
        if tag.id in stale:
            report.add(
                "setup.stale_tag",
                "warning",
                f"the faces of {node.path} were picked at an earlier version of their part",
                suggestion="run tag.rematch",
                nodes=(node.id,),
                fix=_rematch(tag.id),
            )
    spec = BY_ID.get(str(node.params.get("kind")))
    if spec is None:
        return  # an unknown kind: boundary.list reports boundary.invalid_kind, no table row
    _values(report, node, spec)
    _partner(report, project, node, spec)
    _physics(report, node, spec)
    _engine_kind(report, node, spec)


def _overlap(report: _Report, boundaries: Sequence[Node]) -> None:
    """``setup.overlap``: faces that lie in two boundary tags, one finding per pair of tags and
    part, as ``tag.overlap`` reports them (range arithmetic, never expanded to face ids)."""
    from stokes.api.commands.setup_plan import stored_members

    project = report.project
    group = project.find(TAGS_PATH)
    held: dict[str, list[tuple[Node, Member]]] = {}
    for tag in group.children if group is not None else []:
        if tag.type != TAG_TYPE or tag.params.get("kind") != "boundary":
            continue
        try:
            members = stored_members(tag)
        except StokesError:
            continue
        for member in members:
            held.setdefault(member.part, []).append((tag, member))
    users: dict[str, list[Node]] = {}
    for boundary in boundaries:
        tag_id = boundary.params.get("tag")
        if isinstance(tag_id, str):
            users.setdefault(tag_id, []).append(boundary)
    for part, found in held.items():
        for index, (first_tag, first_member) in enumerate(found):
            for second_tag, second_member in found[index + 1 :]:
                shared = _shared(project, part, first_member, second_member)
                if shared == 0:
                    continue
                count = "faces" if shared is None else f"{shared} face{'s' * (shared != 1)}"
                used = sorted(
                    [*users.get(first_tag.id, []), *users.get(second_tag.id, [])],
                    key=lambda node: report.order.get(node.id, len(report.order)),
                )
                report.add(
                    "setup.overlap",
                    "warning",
                    f"{first_tag.name} and {second_tag.name} share {count} of "
                    f"{_where(project, part)}",
                    suggestion="edit the tags so that every face is in one tag",
                    nodes=[node.id for node in used] or [first_tag.id, second_tag.id],
                )


def _legacy(report: _Report, node: Node, *, region: bool) -> None:
    """``setup.legacy``: a plain group under ``/regions`` or ``/regions/<region>/boundaries``,
    left by a version from before ADR-0022 typed them."""
    report.add(
        "setup.legacy",
        "warning",
        f"{node.path} is a plain group from an older project",
        suggestion=(
            "run region.create with its name, or boundary.assign into it, to make it a region"
            if region
            else "run boundary.assign with it as the target to make it a boundary"
        ),
        nodes=(node.id,),
    )


# ---------------------------------------------------------------- the check


def _state(holds_bodies: bool, findings: tuple[Finding, ...]) -> str:
    """Decision 5.2: ``not_defined``, then ``incomplete``, then ``stale`` (a missing or stale tag
    is the only wrong thing), then ``ready``."""
    if not holds_bodies:
        return "not_defined"
    if any(
        finding.severity == "error" and finding.code != "setup.tag_missing" for finding in findings
    ):
        return "incomplete"
    if any(finding.code in ("setup.tag_missing", "setup.stale_tag") for finding in findings):
        return "stale"
    return "ready"


def _next_step(findings: tuple[Finding, ...]) -> str:
    """The first error's suggestion, else the first warning's, else :data:`COMPLETE`."""
    for severity in ("error", "warning"):
        for finding in findings:
            if finding.severity == severity and finding.suggestion:
                return finding.suggestion
    return COMPLETE


def check_setup(project: Project, stale_tags: Collection[str]) -> CheckResult:
    """The set-up's readiness ``state``, what to do ``next``, its ``counts`` and its findings,
    errors first and, inside a severity, in tree order (the module docstring says how the states
    are decided).

    ``stale_tags`` are the ids of the tags the session knows to be stale. An empty set means
    staleness is unknown (no mesh in memory, or no geometry service): no ``setup.stale_tag``
    finding is made then, which is what decision 5.1 asks of the CLI without meshes.
    """
    from stokes.api.commands.region import MATERIAL_TYPE
    from stokes.api.commands.setup_plan import (
        boundary_nodes,
        is_legacy_boundary,
        is_legacy_region,
        region_nodes,
    )

    stale = set(stale_tags)
    report = _Report(project)
    regions = [_Region(project, node) for node in region_nodes(project)]
    listed = boundary_nodes(project)
    holder = project.find(REGIONS_PATH)
    legacy_regions = [
        node for node in (holder.children if holder is not None else []) if is_legacy_region(node)
    ]
    legacy_boundaries = [node for node in listed if is_legacy_boundary(node)]
    boundaries = [node for node in listed if node.type == BOUNDARY_TYPE]

    if not any(region.bodies for region in regions):
        _no_region(report)
    # **A region holding an excluded body** (T-221): the mesh gets a hole nobody meant.
    for region_node, part in _excluded_bodies(project, regions):
        report.add(
            "setup.excluded_body",
            "warning",
            f"{part.name!r} is left out of the simulation, but {region_node.path} holds it",
            suggestion="use the body in the simulation again, or take it out of the region",
            nodes=(part.id, region_node.id),
        )
    for region in regions:
        _region_structure(report, region, stale)
        if region.kind == "fluid" and region.bodies:
            _flow(report, region)
    for boundary in boundaries:
        _boundary(report, boundary, stale)
    _overlap(report, boundaries)
    for node in legacy_regions:
        _legacy(report, node, region=True)
    for node in legacy_boundaries:
        _legacy(report, node, region=False)
    uncovered = [_uncovered(region) for region in regions]
    for region, faces in zip(regions, uncovered, strict=True):
        _uncovered_finding(report, region, faces)

    counts: dict[str, int | None] = {
        "regions": len(regions),
        "materials": sum(1 for node in project.nodes() if node.type == MATERIAL_TYPE),
        "boundaries": len(boundaries),
        "uncovered_faces": (
            None
            if any(faces is None for faces in uncovered)
            else sum(faces for faces in uncovered if faces is not None)
        ),
        "legacy": len(legacy_regions) + len(legacy_boundaries),
    }
    findings = report.sorted()
    return CheckResult(
        _state(any(region.bodies for region in regions), findings),
        _next_step(findings),
        counts,
        findings,
    )
