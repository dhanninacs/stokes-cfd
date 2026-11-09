"""Node and parameter schemas: the declarative description of the tree.

Every node type registers a :class:`NodeSpec`. Inspector forms, the API
command schemas, the CLI help and the MCP tool descriptions are generated from
these specs; nothing is hand-written twice.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from stokes.errors import UnknownNodeType, UnknownParameter, ValidationError
from stokes.units.dimensions import DIMENSIONS

if TYPE_CHECKING:
    from stokes.model.nodes import Node

MAX_JSON_BYTES = 64 * 1024
"""Default limit of a ``json`` parameter's serialised value (64 KB)."""

Kind = Literal["quantity", "number", "int", "bool", "string", "enum", "ref", "array", "json"]
KINDS: tuple[str, ...] = (
    "quantity",
    "number",
    "int",
    "bool",
    "string",
    "enum",
    "ref",
    "array",
    "json",
)
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
COLOR_PATTERN = r"#[0-9a-f]{6}"
DYNAMIC = "dynamic"
"""Dimension marker meaning: read the dimension from the node's ``dimension`` parameter."""
NODE_TYPE_ALIASES: dict[str, str] = {"named_selection": "tag"}
"""Renamed node types: old name to current name (ADR-0021 decisions 2.3 and 2.4).

Every registry that :func:`register_builtin_types` fills resolves these names, ``AddNode`` stores
the current one, and the migration of a version-1 project (:mod:`stokes.model.persist`) renames
stored nodes through them. ``stokes.api.legacy`` lists this dict as ``NODE_TYPES`` beside the
other old names. It is defined here because the model may not import the API. Add entries to this
literal and never rebind the name: other modules hold the dict itself."""


