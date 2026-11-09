# 00 · Open questions and decisions

*Asked and answered interactively on 2026-10-02. The "Decision" column is
binding for the other documents; the few items still open are listed at the
end.*

## A · Product and users

| ID | Question | Decision | Consequence |
|---|---|---|---|
| A1 | First paying user? | **General CFD users from day one.** Stokes is a personal project, not an employer product. | v1 physics and workflows are fully general; no heat-exchanger specifics in the core. |
| A2 | Commercial model? | **Freemium, closed source.** Free tier is the full local workflow; paid features listed under J4; paid support is an option. Price points undecided. | Licence files carry feature flags; no compute caps anywhere (I3). |
| A3 | Product name? | **Stokes is the product name.** | Installer, CLI (`stokes`), API and docs use it; trade-mark search still advisable. |
| A4 | Relationship to fingen? | **A whole new app.** Replaces nothing; takes learnings, code and inspiration from the tools built so far. You own that code (confirmed 2026-10-02); employer designs and study data stay out. | Reuse candidates are listed in docs/02 §11; no employer data in the repo, fixtures or tutorials. |

## B · Platform and runtime

| ID | Question | Decision | Consequence |
|---|---|---|---|
| B1 | Platforms in v1? | **Windows + WSL2 and native Linux.** No macOS. | One agent protocol for both; macOS would need containers (paid remote path) later. |
| B2 | Ship an engine image? | **No. Attach to existing OpenFOAM installations only.** | Stokes probes the user's distros and installs; in-house solvers are built from source inside the user's environment (C2). |
| B3 | Development and test machines? | **Code on this laptop; WSL and engine testing on the remote test workstation.** A dedicated machine later. | The SSH runner is built early for development use even though remote execution is a paid feature for users. |
| B4 | Python baseline? | Decided by me: **3.13 baseline, 3.14 verified in CI.** PySide6 6.11, OCP 8.0.1, VTK 9.7 and PyFluent 0.42 all publish 3.14 wheels. | Revisit at packaging (M8). |
| B5 | GPU? | **NVIDIA only, optional, feature-flagged.** | The build helper offers the CUDA variant of coupledSimpleFoam when `nvidia-smi` and a CUDA PETSc are present. |
| B6 | Minimum hardware? | **Both laptop and workstation, with an explicit reduced-quality mode.** | Full quality on discrete GPUs; a low-detail mode (LOD always on, no edges, fewer lights) on integrated graphics. |
| B7 | Viewport on WebGPU (three.js `WebGPURenderer`) instead of WebGL 2? (added 2026-10-08, from an outside stack review) | **Not now; the option sits beside ADR-0004's deferral of `moderngl`/`wgpu`.** The measured pain is pipeline compilation stalls, not throughput: the first box select linked `#define SHADER_NAME stokes:id` on the cold path (2,507 ms compile, 0 ms once pre-warmed; T-103). `createRenderPipelineAsync` and pipeline caching might help that; compute shaders would not, because the id pass is not throughput-bound. Cost: a second renderer path and WGSL variants, WebGPU flags in the headless-Chrome bench, and a backend in the ANGLE/D3D11 renderer-honesty check of `tools/bench_scene.py`. | **Trigger, falsifiable: revisit only if (a) a first-use stall survives the pre-warm path on a cold cache, or (b) a measured frame cost is attributed to driver-side pipeline work rather than to our own draw or overlay code.** While open: stay on WebGL 2 through ANGLE (D3D11 on Windows, which is already what the benchmark measures) for M1 and M2. ADR-0004; ADR-0013 fixed the renderer as three.js. |

## C · OpenFOAM flavour and engines

