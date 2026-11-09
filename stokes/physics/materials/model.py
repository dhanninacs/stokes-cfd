"""Material node types (ADR-0018 decision 1): ``material`` and its fixed ``property`` children.

:func:`stokes.model.schema.register_builtin_types` calls :func:`register_material_types`, so every
project knows these types. A material lives under ``/physics/materials`` and is created by the
``material.*`` commands with one ``property`` child per property kind its material kind takes,
named after the kind and in the order of :data:`PROPERTY_KINDS`, so that
``physics.materials.water.viscosity.value`` is a stable expression reference.

One ``property`` type carries the union of every method's parameters (the contract's table);
``stokes.physics.materials.catalogue`` says which ones a method uses. A property's ``value`` takes
its dimension from the property's ``dimension`` (``DYNAMIC``), which the kind fixes
(:data:`PROPERTY_DIMENSIONS`); every other quantity has a fixed dimension, and the coefficient
arrays are SI numbers whose units the method's formula defines.

Import rule: ``stokes.model.schema`` imports this module while it registers its node types, so at
module level it imports only the standard library; ``material_specs`` imports the schema when
called. No numpy, nothing else from this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stokes.model.schema import NodeSpec, NodeTypeRegistry, ParamSpec

MATERIALS_PATH = "/physics/materials"
MATERIAL_KINDS: tuple[str, ...] = ("fluid", "solid")
"""``mixture`` is reserved for ADR-0018 slice 6 and is added to the enum then."""
SOURCES: tuple[str, ...] = ("user", "builtin", "library")
PROVIDERS: tuple[str, ...] = (
    "none",
    "builtin",
    "coolprop",
    "coolprop_incomp",
    "cantera",
    "refprop",
)
BASES: tuple[str, ...] = ("mole", "mass", "volume")
PHASES: tuple[str, ...] = ("auto", "liquid", "gas", "supercritical")

PROPERTY_KINDS: tuple[str, ...] = (
    "density",
    "viscosity",
    "specific_heat",
    "thermal_conductivity",
    "molar_mass",
    "formation_enthalpy",
)
"""Every property kind of v1, in creation order. ``surface_tension`` and ``emissivity`` are
reserved for v1.5 and join the enum then."""
PROPERTY_DIMENSIONS: dict[str, str] = {
    "density": "density",
    "viscosity": "dynamic_viscosity",
    "specific_heat": "specific_heat",
    "thermal_conductivity": "thermal_conductivity",
    "molar_mass": "molar_mass",
    "formation_enthalpy": "specific_energy",
}
KIND_PROPERTIES: dict[str, tuple[str, ...]] = {
    "fluid": PROPERTY_KINDS,
    "solid": ("density", "specific_heat", "thermal_conductivity", "molar_mass"),
}
"""The property children a material of each kind is created with, in this order."""

METHOD_IDS: tuple[str, ...] = (
    "constant",
    "ideal_gas",
    "incompressible_ideal_gas",
    "boussinesq",
    "polynomial",
    "piecewise_linear",
    "piecewise_polynomial",
    "rational_polynomial",
    "perfect_fluid",
    "compressible_liquid",
    "peng_robinson",
    "real_gas_table",
    "sutherland",
    "power_law",
    "log_polynomial",
    "andrade",
    "wlf",
    "kinetic_theory",
    "shear_power_law",
    "carreau",
    "cross",
    "herschel_bulkley",
    "casson",
    "janaf",
    "nasa9_polynomial",
    "power",
    "anisotropic",
    "exponential",
)
"""Every method id of the catalogue, repeated here because this module may not import the
catalogue at start-up; a test checks that the two agree."""

_DIMENSION_CHOICES: tuple[str, ...] = tuple(dict.fromkeys(PROPERTY_DIMENSIONS.values()))


def _material_params() -> tuple[ParamSpec, ...]:
    from stokes.model.schema import ParamSpec

    return (
        ParamSpec(
            "kind",
            "enum",
            label="Kind",
            choices=MATERIAL_KINDS,
            default="fluid",
            doc="fluid or solid; fixed by material.create, which builds the property children "
            "this kind takes.",
        ),
        ParamSpec(
            "source",
            "enum",
            label="Source",
            choices=SOURCES,
            default="user",
            doc="Who owns the property methods: the user, the built-in catalogue or a library fit.",
        ),
        ParamSpec(
            "provider",
            "enum",
            label="Provider",
            choices=PROVIDERS,
            default="none",
            doc="The provider that evaluates the material; none for user materials.",
        ),
        ParamSpec(
            "fluid",
            "string",
            label="Library id",
            default="",
            doc="The provider's id of the material (built-in: water_liquid, aluminium).",
        ),
        ParamSpec(
            "composition",
            "string",
            label="Composition",
            default="",
            doc="Fractions for mixtures and solutions; empty for pure fluids.",
        ),
        ParamSpec(
            "basis",
            "enum",
            label="Basis",
            choices=BASES,
            default="mole",
            doc="Basis of the composition: mole, mass or volume.",
        ),
        ParamSpec(
            "phase",
            "enum",
            label="Phase",
            choices=PHASES,
            default="auto",
            doc="Phase the provider is asked for; auto takes the stable phase at T_ref and p_ref.",
        ),
        ParamSpec(
            "T_ref",
            "quantity",
            label="Reference temperature",
            dimension="temperature",
            default=300.0,
            doc="Boussinesq and power-law reference, the operating temperature Fluent needs.",
        ),
        ParamSpec(
            "p_ref",
            "quantity",
            label="Reference pressure",
            dimension="pressure",
            default=101325.0,
            doc="Absolute pressure a library is evaluated at; the incompressible-ideal-gas pRef "
            "and the Tait p0.",
        ),
        ParamSpec(
            "T_min",
            "quantity",
            label="Minimum temperature",
            dimension="temperature",
            default=273.15,
            doc="Lower end of the range the material is fitted over and checked against.",
        ),
        ParamSpec(
            "T_max",
            "quantity",
            label="Maximum temperature",
            dimension="temperature",
            default=373.15,
            doc="Upper end of the range the material is fitted over and checked against.",
        ),
        ParamSpec(
            "tolerance",
            "number",
            label="Fit tolerance",
            default=0.01,
            doc="Largest relative deviation a library fit may have.",
        ),
        ParamSpec(
            "max_degree",
            "int",
            label="Highest degree",
            default=4,
            doc="Highest polynomial degree a library fit may use (at most 7).",
        ),
        ParamSpec("formula", "string", label="Formula", default=""),
        ParamSpec("cas", "string", label="CAS number", default=""),
        ParamSpec("description", "string", label="Description", default=""),
    )


def _property_params() -> tuple[ParamSpec, ...]:
    from stokes.model.schema import DYNAMIC, ParamSpec

    def quantity(
        name: str, label: str, dimension: str, doc: str, default: float = 0.0
    ) -> ParamSpec:
        return ParamSpec(
            name, "quantity", label=label, dimension=dimension, default=default, doc=doc
        )

    return (
        ParamSpec(
            "kind",
            "enum",
            label="Property",
            choices=PROPERTY_KINDS,
            default="density",
            doc="The property this node describes; set at creation and not changed after.",
        ),
        ParamSpec(
            "dimension",
            "enum",
            label="Dimension",
            choices=_DIMENSION_CHOICES,
            default="density",
            doc="Dimension of value and table_values, fixed by the kind.",
        ),
        ParamSpec(
            "method",
            "enum",
            label="Method",
            choices=METHOD_IDS,
            default="constant",
            doc="How the property depends on temperature and pressure (see material.methods).",
        ),
        ParamSpec(
            "value",
            "quantity",
            label="Value",
            dimension=DYNAMIC,
            doc="The constant; the reference value of the Boussinesq, power-law, perfect-fluid, "
            "Tait, exponential, power and WLF methods.",
        ),
        quantity(
            "reference_temperature",
            "Reference temperature",
            "temperature",
            "T0 of the power-law, exponential, power and WLF methods.",
            300.0,
        ),
        quantity(
            "expansion_coefficient",
            "Expansion coefficient",
            "thermal_expansion",
            "Boussinesq beta.",
        ),
        quantity(
            "sutherland_coefficient",
            "Sutherland coefficient",
            "sutherland_coefficient",
            "A_s of mu = A_s T^1.5 / (T + T_s).",
        ),
        quantity(
            "sutherland_temperature",
            "Sutherland temperature",
            "temperature",
            "T_s of mu = A_s T^1.5 / (T + T_s).",
        ),
        ParamSpec(
            "exponent",
            "number",
            label="Exponent",
            default=0.0,
            doc="n of the power-law, exponential, power and Tait methods.",
        ),
        quantity(
            "collision_diameter",
            "Collision diameter",
            "length",
            "Lennard-Jones sigma of the kinetic-theory viscosity.",
        ),
        quantity(
            "well_depth_temperature",
            "Well depth / k_B",
            "temperature",
            "Lennard-Jones epsilon over the Boltzmann constant.",
        ),
        ParamSpec(
            "degrees_of_freedom",
            "int",
            label="Degrees of freedom",
            default=0,
            doc="f of the kinetic-theory specific heat, c_p = (R/M)(f + 2)/2.",
        ),
        ParamSpec(
            "coefficients",
            "array",
            label="Coefficients",
            doc="SI coefficients of the polynomial, log-polynomial, Andrade, rational, WLF, JANAF, "
            "NASA-9 and piecewise-polynomial forms (temperatures in kelvin).",
            # **Hidden from the fields, like the tag's `members`** (Claude's review item 5): this is
            # a list of numbers with no unit of their own, and the generic renderer prints it as
            # JSON.
            #
            # **Found by the gate rather than by a picture**: `test_no_raw_json_fields` scans every
            # node type for a `json`-shaped parameter that is not hidden, and named this and
            # `table_values` immediately - the same class as the tag page, one node type over.
            #
            # The parameter is untouched: the API still takes it, a material from a library still
            # carries it, and the fix for SHOWING it is a coefficients control, which belongs with
            # the
            # material editor rather than with a page that lists them.
            hidden=True,
        ),
        ParamSpec(
            "degree",
            "int",
            label="Degree",
            default=0,
            doc="Polynomial degree of each piecewise-polynomial segment.",
        ),
        ParamSpec(
            "breakpoints",
            "array",
            label="Breakpoints",
            dimension="temperature",
            doc="Segment bounds of the piecewise-polynomial and NASA-9 forms; JANAF's T_low, "
            "T_common, T_high.",
        ),
        ParamSpec(
            "table_temperatures",
            "array",
            label="Table temperatures",
            dimension="temperature",
            doc="Increasing temperatures of the piecewise-linear table.",
        ),
        ParamSpec(
            "table_values",
            "array",
            label="Table values",
            doc="Values of the piecewise-linear table, SI in the property's dimension.",
            # **Hidden with its partner above**: the temperatures beside it keep their field (they
            # are quantities in a unit), so what a user edits is the table's axis - and the values,
            # which pair with it one for one, are a list the generic renderer would print as JSON.
            # A table control is the fix for SHOWING them, the same as the coefficients.
            hidden=True,
        ),
        quantity(
            "critical_temperature", "Critical temperature", "temperature", "Peng-Robinson T_c."
        ),
        quantity("critical_pressure", "Critical pressure", "pressure", "Peng-Robinson p_c."),
        quantity(
            "critical_volume",
            "Critical volume",
            "specific_volume",
            "Peng-Robinson V_c, per unit mass.",
        ),
        ParamSpec(
            "acentric_factor",
            "number",
            label="Acentric factor",
            default=0.0,
            doc="Peng-Robinson omega.",
        ),
        quantity("bulk_modulus", "Bulk modulus", "pressure", "Tait K0 at the reference pressure."),
        ParamSpec(
            "principal_values",
            "array",
            label="Principal conductivities",
            dimension="thermal_conductivity",
            doc="Anisotropic conductivity along the region's three coordinate axes.",
        ),
        quantity(
            "formation_entropy",
            "Formation entropy",
            "specific_entropy",
            "Standard entropy written with temperature-dependent specific heats (OpenFOAM sf).",
        ),
        quantity(
            "viscosity_infinite",
            "Infinite-shear viscosity",
            "dynamic_viscosity",
            "mu_inf of the Carreau and Cross laws.",
        ),
        quantity("viscosity_min", "Minimum viscosity", "dynamic_viscosity", "Lower clamp."),
        quantity("viscosity_max", "Maximum viscosity", "dynamic_viscosity", "Upper clamp."),
        ParamSpec(
            "consistency",
            "number",
            label="Consistency",
            default=0.0,
            doc="k of the non-Newtonian laws, in Pa s^n.",
        ),
        quantity("time_constant", "Time constant", "time", "lambda of the Carreau and Cross laws."),
        ParamSpec(
            "transition_exponent",
            "number",
            label="Transition exponent",
            default=2.0,
            doc="a of the Carreau law.",
        ),
        quantity(
            "yield_stress", "Yield stress", "pressure", "tau0 of Herschel-Bulkley and Casson."
        ),
        quantity(
            "valid_min",
            "Valid from",
            "temperature",
            "Lowest temperature the method is trusted at; 0 for no bound.",
        ),
        quantity(
            "valid_max",
            "Valid to",
            "temperature",
            "Highest temperature the method is trusted at; 0 when no range is known.",
        ),
    )


def material_specs() -> tuple[NodeSpec, ...]:
    """The two material node types. Built on call: ``stokes.model.schema`` imports this module
    while it registers its types, so the schema is imported here, not at module level."""
    from stokes.model.schema import NodeSpec

    material = NodeSpec(
        "material",
        "Material",
        params=_material_params(),
        children=("property",),
        doc="A fluid or solid with one property child per property kind its kind takes "
        "created and owned by the material.* commands.",
    )
    prop = NodeSpec(
        "property",
        "Property",
        params=_property_params(),
        doc="One property of a material: a method from the catalogue and that method's "
        "parameters as quantities.",
    )
    return (material, prop)


def register_material_types(target: NodeTypeRegistry) -> None:
    """Register the material node types (idempotent, like the other built-in types)."""
    for spec in material_specs():
        target.register(spec, replace=True)
