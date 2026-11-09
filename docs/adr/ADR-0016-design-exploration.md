# ADR-0016 · Parametric studies and design exploration: sweeps, DOE, surrogates, sensitivity, optimisation with Pareto fronts

**Status:** accepted · 2026-10-02 · relies on ADR-0006 (run bundles), ADR-0010 (API first), ADR-0011 (engines), ADR-0012 (parameters and expressions), ADR-0015 (workspace)

## Context

The owner asked for a parametric case runner: run one geometry or mesh and
case set-up at many boundary conditions, properties and other inputs chosen
by the user, and then for the design-exploration layer the incumbents offer
on top of it: Pareto fronts, design of experiments and the related tools,
built in Python.

What the incumbents do, from their documentation and user reports:

- **Ansys Workbench / DesignXplorer**: a *Parameter Set* with input and
  output parameters and a *Table of Design Points* (one row per point;
  update selected or all; at least one output parameter required; points
  that share geometry or mesh can be updated in an order that avoids
  recomputing them). DesignXplorer adds DOE types (Latin hypercube, central
  composite, Box-Behnken, optimal space filling, sparse grid), response
  surfaces (polynomial, kriging, neural), sensitivity and correlation,
  single- and multi-objective optimisation (MOGA) with trade-off plots,
  parallel-coordinate charts, Pareto-front display and *candidate points*
  that are verified by solving.
- **Simcenter STAR-CCM+ Design Manager**: study types *sweep*, *DOE* and
  *optimisation* (SHERPA / MO-SHERPA), a design table with input parameters
  and responses, per-design result files, interactive tables and plots,
  Pareto plots between two objectives, scenes synchronised across designs,
  and the option to initialise a design from an earlier solution.
- **Autodesk CFD**: a *Design Study* of *Scenarios* that share geometry; any
  report, plot or image marked as a *Summary item* is captured identically
  in every scenario; the *Decision Center* compares scenarios visually
  (synchronised views), tabularly (critical values) and graphically
  (overlaid XY plots).
- **SimScale**: one parametric definition per set-up (for example several
  inlet flow rates), runs in parallel, results of all runs compiled into one
  plot; a single varied quantity only.
- **COMSOL**: parametric sweeps over several parameters with probes and
  tables accumulating derived values; the option to keep only the last
  solution in memory.
- **OpenFOAM practice**: PyFoam's parameter variation takes a template case
  and the Cartesian product of parameter vectors, with *derived parameters*
  (a time step that follows the mesh spacing) computed in Python, and
  collects results into a database.

Stokes already has what these build on: named parameters with unit-checked
expressions and a dependency graph (ADR-0012), so derived parameters come
for free; immutable run bundles (ADR-0006); engines as separate processes
(ADR-0011); and an API that agents drive (ADR-0010).

## Decision

1. **Studies are nodes.** A new skeleton group `/studies` holds `study`
   nodes with the children `variables` (a parameter or node-parameter path
   plus a *domain*: an explicit value list, a range with a count or a step,
   or a distribution with bounds for sampling), `responses` (scalar outputs:
   reports, monitors, probes, or an expression over them), `objectives` and
   `constraints` (minimise or maximise a response; an inequality over
   responses), and the `design table`: one row per design point with its
   inputs, outputs, status, run reference and a *retain data* flag. Study
   settings carry the type, the sampler and its seed, and the execution
   policy.
2. **Study types** are `sweep` (a hand-edited table or the full factorial
   of listed values), `doe` (sampling: full factorial, Latin hypercube,
   Sobol, Halton, central composite, Box-Behnken, optimal space filling),
   `response_surface` (surrogates fitted to the table: polynomial, radial
   basis functions, Gaussian process, with cross-validation metrics and
   predicted-versus-observed plots; used to predict, to seed optimisation
   and to propose *candidate points* that are verified by solving),
   `sensitivity` (Spearman correlation, Sobol indices, Morris screening on
   the table or on a surrogate), `optimisation` (single objective by
   differential evolution, Nelder-Mead or COBYLA; multi-objective by NSGA-II
   or NSGA-III with constraints, delivering the Pareto front and the
   hypervolume history) and `robustness` (Monte Carlo over the variables'
   distributions with statistics of the responses).
3. **A design point is the base case with parameter overrides applied**,
   compiled, run and reduced to its responses by the run queue. Varied
   parameters are classified through the dependency graph: a point whose
   varied inputs touch only physics, regions or solver settings reuses the
   base mesh; one that touches geometry or mesh re-meshes. Points may
   initialise from the nearest finished point. Execution is sequential or
   parallel-N locally, remote through the paid runners; *retain data* keeps a
   point's run bundle, otherwise only its responses, under a per-study cap.
