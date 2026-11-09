"""Geometry node types: ``tag`` (ADR-0020 decisions 2 and 8), ``region`` and ``boundary``
(ADR-0022 decisions 2.1 and 2.9).

A tag lives at ``/geometry/tags/<name>``. Its ``members`` parameter holds
the faces it groups as JSON text under the ``json`` parameter kind (T-006), so that its value is
structured data in the schema::

    [{"part": "n_3fa9c2d1", "faces": [[0, 3], [7, 7]], "basis": "1f3a9c2e5b7d"},
     {"part": "n_5e0b17aa", "faces": "all"}]

``part`` is a ``part`` node id; ``faces`` is ``"all"`` (every face of the part, whatever its
count) or sorted, merged, inclusive ``[first, last]`` ranges of face ids (ADR-0014: 0 to
faces - 1 per part); ``basis`` names the version of the part's B-rep those ids refer to (see
:mod:`stokes.geometry.fingerprints`) and is written by the ``tag.*`` commands, which own the
value: clients read it, or ``tag.members``, and change it through those commands. The
contract is ``docs/engineering/geometry-schema.md``; the model checks the structure when
``members`` is written (a ``validation`` error names the problem), and the tag commands
check it again when they read it.

The group ``/geometry/tags`` is created by ``tag.create`` when a project lacks
it, inside the same undo step, as ``geometry.import_step`` does for ``/geometry/parts``; it is
not part of the skeleton, so projects that already hold it as a plain group (the demo) and the
plain groups inside it stay valid.

A ``region`` lives at ``/regions/<name>``: it references a region-kind tag (its bodies) and, once
one is assigned, a ``material``, and it holds a ``boundaries`` group in which each ``boundary``
lives, at ``/regions/<region>/boundaries/<name>``, referencing a boundary-kind tag. One
``boundary`` type carries a ``kind`` from the condition catalogue
(:mod:`stokes.physics.conditions.catalogue`) and the union of every kind's parameters, so a
change of kind keeps the node's id, name, tag and expression references
(``regions.fluid.boundaries.inlet.velocity``); the catalogue says which parameters a kind uses.
The vocabulary of the node type (the kind ids and the choices of its other enums) is declared here
and the catalogue imports it, so that registering the type loads no more than this module
(ADR-0022 decision 2.1); the catalogue refuses to load when its kinds and these ids differ.
The ``boundary.*`` and ``region.*`` commands create and own these nodes (T-036). Plain groups at
those paths from before ADR-0022 stay valid as legacy entries. The contract is
``docs/engineering/setup-schema.md``.

The operation types (ADR-0021 decision 8.1) are declared here too: one node type per operation
(``primitive``, ``boolean``, ``enclosure``, ``extract_fluid`` and the eight repairs), living at
``/geometry/operations/<name>``. Each carries its own parameters (quantities in SI, so studies can
vary them) and three common ones: ``inputs`` (the parts it reads, node ids), ``outputs`` (the
parts its last run made, written by the run) and ``auto_run`` (re-run on a change, default true,
decisions log G12). :data:`OPERATION_TYPES` says what the framework needs besides the parameters:
how many inputs a type takes, which parameters name further parts (a boolean's ``tools``) and
which reference a tag whose members the run reads (``<name>_members``). The modules that run
them are :mod:`stokes.geometry.operations`; the commands are ``operation.*``.

Size: over 500 lines on purpose. Every geometry node type the model loads is declared here, and
this is the one module of the package the model may import, so splitting it would only move the
vocabulary behind a second import exception.

Registration: :func:`stokes.model.schema.register_builtin_types` calls
:func:`register_geometry_types` like it calls :func:`register_study_types`, so every project knows
these types. This is the one module of ``stokes.geometry`` that ``stokes.model`` may import: the
layering gate (``tools/check_forbidden_imports.py``) bans the rest of the package, which imports
OCP, and ``stokes.geometry.__init__`` must stay free of such imports for this one to load.

Import rule: the standard library, ``stokes.errors`` and ``stokes.model.ids`` at module level
(all light, no numpy or OCP), so ``stokes.model`` imports this module; ``geometry_specs``
imports the schema when called.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from stokes.errors import ValidationError
from stokes.model.ids import is_id

if TYPE_CHECKING:
    from stokes.model.nodes import Node
    from stokes.model.schema import NodeSpec, NodeTypeRegistry

GEOMETRY_PATH = "/geometry"
TAGS_GROUP = "tags"
TAGS_PATH = f"{GEOMETRY_PATH}/{TAGS_GROUP}"
TAG_TYPE = "tag"
# The names these constants had before the tags rename (T-015); kept as aliases for one release.
SELECTIONS_GROUP = TAGS_GROUP
SELECTIONS_PATH = TAGS_PATH
SELECTION_TYPE = TAG_TYPE
SELECTION_KINDS: tuple[str, ...] = ("boundary", "region", "plain")
"""boundary: faces that become one mesh patch; region: whole parts that become a mesh region
(a fluid or a solid body); plain: a working set with no meaning for the case."""
ALL_FACES = "all"
GROW_ANGLE = 30.0
"""Default feature angle of ``tag.grow``, degrees: above the facet-to-facet jump of a fine
tessellation across a smooth edge (at most its angular deflection, 0.2 rad = 11.5 degrees) and
well below a sharp edge."""
JSON_MEMBERS_BYTES = 32 * 1024 * 1024
"""Limit of ``tag.members`` as a ``json`` parameter. The value is JSON text, so its
serialised form escapes every quote and roughly doubles; the tag commands read up to 16 MB
of members text, and the project file limit is 64 MB."""
MEMBER_KEYS: frozenset[str] = frozenset({"part", "faces", "edges", "vertices", "basis"})
"""The keys one member of a tag may carry (ADR-0022 §15.1).