@dataclass(frozen=True)
class ParamSpec:
    """One parameter of a node type.

    ``array`` is a list of numbers; with a ``dimension`` its values are SI (a bounding box is
    six lengths in metres). ``pattern`` restricts a ``string`` to a full regular-expression
    match (a colour is ``#rrggbb``). ``json`` holds JSON-compatible data (an object, a list, a
    string, a number, a boolean or null), stored as is; ``max_bytes`` caps its serialised size.

    ``required`` marks a ``ref`` that may not be null (a boundary's ``tag``): a node is added
    only with it set, and setting it to null is refused. Every reference starts as null (a
    ``ref`` has no default), so the default cannot tell a required reference from an optional
    one, which null clears (ADR-0022 decision 2.8).

    ``validate`` is an optional structural check, run on the value as it is stored when a
    parameter is written: it returns the value to keep or raises a ``ValidationError`` that
    names the problem (the ``tag``'s ``members``, for example, must parse to the members
    structure the tag commands write).
    """

    name: str
    kind: Kind
    label: str = ""
    dimension: str | None = None
    default: Any = None  # Any: default value in the parameter's own JSON form
    choices: tuple[str, ...] = ()
    ref_type: str | None = None
    doc: str = ""
    pattern: str | None = None
    max_bytes: int | None = None
    required: bool = False
    validate: Callable[[Node, Any], Any] | None = None
    #: Keep this parameter out of the property grid, **while the API keeps accepting it**.
    #:
    #: **For a parameter the UI expresses somewhere else** (claude-win2's 2000 item 2). A part's
    #: `visible` is the case: the model still holds it and `geometry.style` still writes it,
    #: because a headless caller may legitimately want it and a project saved with a part hidden
    #: must load that way - but the property grid offering a second "Visible" toggle beside the
    #: tree's eye gives one meaning two controls, one of which saves and one of which does not.
    #: The tree's eye is the control; this keeps the parameter out of the grid without taking it
    #: out of the model.
    hidden: bool = False

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.name):
            raise ValidationError(f"invalid parameter name {self.name!r}")
        if self.kind not in KINDS:
            raise ValidationError(f"unknown parameter kind {self.kind!r}")
        if self.kind == "quantity":
            if self.dimension is None:
                raise ValidationError(f"quantity parameter {self.name!r} needs a dimension")
            if self.dimension != DYNAMIC and self.dimension not in DIMENSIONS:
                raise ValidationError(f"unknown dimension {self.dimension!r} for {self.name!r}")
        if self.kind == "array" and self.dimension is not None and self.dimension not in DIMENSIONS:
            raise ValidationError(f"unknown dimension {self.dimension!r} for {self.name!r}")
        if self.kind == "enum" and not self.choices:
            raise ValidationError(f"enum parameter {self.name!r} needs choices")
        if self.kind == "ref" and not self.ref_type:
            raise ValidationError(f"ref parameter {self.name!r} needs ref_type")
        if self.required and self.kind != "ref":
            raise ValidationError(f"only ref parameters can be required ({self.name!r})")
        if self.pattern is not None:
            if self.kind != "string":
                raise ValidationError(f"only string parameters take a pattern ({self.name!r})")
            re.compile(self.pattern)
        if self.max_bytes is not None:
            if self.kind != "json":
                raise ValidationError(f"only json parameters take max_bytes ({self.name!r})")
            if self.max_bytes <= 0:
                raise ValidationError(f"max_bytes of {self.name!r} must be positive")

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"title": self.label or self.name}
        if self.doc:
            schema["description"] = self.doc
        # **`x-hidden`, so a property grid can leave a parameter out without it leaving the model.**
        # The `x-` prefix is this schema's own convention for the extensions the front end reads
        # (`x-type`, `x-kind`, `x-dimension`); a plain `hidden` would read as a JSON Schema keyword,
        # where it is an annotation for documentation rather than an instruction to a renderer.
        #
        # **Built into the literal, via a comprehension that yields nothing when False**, because
        # this function sits at the linter's branch limit and one more `if` pushes it over.
        schema.update({key: True for key in ("x-hidden",) if self.hidden})
        match self.kind:
            case "quantity":
                schema.update(
                    {
                        "type": ["string", "number"],
                        "x-kind": "quantity",
                        "x-dimension": self.dimension,
                        "description": (self.doc + " " if self.doc else "")
                        + "A number with a unit, e.g. '2 [m/s]', or an expression.",
                    }
                )
            case "number":
                schema["type"] = "number"
            case "int":
                schema["type"] = "integer"
            case "bool":
                schema["type"] = "boolean"
            case "string":
                schema["type"] = "string"
                if self.pattern is not None:
                    schema["pattern"] = f"^{self.pattern}$"
            case "enum":
                schema.update({"type": "string", "enum": list(self.choices)})
            case "ref":
                schema.update({"type": "string", "x-kind": "ref", "x-ref-type": self.ref_type})
                if self.required:
                    schema["x-required"] = True
            case "array":
                schema.update({"type": "array", "items": {"type": "number"}})
                if self.dimension is not None:
                    schema["x-dimension"] = self.dimension
            case "json":
                schema["x-kind"] = "json"
                if self.max_bytes is not None:
                    schema["x-max-bytes"] = self.max_bytes
        if self.default is not None:
            schema["default"] = self.default
        return schema


def validate_layer_count(node: Node, value: Any) -> Any:  # Any: JSON
    """A ``validate`` hook bounding a layer count to **0..10** (brief M-504; review R13b uses the
    same range for the settings' default, not 0..20).

    Ten is already a silly count for hex layers grown by an expansion ratio, and the field
    accepts `null` (the empty = inherit state), which passes through untouched: the hook sees
    only stored values.
    """
    if value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"the layer count of {node.path} expects an integer; got {value!r}")
    if not 0 <= value <= 10:
        raise ValidationError(
            f"the layer count of {node.path} must be between 0 and 10; got {value}"
        )
    return value


def validate_feature_angle(node: Node, value: Any) -> Any:  # Any: JSON
    """A ``validate`` hook bounding the sharp-edge threshold to **strictly between 0 and 180**
    degrees (brief M-515): 0 would keep every edge however shallow, 180 would keep none, and
    neither is a threshold — each is the threshold's negation. The hook sees only stored values;
    ``None`` (a parameter with no default) passes through untouched.
    """
    if value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValidationError(f"the feature angle of {node.path} expects a number; got {value!r}")
    if not 0 < value < 180:
        raise ValidationError(
            f"the feature angle of {node.path} must be between 0 and 180, exclusive; got {value}"
        )
    return value