4. **Summary items** (Autodesk's idea): any report, plot or scene marked as
   a summary item is captured identically for every point, so comparisons
   are consistent; the design table, the plots and the viewport stay
   synchronised, and two points can be compared side by side (this absorbs
   M4's "run comparison").
5. **The Explore stage** is added to the stage band (the workspace can hide
   it, ADR-0015). Its panels: the *Design Table* (editable cells, add and
   duplicate rows, update selected or all, progress and status per row,
   sort and filter, CSV export), *Plots* (response against input, scatter
   matrix, parallel coordinates, Pareto plot with dominated points greyed,
   sensitivity bars, correlation matrix, surrogate curves and surfaces with
   validation points, optimisation convergence), and the *Study* properties
   (variables, responses, objectives, sampler, execution).
6. **API**: `study.create`, `study.add_variable`, `study.add_response`,
   `study.set_objective`, `study.add_constraint`, `study.generate` (fills
   the table from the sampler, seed stored), `study.add_point`,
   `study.update(points)` (queues runs), `study.cancel`, `study.results`
   (table as JSON or CSV), `study.fit_surrogate`, `study.predict`,
   `study.sensitivity`, `study.optimise` (iterates, emitting
   `study.generation` events), `study.pareto` (the non-dominated set),
   `study.export`. The CLI mirrors them under `stokes study`. Because the
   API is the surface, an agent can set up and run a DOE on request.
7. **Python packages**: `stokes.explore` holds samplers, surrogates,
   sensitivity, optimisation drivers and Pareto utilities. Numerics come from
   numpy and scipy (`scipy.stats.qmc` for Latin hypercube, Sobol and Halton;
   `scipy.optimize`; RBF interpolation), with an optional `explore` extra for
   pymoo (Apache-2.0, NSGA-II/III), SALib (MIT, Sobol and Morris) and
   scikit-learn (BSD-3, Gaussian processes). All are permissive; GPL
   optimisers (Platypus) are excluded; Dakota or OpenMDAO may later be
   attached as external processes under ADR-0001 if a user needs them.
   Imports are lazy and live only in `stokes.explore`.
8. **A `function` engine evaluates responses from expressions alone** (no
   solver), so the study model, the table, the samplers, the plots and the
   algorithms can be built and tested now, before real runs exist, and so
   analytical models can be explored the same way later.
9. **Storage and history**: study definitions are undoable commands; runs
   are jobs, not undoable, but points can be cleared and re-run. Tables are
   stored as `.stokes/studies/<study_id>/points.jsonl` (plus a CSV mirror),
   run bundles under `.stokes/runs/` per ADR-0006.
10. **Free or paid** is left open (decisions log A4). Until decided, the
    whole of Explore runs in the free tier behind the `explore` feature flag
    switched on; remote execution remains paid as before.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Script it with PyFoam-style parameter variation outside the app | No table, no plots, no comparison, no agent surface; and PyFoam is GPL, so it could only run as a process anyway. |
| Only a single varied quantity (SimScale's model) | Users asked for several inputs at once; DOE and optimisation need it. |
| Wait for real runs (M3) before building any of it | The `function` engine lets the model, table, samplers, plots and algorithms be built and tested now; only the run-queue wiring waits. |
| A proprietary optimiser like SHERPA | Not available; NSGA-II/III, differential evolution and surrogate-assisted search cover the published use cases. |

## Consequences

- New skeleton group `/studies`, node types `study`, `variable`,
  `response`, `objective`, `constraint`; `stokes.explore` package; the
  `function` engine; `study.*` commands; the Explore stage with the Design
  Table and Plots panels; CSV and JSON export.
- Roadmap: *Explore v1* (model, table, sweep and DOE generation, function
  engine, plots, export) is built after the workspace and STEP front-end
  work and before M2; *Explore v2* (surrogates, sensitivity, optimisation
  with Pareto fronts, mesh reuse and initialisation from neighbours, remote
  execution) follows M3 as its own milestone, replacing the "parametric
  sweeps (flag)" entry of M10.
- Dependencies: scipy joins the core requirements; pymoo, SALib and
  scikit-learn form the optional `explore` extra after a licence audit entry
  each.
- Tests: samplers (determinism by seed, coverage properties), surrogate
  fitting on known functions, NSGA-II on the ZDT benchmarks recovering the
  known fronts, Pareto utilities, the function engine end to end through
  the API and the CLI.