**`edges` and `vertices` were missing here while the checks below already validated them**, so
`tag.create` refused any member naming an edge or a vertex — "a member that is not an object of
part,
faces, edges, vertices and basis" — even though the code immediately below accepts all three.
`selection._MEMBER_KEYS` had the right set, which is why this went unnoticed: the reader accepted
what
the writer rejected. Found by T-056-fix's own test, which is exactly the case that needed it."""
MAX_MEMBER_PARTS = 100_000
MAX_FACE_RANGES = 1_000_000
MAX_FACE_ID = 2**31 - 1
MAX_MEMBERS_TEXT = 16 * 1024 * 1024
_BASIS_RE = re.compile(r"^[0-9a-f]{1,32}$")
"""The shape of one stored member and its caps. The heavy :mod:`stokes.geometry.selection`
module holds the same keys and caps beside its numpy readers and writers of the members,
and it cannot be imported here, so this module repeats them: keep the two in step."""
SELECTION_PALETTE: tuple[str, ...] = (
    "#3b82c4",
    "#e0803a",
    "#d64f4f",
    "#4fb3a9",
    "#5aa452",
    "#d9b43c",
    "#a275c9",
    "#e88aa8",
    "#8d6e57",
    "#7f8c99",
)
"""Colours given to new tags in turn, so neighbouring patches tell apart in the viewport."""
REGIONS_PATH = "/regions"
REGION_TYPE = "region"
BOUNDARY_TYPE = "boundary"
BOUNDARIES_GROUP = "boundaries"
"""The group inside each region that holds its boundaries; the region's commands create it."""
REGION_KINDS: tuple[str, ...] = ("fluid", "solid")
"""fluid: bodies the flow fills; solid: bodies that only conduct heat. A region takes a material
of its own kind (``materials.kind_mismatch``)."""
DEFAULT_REGION = "fluid"
"""The region a boundary goes to when none is named and the project has no single fluid region
(ADR-0022 decision 2.9)."""
CONDITION_KINDS: tuple[str, ...] = (
    "velocity_inlet",
    "mass_flow_inlet",
    "pressure_inlet",
    "pressure_outlet",
    "wall",
    "symmetry",
    "cyclic",
    "interface",
)
"""The choices of ``boundary.kind``: the ids of the condition catalogue, in its order."""
DEFAULT_CONDITION = "wall"
DIRECTIONS: tuple[str, ...] = ("normal", "components", "vector")
TURBULENCE_INPUTS: tuple[str, ...] = ("intensity_ratio", "intensity_length")
SHEARS: tuple[str, ...] = ("no_slip", "slip")
THERMALS: tuple[str, ...] = ("adiabatic", "temperature", "heat_flux", "convection")
"""The choices of the boundary's other enums: ``direction``, ``turbulence``, ``shear`` and
``thermal``. The first choice of each is its default, and the catalogue's ``when`` mappings name
them."""
OPERATIONS_GROUP = "operations"
OPERATIONS_PATH = f"{GEOMETRY_PATH}/{OPERATIONS_GROUP}"
"""Where operation nodes live; ``operation.create`` adds the group, in its undo step, when it
is missing."""
OPERATION_COMMON: tuple[str, ...] = ("inputs", "outputs", "auto_run")
"""The parameters every operation type has besides its own."""
JSON_PARTS_BYTES = 1024 * 1024
"""Limit of a list of part ids (``inputs``, ``outputs``, ``tools``): about 70,000 ids."""
AXES: tuple[str, ...] = ("x", "y", "z")
FLOWS: tuple[str, ...] = ("+x", "-x", "+y", "-y", "+z", "-z")