def validate_number_between(label: str, low: float, high: float) -> Callable[[Node, Any], Any]:
    """A ``validate`` hook bounding a number parameter to ``low..high``, naming the parameter by
    its ``label`` (the hook receives the node and the stored value only)."""

    def check(node: Node, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValidationError(f"{label} expects a number; got {value!r}")
        if not low <= value <= high:
            raise ValidationError(f"{label} must be between {low} and {high}; got {value}")
        return value

    return check


@dataclass(frozen=True)
class NodeSpec:
    """A node type: its parameters and which child types it accepts."""

    type: str
    label: str
    params: tuple[ParamSpec, ...] = ()
    children: tuple[str, ...] = ()
    doc: str = ""

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.type):
            raise ValidationError(f"invalid node type {self.type!r}")
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            raise ValidationError(f"duplicate parameter names in node type {self.type!r}")

    def param(self, name: str) -> ParamSpec:
        for spec in self.params:
            if spec.name == name:
                return spec
        raise UnknownParameter(
            f"node type {self.type!r} has no parameter {name!r}; "
            f"known: {', '.join(p.name for p in self.params) or 'none'}",
            details={"type": self.type, "parameter": name},
        )

    def allows_child(self, child_type: str) -> bool:
        return "*" in self.children or child_type in self.children

    def json_schema(self) -> dict[str, Any]:
        return {
            "title": self.label,
            "description": self.doc,
            "x-type": self.type,
            "x-children": list(self.children),
            "type": "object",
            "properties": {p.name: p.json_schema() for p in self.params},
        }


class NodeTypeRegistry:
    """Registry of node types. The module-level :data:`registry` is the default.

    A renamed type keeps its old name as an alias (:meth:`alias`), so that old projects and
    journals still name it: :meth:`get` resolves an alias, :meth:`canonical` gives the current
    name, and :meth:`types` lists current names only. A name is a type or an alias, never both.
    """

    def __init__(self) -> None:
        self._specs: dict[str, NodeSpec] = {}
        self._aliases: dict[str, str] = {}

    def register(self, spec: NodeSpec, *, replace: bool = False) -> NodeSpec:
        if spec.type in self._aliases:
            current = self.canonical(spec.type)
            raise ValidationError(
                f"node type {spec.type!r} is the old name of {current!r}",
                details={"type": spec.type, "alias_of": current},
            )
        if spec.type in self._specs and not replace and self._specs[spec.type] != spec:
            raise ValidationError(f"node type {spec.type!r} already registered differently")
        self._specs[spec.type] = spec
        return spec

    def alias(self, old: str, new: str) -> None:
        """Make ``old``, the name of a renamed type, stand for ``new``.

        ``new`` may be registered later, and may itself be renamed later: aliases chain. Giving
        the same pair again changes nothing.
        """
        for name in (old, new):
            if not NAME_RE.match(name):
                raise ValidationError(f"invalid node type {name!r}")
        if old in self._specs:
            raise ValidationError(
                f"node type {old!r} is registered; only the name of a renamed type is an alias",
                details={"type": old},
            )
        if self._aliases.get(old, new) != new:
            raise ValidationError(
                f"node type {old!r} is the old name of {self._aliases[old]!r} already",
                details={"type": old, "alias_of": self._aliases[old]},
            )
        if self.canonical(new) == old:
            raise ValidationError(f"node type alias {old!r} -> {new!r} would make a cycle")
        self._aliases[old] = new

    def canonical(self, node_type: str) -> str:
        """The current name of ``node_type``: the type an alias leads to, else the name itself."""
        while node_type in self._aliases:  # acyclic: alias() refuses a cycle
            node_type = self._aliases[node_type]
        return node_type

    def aliases(self) -> dict[str, str]:
        """Every alias with the current name it resolves to."""
        return {old: self.canonical(old) for old in sorted(self._aliases)}

    def get(self, node_type: str) -> NodeSpec:
        current = self.canonical(node_type)
        try:
            return self._specs[current]
        except KeyError as exc:
            renamed = f" (the old name of {current!r})" if current != node_type else ""
            raise UnknownNodeType(
                f"unknown node type {node_type!r}{renamed}; "
                f"known: {', '.join(sorted(self._specs))}",
                details={"type": node_type},
            ) from exc

    def types(self) -> list[str]:
        """The registered types; aliases are not listed."""
        return sorted(self._specs)

    def json_schema(self) -> dict[str, Any]:
        return {t: self._specs[t].json_schema() for t in self.types()}


