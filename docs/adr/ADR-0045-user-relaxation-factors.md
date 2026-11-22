# ADR-0045 · Under-relaxation factors the user can set

**Status:** accepted · 2026-11-21 · (ADR-0011 solver settings, ADR-0027 energy, ADR-0044 coupled engine)

## Context

Every steady case Stokes writes carries fixed under-relaxation factors: SIMPLEC's `U 0.9` and
`".*" 0.9` (pressure unrelaxed) for `of14.incompressibleFluid`, `p_rgh 0.3` and `0.7` for the
equations in `of14.fluid` (0.2 and 0.5 on a non-orthogonal mesh). A case that diverges, or one
that converges too slowly, has no way to change them short of editing the written case, which the
case-is-an-output rule (constitution rule 9) makes a recorded override at best. The coupled engine
showed the cost of a fixed value from the other side: the SIMPLE case's `".*" 0.9` caught its
temperature equation and made a heated duct take 267 outer iterations instead of 16 (ADR-0044
decision 9).

## Decision

1. **Four optional settings on `/solver/settings`**: `relax_pressure`, `relax_velocity`,
   `relax_turbulence` and `relax_energy`, each a number greater than 0 and at most 1, checked on
   every write (`solve.invalid`). Each may be left unset, which means the engine's own value.
2. **Unset is a model state, not a magic number.** `ParamSpec` gains `optional` (numbers only,
   no default): null is stored and clears a set value, and the JSON schema says
   `["number", "null"]` with `x-optional`. Existing number parameters are unchanged.
3. **The writers use a set factor where their engine relaxes that equation**, and their own value
   otherwise:
   - `of14.incompressibleFluid`: `relax_pressure` adds `fields { p ...; }` (SIMPLEC normally has
     none), `relax_velocity` sets `U`, `relax_turbulence` sets `".*"`;
   - `of14.fluid`: the pressure field, `U`, the energy variable (`h` or `e`) and `".*"`;
   - `coupled.simple`: `relax_energy` sets `T` (1 when unset) and `relax_turbulence` the segregated
     turbulence equations; it relaxes neither pressure nor velocity.
   A transient run writes no relaxation, as before.
4. **With every factor unset the case is byte for byte the one written before** (the golden files
   hold this), and unset factors are out of the run basis, so no earlier run is reported stale.
   Setting one moves the basis.
5. **The settings page shows a factor only where the run reads it**: a "Relaxation" section,
   collapsed by default; none in a transient run; energy with the energy equation on; turbulence
   with a turbulence model; pressure and velocity hidden for the coupled engine. An empty field
   shows "Automatic" and clears the factor.

## Alternatives considered

| Alternative | Why not |
|---|---|
| A relaxation dictionary as a recorded override | Overrides are for what Stokes cannot express; relaxation is an ordinary solver setting every CFD tool exposes. |
| Plain numbers with defaults of 0.9 | One default cannot be right for SIMPLEC (pressure unrelaxed), `of14.fluid` (0.3/0.7) and the coupled engine (temperature 1); an unset state lets each engine keep its own. |
| 0 meaning "automatic" | A zero in a field reads as a value; null with an "Automatic" placeholder says what happens. |
| One factor for every equation | Pressure and velocity are tuned separately in practice, and the coupled engine reads only the energy and turbulence factors. |

## Consequences

- A user or an API client can stabilise a diverging steady case, or speed one up, without
  touching the written case.
- `ParamSpec.optional` is available for any other "leave it to the engine" number.
- The Guided flow does not show the factors; they are an Advanced setting.