| ID | Question | Decision | Consequence |
|---|---|---|---|
| C1 | Which OpenFOAM line first? | **Foundation 14 in v1; ESI in v1.5 through the dialect layer.** | Other Foundation versions are probed and reported as unsupported with the reason. |
| C2 | How do the in-house solvers reach users? | **Build-from-source helper inside the user's distro** at a pinned tag against the detected OpenFOAM. | GPL compliance is trivial: the source is the distribution. |
| C3 | Engines in v1? | **foamRun (incompressibleFluid, fluid), foamMultiRun (CHT), coupledSimpleFoam CPU (flow + passive energy).** Coupled CHT when its stage-4 gate passes; GPU flagged. | The engine capability matrix drives what the UI offers. |
| C4 | Canonical solver sources? | Assumed: **GitHub `dhanninacs/coupledSimpleFoam` and `-gpu` at tags.** | The helper pins tags; the local Desktop clones are development copies. |
| C5 | Fluent as an engine? | **Yes, in v1, as a paid feature.** PyFluent is MIT (copyright Synopsys and ANSYS; verified 2026-10-02); the user needs their own commercially licensed Fluent. | A Fluent "dialect" of the compiler emits PyFluent settings calls instead of dictionaries. |

## D · CAD scope

| ID | Question | Decision | Consequence |
|---|---|---|---|
| D1 | CAD capability? | **Import and prepare, plus primitives and booleans.** No sketching. | Enclosures, fluid extraction, extensions, defeaturing, splitting, boxes, cylinders, spheres, booleans. |
| D2 | Kernel? | **Open CASCADE only, ever** (via OCP, Apache-2.0 bindings). | Import robustness is OCCT's; the healing pipeline and STL fallback carry the load. |
| D3 | Native CAD formats? | **No. STEP, IGES, BREP, STL, OBJ, 3MF only.** | No commercial translators, ever, unless this decision is reopened. |
| D4 | Model sizes? | **50 k faces, 20 M triangles.** | Id-buffer picking and LOD are designed for this. |

## E · Meshing

| ID | Question | Decision | Consequence |
|---|---|---|---|
| E1 | Meshers in v1? | **snappyHexMesh, cfMesh, blockMesh, and Gmsh as an external process.** | Four drivers, each with a dictionary or `.geo` writer, a progress parser, a quality mapping and a tutorial. cfMesh compatibility with Foundation 14 is verified in M2 (it may need the ESI dialect). |
| E2 | finmesh? | **Code is reusable** (you own it) for a later parametric-part feature; the internal component library and designs are not. | v2 feature; no employer design data. |
| E3 | Mesh sizes? | **Interactive to 20 M cells, batch to 100 M.** | The desktop never loads volume data. |

## F · Physics

| ID | Question | Decision | Consequence |
|---|---|---|---|
| F1 | v1 physics? | **As listed:** steady and transient incompressible, laminar / RANS / LES, energy (passive and full), compressible and buoyant, conjugate heat transfer, porous media, MRF. | Two-phase VOF with the Lee model, radiation and species follow in v1.5. |
| F3 | Units? | **Full unit systems with expressions.** | SI stored; any numeric field accepts a unit-tagged value or an expression over named quantities; unit-checked at edit time (ADR-0012). |
| F4 | Materials? | **Built-in catalogue, CoolProp, Cantera, and REFPROP when the user has it** (through CoolProp's REFPROP backend; nothing of REFPROP ships). | Four property providers behind one material interface; the material model, method catalogue, library fits and compile mapping are ADR-0018. |
| F5 | How are material properties modelled, and how does a library's T- and p-dependence reach the solver? (added 2026-10-03, from the owner's request for Fluent-style materials built on the libraries) | **ADR-0018:** `material` nodes with fixed `property` children, each with one method from a Fluent-parity catalogue (constant, ideal and incompressible ideal gas, Boussinesq, polynomials, piecewise forms, rational polynomial, Tait, Peng-Robinson; Sutherland, power law, log-polynomial, kinetic theory, non-Newtonian laws; JANAF; anisotropic solids); library materials are **fitted** from CoolProp, Cantera or REFPROP over a user-set T range at the material's reference pressure, accepted within a tolerance (default 1 %), refused above it, with tables as the always-available fallback; the library stays the source of truth and the fit is a regenerable artefact with its citation. Foundation 14 gets precompiled thermoTypes where they exist and tables otherwise (runtime-compiled polynomials by choice); Fluent gets its native methods or expressions. | Contract: `docs/engineering/materials-schema.md`; slices 1–6 in the ADR; the `materials` extra (CoolProp 8 MIT, Cantera 3.2 BSD-3). |

## G · Post-processing

