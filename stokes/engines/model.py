"""The `solver_settings` node type (ADR-0011, T-214a).

One node under the existing structural group:

    /solver/settings    solver_settings   the engine, the model and the run controls

## Why this is not part of `/mesh/settings`

Meshing and solving are separate steps with separate lifetimes: a mesh is made once and solved
several
times with different models, and a solve reads a mesh rather than producing one. Sharing a
node would
make every mesh edit look like a solve edit.

## Everything numeric is a quantity, and the unitless things say so

`slice_origin` is a **length**; `slice_normal` and `residual_target` are dimensionless and
take `number`
or `array` accordingly. A bare float for a physical value is the bug ADR-0024 exists to
prevent, so the
only parameters without a dimension here are the ones that genuinely have none.

## Import rule

Standard library at module level; the specs are built inside `solver_specs`, which imports the
schema.
`stokes.model.schema` imports this module while registering, so anything heavy here would land in
``import stokes.api`` — which has a 300 ms budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stokes.engines.turbulence import MODEL_NAMES


def _positive_solver_value(_node: object, value: Any) -> Any:  # Any: stored JSON or QuantityValue
    """Protect unit-aware time and initialization parameters on every API write."""
    import math

    from stokes.errors import ValidationError
    from stokes.model.nodes import QuantityValue

    number = value.value if isinstance(value, QuantityValue) else value
    if (
        isinstance(number, bool)
        or not isinstance(number, (int, float))
        or not math.isfinite(number)
        or number <= 0
    ):
        raise ValidationError(
            "solver time, molecular mass and turbulence initialization values "
            "must be positive and finite"
        )
    return value


if TYPE_CHECKING:  # pragma: no cover - typing only, so the import stays out of the module load
    from stokes.model.schema import NodeSpec, NodeTypeRegistry

#: Where the solve settings live. One node, like `/mesh/settings`.
SETTINGS_PATH = "/solver/settings"

#: The group the settings sit under, part of the skeleton.
SOLVER_PATH = "/solver"

#: A host name, shared with `mesh_settings` so the two forms accept the same names (ADR-0024).
#: Imported in `solver_specs`, keeping this module's module-level imports to the standard library.

#: The values the model accepts; the catalogue says which engine offers which.
TURBULENCE: tuple[str, ...] = MODEL_NAMES


def solver_specs() -> tuple[NodeSpec, ...]:
    """The `solver_settings` type, built on call like the geometry, mesh and study types."""
    from stokes.cores import validate_core_count
    from stokes.hostnames import HOST_NAME_PATTERN
    from stokes.model.schema import NodeSpec, ParamSpec

    return (
        NodeSpec(
            "solver_settings",
            "Solver settings",
            params=(
                ParamSpec(
                    "engine",
                    "string",
                    label="Engine",
                    default="of14.incompressibleFluid",
                    doc="The solver that writes and runs the case (stokes/engines/catalogue.py).",
                ),
                ParamSpec(
                    "host",
                    "string",
                    label="Host",
                    default="",
                    pattern=HOST_NAME_PATTERN,
                    doc="The machine that runs the solver; empty uses the Compute default.",
                ),
                ParamSpec(
                    "turbulence",
                    "enum",
                    label="Turbulence",
                    default="laminar",
                    choices=TURBULENCE,
                    doc="The Foundation 14 momentum-transport model. The engine catalogue "
                    "states which models are registered for each solver.",
                ),
                ParamSpec(
                    "energy",
                    "bool",
                    label="Energy equation",
                    default=False,
                    doc="Solve the energy equation (temperature). Needs the of14.fluid engine; "
                    "works with any of its flow models, steady or transient. Inlets then take a "
                    "temperature.",
                ),
                ParamSpec(
                    "density_model",
                    "enum",
                    label="Density model",
                    default="constant",
                    choices=("constant", "perfectGas"),
                    doc="Constant density or compressible perfect gas. "
                    "Perfect gas needs the fluid engine and energy.",
                ),
                ParamSpec(
                    "molar_mass",
                    "quantity",
                    label="Molar mass",
                    dimension="molar_mass",
                    default=0.02897,
                    validate=_positive_solver_value,
                    doc="Gas molecular mass; stored in kg/mol.",
                ),
                ParamSpec(
                    "transient",
                    "bool",
                    label="Transient simulation",
                    default=False,
                    doc="Advance physical time; required for LES, DES and scale-adaptive models.",
                ),
                ParamSpec(
                    "delta_t",
                    "quantity",
                    label="Time step",
                    dimension="time",
                    default=0.001,
                    validate=_positive_solver_value,
                ),
                ParamSpec(
                    "end_time",
                    "quantity",
                    label="End time",
                    dimension="time",
                    default=1.0,
                    validate=_positive_solver_value,
                ),
                ParamSpec(
                    "adjustable_time_step",
                    "bool",
                    label="Adaptive time step",
                    default=False,
                    doc="Transient only: let the time step change to keep the Courant number "
                    "under Max Courant, starting from Time step.",
                ),
                ParamSpec(
                    "max_courant",
                    "number",
                    label="Max Courant",
                    default=1.0,
                    validate=_positive_solver_value,
                    doc="The largest Courant number the adaptive time step allows (maxCo). "
                    "About 1 for PIMPLE with an implicit scheme; below 1 for LES accuracy.",
                ),
                ParamSpec(
                    "write_time",
                    "quantity",
                    label="Write every (time)",
                    dimension="time",
                    default=0.0,
                    doc="Transient only: write the fields every this much physical time. Zero "
                    "writes every 'Write every' time steps instead.",
                ),
                ParamSpec(
                    "time_average",
                    "bool",
                    label="Time-averaged fields",
                    default=False,
                    doc="Transient only: also write the time averages UMean and pMean (plus kMean "
                    "or nutMean for a turbulence model), averaged from Average from.",
                ),
                ParamSpec(
                    "average_start",
                    "quantity",
                    label="Average from",
                    dimension="time",
                    default=0.0,
                    doc="The physical time the averaging starts at, after the start-up transient.",
                ),
                ParamSpec(
                    "initial_velocity",
                    "quantity",
                    label="Initial turbulence reference speed",
                    dimension="velocity",
                    default=1.0,
                    validate=_positive_solver_value,
                ),
                ParamSpec(
                    "initial_intensity",
                    "number",
                    label="Initial turbulence intensity",
                    default=0.05,
                    validate=_positive_solver_value,
                ),
                ParamSpec(
                    "initial_length_scale",
                    "quantity",
                    label="Initial turbulence length scale",
                    dimension="length",
                    default=0.01,
                    validate=_positive_solver_value,
                ),
                ParamSpec(
                    "initial_viscosity_ratio",
                    "number",
                    label="Initial turbulent viscosity ratio",
                    default=10.0,
                    validate=_positive_solver_value,
                ),
                ParamSpec(
                    "gravity",
                    "array",
                    label="Gravity",
                    dimension="acceleration",
                    default=[0.0, 0.0, 0.0],
                    doc="The gravity vector, written to constant/g. Only with the energy equation "
                    "on: the buoyancy term needs it, and the flow alone does not read it.",
                ),
                ParamSpec(
                    "operating_pressure",
                    "quantity",
                    label="Operating pressure",
                    dimension="pressure",
                    default=101325.0,
                    doc="The reference pressure, written to constant/pRef. Only with the energy "
                    "equation on, where the density depends on the absolute pressure.",
                ),
                ParamSpec(
                    "iterations",
                    "int",
                    label="Iterations",
                    default=500,
                    doc="The most iterations the solver may take (endTime for a steady run).",
                ),
                ParamSpec(
                    "write_interval",
                    "int",
                    label="Write every",
                    default=50,
                    doc="Write a time directory every this many iterations.",
                ),
                ParamSpec(
                    "residual_target",
                    "number",
                    label="Residual target",
                    default=1e-3,
                    doc="Stop when every field's initial residual falls below this. Positive, "
                    "checked by solve.configure rather than by the schema.",
                ),
                ParamSpec(
                    "cores",
                    "int",
                    label="Cores",
                    default=1,
                    validate=validate_core_count,
                    doc="MPI processes (1 to 256). One runs serially; larger counts require MPI "
                    "on the selected host.",
                ),
                ParamSpec(
                    "slice_origin",
                    "array",
                    label="Slice origin",
                    dimension="length",
                    default=[],
                    doc="The slice plane's origin in metres; empty means the centre of the "
                    "mesh's bounding box.",
                ),
                ParamSpec(
                    "slice_normal",
                    "array",
                    label="Slice normal",
                    default=[0.0, 0.0, 1.0],
                    doc="The slice plane's normal; dimensionless, so a plain array.",
                ),
            ),
            doc="The solver, the turbulence model and the run controls. One node, "
            "`/solver/settings`.",
        ),
    )


def register_engine_types(target: NodeTypeRegistry) -> None:
    """Register the solver node type (idempotent, like the other built-in types)."""
    for spec in solver_specs():
        target.register(spec, replace=True)


def settings_summary(node: Any) -> dict[str, Any]:
    """A settings node as a plain dict, for a command result or an event."""
    return {
        name: node.params.get(name)
        for name in (
            "engine",
            "host",
            "turbulence",
            "energy",
            "density_model",
            "molar_mass",
            "transient",
            "delta_t",
            "end_time",
            "initial_velocity",
            "initial_intensity",
            "initial_length_scale",
            "initial_viscosity_ratio",
            "gravity",
            "operating_pressure",
            "iterations",
            "write_interval",
            "residual_target",
            "cores",
            "slice_origin",
            "slice_normal",
        )
    }