registry = NodeTypeRegistry()


def register_builtin_types(target: NodeTypeRegistry) -> None:
    """The node types every project has, and the old names of renamed ones
    (:data:`NODE_TYPE_ALIASES`). Later milestones add their own."""
    target.register(
        NodeSpec(
            "project",
            "Project",
            children=("group",),
            doc="The root. Its children are the fixed top-level groups.",
        ),
        replace=True,
    )
    target.register(
        NodeSpec(
            "group",
            "Group",
            children=("*",),
            doc="A container with no parameters of its own.",
        ),
        replace=True,
    )
    target.register(
        NodeSpec(
            "parameter",
            "Parameter",
            params=(
                ParamSpec(
                    "dimension",
                    "enum",
                    label="Dimension",
                    choices=tuple(DIMENSIONS),
                    default="dimensionless",
                    doc="Physical dimension of the value.",
                ),
                ParamSpec(
                    "value",
                    "quantity",
                    label="Value",
                    dimension=DYNAMIC,
                    default=0.0,
                    doc="A quantity or an expression over other parameters.",
                ),
                ParamSpec("description", "string", label="Description", default=""),
            ),
            doc="A named quantity usable in expressions anywhere in the project.",
        ),
        replace=True,
    )
    target.register(
        NodeSpec(
            "part",
            "Part",
            params=(
                ParamSpec(
                    "source",
                    "string",
                    label="Source",
                    default="",
                    doc="The CAD file the part was imported from.",
                ),
                ParamSpec(
                    "solid",
                    "int",
                    label="Solid",
                    default=0,
                    doc="Which solid of the source file this part is (0 is the first).",
                ),
                ParamSpec(
                    "units",
                    "string",
                    label="File units",
                    default="",
                    doc="Length unit the source file declares; the mesh is in metres.",
                ),
                ParamSpec(
                    "family",
                    "enum",
                    label="Family",
                    choices=("brep", "mesh"),
                    default="brep",
                    doc="B-rep or mesh-only part: what the import read the source as ",
                ),
                ParamSpec(
                    "triangles",
                    "int",
                    label="Triangles",
                    default=0,
                    doc="Triangles in the display mesh.",
                ),
                ParamSpec(
                    "faces",
                    "int",
                    label="Faces",
                    default=0,
                    doc="B-rep faces; every triangle carries the id of its face.",
                ),
                ParamSpec(
                    "bbox",
                    "array",
                    label="Bounding box",
                    dimension="length",
                    doc="Minimum x, y, z, then maximum x, y, z of the mesh; empty until imported.",
                ),
                ParamSpec(
                    "color",
                    "string",
                    label="Colour",
                    default="#c7ccd1",
                    pattern=COLOR_PATTERN,
                    doc="Display colour as #rrggbb.",
                ),
                ParamSpec(
                    "visible",
                    "bool",
                    label="Visible",
                    default=True,
                    # **Out of the grid, still in the model**: the tree's eye is the control
                    # for this, and a second toggle beside it would save what the eye does not.
                    hidden=True,
                ),
                ParamSpec(
                    "opacity",
                    "number",
                    label="Opacity",
                    default=1.0,
                    doc="0 transparent to 1 opaque.",
                ),
                ParamSpec(
                    "simulate",
                    "bool",
                    label="In simulation",
                    default=True,
                    doc=(
                        "Whether meshing, compiling and the checks use this body. "
                        "Different from Visible: hiding is display-only and never leaves "
                        "a body out of a mesh."
                    ),
                ),
                # ================================================= per-body mesh
                #
                # **THREE properties of the BODY, not of the mesh** - `mesh_size`, `refinement`
                # and `layers`. The proposal of 2026-10-19: "this flange is finer than the pipe"
                # is a fact about the flange, so storing it here means it survives a rename or a
                # re-import and the mesh settings do not grow a key per body.
                #
                # `layers` was added once before (2026-10-18) and withdrawn the same day as a dead
                # field - the snappy driver still wrote an EMPTY `layers {}` block, so the control
                # changed nothing. It returns with the reader that gives it its meaning:
                # `stokes.mesh.layers.effective_layers` resolves the counts into snappy's
                # `addLayersControls` and the writer emits them.
                #
                # **Empty means INHERIT, 0 means NONE, n means n** - the rule the boundary
                # override carries too (2026-10-19 1130). An untouched body meshes exactly as
                # before - the golden dictionary is the test that says so. A stored `0` written
                # while the withdrawn parameter existed (its default was 0 meaning INHERIT) is
                # read as empty by the persist v2->v3 migration, which drops it at load.
                #
                # **No default on purpose**: the parameter's absence is the empty state, and
                # `parameter.set` with null clears such a parameter back to it.
                ParamSpec(
                    "mesh_size",
                    "quantity",
                    label="Mesh size",
                    dimension="length",
                    doc=(
                        "Local surface size for this body. Precedence for its faces: this "
                        "size, else the mesh settings' surface size (when set), else the "
                        "wall/other rule - a wall gets half the base size, any other face "
                        "the base size."
                    ),
                ),
                ParamSpec(
                    "refinement",
                    "int",
                    label="Refinement",
                    default=0,
                    doc=(
                        "Extra snappyHexMesh levels on this body, above what its size "
                        "gives: 0 none, 3 the finest. Ignored by a mesher without "
                        "refinements."
                    ),
                ),
                ParamSpec(
                    "layers",
                    "int",
                    label="Boundary layers",
                    validate=validate_layer_count,
                    doc=(
                        "Boundary-layer cells on this body's walls, as its default every "
                        "wall of it inherits: empty takes the mesh settings' count, "
                        "0 turns layers off here, n asks for n on each wall. A layer "
                        "needs a wall, so a body with no wall patch gets none."
                    ),
                ),
                ParamSpec(
                    "basis",
                    "string",
                    label="B-rep version",
                    default="",
                    pattern=r"[0-9a-f]{0,32}",
                    doc="The version of the part's B-rep that its face ids refer to (ADR-0021 "
                    "decision 8.4), written by the import and by the operation that made the "
                    "part; a tag member whose basis differs is stale. Empty in projects saved "
                    "before it existed.",
                ),
                ParamSpec(
                    "derived",
                    "string",
                    label="Made by",
                    default="",
                    pattern=r"([a-z]{1,4}_[0-9a-f]{8})?",
                    doc="The id of the operation whose output this part is ( "
                    "8.2); empty for an imported part.",
                ),
            ),
            doc="One imported solid with its display mesh, or the output of an operation; a file "
            "of several solids is a group of parts (cision 8).",
        ),
        replace=True,
    )
    from stokes.explore.model import register_study_types  # ADR-0016; numpy-free, imported here

    register_study_types(target)
    from stokes.physics.materials.model import register_material_types  # ADR-0018; numpy-free

    register_material_types(target)
    from stokes.geometry.model import register_geometry_types  # ADR-0020; standard library only

    register_geometry_types(target)
    from stokes.mesh.model import register_mesh_types  # ADR-0024; standard library only

    register_mesh_types(target)
    from stokes.engines.model import register_engine_types  # ADR-0011; standard library only

    register_engine_types(target)
    from stokes.cases.model import register_case_types  # ADR-0029; standard library only

    register_case_types(target)
    from stokes.model.views import register_view_types  # saved views; standard library only

    register_view_types(target)
    from stokes.model.field_functions import register_field_function_types  # standard library only

    register_field_function_types(target)
    for old, new in NODE_TYPE_ALIASES.items():
        target.alias(old, new)


register_builtin_types(registry)