| ID | Question | Decision | Consequence |
|---|---|---|---|
| G1 | Depth? | **Scenes, slices, iso-surfaces, streamlines, probes, reports, run comparison.** ParaView for the rest. | Reports and comparison are paid features (J4). |
| G2 | Reports? | **Self-contained HTML in v1, PDF via print, templates in v1.5.** | Jinja2 templates; the report is a paid feature. |

## H · UI

| ID | Question | Decision | Consequence |
|---|---|---|---|
| H1 | UI stack? | **Web front end: TypeScript, React 19, Vite and a three.js viewport, in a frameless Qt + QtWebEngine host** (ADR-0013, which supersedes ADR-0002 and ADR-0004). | The API is the only path to the model: no model state in the page, every edit an API command; VTK moves to the agent and post; the same page runs in a browser against `python -m stokes.server`. |
| H2 | Layout? | **Tree on the left** (Fluent and ParaView habit), inspector below it, viewport centre, assistant and context panels on the right. | docs/03 §2 redrawn. |
| H3 | Tree vocabulary? | **STAR-CCM+ structure with Fluent names.** | As specified in docs/03 §4. |
| H4 | Theme? | **Follow the OS theme.** | Dark and light maintained equally from day one. |
| H5 | Scripting? | **Yes** (implied by L1): Python console, journals, API-first. | — |

## I · Execution

| ID | Question | Decision | Consequence |
|---|---|---|---|
| I1 | Projects on network shares? | **Yes: project anywhere, jobs on the engine host's local disk.** | Only compact artifacts return to the project. |
| I2 | Remote execution? | **Local (WSL, native Linux) is free in v1. SSH hosts, SLURM/PBS schedulers and container hosts are paid features.** SSH is built early anyway for the remote test workstation testing. | Runner interface is host-agnostic from day one; schedulers and containers after v1. |
| I3 | Concurrency and limits? | **No limits anywhere.** | Freemium differentiates on features, never on cells, cores or jobs. |

## J · Licensing and protection

| ID | Question | Decision | Consequence |
|---|---|---|---|
| J1 | Licence system? | **In-house: Ed25519-signed licence files, offline-capable.** | Floating server only if ever needed. |
| J2 | Code protection? | **Nuitka-compiled, signed, no obfuscation.** | — |
| J3 | Telemetry? | **Opt-in only; local crash bundles.** | — |
| J4 | Paid features? | **Remote execution (SSH, schedulers, containers); the Fluent engine; the embedded LLM assistant and the MCP server; reports, run comparison and parametric sweeps.** Everything else, including the Python API, CLI, journals and headless server, is free. | Feature flags in the licence file; the free tier needs no licence file at all. |

## K · Team, timeline, validation, IP

| ID | Question | Decision | Consequence |
|---|---|---|---|
| K1 | Who builds it? | **You plus an AI pair, evenings and weekends.** | The roadmap is costed in engineer-weeks and converted to calendar time at about 15 hours per week (docs/06). |
| K2 | First demo? | **Internal: STEP to a converged run after M3.** | Everything not needed for that demo is ordered after it. |
| K3 | Validation set? | **Classic benchmarks, coupledSimpleFoam's own tutorials, NASA TMR and ERCOFTAC cases.** No employer cases. | References from literature and the OpenFOAM tutorials only. |
| IP1 | What may Stokes reuse? | **Your code (fingen, finmesh, the solver work): yes, you own it. employer IP (designs, internal component library, study data): no.** | A licence-header sweep on reused files; PyQt5 imports become PySide6; fixtures use public geometry only. |

## L · LLM integration (added 2026-10-02)

| ID | Question | Decision | Consequence |
|---|---|---|---|
| L1 | LLM-facing surfaces from day one? | **All of them: an MCP server over the Stokes API, a local HTTP/JSON-RPC API with a headless server mode, an embedded assistant panel, an agent-friendly CLI with JSON output and replayable journals.** Local LLM connections and API providers both supported. | API-first is now a hard constraint: the UI, CLI, MCP and HTTP all call one `stokes.api` (ADR-0010). |
| L2 | Assistant backends? | **Anthropic (Claude), local OpenAI-compatible servers (LM Studio, Ollama), OpenAI and Gemini.** | One provider abstraction with tool-use mapping; keys stay local. |
| L3 | Agent autonomy? | **Full control, no confirmations.** | Agents may launch and cancel runs and delete; an optional "confirm destructive actions" setting exists but is off by default. |

