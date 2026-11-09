"""Study node types (ADR-0016 point 1): ``study``, its ``variables``, ``responses``,
``objectives`` and ``constraints`` groups, and the leaf types ``variable``, ``response``,
``objective`` and ``constraint``.

:func:`stokes.model.schema.register_builtin_types` calls :func:`register_study_types`, so every
project knows these types. A study's design table is not part of the tree: its points live in the
session's ``StudyStore`` (``study_store``) and in ``<name>.stokes/studies/<study_id>/``, so node
summaries stay small JSON. The contract for clients is ``docs/engineering/study-schema.md``.

Quantities follow the ``parameter`` node: a ``variable`` carries the ``dimension`` of the
quantity it varies and its bounds, step, mean and standard deviation are ``DYNAMIC`` quantities
of that dimension; a ``constraint`` carries the dimension of its expression and its ``limit``.

Import rule: ``stokes.model.schema`` imports this module while it registers its node types, so at
module level it imports only ``stokes.units.dimensions`` and the standard library (constants), and
``study_specs`` imports the schema when called. No numpy, nothing else from this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stokes.units.dimensions import DIMENSIONS

if TYPE_CHECKING:
    from stokes.model.schema import NodeSpec, NodeTypeRegistry, ParamSpec

STUDIES_PATH = "/studies"
STUDY_GROUPS: tuple[str, ...] = ("variables", "responses", "objectives", "constraints")
"""The groups ``study.create`` gives every study, in this order."""
STUDY_TYPES: tuple[str, ...] = ("sweep", "doe")
SAMPLERS: tuple[str, ...] = (
    "full_factorial",
    "latin_hypercube",
    "sobol",
    "halton",
    "central_composite",
    "box_behnken",
    "optimal_space_filling",
    "monte_carlo",
)
"""``stokes.explore.sampling.sampler_names()``, repeated here because this module may not import
numpy; a test checks that the two agree."""
SIZED_SAMPLERS: frozenset[str] = frozenset(
    {"latin_hypercube", "sobol", "halton", "optimal_space_filling", "monte_carlo"}
)
"""Samplers that take the number of points (the study's ``count``); the others have their own."""
STUDY_STATUSES: tuple[str, ...] = ("idle", "generated", "running", "done", "failed")
DOMAINS: tuple[str, ...] = ("values", "range", "uniform", "normal", "categorical")
DIRECTIONS: tuple[str, ...] = ("minimise", "maximise")
OPERATORS: tuple[str, ...] = ("<=", ">=", "==")

_DIMENSION_NAMES = tuple(DIMENSIONS)


def study_specs() -> tuple[NodeSpec, ...]:
    """The five study node types. Built on call: ``stokes.model.schema`` imports this module
    while it registers its types, so the schema is imported here, not at module level (either
    module may then be imported first)."""
    from stokes.model.schema import DYNAMIC, NodeSpec, ParamSpec

    def _dimension(doc: str) -> ParamSpec:
        return ParamSpec(
            "dimension",
            "enum",
            label="Dimension",
            choices=_DIMENSION_NAMES,
            default="dimensionless",
            doc=doc,
        )

    study = NodeSpec(
        "study",
        "Study",
        params=(
            ParamSpec(
                "type",
                "enum",
                label="Type",
                choices=STUDY_TYPES,
                default="sweep",
                doc="sweep: every combination of the variables' levels, or points added by hand; "
                "doe: a design of experiments placed by the sampler.",
            ),
            ParamSpec(
                "sampler",
                "enum",
                label="Sampler",
                choices=SAMPLERS,
                default="full_factorial",
                doc="How a doe study places its points; a sweep always takes the full factorial.",
            ),
            ParamSpec(
                "seed",
                "int",
                label="Seed",
                default=0,
                doc="Seed of the randomised samplers: the same seed gives the same design.",
            ),
            ParamSpec(
                "count",
                "int",
                label="Points",
                default=0,
                doc="Number of points for the samplers that take one (Latin hypercube, Sobol, "
                "Halton, optimal space filling, Monte Carlo).",
            ),
            ParamSpec(
                "status",
                "enum",
                label="Status",
                choices=STUDY_STATUSES,
                default="idle",
                doc="State of the design table, kept by the study.* commands: idle (no points), "
                "generated (points to evaluate), running, done (every point evaluated), failed "
                "(a point failed).",
            ),
            ParamSpec("description", "string", label="Description", default=""),
        ),
        children=("group",),
        doc="A parametric study: variables, responses, objectives and constraints; its "
        "design table is kept beside the project, not in the tree.",
    )

    variable = NodeSpec(
        "variable",
        "Variable",
        params=(
            ParamSpec(
                "parameter",
                "string",
                label="Parameter",
                default="",
                doc="The varied quantity: a parameter name (U_in), a parameter path "
                "(/physics/parameters/U_in) or a node quantity (regions.fluid.inlet.velocity).",
            ),
            _dimension("Dimension of the varied quantity; study.add_variable copies it."),
            ParamSpec(
                "domain",
                "enum",
                label="Domain",
                choices=DOMAINS,
                default="values",
                doc="values: the listed levels; range: min to max, with a count or a step for a "
                "sweep; uniform or normal: a distribution for sampling; categorical: unordered "
                "levels with labels.",
            ),
            ParamSpec(
                "values",
                "array",
                label="Values",
                doc="Levels in SI units of the dimension (values and categorical domains).",
                # **Hidden from the fields** (Claude's review item 5, found by
                # `test_no_raw_json_fields`): the generic renderer prints this list as JSON, so a
                # variable sweeping five levels showed `[0.5, 0.75, 1.0, 1.25, 1.5]` as a blob. The
                # Design table is where levels are read and edited, and it renders them as a column
                # -
                # which is the control this field was standing in for.
                hidden=True,
            ),
            ParamSpec(
                "labels",
                "string",
                label="Labels",
                default="",
                doc="Category labels separated by commas, one per value (categorical; optional).",
            ),
            ParamSpec(
                "min",
                "quantity",
                label="Minimum",
                dimension=DYNAMIC,
                doc="Lower bound (range, uniform; the truncation of a normal).",
            ),
            ParamSpec(
                "max",
                "quantity",
                label="Maximum",
                dimension=DYNAMIC,
                doc="Upper bound (range, uniform; the truncation of a normal).",
            ),
            ParamSpec(
                "count",
                "int",
                label="Levels",
                default=0,
                doc="Levels of a range, or of a distribution in a full factorial; 0 for none.",
            ),
            ParamSpec(
                "step",
                "quantity",
                label="Step",
                dimension=DYNAMIC,
                doc="Spacing of a range's levels; 0 for none. Give a range a count or a step.",
            ),
            ParamSpec(
                "mean",
                "quantity",
                label="Mean",
                dimension=DYNAMIC,
                doc="Mean of a normal distribution.",
            ),
            ParamSpec(
                "std",
                "quantity",
                label="Standard deviation",
                dimension=DYNAMIC,
                doc="Standard deviation of a normal distribution.",
            ),
        ),
        doc="A study input: the quantity it varies and the values it may take.",
    )

    response = NodeSpec(
        "response",
        "Response",
        params=(
            ParamSpec(
                "expression",
                "string",
                label="Expression",
                default="",
                doc="What each design point records: an expression over parameters and node "
                "quantities (reported once a solve has run), e.g. dp_dyn or 0.5 * rho * U_in**2.",
            ),
            _dimension("Dimension of the result; study.add_response infers it."),
            ParamSpec(
                "label",
                "string",
                label="Label",
                default="",
                doc="Display name; the node name when empty.",
            ),
        ),
        doc="A scalar study output, one value per design point.",
    )

    objective = NodeSpec(
        "objective",
        "Objective",
        params=(
            ParamSpec(
                "response",
                "ref",
                label="Response",
                ref_type="response",
                doc="The response to optimise: a response of the same study.",
            ),
            ParamSpec(
                "direction",
                "enum",
                label="Direction",
                choices=DIRECTIONS,
                default="minimise",
            ),
        ),
        doc="Minimise or maximise one response (Pareto fronts, optimisation).",
    )

    constraint = NodeSpec(
        "constraint",
        "Constraint",
        params=(
            ParamSpec(
                "expression",
                "string",
                label="Expression",
                default="",
                doc="An expression over the study's responses (by name) and the parameters.",
            ),
            _dimension(
                "Dimension of the expression and the limit; study.add_constraint infers it."
            ),
            ParamSpec(
                "operator",
                "enum",
                label="Operator",
                choices=OPERATORS,
                default="<=",
            ),
            ParamSpec(
                "limit",
                "quantity",
                label="Limit",
                dimension=DYNAMIC,
                doc="The bound the expression is compared with.",
            ),
        ),
        doc="A condition a design point must satisfy to be feasible.",
    )

    return (study, variable, response, objective, constraint)


def register_study_types(target: NodeTypeRegistry) -> None:
    """Register the study node types (idempotent, like the other built-in types)."""
    for spec in study_specs():
        target.register(spec, replace=True)
