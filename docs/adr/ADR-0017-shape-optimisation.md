# ADR-0017 · Shape optimisation as a premium feature: CAD-parametric first, adjoint through adjoint-capable engines

**Status:** accepted · 2026-10-02 · relies on ADR-0001 (GPL process boundary), ADR-0003 (CAD kernel), ADR-0008 (OpenFOAM dialect), ADR-0011 (engines), ADR-0016 (design exploration)

## Context

The owner asked how mature adjoint and parametric shape optimisation are in
OpenFOAM, whether Stokes could offer shape optimisation through its CAD
backend instead, and how the other applications do it, with a view to a
premium feature.

**OpenFOAM, ESI line (openfoam.com).** `adjointOptimisationFoam`, written
by NTUA's PCOpt group, has shipped since v1906 and is the mature route: a
continuous adjoint for steady incompressible turbulent flow with adjoint
Spalart–Allmaras and, since v2206, adjoint k-ω SST (differentiated rather
than frozen turbulence, which the authors show converges to better designs);
objectives from the usual engineering quantities (losses, forces, flow
uniformity); shape parameterisation by volumetric B-splines whose control
points are the design variables, with mesh morphing; surface sensitivity
maps; steepest descent, BFGS and SQP with constraints; and, since v2312,
topology optimisation by porosity or level set with STL export of the
designed geometry. It has a user manual and workshop courses, and v2412 and
v2506 continue to refine it.

**OpenFOAM, Foundation line (openfoam.org).** Only
`adjointShapeOptimizationFoam`, Othmer's porosity-based duct "blockage"
topology optimiser with frozen turbulence, exists; it is a legacy
application, not a shape optimiser, and its presence in OpenFOAM 14 must be
verified when Stokes attaches to it (ADR-0011). Stokes' first dialect is
Foundation 14 (ADR-0008), so OpenFOAM adjoint shape optimisation in Stokes
depends on the ESI dialect.

**Commercial tools.** Fluent's Adjoint Solver (discrete adjoint) works from
*observables* (reports), computes surface sensitivities, deforms the mesh in
user-drawn design regions with polynomial or radial-basis morphing under
*design conditions* (objective, constraints, fixed walls, symmetry), previews
the displacement and loops through a gradient-based optimiser; it is
reachable through PyFluent, Stokes' paid Fluent route. STAR-CCM+ pairs its
adjoint flow solver with a morpher driven by control points, cost functions
from reports, sensitivity filtering, SQP, "morph then re-mesh" for large
steps and a faster recycled-Krylov adjoint solver; HEEDS adds CAD-parametric
optimisation using CAD sensitivities. Dassault's TOSCA Fluid does topology
and shape optimisation on top of other solvers.

**What Stokes already has.** Parameters with expressions and a dependency
graph (ADR-0012), the design-exploration layer with optimisers and Pareto
fronts (ADR-0016), the CAD kernel (ADR-0003) and engines as processes
(ADR-0011).

## Decision

1. **Two routes, one user-facing concept.** A *Shape study* is a study type
   of ADR-0016 whose variables move geometry. Route A, **CAD-parametric**,
   needs no adjoint and works with every engine; Route B, **adjoint**, needs
   an adjoint-capable engine and gives gradients and sensitivity maps. Both
   live in the Explore stage and share objectives, constraints, the design
   table, history plots and export.
2. **Route A — CAD-parametric shape optimisation** (Explore v2, M3.5):
   geometry-driving parameters (primitive dimensions, placements, extrusion
   lengths, the parametric-part generator ported from finmesh, and later
   feature parameters of imported models) are study variables; each design
   point re-tessellates, re-meshes and runs. Optimisers come from ADR-0016:
   differential evolution, surrogate-assisted search and NSGA-II/III for
   trade-offs, with finite-difference gradients evaluated in parallel for
   the gradient methods. For small movements an RBF mesh morpher (scipy) in
   `stokes.explore.morph` deforms the existing mesh instead of re-meshing,
   with a quality check that falls back to re-meshing.
3. **Route B — adjoint shape and topology optimisation** through engines
   that have an adjoint:
   - *OpenFOAM ESI*: Stokes compiles `optimisationDict` and the adjoint
     turbulence set-up for `adjointOptimisationFoam`: objectives from
     reports; design regions as volumetric B-spline boxes the user places
     around chosen patches in the viewport (control-point counts, fixed and
     floating boundaries, symmetry); constraints (volume, bounds); the
     optimiser and step. Stokes runs the primal–adjoint cycles, reads the
     surface sensitivity map after each cycle and colours it on the geometry,
     shows the morphed shape per cycle with the objective history, and
     exports the final surface as STL or the deformed mesh. Topology
     optimisation (porosity or level set) is exposed the same way with STL
     export of the designed passage.
   - *Fluent*: through PyFluent, mapping observables, design regions, design
     conditions and the gradient-based optimiser onto the same Shape study,
     where PyFluent exposes them (verified in M7).
   - *Foundation*: `adjointShapeOptimizationFoam`, when present in the
     attached installation, as an experimental duct-topology option only.
4. **Sensitivity maps stand alone.** One primal–adjoint solve with the map
   painted on the surface ("where to change the shape, and which way") is a
   feature in itself and the first thing Route B ships.
5. **Dialect order.** The core workflow stays Foundation-first (ADR-0008);
   the ESI dialect moves up from "v1.5, M10" to the premium-optimisation
   milestone, because Route B depends on it. Its scope there is the subset
   `adjointOptimisationFoam` needs (incompressible steady, SA and k-ω SST),
   not a second full dialect.
6. **Premium.** Shape studies (both routes), sensitivity maps and topology
   optimisation sit behind the `optimise` feature flag, paid. The underlying
   DOE and sweeps remain governed by ADR-0016 and decisions log A4.
7. **Validation.** Route A on the ADR-0016 benchmark (a duct trading
   pressure drop against heat transfer); Route B on the published
   `adjointOptimisationFoam` tutorials (the S-bend and the duct-with-losses
   cases) reproducing their objective reductions within a few per cent, and
   on at least one Fluent adjoint tutorial.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Write our own adjoint solver | Years of work; ESI's is mature and GPL-compatible as a process (ADR-0001). |
| Adjoint first, parametric later | Adjoint needs the ESI dialect and an incompressible steady case; CAD-parametric works with every engine and every physics from Explore v2 on, and is what most users can run first. |
| Fluent only for adjoint | Fluent is a paid add-on route for Fluent licence holders; OpenFOAM users need the ESI route. |
| Topology optimisation as the lead feature | Valuable for ducts and manifolds, but less requested than shape; it ships alongside Route B since the same solver provides it. |

## Consequences

- `stokes.explore` gains `shape` (the Shape study type, design regions,
  B-spline boxes), `morph` (RBF mesh morphing with quality checks) and
  adjoint drivers per engine; the ESI dialect compiler gains the adjoint
  subset; the viewport gains sensitivity-map colouring and B-spline box
  gizmos; exports gain STL of optimised surfaces.
- Roadmap: Route A joins M3.5; a new milestone *M8.5 Premium optimisation*
  holds the ESI adjoint subset, sensitivity maps, Route B for OpenFOAM and
  Fluent, topology optimisation and the morph-preview UI.
- Verify early (M2): whether OpenFOAM 14 still ships
  `adjointShapeOptimizationFoam`; and (M7) which adjoint settings PyFluent
  exposes.
- Marketing: sensitivity maps and the Pareto front of a CAD-parametric
  study are the premium demonstrations.