## C · Customisation (added 2026-10-02)

| ID | Question | Decision | Consequence |
|---|---|---|---|
| C1 | How far does the user shape the interface? | **All the way: placement of every panel, island and top-bar item, the tree's organisation, the look (themes as editable token sets), shortcuts and the default engine are user data with shipped presets (Stokes, Classic, Ribbon, Workflow, Minimal).** The owner's words: "we give you the tools to make a modern CFD interface that you like to make"; this is the selling point. | ADR-0015: a workspace document managed by the API (`workspace.*`), a panel registry and slot layout engine, Customise mode, a theme editor; the Stokes preset stays the reviewed design contract. |
| C2 | Can agents arrange the interface too? | **Yes.** The workspace is API state like everything else. | Journals record arrangement changes; the assistant can apply or build presets on request. |

## P · Parametric studies, design exploration and shape optimisation (added 2026-10-02)

| ID | Question | Decision | Consequence |
|---|---|---|---|
| P1 | A parametric case runner? | **Yes: studies as nodes with variables, responses and a design table; sweep and DOE types first, run through the same run queue as single cases.** Inspiration: Workbench design points, STAR-CCM+ Design Manager, Autodesk CFD design studies and summary items, PyFoam parameter variation. | ADR-0016; a `function` engine lets the table, samplers and plots be built before real runs exist. |
| P2 | Design exploration beyond sweeps? | **Yes, in Python: surrogates, sensitivity, single- and multi-objective optimisation with Pareto fronts (scipy, pymoo, SALib, scikit-learn; no GPL optimisers).** | Explore v2 milestone after M3; `stokes.explore` package; Explore stage with Design Table and Plots panels. |
| P5 | Dual-licensed libraries inside wheels (FreeImage, LibRaw, FreeType, gl2ps)? | **Elect the non-GPL option each time** (FIPL, CDDL-1.0, FTL, GL2PS); the GCC runtime exception covers numpy/scipy's Fortran runtime. | docs/04 §4 and `THIRD-PARTY-NOTICES.md`; the build adds the missing licence texts and drops GPL-only Qt modules. |
| P4 | pymoo pulls in moocore (LGPL-2.1-or-later). Accept? | **Yes, under the Qt/OCCT conditions (unmodified, separate replaceable file, notices); only in the optional `explore` extra.** Fallback: pin pymoo 0.6.1.5 or ship without pymoo. | docs/04 §4; `tools/license_allowlist.toml` entry; frozen builds keep the extension as a separate file. |
| P3 | Shape optimisation, adjoint or CAD-parametric? | **Both, premium: CAD-parametric shape studies with every engine first (Explore v2); adjoint shape and topology optimisation and sensitivity maps through adjoint-capable engines (OpenFOAM ESI `adjointOptimisationFoam`, Fluent via PyFluent) in a premium-optimisation milestone.** The Foundation line has no shape adjoint, so the ESI dialect's adjoint subset moves up to that milestone. | ADR-0017; `optimise` feature flag; M8.5. |

## G · Geometry import (added 2026-10-03, from the ADR-0014 build)