@dataclass(frozen=True)
class OperationType:
    """What the operations framework needs to know about one operation type beyond its
    parameters (ADR-0021 decision 8).

    ``min_inputs``: the fewest parts ``inputs`` may name (a primitive takes none).
    ``part_params``: ``json`` parameters that list further parts the run reads, which are loaded,
    hidden and watched like the inputs (a boolean's ``tools``). ``tag_params``: ``ref`` parameters
    to a tag whose members the run reads; the run receives them as ``<name>_members``.
    """

    type: str
    label: str
    min_inputs: int = 1
    part_params: tuple[str, ...] = ()
    tag_params: tuple[str, ...] = ()


OPERATION_TYPES: tuple[OperationType, ...] = (
    OperationType("primitive", "Primitive", min_inputs=0),
    OperationType("boolean", "Boolean", part_params=("tools",)),
    OperationType("enclosure", "Enclosure"),
    OperationType("extract_fluid", "Extract fluid", tag_params=("caps",)),
    OperationType("heal", "Heal"),
    OperationType("sew", "Sew"),
    OperationType("cap", "Close openings", tag_params=("loop",)),
    OperationType("unify", "Unify faces", tag_params=("keep",)),
    OperationType("slivers", "Drop slivers"),
    OperationType("defeature", "Remove faces"),
    OperationType("orient", "Fix orientation"),
    OperationType("solidify", "Make solid"),
)
"""Every operation type, in the order the Prepare menu lists them (ADR-0021 decisions 5 to 7)."""
OPERATION_TYPE_NAMES: tuple[str, ...] = tuple(t.type for t in OPERATION_TYPES)


def operation_type(name: str) -> OperationType | None:
    """The operation type called ``name``, or None for any other node type."""
    for kind in OPERATION_TYPES:
        if kind.type == name:
            return kind
    return None


def validate_tag_members(node: Node, value: Any) -> Any:
    """The structural check of ``tag.members`` when it is written (T-013): the value must
    parse to the members structure the ``tag.*`` commands produce, a list of
    ``{"part": <part id>, "faces": "all" or [[first, last], ...], "basis": <hex>}``, or
    the JSON text of that list, which the commands store and projects from before the
    ``json`` kind hold. Anything else is refused with a ``validation`` error that names
    the problem; the check the tag commands apply when they read
    (:func:`stokes.geometry.selection.load_members`) stays a backstop for values written
    before this one began.
    """
    where = f"the members of {node.path}"
    if isinstance(value, str):
        if len(value) > MAX_MEMBERS_TEXT:
            raise ValidationError(f"{where} exceed {MAX_MEMBERS_TEXT} bytes of JSON text")
        try:
            data: Any = json.loads(value)
        except ValueError as exc:
            raise ValidationError(f"{where} are not JSON text: {exc}") from exc
    elif isinstance(value, list):
        data = value
    else:
        hint = "; an empty list clears the tag" if value is None else ""
        raise ValidationError(
            f"{where} must be a list of members or the JSON text of one; got {_json_type(value)}"
            f"{hint}"
        )
    if not isinstance(data, list):
        raise ValidationError(f"{where} must be a list of members; got {_json_type(data)}")
    if len(data) > MAX_MEMBER_PARTS:
        raise ValidationError(f"{where} name {len(data)} parts; at most {MAX_MEMBER_PARTS}")
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or not set(item) <= MEMBER_KEYS:
            raise ValidationError(
                f"{where} hold a member that is not an object of part, faces, edges, vertices "
                "and basis"
            )
        part = item.get("part")
        if not isinstance(part, str) or not is_id(part):
            raise ValidationError(f"{where} name a part that is not a node id: {part!r}")
        if part in seen:
            raise ValidationError(f"{where} name the part {part} twice")
        seen.add(part)
        # ADR-0022 §15.4: an edge- or point-only member omits `faces` ("absent fields mean none"),
        # but a member that names nothing at all stays malformed as it always was.
        edges = item.get("edges", [])
        vertices = item.get("vertices", [])
        if "faces" not in item and not edges and not vertices:
            raise ValidationError(
                f"{where}: the member for {part} names nothing; give faces, edges or vertices"
            )
        faces = item.get("faces", [])
        basis = item.get("basis")
        if basis is not None and not (isinstance(basis, str) and _BASIS_RE.match(basis)):
            raise ValidationError(
                f"{where} carry a basis that is not 1 to 32 hex characters: {basis!r}"
            )
        _check_member_faces(where, part, faces)
        # Edges and vertices are optional (ADR-0022 §15.1) and validated exactly like faces;
        # their absence means "none", never "all", so an old member gains no edge members.
        _check_member_faces(where, part, edges, what="edge")
        _check_member_faces(where, part, vertices, what="vertex")
        explicit = faces != ALL_FACES or bool(edges) or bool(vertices)
        if basis is not None and not explicit:
            raise ValidationError(
                f"{where}: the member for {part} carries a basis but names no explicit ids; "
                "a basis names the version explicit ids refer to"
            )
    return value


