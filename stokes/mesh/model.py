"""The mesh node types (ADR-0024, T-143).

Three types under the existing structural group ``/mesh``:

    /mesh/settings                 mesh_settings   the mesher, the domain and the sizes
    /mesh/results/<mesher>         mesh_result     one finished (or running) meshing job
    /mesh/results/<mesher>/<patch> mesh_patch      one patch of that result

## Sizes are lengths, never "levels"

ADR-0024 and the owner's decision of 2026-10-13 bind this: every size is a **quantity**, so a user
writes ``0.5 [mm]`` and the model stores ``0.0005`` metres. snappyHexMesh's own ``levels`` appear
nowhere in this module, because a level is a resolution relative to a background mesh and cannot be
converted back to a length without knowing that mesh. A parameter that cannot be stated in SI is a
parameter whose meaning changes when someone edits a different field.

## Two reference choices, and why they are not the same

``domain_part`` is a **ref**, so deleting the part it names is refused (``model.referenced``) until
the settings stop pointing at it. That is deliberate: a settings node naming a part which no longer
exists would mesh nothing, and failing at the delete is where the user can still do something.

``mesh_patch.tag_id`` is a **string, not a ref**, and that is equally deliberate: a derived result
must never block an edit. An old patch remembering a tag that was since deleted is a stale colour,
not a broken project — and a ``ref`` would make ``node.remove`` on a tag raise because a result
nobody is looking at still names it.

## Import rule

Standard library at module level; the specs are built inside `mesh_specs`, which imports the schema.
This module is imported by `stokes.model.schema`, so anything heavy here would land in
``import stokes.api`` — which has a 300 ms budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, so the import stays out of the module load
    from stokes.model.schema import NodeSpec, NodeTypeRegistry

#: The four meshers, as the enum the settings' ``mesher`` parameter accepts. The catalogue holds the
#: labels and the reasons; this is only the spelling the model validates.
MESHERS: tuple[str, ...] = (
    "blockmesh",
    "snappy",
    "cfmesh",
    # ADR-0032: the volume fills beyond cfMesh's Cartesian one. A stored value is the id string,
    # so the order is the catalogue's (the UI's) and a new id changes no existing project.
    "cfmesh_tet",
    "cfmesh_poly",
    "polyhex",
    "gmsh",
)

#: The meshers served by the cfMesh driver (ADR-0032): one joined surface, `system/meshDict`.
CFMESH_MESHERS: tuple[str, ...] = ("cfmesh", "cfmesh_tet", "cfmesh_poly", "polyhex")

#: The meshers that follow the part's own surfaces - one surface per boundary tag, per-surface
#: sizes and wall layers - as opposed to blockMesh's bounding box. The basis, the task rows and the
#: front end's sizing and layer pages all ask this one question.
SURFACE_MESHERS: tuple[str, ...] = ("snappy", *CFMESH_MESHERS)

#: Where the domain comes from. ``region`` joins this with snappy (T-150), which is why it is an
#: enum and not a boolean.
DOMAINS: tuple[str, ...] = ("part", "box", "enclosure")

#: The 2D mode and plane choices (ADR-0031). Spelled here rather than imported from
#: `stokes.mesh.two_d` so this module keeps its standard-library-only import rule; a test pins the
#: two lists equal.
TWO_D_MODES: tuple[str, ...] = ("off", "planar", "axisymmetric")
TWO_D_PLANES: tuple[str, ...] = ("xy", "xz", "yz")

#: How layer thickness is given (brief M-504). `final_ratio` writes snappy's
#: `relativeSizes true` with `finalLayerThickness`; `first_height` writes `relativeSizes false`
#: with `firstLayerThickness` in metres. **One mode per project, not per patch**: snappy's
#: `relativeSizes` is a single bool for the whole dictionary (measured in the install's source,
#: `layerParameters.C:106`), so per-patch mixing does not exist to support.
LAYER_MODES: tuple[str, ...] = ("final_ratio", "first_height")

#: Every state a result may be in (ADR-0024 correction, binding on T-143).
#:
#: ``queued`` and ``running`` are work in flight; ``cancelling`` is a cancel that has been asked
#: for and not yet observed; ``done`` and ``failed`` are terminal; ``cancelled`` is a cancel that
#: was honoured; ``interrupted`` is a job the agent lost — a server restart, a killed process —
#: which is **not** the same as failed, because the work may have been fine and only the reporting
#: stopped; ``skipped`` is a replay that found no host, which must never be ``failed`` and must
#: never stop a replay.
RESULT_STATES: tuple[str, ...] = (
    "queued",
    "running",
    "cancelling",
    "done",
    "failed",
    "cancelled",
    "interrupted",
    "skipped",
)

SETTINGS_PATH = "/mesh/settings"
RESULTS_PATH = "/mesh/results"


def result_nodes(group: Any) -> list[Any]:
    """The `mesh_result` children of the `/mesh/results` group (None: no group), in tree order."""
    if group is None:
        return []
    return [child for child in group.children if child.type == "mesh_result"]


def active_result(group: Any) -> Any:
    """The result in use: the one marked `active`, else the latest. None when there is none.

    **One result per mesher is kept** (ADR-0034, which amends ADR-0024 §4.3 "latest only"), so a
    project can hold a tetrahedral, a polyhedral and a snappyHexMesh mesh at once and the user picks
    which one is shown and solved on. A project saved before that holds one result and no mark, and
    the latest is then the one in use, exactly as before.
    """
    results = result_nodes(group)
    marked = [child for child in results if child.params.get("active") is True]
    if marked:
        return marked[-1]
    return results[-1] if results else None


#: A job id, or empty. The parentheses make the whole thing optional rather than the last character.
JOB_PATTERN = r"(j_[0-9a-f]{12})?"

#: A content basis, or empty: what a result was built from, so a later change can mark it stale.
BASIS_PATTERN = r"[0-9a-f]{0,40}"

#: A tag id, or empty. **A string, not a ref** — see the module docstring.
TAG_ID_PATTERN = r"([a-z]{1,4}_[0-9a-f]{8})?"


def mesh_specs() -> tuple[NodeSpec, ...]:
    """The three mesh node types, built on call like the geometry and study types."""
    from stokes.cores import validate_core_count
    from stokes.hostnames import HOST_NAME_PATTERN
    from stokes.model.schema import (
        COLOR_PATTERN,
        NodeSpec,
        ParamSpec,
        validate_feature_angle,
        validate_layer_count,
        validate_number_between,
    )

    settings = NodeSpec(
        "mesh_settings",
        "Mesh settings",
        params=(
            ParamSpec(
                "mesher",
                "enum",
                label="Mesher",
                # **snappyHexMesh is the DEFAULT** (the owner, via claude-win2's 1045 message):
                # "make snappyHexMesh work and make it default so that any CAD that user loads is
                # meshable, blockMesh is user selectable". blockMesh meshes the bounding box, so a
                # round part came out a box and the faces inside it could not become patches;
                # snappyHexMesh follows the shape. `blockmesh` stays a choice, and its refusal still
                # names snappyHexMesh as the alternative.
                default="snappy",
                choices=MESHERS,
                doc="Which mesher builds the mesh.",
            ),
            ParamSpec(
                "host",
                "string",
                label="Host",
                default="",
                pattern=HOST_NAME_PATTERN,
                doc="The machine that runs the mesher; empty uses the Compute default.",
            ),
            ParamSpec(
                "base_size",
                "quantity",
                label="Base size",
                dimension="length",
                default=0.005,
                doc="The background cell size, in metres.",
            ),
            ParamSpec(
                "surface_size",
                "quantity",
                label="Surface size",
                dimension="length",
                default=0.0,
                # **No longer a dead setting** (brief M-503): the snapshot reads it, third in the
                # precedence after a body's own size and before the wall/other rule. 0 (the
                # default) keeps that rule, so an untouched project is byte-identical.
                doc="Surface size for snappy: 0 (the default) means the rule - walls half the "
                "base size, other faces the base size. A body's own size, and a boundary's, "
                "win over this.",
            ),
            ParamSpec(
                "domain",
                "enum",
                label="Domain",
                default="part",
                choices=DOMAINS,
                doc="Mesh one part, or a box the user gives.",
            ),
            ParamSpec(
                "domain_part",
                "ref",
                label="Domain part",
                ref_type="part",
                default=None,
                doc="The part whose fluid volume is meshed.",
            ),
            ParamSpec(
                "box",
                "array",
                label="Box",
                dimension="length",
                default=[],
                doc="xmin ymin zmin xmax ymax zmax, in metres.",
            ),
            ParamSpec(
                "margin",
                "quantity",
                label="Margin",
                dimension="length",
                default=0.0,
                doc="Added on every side of the domain.",
            ),
            ParamSpec(
                "max_cells",
                "int",
                label="Max cells",
                default=5_000_000,
                doc="Refuse a mesh larger than this.",
            ),
            ParamSpec(
                "cores",
                "int",
                label="Cores",
                default=1,
                validate=validate_core_count,
                doc="Processes for the mesher: MPI ranks (1 to 256) for snappyHexMesh, OpenMP "
                "threads for cfMesh. blockMesh runs serially.",
            ),
            ParamSpec(
                "max_non_ortho",
                "number",
                label="Max non-orthogonality",
                default=70.0,
                doc="checkMesh threshold in degrees.",
            ),
            ParamSpec(
                "max_skewness",
                "number",
                label="Max skewness",
                default=4.0,
                doc="checkMesh threshold.",
            ),
            ParamSpec(
                "accept_unassigned",
                # **`"bool"`, not `"boolean"`**: `ParamSpec`'s kinds are the schema's own and
                # `ParamDef`'s are not - a difference that fails at import with "unknown
                # parameter kind 'boolean'".
                "bool",
                label="Accept unassigned faces",
                default=False,
                doc="Treat faces without a boundary as a wall instead of refusing.",
            ),
            # ------------------------------------------------- boundary layers (brief M-504)
            #
            # **Ranges are enforced by the `validate` hooks** (`ParamSpec` has no min/max fields;
            # `stokes/model/schema.py`). The defaults are the 2026-10-19 1130 decision: layers
            # OFF (Q4 - the "Add 3 layers on every wall" button turns them on), expansion 1.2,
            # final ratio 0.3.
            #
            # **`layer_feature_angle` is 100.0 [VERIFY-OF14, measured]**: `featureAngle` in
            # snappy's `addLayersControls`; 9 of the 15 OF14 tutorials that write the block use
            # 100 (motorBike among them), 5 use 60 (propeller), one 180 - recorded in
            # `tests/fixtures/of14/inventory-thinkyboy.md`.
            ParamSpec(
                "layers",
                "int",
                label="Layers",
                default=0,
                validate=validate_layer_count,
                doc="Default boundary-layer count on every wall; 0 off (the default).",
            ),
            ParamSpec(
                "layer_mode",
                "enum",
                label="Layer mode",
                default="final_ratio",
                choices=LAYER_MODES,
                doc="How layer thickness is given: the final layer as a fraction of the "
                "local cell size (final_ratio), or an absolute height for the first "
                "layer (first_height).",
            ),
            ParamSpec(
                "expansion_ratio",
                "number",
                label="Expansion ratio",
                default=1.2,
                validate=validate_number_between("the expansion ratio", 1.0, 2.0),
                doc="Growth from one layer to the next, 1.0 (none) to 2.0.",
            ),
            ParamSpec(
                "final_ratio",
                "number",
                label="Final layer ratio",
                default=0.3,
                validate=validate_number_between("the final layer ratio", 0.05, 1.0),
                doc="final_ratio mode: the last layer's thickness as a fraction of the "
                "local cell size.",
            ),
            ParamSpec(
                "first_layer_height",
                "quantity",
                label="First layer height",
                dimension="length",
                default=0.0,
                doc="first_height mode: the first layer's thickness, in metres.",
            ),
            ParamSpec(
                "min_thickness_ratio",
                "number",
                label="Min thickness ratio",
                default=0.1,
                validate=validate_number_between("the minimum thickness ratio", 0.01, 1.0),
                doc="Below this thickness a layer is not added: in final_ratio mode the "
                "fraction of the local cell size snappy uses directly; in first_height "
                "mode the same fraction of the first layer height.",
            ),
            ParamSpec(
                "layer_feature_angle",
                "number",
                label="Layer feature angle",
                default=100.0,
                validate=validate_number_between("the layer feature angle", 0.0, 180.0),
                doc="Layers stop growing round corners sharper than this, in degrees.",
            ),
            ParamSpec(
                "feature_angle",
                # **The sharp-edge threshold** (brief M-515): edges sharper than this are kept by
                # the mesher. It reaches BOTH dictionaries, keeping their existing relation —
                # `resolveFeatureAngle` in snappyHexMeshDict, and surfaceFeaturesDict's
                # `includedAngle` as the complement (180 - angle; the utility measures the angle
                # BETWEEN two faces). At the default 30 both dictionaries are byte-identical to
                # today (the golden test is that).
                "number",
                label="Feature angle",
                default=30.0,  # today's RESOLVE_FEATURE_ANGLE (drivers/snappy.py:75)
                validate=validate_feature_angle,
                doc="Edges sharper than this are kept by the mesher, in degrees.",
            ),
            # ------------------------------------------------- 2D (ADR-0031)
            #
            # **Off by default, and an untouched project writes byte-identical dictionaries**: the
            # snapshot carries an empty 2D record when `two_d` is off, and the basis hashes it
            # only when it is not empty.
            ParamSpec(
                "two_d",
                "enum",
                label="2D",
                default="off",
                choices=TWO_D_MODES,
                doc="Mesh one cell thick for a 2D run: planar (empty front and back) or "
                "axisymmetric (a wedge about the plane's first axis).",
            ),
            ParamSpec(
                "plane",
                "enum",
                label="Plane",
                default="xy",
                choices=TWO_D_PLANES,
                doc="The 2D plane, by its two axes; the third axis is the thickness. "
                "Axisymmetric: the first axis is the symmetry axis, the second the radius.",
            ),
            ParamSpec(
                "thickness",
                "quantity",
                label="Thickness",
                dimension="length",
                default=0.0,
                doc="2D planar: the mesh's thickness across the plane; 0 keeps the domain's own.",
            ),
            ParamSpec(
                "wedge_angle",
                "number",
                label="Wedge angle",
                default=5.0,
                validate=validate_number_between("the wedge angle", 0.1, 10.0),
                doc="2D axisymmetric: the wedge's full angle, in degrees.",
            ),
        ),
    )

    result = NodeSpec(
        "mesh_result",
        "Mesh result",
        children=("mesh_patch",),
        params=(
            # **The RESULT names what BUILT it, so it does not follow the settings default above.**
            # A stored result records the mesher that produced it, and the owner's rule is that
            # nothing rewrites that: an existing project's results must not start claiming snappy
            # because the default moved (claude-win2's 1045, point 2). A reader loading an old
            # project sees what actually ran.
            ParamSpec("mesher", "enum", label="Mesher", default="blockmesh", choices=MESHERS),
            ParamSpec("host", "string", label="Host", default="local", pattern=HOST_NAME_PATTERN),
            ParamSpec(
                "basis",
                "string",
                label="Basis",
                default="",
                pattern=BASIS_PATTERN,
                doc="What this result was built from, so a later change can mark it stale.",
            ),
            ParamSpec(
                "state",
                "enum",
                label="State",
                default="queued",
                choices=RESULT_STATES,
            ),
            ParamSpec("job", "string", label="Job", default="", pattern=JOB_PATTERN),
            ParamSpec("cells", "int", label="Cells", default=0),
            ParamSpec("points", "int", label="Points", default=0),
            ParamSpec("faces", "int", label="Faces", default=0),
            ParamSpec(
                "quality",
                "json",
                label="Quality",
                default={},
                max_bytes=65536,
                doc="The checkMesh summary the agent reported.",
            ),
            ParamSpec("ok", "bool", label="Passed", default=False),
            ParamSpec("seconds", "number", label="Seconds", default=0.0),
            ParamSpec("error", "string", label="Error", default=""),
            ParamSpec(
                "active",
                "bool",
                label="In use",
                default=False,
                doc=(
                    "The mesh the viewport shows and a run solves on (`mesh.use`). One result per "
                    "mesher is kept; when none is marked, the latest is the one in use."
                ),
            ),
        ),
    )

    patch = NodeSpec(
        "mesh_patch",
        "Mesh patch",
        params=(
            ParamSpec("index", "int", label="Index", default=0),
            ParamSpec("patch_type", "string", label="Type", default="patch"),
            ParamSpec("faces", "int", label="Faces", default=0),
            ParamSpec("triangles", "int", label="Triangles", default=0),
            ParamSpec(
                "tag_id",
                "string",
                label="Tag",
                default="",
                pattern=TAG_ID_PATTERN,
                doc="The tag this patch's faces belong to, if any. A string, not a ref.",
            ),
            ParamSpec("visible", "bool", label="Visible", default=True),
            ParamSpec("color", "string", label="Colour", default="#9aa3ad", pattern=COLOR_PATTERN),
            ParamSpec("opacity", "number", label="Opacity", default=1.0),
        ),
    )
    return (settings, result, patch)


def register_mesh_types(target: NodeTypeRegistry) -> None:
    """Register the mesh node types (idempotent, like the other built-in types)."""
    for spec in mesh_specs():
        target.register(spec, replace=True)


def settings_summary(node: Any) -> dict[str, Any]:
    """A settings node as a client reads it: every parameter plus the id and path.

    Kept here rather than in the command module so that the HTTP server, the CLI and the front
    end all describe a settings node the same way.
    """
    summary: dict[str, Any] = dict(node.summary())
    summary["id"] = node.id
    summary["path"] = node.path
    return summary