| ID | Question | Decision | Consequence |
|---|---|---|---|
| G1 | One part per STEP file or per solid? | **Per solid.** A multi-solid file creates a group named after the file with one `part` per solid (so conjugate heat transfer can assign regions and materials per body); a single-solid file creates the part alone. Because the solid count is known only after OCCT has read the file, the command adds a placeholder part at once and, for N > 1 solids, replaces it with a group of the same id and name holding one part per solid (done 2026-10-03). | Front-end loads parts by node id; a group id serves no mesh. |
| G2 | What does cancel leave behind? | **The node, with status `failed`;** undo or delete removes it. A colour chosen before the file was read is kept. | Matches the undo model: the import command stays one undo step. |
| G3 | The mesh codec's home | **`stokes.geometry.transport`** (re-exported by `stokes.server.geometry`), because the store and the CLI write caches without the server. `import_step` gained `wait`, and `geometry.prepare` pre-starts a worker so OCP's import time is hidden. | ADR-0014 consequences amended. |
| G4 | Import speed | **Known gap:** a 20 k-face, 4,500-solid, 54 MB STEP reads in about 84 s on this laptop (OCCT's reader, not tessellation); first solid at 85 s, fine pass at 117 s. **Update 2026-11-09:** on this host (24 CPUs, unloaded) the same kind of file imports in 41 s, not 75 s, because both tessellation passes run in helper processes (`geometry/helpers.py`); the read (26.5 s: parse 4.9, translate 17) is now the floor, and a saved project opens cold in 1.5 s, not 49.5 s, because the import writes the pack. Remedies still queued: root-by-root streaming read, OCCT healing controls (`docs/engineering/performance.md`, "Import and cold start at 4,500 parts"). | Perf tests guard tessellation (coarse 10 s, fine 12 s before the machine factor and 25 % headroom; raised 2026-10-03 because other sessions load this laptop); the import budget stays an open target. |
| G6 | Thousands of parts: how do the viewport, the transfer and the tree scale? | **ADR-0019:** batched drawing per look from 100 parts, batched mesh transfer as binary frames (gzip only for clients not on loopback), and a tree that scales (virtualised rows, node event patches, `node.tree` built in short slices). | The orbit gate is met on the GPU timer for 4,500 parts (median 10.6 ms, p95 16.6 ms). Still open: cold-start geometry load and parallel STEP import (`docs/TASKLIST.md`, P0). |
| G7 | How are faces selected and geometry prepared (M1)? | **ADR-0020:** faces by (part id, face id) with per-face fingerprints for re-matching; `tag` nodes with face-range members; `tag.*` commands (click, box, lasso, grow by angle, by colour and name, invert); operations (`primitive`, `boolean`, `enclosure`, `extract_fluid`, `extend`, `defeature`, `split`) as parametric nodes under `/geometry/operations` whose outputs are derived parts, re-run from the dependency graph in the worker, with OCCT shape history carrying selections; GPU id-buffer picking on the client; `geometry.check`; one STL solid per tag for snappyHexMesh. Open: whether `split` writes into the B-rep (default no). | M1 slices 1–5 in the ADR. **Slice 1 built 2026-10-04 (866 tests; contract `docs/engineering/geometry-schema.md`). Deviations to confirm: `members` is canonical JSON text in a string parameter (no structured parameter kind exists yet); the `tag` type is registered when the API loads its commands, because the layering gate forbids `stokes.model` importing `stokes.geometry` (proposed: whitelist the light `stokes.geometry.model` and register in `register_builtin_types` like the study types); growth measures the dihedral angle across each shared triangle edge, not between face mean normals; checks run on the tessellation only and `export_step` is unsupported until the worker gets job types; fingerprints are archived per project on save; `by_color`/`by_name` match whole parts. Follow-ups: a `warning` node status; face adjacency built in the import worker (2.2 s in numpy for 4,500 parts). |
| G8 | Compress mesh replies on loopback? | **No:** gzip at level 6 only for clients not on loopback; `--compress-loopback` turns it on for a browser behind an SSH tunnel. | Over loopback the warm transfer of 4,500 parts takes 0.23 s uncompressed against 1.73 s gzipped (ADR-0019, decision 5). |
| G9 | OCCT issue 1564: eight `TKGeomBase` files carry a proprietary-licence header (since 7.7, still in 8.0.1). Proceed? | **Yes, as an accepted risk** (owner, 2026-10-04). | Re-check the issue at every OCCT upgrade and before the first public release; if it is still open then, ask counsel. |
| G10 | May one face carry two `boundary` tags? | **Allowed while preparing, with a `tag.overlap` warning; the case compiler refuses it** with a message naming the faces and tags (owner, 2026-10-04). Region and plain tags may overlap freely. | T-033 reports overlaps; M3's compiler enforces it. |
| G11 | Enclosure defaults | **3 body extents on every side and 8 downstream of the flow direction, each editable; a warning above 1 % blockage** (owner, 2026-10-04). | T-025's defaults. |
| G12 | Do operations re-run when an input changes? | **Yes, by default:** `auto_run` true, 300 ms after the last change, cancelling a run in progress; it can be switched off per operation (owner, 2026-10-04). | T-024. |
| G13 | Repair tolerances | **Start from ADR-0021's values and tune them on the public STEP test set** of the M1 gate (owner, 2026-10-04). | T-034. |

## Still open

| ID | What is left |
|---|---|
| U3 | Autosave: a recovery copy, or saving into the project? Assumed (2026-11-08): **a recovery copy**. While there are unsaved changes, the web app calls `project.autosave` once a minute. That writes `recovery.json` beside `project.json` and never over it. The next open after a crash or a restart offers it back (Restore or Discard). A Save, a Save as, or a Close without saving removes it. The project file changes only when the user saves, as it always has. Open: whether the minute should be a setting, and whether a never-saved project should get a copy too (it has no file to recover against today). |
| U3 | FoamWin: a **parallel** `of14.fluid` run with energy and a turbulence model fails at `decomposePar` ("Unknown patchField type compressible::alphatJayatillekeWallFunction"), found in the mixing-elbow walkthrough (2026-11-04). Root cause: the Windows `decomposePar` (and presumably `reconstructPar`) has no generic patch-field fallback, which the Linux build has through `libgenericFvFields`, so it cannot pass through a type only the solver's libraries define; naming `libfluidThermophysicalTransportModel.so` in `controlDict` fixes it by hand but the agent's dictionary lint refuses `libs` (ADR-0024 §1.8). Options: (a) rebuild FoamWin so the decompose/reconstruct utilities link `libgenericFvFields` (preferred, no policy change); (b) an ADR that lets the current lint profile accept an exact allow-list of install libraries, as it does for `#includeEtc`. Assumed until decided: such runs use 1 core on native Windows (WSL hosts are unaffected). |
| U2 | FoamWin: `#codeStream` / `dynamicCode` still has no compiler in the package. `libfvModels.dll` was missing from `pkg/windows` (the build in WSL `~/foamwin-clean` had made it on 2026-10-30, 3.7 MB); it was copied in on 2026-11-04 and verified by running the cavity with a `semiImplicitSource` fvModel (loads, constructs, runs to its end time). `libinterRegionFvModels.dll` is in the same state (built in `~/foamwin-clean`, absent from the package) and has not been copied or tested. |
| U1 | Whether the user can give a condition kind a preferred display name. Assumed: no. The patch name is the boundary's own name; a kind alias, if wanted, would be a workspace setting (ADR-0015), never a project parameter. |
| A2 | Price points and whether paid support is sold separately from paid features. |
| A3 | A trade-mark search for "Stokes" in software. |
| A4 | Whether design exploration (DOE, surrogates, optimisation) is free or a paid flag; until decided it runs ungated in the free tier (the free tier carries no feature flags, so gating would lock free users out), with remote execution paid as before. Shape optimisation is premium regardless (P3). **Deferred by the owner on 2026-10-03: every free-or-paid decision (A2, A4, the `optimise` flag) waits until the full app is built; features stay ungated meanwhile.** |
| E2 | Whether OpenFOAM 14 still ships `adjointShapeOptimizationFoam` (verify in M2) and which adjoint settings PyFluent exposes (verify in M7). |
| E1 | cfMesh's compatibility with Foundation 14 (verify in M2; it may arrive with the ESI dialect instead). |
| L2 | Which model is the default in the assistant and how keys are stored (OS keychain is the proposal). |
| G5 | What the viewport shows around a failed import, and what a part's patch is called. Assumed, from the viewport build of 2026-10-03: the sample part shows only while the tree has no `part` node, so a failed or cancelled import (its part stays, marked failed, G2) leaves the viewport empty until it is undone or deleted; a part's patch is named after its node, made unique in tree order with `_2`, `_3`, and that is the name "Assign boundary" gives the boundary. |
| F5 | Four assumptions in ADR-0018 for the owner to confirm or change: (a) the default fit tolerance is 1 % maximum relative deviation and the default OpenFOAM strategy writes temperature-dependent liquids as tables rather than runtime-compiled polynomials (`property_tables` on); (b) mixtures with species transport, humid air and (p, T) real-gas tables wait for v1.5, so a Cantera or CoolProp mixture is a frozen-composition fluid in v1; (c) the built-in catalogue: **no values are copied from textbook or handbook tables; fluid records are generated from CoolProp (MIT) at stated reference states; solid records use widely published values, each cited to a public source (for example NIST) and cross-checked against a second public source; tests use NIST WebBook or CoolProp values;** (d) every provider is free (REFPROP is the user's own licence), no feature flag. Also to verify in M3: whether CoolProp 8's nanobind wrapper releases the GIL (decides thread or process worker) and OpenFOAM's out-of-range behaviour for its T-only tables. **Answered 2026-10-04: CoolProp 8 holds the GIL (a background thread stalled the main thread up to 106 ms, its import 3.1 s), so library calls run in one persistent process worker.** |
| S1 | ADR-0022 (analysis set-up, 2026-10-05) proceeds on these assumptions until the owner confirms or changes them: `pressure_inlet` joins the boundary kinds (docs/05 §2 gains a row for M3's compiler); the kind colours (red inlets, blue outlet, grey wall, violet symmetry, orange cyclic, green interface, from the tag palette) are written to a tag when a condition is assigned (`keep_color` keeps a hand-picked colour); the flow helpers make `air` (external) or `water` (internal) from the built-in templates when the project has no fluid material; `P` toggles paint and `Alt` + wheel cycles the candidates under the pointer; the set-up readout lives in the output drawer's collapsed strip; Vitest (MIT) is the front end's test runner; condition and region statuses are derived from their tag and `setup.check`, not stored. |
| S2 | T-036 (the assign commands, 2026-10-05) reads ADR-0022 this way until the owner confirms or changes it: a boundary or region made as a plain group before ADR-0022 becomes typed in place (`ConvertGroup`: the same id, name and children) instead of being removed and added again, which would drop a region's `boundaries` group; `region.assign` on bodies gives the material to the only region of its kind that has none (the `fluid` region a first `boundary.assign` made) before it makes a region named after the material, so conditions first and a material second end in one region; a boundary takes only a boundary-kind tag and a region only a region-kind tag (a plain tag is refused, not converted); region tags take tag-palette colours other than the inlet red and the outlet blue; `node.remove` refuses every referenced node, a study objective's response included. |
| F6 | Decided by the owner 2026-10-03: (a) **material templates**: a case loaded from or continued in Fluent keeps the properties extracted from that case; a case built from scratch uses the app's own library, so `material.create` templates must come from the built-in catalogue, not from Fluent's database (open task: swap the slice-1 defaults); (b) **fixed properties**: properties default to fixed (constant) values, and fixed properties are accepted when a loaded case already has them or the user chooses them (reading to confirm: if protecting the property nodes from removal or renaming was meant, the model gets an owned-children rule instead); (c) editing a library property through `parameter.set` should mark it detached from the library, as `material.set_method` does (add with refit in slice 2). **(a) and (c) done 2026-10-04 in materials slice 2; (b) still to confirm.** |
| R1 | ADR-0026 amendment 1 (2026-10-22): the plane clip is the existing slice, served by the results routes with no `run.slice` command. Assumed until the owner decides: no `run.slice` command is added; a client cuts a plane through `/api/results/slice/{run}?plane=`. **Range check resolved 2026-10-22 (ADR-0026 amendment 2, commit `1d17189`):** the agent measures each scalar field's min and max over every cell and point when it extracts the fields, `run.fields` reports it as `volume`, and `run.iso_surface` checks against it widened by the recorded range; a cache from before has no `volume` and keeps the recorded range. Only the `run.slice` question stays open. |
| T1 | ADR-0027 step 2 (the energy fields of the solve snapshot, 2026-10-22) proceeds on these assumptions until the owner confirms or changes them: a pressure outlet's temperature is `inletOutlet` with the boundary's `backflow_temperature`; a patch no boundary holds (`unassigned`) is adiabatic (`zeroGradient`); a symmetry patch carries no thermal condition (its patch type does it); a convection wall is refused (`solve.thermal_unsupported`) rather than written; `mu`, `cp`, `kappa` and `T_ref` are filled only with energy on and keep their defaults otherwise; the gas/liquid energy form is left to the writer step. |
| T2 | ADR-0027 step 3 (the `of14.fluid` writer, 2026-10-22) proceeds on these assumptions until the owner or step 5's run says otherwise: (a) the energy case is a constant-property liquid (`rhoConst`, `eConst` with `Cv = cp`, `sensibleInternalEnergy`); (b) the snapshot has no phase, so a density below 100 kg/m³ is refused as a gas; (c) a heat flux is positive into the fluid and written as `externalTemperature` with `q`; (d) `p_rgh` is gauge and `pRef` is the operating pressure. None is verified by a solver run yet. |
| S3 | ADR-0023 (runs, comparison, set-up modules and templates, 2026-10-05) proceeds on these assumptions until the owner confirms or changes them: runs are **records**, not undoable nodes, listed in the Runs panel while `/runs` stays a stage mapping; a run is auto-named `parameter=value` in the project's display unit (`U_in=2.0`), several joined with `, `; the default parallel slots are `cpu_count - 1`; a set-up module is a versioned snapshot of one facet (conditions, materials, solver, mesh) with `.stokesmod` files; templates ship as data for cold plate, heat sink, manifold and heat exchanger; the material library import conflict policy defaults to keeping both with a rename; `run.compare` offers a Pareto plot over any two responses, not only declared objectives; the shape-optimisation items (seed-design deviation limits, patterning and periodicity, manufacturing constraints, shape-evolution playback, a field viewer with free slicing, external CAD plug-in round trips) are briefs whose board status is `later (shape optimisation)`. |
| E3 | ADR-0029 (external OpenFOAM 14 cases, 2026-10-28) proceeds on these assumptions until the owner confirms or changes them: tutorial import is in the free tier; the catalogue is read at run time from the user's own install and never shipped; a case whose solver module or library is missing from the FoamWin install shows "engine lacks X" rather than running. |
| E5 | T-306/T-307 (recipes and `case.import`, 2026-10-28) proceed on these assumptions until the owner confirms or changes them: (a) an Allrun step the external allow-list refuses is listed in the recipe's `refused` and the case still imports as `needs_recipe`; only a lint finding refuses the import; (b) with no host probe the files are linted against an empty install (every `libs`, `#includeEtc` and `#includeFunc` refused) and the recipe is `unverified`; (c) Allrun scripts are kept in `cases/<id>/scripts/`, unlinted and never staged, beside the linted `source/`; (d) undo and `case.remove` leave the copy on disk, so nothing prunes copies of removed cases yet; (e) the copy skips hidden entries, `processor*`, `postProcessing`, `dynamicCode` and `log.*`, and refuses any link (or Windows junction) leaving the case; (f) a sub-case is followed through `cd <folder>` lines only; `(cd x && ./Allrun)` and nested Allruns are listed, not split. |
| E6 | T-308/T-309 (overrides and running an imported case, 2026-10-28) proceed on these assumptions until the owner confirms or changes them: (a) the known physical keys are only `startTime`, `endTime`, `deltaT`, `maxDeltaT`, `nu` and `rho`; every other number is a typed token without a unit; (b) a run of an imported case is serial: `decomposePar`/`reconstructPar` steps are left out and parallel steps run on one rank, until `numberOfSubdomains` follows the run's cores; (c) recipe file actions after the first step, and files outside `system/`, `constant/`, time folders and `0.orig/`, are listed and not staged; (d) a transient case's finished run reads `done_unconverged`, because "converged" is the steady-state notion; the label is still to be revisited; (e) an imported case's results are single-region: a multi-region case's regions are not yet offered (T-310 part 2); (f) shortening `endTime` below the case's first write interval leaves nothing to show, as OpenFOAM writes no final time unless asked; (g) a run's mesh becomes the project's mesh result (`MeshService.adopt_job_mesh`) without a checkMesh report, so the status bar reads "0 cells, quality not met" for it until the recipe's mesh is measured; (h) the case's import facts are hidden from the generic editor and shown as text by the case card, but `parameter.set` can still change them from the console or an assistant. |
| E4 | Whether `tests/fixtures/of14/` (tutorial copies, GPL-3 per their README, shipped in the sdist) is removed. ADR-0029 decision 2 makes them inconsistent; until the owner decides no new copies are added. |
