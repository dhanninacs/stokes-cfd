# 06 · Roadmap

*Revised 2026-10-02. Effort is in engineer-weeks (one person, full time, with
an AI pair). You build this evenings and weekends (K1); at about 15 hours a
week, multiply engineer-weeks by roughly 2.7 for calendar time. Milestones are
ordered so the internal demo (K2) comes as early as possible; everything the
demo does not need is after it.*

| M | Name | Eng-weeks | Scope | Gate |
|---|---|---|---|---|
| M0 | Foundations · **steps 1–7 done 2026-10-02 (the web front end, design system and three.js viewport of ADR-0013 replace the QML shell and the VTK viewport; the polish pass is in its review loop); step 8 (STEP import, ADR-0014) done end to end (face selection and many-parts performance in M1, G6); step 9 (workspace v1, ADR-0015) done end to end apart from the shortcut editor; step 10 (Explore v1, ADR-0016) done end to end. M0 gate: undo, expressions, CLI and licence audit pass; the orbit gate is still open: on a 4 500-solid, 20 k-face assembly ADR-0019's batched renderer gives a median of 60 fps (16.7 ms per frame) but not yet the p95 (117 ms; re-measured 2026-10-04, decisions log G6)** | 5 | Repository, CI with the licence audit and the employer-identifier grep, `stokes.model` with commands and undo, `stokes.units` with unit systems and the expression engine, `stokes.api` with the command registry and generated schemas, `stokes` CLI skeleton, design system and the frameless QtWebEngine shell over the React front end (OS theme, frameless window, rail, top bar, tree, inspector), the three.js viewport with the orientation gizmo (no LOD or reduced-quality mode: those were VTK-only and are not carried, ADR-0013), STEP import in workers, Python console. | Open a 20 k-face STEP; orbit at 60 fps; undo any edit; `25 mm` and `U_in*2` accepted in a field; the same edit works from the CLI; licence audit green. |
| M1 | Geometry and workspace polish (ADR-0020; many-part rendering ADR-0019) · **ADR-0020 slices 1–5 landed 2026-10-05 (T-017 to T-034): pick, selection, box selection, feature-angle growth, tags, the twelve operations (primitive, boolean, enclosure, extract_fluid, heal, sew, orient, solidify, cap, unify, slivers, defeature), B-rep check, STEP and per-tag STL export through the shape cache, mesh-only import (STL/OBJ/3MF) and the operation cards; the M1 test set exists as ten synthetic STEP files plus one IGES (`tests/m1_fixtures.py`, generated at test time, no binary in the tree) and `tests/test_m1_gate.py` (slow) runs every one through import → check → repair → enclosure or extraction → tags → STL export and replays each journal through `stokes replay` to byte-identical STL. Remaining: the owner's interactive five-minute run on one agreed file — the M1 gate itself, which no test can time. `extend`, `split` and `connect` are **deferred by ADR-0021, not M1 gaps**: that ADR lists them as "Later, planned and not briefed now" (ADR-0021 §Operations; ADR-0020's open question on `split` stands), and board row C-6 keeps `split` there. The Scope column above still names them because it predates ADR-0021 — read it with that ADR. The set-up commands (`setup.check`, `setup.defaults`, ADR-0022) also postdate this row.**
  **ADR-0022's front end is partly landed (2026-10-12): the value panel (T-044), the drawer's set-up
  status readout (T-049), typed edge/vertex tag members (T-056) with the topology endpoint, the
  edge/point pick modes and their overlay (T-058/T-060), and element-aware Measure (T-113). Still
  open from it: the region/boundary tree rows (T-047), the Conditions panel (T-048), the material
  pick (T-045), the condition glyphs (T-046), the flow helpers (T-050) and the Related menu (T-051).** | 3.5 | Id-buffer picking, box and lasso, feature-angle growth (C++), tags, primitives and booleans, enclosure, fluid extraction, extend, defeature, split, checks, STL export per tag; workspace: shortcut editor with STAR-CCM+ and Fluent key presets, preset export and import, floating panels. | A public manifold STEP to fluid domain plus inlet, outlet and wall tags in under 5 minutes. |
