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
| B3 | Development and test machines? | **Code on this laptop (LP-0040); WSL and engine testing on the remote test workstation.** A dedicated machine later. | The SSH runner is built early for development use even though remote execution is a paid feature for users. |
| B4 | Python baseline? | Decided by me: **3.13 baseline, 3.14 verified in CI.** PySide6 6.11, OCP 8.0.1, VTK 9.7 and PyFluent 0.42 all publish 3.14 wheels. | Revisit at packaging (M8). |
| B5 | GPU? | **NVIDIA only, optional, feature-flagged.** | The build helper offers the CUDA variant of coupledSimpleFoam when `nvidia-smi` and a CUDA PETSc are present. |
| B6 | Minimum hardware? | **Both laptop and workstation, with an explicit reduced-quality mode.** | Full quality on discrete GPUs; a low-detail mode (LOD always on, no edges, fewer lights) on integrated graphics. |

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
| H1 | UI stack? | **Native: PySide6 shell + Qt Quick (QML) panels + VTK viewport.** Chosen on large-data suitability after you asked for a more modern, responsive UI and floated three.js. | Modern chrome from QML; CAD and meshes stay in C++ memory; pure Python plus C++ kernels (ADR-0002, amended). |
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
| G4 | Import speed | **Known gap:** a 20 k-face, 4,500-solid, 54 MB STEP reads in about 84 s on this laptop (OCCT's reader, not tessellation); first solid at 85 s, fine pass at 117 s. Remedies queued: root-by-root streaming read, parallel workers, OCCT healing controls. | Perf tests guard tessellation (coarse 10 s, fine 12 s before the machine factor and 25 % headroom; raised 2026-10-03 because other sessions load this laptop); the import budget stays an open target. |

## Still open

| ID | What is left |
|---|---|
| A2 | Price points and whether paid support is sold separately from paid features. |
| A3 | A trade-mark search for "Stokes" in software. |
| A4 | Whether design exploration (DOE, surrogates, optimisation) is free or a paid flag; until decided it runs ungated in the free tier (the free tier carries no feature flags, so gating would lock free users out), with remote execution paid as before. Shape optimisation is premium regardless (P3). **Deferred by the owner on 2026-10-03: every free-or-paid decision (A2, A4, the `optimise` flag) waits until the full app is built; features stay ungated meanwhile.** |
| E2 | Whether OpenFOAM 14 still ships `adjointShapeOptimizationFoam` (verify in M2) and which adjoint settings PyFluent exposes (verify in M7). |
| E1 | cfMesh's compatibility with Foundation 14 (verify in M2; it may arrive with the ESI dialect instead). |
| L2 | Which model is the default in the assistant and how keys are stored (OS keychain is the proposal). |
| G5 | What the viewport shows around a failed import, and what a part's patch is called. Assumed, from the viewport build of 2026-10-03: the sample part shows only while the tree has no `part` node, so a failed or cancelled import (its part stays, marked failed, G2) leaves the viewport empty until it is undone or deleted; a part's patch is named after its node, made unique in tree order with `_2`, `_3`, and that is the name "Assign boundary" gives the boundary. |
| F5 | Four assumptions in ADR-0018 for the owner to confirm or change: (a) the default fit tolerance is 1 % maximum relative deviation and the default OpenFOAM strategy writes temperature-dependent liquids as tables rather than runtime-compiled polynomials (`property_tables` on); (b) mixtures with species transport, humid air and (p, T) real-gas tables wait for v1.5, so a Cantera or CoolProp mixture is a frozen-composition fluid in v1; (c) the built-in catalogue: **no values are copied from textbook or handbook tables; fluid records are generated from CoolProp (MIT) at stated reference states; solid records use widely published values, each cited to a public source (for example NIST) and cross-checked against a second public source; tests use NIST WebBook or CoolProp values;** (d) every provider is free (REFPROP is the user's own licence), no feature flag. Also to verify in M3: whether CoolProp 8's nanobind wrapper releases the GIL (decides thread or process worker) and OpenFOAM's out-of-range behaviour for its T-only tables. |
| F6 | Decided by the owner 2026-10-03: (a) **material templates**: a case loaded from or continued in Fluent keeps the properties extracted from that case; a case built from scratch uses the app's own library, so `material.create` templates must come from the built-in catalogue, not from Fluent's database (open task: swap the slice-1 defaults); (b) **fixed properties**: properties default to fixed (constant) values, and fixed properties are accepted when a loaded case already has them or the user chooses them (reading to confirm: if protecting the property nodes from removal or renaming was meant, the model gets an owned-children rule instead); (c) editing a library property through `parameter.set` should mark it detached from the library, as `material.set_method` does (add with refit in slice 2). |
| G6 | Thousands of parts (M1 performance): one HTTP fetch per part limits loading to about 40 parts/s inside the app and `node.tree` with 1,440 nodes takes 1.2–1.9 s and blocks the event loop; the tree panel has no row virtualisation. Planned: a batched mesh endpoint or binary WebSocket frames, node patches in events or children on demand, virtualised tree rows, an id-buffer pick (already in M1). **M0 gate measurement (2026-10-03, desktop window, this laptop while other agents ran): a 20 250-face STEP of 4 500 solids imports in 3 min 36 s, shows all 4 500 parts after 347 s, and orbits at a median 250 ms per frame (about 4 fps) against the 60 fps gate.** The rendering side needs batching (one draw call per material, e.g. `THREE.BatchedMesh`, with per-part visibility and colour), GPU id-buffer picking and a bounding-volume hierarchy for hover; the loading side needs batched mesh transfer. A single-solid 20 k-face part was not measured. |
