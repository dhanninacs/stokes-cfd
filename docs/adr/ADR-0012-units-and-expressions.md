# ADR-0012 · Units and expressions

**Status:** accepted · 2026-10-02 (decision F3)

## Context

Fluent and STAR-CCM+ users expect unit-aware fields and expressions; fingen's
convergence and KPI logic showed how often a value is derived from others
(mass flow from velocity and area, pumping power from flow and pressure
drop). You chose full unit systems with expressions over SI-only or
display-only units.

## Decision

1. **Every numeric parameter is a quantity** with a dimension declared in the
   node schema. Storage is SI; the project carries a display unit system
   (SI, mm-based, imperial, or custom per quantity).
2. **Any numeric field accepts** a plain number in the display unit, a value
   with a unit (`25 mm`, `40 degC`, `2 L/min`, `1.2 bar`), or an expression.
3. **Expressions** use our own small grammar (parsed with `lark`): arithmetic,
   unit literals, functions (`sqrt`, `min`, `max`, `abs`, trig, `log`),
   references to named parameters (`U_in`, `T_in`) and to node quantities by
   path (`regions.fluid.boundaries.inlet.velocity`), and selection
   properties (`area(inlet)`). Evaluation is unit-checked; a dimension
   mismatch is an inline error, never a silent conversion.
4. **Parameters** are first-class nodes under Physics → Parameters, so sweeps
   and agents change one named value and everything derived follows.
5. **Storage** keeps the expression text and the evaluated SI value; the
   compiler writes the value, and the compiled-case preview shows the
   expression as a comment.
6. **Implementation**: `pint` (BSD-3) for quantities and unit registries; our
   grammar on top; no `eval`.

## Alternatives considered

| Alternative | Why not |
|---|---|
| SI only | Users convert by hand; the first support ticket is a unit mistake. |
| Display units without expressions | Half the benefit; expressions are what make parameters and sweeps coherent. |
| Python `eval` for expressions | Unsafe with agent-written input and untyped for units. |

## Consequences

- Every input widget, the API schema and the CLI share one quantity codec.
- Unit tests cover conversions, dimension checks and expression edge cases
  from day one (M0).
- OpenFOAM dictionaries receive SI values; Fluent receives SI through
  PyFluent, with Fluent's own unit settings left at SI.