| M2 | Agent, attachment, meshing | 5 | Agent and protocol; local-WSL and local-Linux runners; SSH runner for the remote test workstation testing; install probing; mesh model; snappyHexMesh and blockMesh drivers; cfMesh and Gmsh drivers (cfMesh's Foundation-14 compatibility verified here); checkMesh quality tree; boundary-surface extraction; live cell estimate. | A 5 M-cell snappy mesh from the UI on the remote test workstation over SSH and on a local WSL distro; surfaces visible in under 10 s. Decisions 2026-10-13: v1 meshers are blockMesh and snappyHexMesh (cfMesh and Gmsh behind explicit gate tasks); the mesh host is the OpenFOAM 14 on the Linux test workstation over SSH (blueCFD is not used, no WSL on the laptop); fluid only until M5 (no region splitting); VTK is a probed host dependency and msgpack ships with the agent (ADR-0024). |
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

UI usability follow-up (2026-10-31, M0 step 5 / M1 workspace polish): tree navigation help and direct
Settings access; mesh/solver settings search, expandable optional controls and essential-view shortcut;
section explanations and immediate disclosure of rejected fields. This does not close the outstanding milestone gates.
Opening-screen follow-up: explicit New/Open actions, recent projects and optional learning resources;
no hidden viewport or implicit example geometry. Settings follow-up: focused category pages and separate
solver configuration/monitoring, based on the requested Astra UX review.

1. Repository skeleton, `pyproject`, pre-commit, CI with `pip-licenses` and
   the employer-identifier grep gate.
2. `stokes.units`: quantities, unit systems, expression grammar, tests.
3. `stokes.model`: node schema, commands, undo, JSON persistence, tests.
4. `stokes.api` and the CLI: command registry, generated JSON schemas, one
   end-to-end command (`project.new`, `parameter.set`).
5. ~~Shell prototype: Qt Widgets window hosting QML panels (`QQuickWidget`)
   next to a native VTK widget, tested on Windows and Linux for focus, HiDPI
   and compositing before anything is built on it.~~ **Superseded by ADR-0013
   (2026-10-02):** the shell is a frameless Qt window hosting QtWebEngine over
   the React front end, so no QML panel and no native VTK widget was built. See
   item 7.
6. Design system: tokens, OS-theme switching, icon pipeline, tree and
   inspector generated from schemas.
7. ~~VTK viewport: orientation cube, LOD swap, reduced-quality mode.~~
   **Superseded by ADR-0013 (2026-10-02):** the viewport is three.js in the web
   front end. The orientation cube is built (`web/src/viewport/gizmo.ts`); LOD
   swap and reduced-quality mode are *not* web-viewport features and are not
   carried into M1 — many-part drawing is ADR-0019's batched path instead.
8. STEP import through OCP in a worker with progressive display
   (ADR-0014): `part` nodes, transport version 2 with face ids, mesh cache,
   `geometry.import`, Import geometry… in the UI.
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
| ~~QML beside native VTK misbehaves on one platform~~ **Resolved by ADR-0013** | M0 | The web front end removed the mixed QML/native-VTK shell this risk was about; nothing to prototype. |
| OCCT import robustness | M1 | Ten public STEP files as the M1 test set before M1 starts |
| cfMesh and Foundation 14 | M2 | If it needs the ESI line, ship it with the ESI dialect in M10 and say so in the UI |
| snappyHexMesh layer quality on thin channels | M2 | Presets on the benchmark set; layer coverage as a first-class metric |
| User OpenFOAM installs vary | M2 | Precise probe reports; Foundation 14 only in v1 |
| PyFluent version churn | M7 | Pin PyFluent per Stokes release |
| Part-time effort | all | Demo-first ordering; the deferral levers above |

## Local Windows development validation

The M2/M3 native-engine preparation includes a local Windows checkout with
isolated application state. The desktop and CAD demo start successfully.
The Sizing page import explicitly selects the TSX component for Windows.

Native FoamWin execution landed with ADR-0025: the local runner is available
on Windows, the agent discovers Foundation 14 through `STOKES_FOAMWIN_ENV`,
and jobs run under kill-on-close Job Objects. A real validation case
(blockMesh → decomposePar → `foamRun -parallel` on two MS-MPI ranks →
reconstructPar) completed with reconstructed time directories. blockMesh
remains serial; see the ADR for the scope and its limits.

## Energy equation (ADR-0027)

| Step | Status |
|---|---|
| 1–4 Setting and checks, snapshot, `of14.fluid` writer with goldens, run path and temperature fields | Done |
| 5 Native run on the mixing elbow; engine `built: true` | Done 2026-10-22: 300 iterations, `T` within 293–353 K, outlet 294.92 K against a 294.86 K mixing balance; residuals not converged to 1e-4 (`p_rgh` 0.045) |

| 6 Convergence, gravity direction, tilted outlet, parallel | Done 2026-10-22: outlet is a hydrostatic `fixedValue` `p_rgh` (gravity along x now runs); 2-rank run verified for 50 iterations; 1,000 serial iterations plateau at `p_rgh` ≈ 0.05 (laminar at Re ≈ 8e4) |
| 7 kOmegaSST turbulence on the `of14.fluid` writer (ADR-0028) | Written and tested 2026-10-22: `of14-fluid/4`, goldens, `solve.check` gate; native elbow run with kOmegaSST pending |

Still open: with kOmegaSST, 300 iterations on 0, 3 and 5 wall layers did not reach 1e-4 (ADR-0028); the executor does not
call the reconstruct check; the front end has no `T` legend yet.

## Sequential GUI review — first Windows batch

M1/M2/M4 workflow inspection has started in the private local checkout.
The first batch fixes outline stage focus, Geometry Check lifecycle/retry,
missing-status accessibility, and duplicate task-page guidance. See
[audit/WINDOWS-GUI-REVIEW.md](audit/WINDOWS-GUI-REVIEW.md) for evidence and the
ordered follow-up queue.

The M2/M3 settings review now shares grouping between Mesh, Solve and
Properties. Guided meshing is being aligned with the same quantity editors,
applicability and edit validation. Mesh and solver core counts are separate
saved settings, each editable under Execution in both dedicated panels and
Properties; the presentation work and native execution are independent and
both now present.