def _check_member_faces(where: str, part: str, faces: Any, *, what: str = "face") -> None:  # Any
    """One id set of a stored member: ``"all"``, or a list of ids and ranges.

    Faces, edges and vertices share this check: their stored form is identical, and only the noun
    in the message differs, so a user is told which set is wrong.
    """
    if faces == ALL_FACES:
        return
    if not isinstance(faces, list):
        raise ValidationError(
            f"{where}: the {what}s of part {part} must be 'all' "
            "or a list of ids or [first, last] ranges"
        )
    if len(faces) > MAX_FACE_RANGES:
        raise ValidationError(
            f"{where}: part {part} names {len(faces)} {what}s; at most {MAX_FACE_RANGES}"
        )
    for entry in faces:
        if _is_face_id(entry):
            first = last = entry
        elif (
            isinstance(entry, list | tuple)
            and len(entry) == 2
            and all(_is_face_id(v) for v in entry)
        ):
            first, last = entry[0], entry[1]
        else:
            raise ValidationError(
                f"{where}: a {what} of part {part} is not an id or a [first, last] range: "
                f"{_preview(entry)}"
            )
        if not 0 <= first <= last <= MAX_FACE_ID:
            raise ValidationError(
                f"{where}: the {what} range [{first}, {last}] of part {part} "
                "is not 0 <= first <= last"
            )


def _is_face_id(value: Any) -> bool:  # Any: JSON
    return isinstance(value, int) and not isinstance(value, bool)


def _json_type(value: Any) -> str:  # Any: JSON
    """What a JSON value is, in the words of a message: null, text, a number, a list ..."""
    names = {type(None): "null", str: "text", bool: "a boolean", int: "a number"}
    names |= {float: "a number", list: "a list", dict: "an object"}
    return names.get(type(value), type(value).__name__)


def _preview(value: Any) -> str:  # Any: JSON
    text = json.dumps(value, default=str)
    return text if len(text) <= 80 else text[:77] + "..."


def validate_part_ids(node: Node, value: Any) -> Any:  # Any: JSON
    """An operation's ``inputs``, ``outputs`` or ``tools`` when written: a list of distinct
    node ids. ``operation.create`` resolves paths and groups to ids before it stores them."""
    if not isinstance(value, list) or not all(isinstance(v, str) and is_id(v) for v in value):
        raise ValidationError(
            f"the parts of {node.path} must be a list of node ids; got {_preview(value)}"
        )
    if len(set(value)) != len(value):
        raise ValidationError(f"the parts of {node.path} name a part twice: {_preview(value)}")
    return value


