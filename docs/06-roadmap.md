# 06 · Roadmap

*Revised 2026-10-02. Effort is in engineer-weeks (one person, full time, with
an AI pair). You build this evenings and weekends (K1); at about 15 hours a
week, multiply engineer-weeks by roughly 2.7 for calendar time. Milestones are
ordered so the internal demo (K2) comes as early as possible; everything the
demo does not need is after it.*

| M | Name | Eng-weeks | Scope | Gate |
|---|---|---|---|---|
| M0 | Foundations · **steps 1–7 done 2026-10-02 (the web front end, design system and three.js viewport of ADR-0013 replace the QML shell and the VTK viewport; the polish pass is in its review loop); step 8 (STEP import, ADR-0014) done end to end (face selection and many-parts performance in M1, G6); step 9 (workspace v1, ADR-0015) done end to end apart from the shortcut editor; step 10 (Explore v1, ADR-0016) done end to end. M0 gate: undo, expressions, CLI and licence audit pass; orbit at 60 fps fails for a 4 500-solid, 20 k-face assembly (about 4 fps, decisions log G6), so many-part rendering moves to the front of M1** | 5 | Repository, CI with the licence audit and the employer-identifier grep, `stokes.model` with commands and undo, `stokes.units` with unit systems and the expression engine, `stokes.api` with the command registry and generated schemas, `stokes` CLI skeleton, design system and QML shell (OS theme, frameless window, rail, top bar, tree, inspector), VTK viewport with orientation cube, LOD and reduced-quality mode, STEP import in workers, Python console. | Open a 20 k-face STEP; orbit at 60 fps; undo any edit; `25 mm` and `U_in*2` accepted in a field; the same edit works from the CLI; licence audit green. |
| M1 | Geometry and workspace polish | 3.5 | Id-buffer picking, box and lasso, feature-angle growth (C++), named selections, primitives and booleans, enclosure, fluid extraction, extend, defeature, split, checks, STL export per selection; workspace: shortcut editor with STAR-CCM+ and Fluent key presets, preset export and import, floating panels. | A public manifold STEP to fluid domain plus inlet, outlet and wall selections in under 5 minutes. |
| M2 | Agent, attachment, meshing | 5 | Agent and protocol; local-WSL and local-Linux runners; SSH runner for the remote test workstation testing; install probing; mesh model; snappyHexMesh and blockMesh drivers; cfMesh and Gmsh drivers (cfMesh's Foundation-14 compatibility verified here); checkMesh quality tree; boundary-surface extraction; live cell estimate. | A 5 M-cell snappy mesh from the UI on the remote test workstation over SSH and on a local WSL distro; surfaces visible in under 10 s. |
| M3 | Physics, compile, run, API servers | 5 | Physics nodes, materials (ADR-0018 slices 1–4: catalogue, CoolProp, Cantera, REFPROP via CoolProp, OF14 mapping, Materials panel), regions and boundaries with tinting, BC library, OF14 compiler, compiled-case preview, overrides, `foamRun` engine, parallel runs, residual and report streaming, stopping criteria, HTTP/JSON-RPC server with OpenAPI and headless mode, MCP server (flag), journals. | STEP to a converged steady run with reports, no dictionary opened, once from the UI and once driven entirely through MCP by Claude Code. **Internal demo.** |
| M3.5 | Design exploration v2 (ADR-0016, ADR-0017 route A) | 3.5 | Studies run through the real run queue with mesh reuse and initialisation from neighbours; response surfaces (polynomial, RBF, Gaussian process) with validation; sensitivity (Spearman, Sobol, Morris); single-objective optimisation; NSGA-II/III multi-objective optimisation with Pareto plots, hypervolume history and verified candidate points; robustness by Monte Carlo; summary items captured per point; side-by-side point comparison; remote execution through the paid runners; CAD-parametric shape studies with RBF mesh morphing and re-mesh fallback (premium). | A two-objective study (pressure drop against heat transfer) recovers a Pareto front on a benchmark case; a DOE of 30 points runs unattended with mesh reuse; a parametric duct shape improves its objective over 20 evaluations. |
| M4 | Post-processing | 3 | Scenes and displayers, slices, iso-surfaces, streamlines, colour maps, derived parts, probes, report tables, exports, HTML report (flag), ParaView hand-off (run comparison moved into M3.5). | The demo run's report generated in one click. |
| M5 | Coupled engines and CHT | 3 | Build helper (clone, prerequisites, Allwmake, smoke test), coupledSimpleFoam engine with presets and parsers, multi-region (`splitMeshRegions`, interfaces, `foamMultiRun`), coupled CHT when gated, GPU flag. | heatedDuct reproduces Nu = 7.54 through Stokes; the multi-region heater benchmark runs on both engines. |
| M6 | Assistant | 2 | Embedded assistant panel (flag), provider abstraction (Anthropic, OpenAI, Gemini, local OpenAI-compatible), keys in the keychain, tool calls as undoable actions, journal export. | The assistant completes the tutorial from a one-line instruction. |
| M7 | Fluent engine | 3 | PyFluent meshing and solver sessions, the `fluent` dialect, residual streaming, results via field data or EnSight export, seat semaphore (flag). | The cavity and heated-plates benchmarks run on Fluent from the same tree with matching numbers. |
| M8 | Packaging and licensing | 3 | Licence files and flags, Nuitka builds, signed installers for Windows and Linux, updater, third-party notices, documentation site, trade-mark wording. | Clean machine: install, attach to an existing OpenFOAM, run the tutorial in 15 minutes. |
| M8.5 | Premium optimisation (ADR-0017 route B) | 4 | The ESI dialect subset for `adjointOptimisationFoam` (incompressible steady, SA and k-ω SST adjoints); sensitivity maps painted on the geometry after one primal–adjoint solve; adjoint shape optimisation with volumetric B-spline design boxes placed in the viewport, constraints, per-cycle morphed shapes and objective history, STL and mesh export; topology optimisation (porosity, level set) with STL export; the Fluent adjoint through PyFluent where exposed; `adjointShapeOptimizationFoam` as an experimental duct option when the Foundation install has it. | The S-bend and duct tutorials of `adjointOptimisationFoam` reproduce their published objective reductions within a few per cent from the Stokes UI; a Fluent adjoint tutorial runs from the same study. |
| M9 | Beta hardening | 3 | Validation matrix (classic benchmarks, coupled tutorials, NASA TMR, ERCOFTAC), three tutorials, crash and support bundles, performance budgets in CI, usability fixes from three pilot users. | Pilots complete the tutorials unaided; budgets green. |
| M10 | Expansion | ongoing | The full ESI dialect (its adjoint subset arrives in M8.5), VOF with the Lee model, radiation, species, scheduler and container runners (flag), report templates, existing-case import, a parametric-part generator ported from finmesh (also feeds route A shape studies), external optimisers (Dakota, OpenMDAO) as processes. | Per feature. |

Totals: the internal demo after M3 is about 17 engineer-weeks, roughly 10 to
11 months of evenings and weekends; the public beta after M9 is about 31
engineer-weeks, roughly 20 months. If that is too slow, the levers are, in
order: defer cfMesh and Gmsh from M2 (saves about 1.5 weeks), defer Fluent to
after the beta (3 weeks), defer the assistant panel while keeping MCP (2
weeks).

## Order of work inside M0 (first weeks)

1. Repository skeleton, `pyproject`, pre-commit, CI with `pip-licenses` and
   the employer-identifier grep gate.
2. `stokes.units`: quantities, unit systems, expression grammar, tests.
3. `stokes.model`: node schema, commands, undo, JSON persistence, tests.
4. `stokes.api` and the CLI: command registry, generated JSON schemas, one
   end-to-end command (`project.new`, `parameter.set`).
5. Shell prototype: Qt Widgets window hosting QML panels (`QQuickWidget`)
   next to a native VTK widget, tested on Windows and Linux for focus, HiDPI
   and compositing before anything is built on it.
6. Design system: tokens, OS-theme switching, icon pipeline, tree and
   inspector generated from schemas.
7. VTK viewport: orientation cube, LOD swap, reduced-quality mode.
8. STEP import through OCP in a worker with progressive display
   (ADR-0014): `part` nodes, transport version 2 with face ids, mesh cache,
   `geometry.import_step`, Import STEP… in the UI.
9. Workspace v1 (ADR-0015): `stokes.workspace` with schema, presets and
   merge, `workspace.*` commands, the panel registry and slot layout engine
   in the front end, Customise mode for moving panels and islands, the theme
   editor with custom token sets, the Stokes/Classic/Ribbon/Workflow/Minimal
   presets with a review screenshot each.
10. Explore v1 (ADR-0016): the `/studies` group and study node types,
    `stokes.explore` samplers (full factorial, Latin hypercube, Sobol,
    Halton, central composite, Box-Behnken, optimal space filling), the
    `function` engine, `study.*` commands and `stokes study` CLI, the Explore
    stage with the Design Table and Plots panels (response against input,
    scatter matrix, parallel coordinates), CSV and JSON export.

## Schedule risks

| Risk | Milestone | Response |
|---|---|---|
| QML beside native VTK misbehaves on one platform | M0 | Decide in week one; Widgets fallback for the affected panels |
| OCCT import robustness | M1 | Ten public STEP files as the M1 test set before M1 starts |
| cfMesh and Foundation 14 | M2 | If it needs the ESI line, ship it with the ESI dialect in M10 and say so in the UI |
| snappyHexMesh layer quality on thin channels | M2 | Presets on the benchmark set; layer coverage as a first-class metric |
| User OpenFOAM installs vary | M2 | Precise probe reports; Foundation 14 only in v1 |
| PyFluent version churn | M7 | Pin PyFluent per Stokes release |
| Part-time effort | all | Demo-first ordering; the deferral levers above |