def validate_axes(node: Node, value: Any) -> Any:  # Any: JSON
    """An enclosure's ``symmetry``: a list of distinct planes, each "x", "y" or "z"."""
    if (
        not isinstance(value, list)
        or not all(v in AXES for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValidationError(
            f"the symmetry planes of {node.path} must be distinct 'x', 'y' or 'z'; "
            f"got {_preview(value)}"
        )
    return value


def geometry_specs() -> tuple[NodeSpec, ...]:
    """The geometry node types: ``tag`` (ADR-0020 slice 1), ``region`` and ``boundary``
    (ADR-0022) and the operation types (ADR-0021 decision 8.1). Built on call, like the study
    types."""
    from stokes.model.schema import COLOR_PATTERN, NodeSpec, ParamSpec

    tag = NodeSpec(
        TAG_TYPE,
        "Tag",
        params=(
            ParamSpec(
                "members",
                "json",
                label="Members",
                default="[]",
                max_bytes=JSON_MEMBERS_BYTES,
                validate=validate_tag_members,
                doc='The faces, as JSON: [{"part": <part id>, "faces": "all" or [[first, last], '
                "...]}, ...] with inclusive ranges of face ids. Written by the tag.* "
                "commands; read it expanded with tag.members.",
                # **Hidden from a node's fields** (claude-win2's package-two review item 5): the
                # page rendered this raw, so a user saw `[{"part":"n_1364…","faces":[[2,2]]}]`
                # where a sentence belongs. `tag.members` reads it EXPANDED - part names and
                # face counts - which is what a page should show, as Conditions already does.
                #
                # The parameter is untouched: the API and the journal still carry it, and an editor
                # that wants the raw form can still ask for it.
                hidden=True,
            ),
            ParamSpec(
                "kind",
                "enum",
                label="Kind",
                choices=SELECTION_KINDS,
                default=SELECTION_KINDS[0],
                doc="boundary: one mesh patch; region: whole parts that make one mesh region; "
                "plain: a working set.",
            ),
            ParamSpec(
                "color",
                "string",
                label="Colour",
                default=SELECTION_PALETTE[0],
                pattern=COLOR_PATTERN,
                doc="Tint of the tag's faces in the viewport, as #rrggbb.",
            ),
            ParamSpec("visible", "bool", label="Visible", default=True),
            ParamSpec(
                "opacity",
                "number",
                label="Opacity",
                default=1.0,
                doc="0 transparent to 1 opaque.",
            ),
        ),
        doc="A group of faces of one or more parts: one STL solid when "
        "exported, one boundary patch once meshed.",
    )
    return (tag, _region_spec(), _boundary_spec(), *_operation_specs())


def _region_spec() -> NodeSpec:
    from stokes.model.schema import NodeSpec, ParamSpec

    return NodeSpec(
        REGION_TYPE,
        "Region",
        params=(
            ParamSpec(
                "kind",
                "enum",
                label="Kind",
                choices=REGION_KINDS,
                default=REGION_KINDS[0],
                doc="fluid or solid; the region's material must be of the same kind.",
            ),
            ParamSpec(
                "tag",
                "ref",
                label="Tag",
                ref_type=TAG_TYPE,
                doc="The region-kind tag that holds the region's bodies; region.create makes it "
                "with the region, and it is required, so it is never null.",
                required=True,
            ),
            ParamSpec(
                "material",
                "ref",
                label="Material",
                ref_type="material",
                doc="The material of the region's bodies; null until one is assigned.",
            ),
        ),
        children=("group",),
        doc="A mesh region: the bodies of a region-kind tag, fluid or "
        "solid, and the material they are made of. It lives at /regions/<name> and holds its "
        "boundaries in a boundaries group; region.create makes the tag and the group with it.",
    )


def _boundary_spec() -> NodeSpec:
    """ADR-0022 decision 2.4's parameters, in its order; each doc names the kinds that use it."""
    from stokes.model.schema import NodeSpec, ParamSpec, validate_layer_count

    def quantity(name: str, label: str, dimension: str, default: float, doc: str) -> ParamSpec:
        return ParamSpec(
            name, "quantity", label=label, dimension=dimension, default=default, doc=doc
        )

    def switch(name: str, label: str, choices: tuple[str, ...], doc: str) -> ParamSpec:
        return ParamSpec(name, "enum", label=label, choices=choices, default=choices[0], doc=doc)

    def component(axis: str) -> ParamSpec:
        return quantity(
            f"velocity_{axis}",
            f"Velocity {axis}",
            "velocity",
            0.0,
            f"Velocity inlet with direction components: the {axis} component of the velocity.",
        )

    return NodeSpec(
        BOUNDARY_TYPE,
        "Boundary",
        params=(
            ParamSpec(
                "kind",
                "enum",
                label="Kind",
                choices=CONDITION_KINDS,
                default=DEFAULT_CONDITION,
                doc="Every kind: the condition (boundary.kinds lists them); changing it keeps "
                "the node, its tag and every expression that refers to it.",
            ),
            ParamSpec(
                "tag",
                "ref",
                label="Tag",
                ref_type=TAG_TYPE,
                doc="Every kind: the boundary-kind tag whose faces the condition applies to; "
                "required, so it is never null.",
                required=True,
            ),
            switch(
                "direction",
                "Direction",
                DIRECTIONS,
                "Velocity inlet: the velocity normal to the faces, by components or along "
                "direction_vector.",
            ),
            quantity(
                "velocity",
                "Velocity",
                "velocity",
                0.0,
                "Velocity inlet with direction normal or vector: the speed of the inflow.",
            ),
            component("x"),
            component("y"),
            component("z"),
            ParamSpec(
                "direction_vector",
                "array",
                label="Direction vector",
                dimension="dimensionless",
                default=(1.0, 0.0, 0.0),
                doc="Velocity inlet with direction vector: the flow direction as three numbers, "
                "of any nonzero length.",
            ),
            quantity(
                "mass_flow",
                "Mass flow",
                "mass_flow",
                0.0,
                "Mass-flow inlet: the mass flow rate through all of its faces.",
            ),
            quantity(
                "pressure",
                "Pressure",
                "pressure",
                0.0,
                "Pressure outlet: the static pressure, gauge, relative to the operating pressure.",
            ),
            quantity(
                "total_pressure",
                "Total pressure",
                "pressure",
                0.0,
                "Pressure inlet: the total pressure, gauge, relative to the operating pressure.",
            ),
            quantity(
                "temperature",
                "Temperature",
                "temperature",
                300.0,
                "Inlets, and a wall with thermal temperature: the temperature of the inflow or "
                "of the wall.",
            ),
            quantity(
                "backflow_temperature",
                "Backflow temperature",
                "temperature",
                300.0,
                "Pressure outlet: the temperature of fluid that flows back in.",
            ),
            switch(
                "turbulence",
                "Turbulence",
                TURBULENCE_INPUTS,
                "Inlets and the pressure outlet's backflow: the turbulence given by intensity "
                "and viscosity ratio, or by intensity and length scale.",
            ),
            ParamSpec(
                "intensity",
                "number",
                label="Turbulence intensity",
                default=0.05,
                doc="Inlets and the pressure outlet's backflow: the turbulence intensity as a "
                "fraction (0.05 is 5 %).",
            ),
            ParamSpec(
                "viscosity_ratio",
                "number",
                label="Viscosity ratio",
                default=10.0,
                doc="Inlets and the pressure outlet's backflow with turbulence intensity_ratio: "
                "the ratio of turbulent to molecular viscosity.",
            ),
            quantity(
                "length_scale",
                "Length scale",
                "length",
                0.01,
                "Inlets and the pressure outlet's backflow with turbulence intensity_length: "
                "the turbulence length scale.",
            ),
            switch("shear", "Shear", SHEARS, "Wall: no_slip or slip."),
            switch(
                "thermal",
                "Thermal",
                THERMALS,
                "Wall: adiabatic, a fixed temperature, a heat flux, or convection to an ambient "
                "temperature.",
            ),
            quantity(
                "heat_flux",
                "Heat flux",
                "heat_flux",
                0.0,
                "Wall with thermal heat_flux: the heat flux, positive into the fluid.",
            ),
            quantity(
                "heat_transfer_coefficient",
                "Heat transfer coefficient",
                "heat_transfer_coefficient",
                10.0,
                "Wall with thermal convection: the heat transfer coefficient to the ambient.",
            ),
            quantity(
                "ambient_temperature",
                "Ambient temperature",
                "temperature",
                300.0,
                "Wall with thermal convection: the temperature of the ambient.",
            ),
            ParamSpec(
                "partner",
                "ref",
                label="Partner",
                ref_type=BOUNDARY_TYPE,
                doc="Cyclic and fluid-solid interface: the matching boundary, which names this "
                "one back; null until set.",
            ),
            ParamSpec(
                "layers",
                # **A layer override for walls** (brief M-504, the 2026-10-19 1130 decision):
                # empty = inherit (the body's default, else the mesh settings'), `0` = none on
                # this wall, `n` = n. **No default on purpose** - the parameter's absence is the
                # empty state, and `parameter.set` with null clears it back; `null` is refused on
                # a parameter WITH a default, which is why this one has none.
                #
                # **Set on a non-wall it is refused at the snapshot** ("layers need a wall; inlet
                # is an inlet", naming the boundary) - a wall-only control that a patch cannot
                # carry. The rule lives in `stokes.mesh.layers.effective_layers`, which the
                # snapshot builder calls.
                "int",
                label="Layers",
                validate=validate_layer_count,
                doc="Layer override on this wall: empty inherits (the body's default, else "
                "the mesh settings'), 0 none, n asks for n. Walls only.",
            ),
            ParamSpec(
                "mesh_size",
                # **The per-boundary surface size** (brief M-503): the size snappyHexMesh aims for
                # on this tag's faces, when one boundary should be finer than the rest. Quantity
                # in SI (rule 6), **no default on purpose** - the absence is the empty = inherit
                # state, and `parameter.set` with null clears back to it, exactly like `layers`
                # above.
                #
                # **Precedence for one tag's surface** (the one sentence the docs repeat):
                # boundary size > domain body size > the mesh settings' surface_size (when > 0) >
                # the rule (a wall gets half the base size, any other face the base size).
                # Resolved by `stokes.mesh.sizing.surface_target`, the one function the snapshot
                # and the placeholders share.
                "quantity",
                label="Mesh size",
                dimension="length",
                doc="Surface size on this boundary's faces: empty inherits (the body's size, "
                "else the mesh settings', else the wall/other rule). A negative size is refused "
                "at the snapshot.",
            ),
        ),
        doc="A boundary condition on the faces of a tag: one type for every "
        "condition kind, carrying the union of their parameters; boundary.kinds says which a kind "
        "uses, and the rest keep their values and are ignored. It lives at "
        "/regions/<region>/boundaries/<name>.",
    )


def _operation_specs() -> tuple[NodeSpec, ...]:
    """One node type per operation (ADR-0021 decisions 5, 6 and 8.1), each with its own
    parameters, in the order and with the defaults the ADR and the briefs of T-024 to T-028 give
    them, then the common ``inputs``, ``outputs`` and ``auto_run``."""
    from stokes.model.schema import NodeSpec, ParamSpec

    def length(name: str, label: str, default: float, doc: str) -> ParamSpec:
        return ParamSpec(
            name, "quantity", label=label, dimension="length", default=default, doc=doc
        )

    def switch(name: str, label: str, default: bool, doc: str) -> ParamSpec:
        return ParamSpec(name, "bool", label=label, default=default, doc=doc)

    def choice(name: str, label: str, choices: tuple[str, ...], doc: str) -> ParamSpec:
        return ParamSpec(name, "enum", label=label, choices=choices, default=choices[0], doc=doc)

    def lengths(name: str, label: str, default: tuple[float, ...], doc: str) -> ParamSpec:
        return ParamSpec(name, "array", label=label, dimension="length", default=default, doc=doc)

    def tag_ref(name: str, label: str, doc: str) -> ParamSpec:
        return ParamSpec(name, "ref", label=label, ref_type=TAG_TYPE, doc=doc)

    shapes = ("box", "cylinder", "sphere")
    own: dict[str, tuple[ParamSpec, ...]] = {
        "primitive": (
            choice("shape", "Shape", shapes, "box, cylinder or sphere."),
            lengths(
                "size",
                "Size",
                (0.01, 0.01, 0.01),
                "Box: the edge lengths along x, y and z. Cylinder: the radius, the length along "
                "the axis, and a third value that is not used. Sphere: the radius; the other two "
                "are not used.",
            ),
            lengths(
                "position",
                "Position",
                (0.0, 0.0, 0.0),
                "Box: its corner with the smallest coordinates. Cylinder: the centre of its base. "
                "Sphere: its centre.",
            ),
            ParamSpec(
                "axis",
                "enum",
                label="Axis",
                choices=AXES,
                default="z",
                doc="The axis of a cylinder.",
            ),
        ),
        "boolean": (
            choice("op", "Operation", ("fuse", "cut", "common"), "fuse, cut or common."),
            ParamSpec(
                "tools",
                "json",
                label="Tools",
                default=[],
                max_bytes=JSON_PARTS_BYTES,
                validate=validate_part_ids,
                doc="The parts that cut, join or intersect the inputs, as node ids. Empty for a "
                "fuse or a common of the inputs alone.",
            ),
            length("fuzzy", "Fuzzy tolerance", 0.0, "Gaps and overlaps below it count as none."),
            switch(
                "simplify",
                "Simplify",
                True,
                "Merge the faces of a fuse or a cut that lie on one surface.",
            ),
        ),
        "enclosure": (
            choice("shape", "Shape", shapes, "box, cylinder or sphere around the bodies."),
            switch(
                "relative",
                "Relative margins",
                True,
                "Margins as multiples of the bodies' extent along each axis (factors), else the "
                "absolute margins.",
            ),
            ParamSpec(
                "factors",
                "array",
                label="Margin factors",
                dimension="dimensionless",
                default=(3.0, 8.0, 3.0, 3.0, 3.0, 3.0),
                doc="Relative margins for -x, +x, -y, +y, -z and +z.",
            ),
            lengths(
                "margins",
                "Margins",
                (0.0,) * 6,
                "Absolute margins for -x, +x, -y, +y, -z and +z, used when relative is false.",
            ),
            choice(
                "flow",
                "Flow direction",
                FLOWS,
                "Names the inlet (upstream face) and the outlet (downstream face).",
            ),
            choice("axis", "Axis", AXES, "The axis of a cylinder."),
            ParamSpec(
                "symmetry",
                "json",
                label="Symmetry planes",
                default=[],
                validate=validate_axes,
                doc='Planes through the bodies\' bounding-box centre, of "x", "y" and "z"; the '
                "positive half is kept.",
            ),
            switch(
                "ground",
                "Ground",
                False,
                "The -z face is tagged ground and sits at the bodies' lowest point.",
            ),
            switch(
                "subtract",
                "Subtract bodies",
                True,
                "The output is the enclosure cut by the bodies.",
            ),
            length("fuzzy", "Fuzzy tolerance", 0.0, "Fuzzy value of the cut."),
            switch(
                "tag_walls",
                "Tag walls",
                True,
                "Wetted body faces without a tag get the walls tag.",
            ),
        ),
        "extract_fluid": (
            choice(
                "mode",
                "Mode",
                ("region", "cavity"),
                "region: the volume around the seed point once the openings are capped; cavity: "
                "the inner voids of closed bodies.",
            ),
            lengths("seed", "Seed point", (0.0, 0.0, 0.0), "A point inside the fluid (region)."),
            tag_ref("caps", "Caps", "Faces that seal the openings; optional."),
            switch(
                "auto_cap",
                "Cap openings",
                True,
                "Cap the remaining closed free-edge loops with planar faces (region).",
            ),
            length("fuzzy", "Fuzzy tolerance", 0.0, "Fuzzy value of the volume maker."),
            ParamSpec(
                "min_volume",
                "quantity",
                label="Smallest cavity",
                dimension="volume",
                default=0.0,
                doc="Cavities below this volume are skipped.",
            ),
            ParamSpec(
                "cap_tag",
                "string",
                label="Cap tag",
                default="caps",
                pattern=r"[A-Za-z_][A-Za-z0-9_]*",
                doc="The tag the cap faces of the output get.",
            ),
        ),
        "heal": (
            length("tolerance", "Tolerance", 1e-6, "The precision the fixes work to."),
            length(
                "max_tolerance", "Largest tolerance", 1e-3, "The largest tolerance a fix may set."
            ),
            length("small_edges", "Small edges", 0.0, "Drop edges below this length; 0 is off."),
        ),
        "sew": (
            length("tolerance", "Tolerance", 1e-5, "Gaps below it are sewn."),
            switch("make_solid", "Make solid", True, "Closed shells become solids."),
            switch("non_manifold", "Non-manifold", False, "Allow edges of more than two faces."),
        ),
        "cap": (
            tag_ref(
                "loop",
                "Opening",
                "Edges of one opening to cap; optional. Without it every opening is capped.",
            ),
            length(
                "max_size", "Largest opening", 0.0, "Cap only loops up to this size; 0 caps all."
            ),
            switch("planar_only", "Planar only", False, "Leave openings that are not planar open."),
            length("tolerance", "Tolerance", 1e-5, "Tolerance of the free-edge search and sewing."),
        ),
        "unify": (
            length("linear_tolerance", "Linear tolerance", 1e-6, "Distance within one surface."),
            ParamSpec(
                "angular_tolerance",
                "quantity",
                label="Angular tolerance",
                dimension="angle",
                default=1e-3,
                doc="Angle within one surface.",
            ),
            tag_ref("keep", "Keep", "Faces to keep apart; optional."),
        ),
        "slivers": (
            length("size", "Size", 1e-4, "Spot and strip faces and edges below it go."),
            switch("holes", "Holes", False, "Also remove inner loops below size squared in area."),
        ),
        "defeature": (
            ParamSpec(
                "faces",
                "json",
                label="Faces",
                default=[],
                max_bytes=JSON_MEMBERS_BYTES,
                validate=validate_tag_members,
                doc='The faces to remove, as members: [{"part": <input id>, "faces": [[first, '
                "last], ...]}].",
            ),
            switch("unify", "Unify faces", True, "Unify the faces of the result."),
        ),
        "orient": (),
        "solidify": (
            length("tolerance", "Tolerance", 1e-5, "Tolerance of the sewing."),
            length("fuzzy", "Fuzzy tolerance", 0.0, "Fuzzy value of the volume maker."),
            switch("avoid_internal", "Avoid internal faces", True, "Leave internal faces out."),
        ),
    }
    common = (
        ParamSpec(
            "inputs",
            "json",
            label="Inputs",
            default=[],
            max_bytes=JSON_PARTS_BYTES,
            validate=validate_part_ids,
            doc="The parts the operation reads, as node ids (operation.create resolves paths "
            "and groups).",
        ),
        ParamSpec(
            "outputs",
            "json",
            label="Outputs",
            default=[],
            max_bytes=JSON_PARTS_BYTES,
            validate=validate_part_ids,
            doc="The parts the last run made, as node ids; written by the run.",
        ),
        ParamSpec(
            "auto_run",
            "bool",
            label="Run on change",
            default=True,
            doc="Run again 300 ms after a parameter or an input changes.",
        ),
    )
    return tuple(
        NodeSpec(
            kind.type,
            kind.label,
            params=(*own[kind.type], *common),
            doc=f"The {kind.label.lower()} operation: its outputs are "
            "derived parts under /geometry/parts; run it with operation.run.",
        )
        for kind in OPERATION_TYPES
    )


def register_geometry_types(target: NodeTypeRegistry) -> None:
    """Register the geometry node types (idempotent, like the other built-in types)."""
    for spec in geometry_specs():
        target.register(spec, replace=True)
