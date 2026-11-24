# 01 · Vision and scope

*Revised 2026-10-02 after the decisions in docs/00.*

## Elevator pitch

Stokes is the desktop CFD workbench that takes an engineer, or an LLM agent
acting for one, from a STEP file to a converged, reported result on OpenFOAM
without ever opening a dictionary. It feels like a modern CAD tool (Shapr3D),
thinks like the simulation tools engineers already know (STAR-CCM+, Fluent),
exposes everything it can do as tool calls, and drives the open-source solver
stack as an arm's-length engine so the product itself can be closed and sold.

## Who it is for

| Persona | What they need | What they must never see |
|---|---|---|
| **General CFD engineer coming from Fluent or STAR-CCM+** (first users) | A tree that matches their mental model, boundary conditions by picking faces, turbulence model choice, residual and report monitors, scenes. Their existing OpenFOAM install, found automatically. | Having to learn "the OpenFOAM way" to be productive. |
| **Heat-transfer and thermal engineer** | Conjugate heat transfer with fluid and solid regions, materials with real properties, duty and pressure-drop reports, run comparison. | Dictionary syntax, `splitMeshRegions`, interface patch naming. |
| **Analyst or LLM agent running studies** | Everything in the UI as an API, MCP tools, a headless server, journals, batch runs, sweeps, reproducible run bundles. Full control, no confirmation prompts. | A GUI-only product with no automation surface. |
| **Developer of in-house solvers** (you) | A clean engine interface so `coupledSimpleFoam` and its successors appear as engines with their own options, build helper and validation gates. | Forking the GUI to add a solver. |

## Product principles

1. **One tree, one truth.** The project tree is the only model. Every panel,
   every tool call and every generated file derives from it.
2. **API-first, agent-first.** The UI is one client of the Python API; the
   CLI, the HTTP server and the MCP server are others. If a human can do it,
   an agent can do it with the same call.
3. **The case is an output, never an input.** Stokes compiles the OpenFOAM
   case (or the PyFluent script) from the model, deterministically. Hand
   edits live as recorded *overrides* in the model.
4. **Tags are the contract.** Geometry faces → tags
   → mesh patches → boundaries. Rename once, everything follows.
5. **Never block the UI.** Anything above a few milliseconds runs in a worker
   or in the agent. Progress and cancel are always available.
6. **Big data stays where it is produced.** The agent extracts surfaces,
   slices and reports next to the solver; the desktop receives compact
   geometry, not time directories.
7. **Convergence is judged by physics.** Balances, report stationarity and
   stopping criteria decide; residuals are a monitor.
8. **Every quantity carries a unit.** SI stored, any unit displayed, every
   numeric field accepts a unit-tagged value or an expression.
9. **Expert escape hatches everywhere.** Dictionary overrides, a Python
   console, "open in ParaView".
10. **Every number is reproducible.** A run is an immutable bundle.
11. **Licence-clean by construction.** The dependency allow-list is enforced
    in CI; GPL software is driven, never linked.
12. **Fast by default.** The budgets in docs/02 are tested, not hoped for.

## The selling point: an interface you shape

Every CFD tool ships one arrangement and asks the user to adapt. Stokes
ships an opinionated default and then hands over the tools: where each panel
sits, what the top bar carries, how the tree is organised, the look down to
each colour token, the shortcuts and the engine that runs by default are all
user data (ADR-0015). Presets recreate the habits of STAR-CCM+, Fluent and
SimScale users in one click; anything can be dragged elsewhere in Customise
mode; themes are edited live and shared as files; and because the
arrangement is API state, the assistant can do it on request ("put the
console on the right and use the classic layout"). The promise, in the
owner's words: *we give you the tools to make a modern CFD interface that
you like to make.*

## Scope

### v1 — free tier (the full local workflow)

| Area | In v1, free |
|---|---|
| Geometry | Import STEP, IGES, BREP, STL, OBJ, 3MF. Units, scaling, placement. Primitives (box, cylinder, sphere) and booleans. Fluid-domain extraction, enclosure boxes, inlet/outlet extension, defeaturing by face deletion and healing, face splitting by feature angle. Tags by click, box, lasso, feature-angle growth, STEP colour and name, inversion. |
| Meshing | snappyHexMesh, cfMesh, blockMesh, and Gmsh as an external process, behind one mesh model: base size, per-tag surface refinement, region refinement, feature edges, layers per tag. checkMesh quality tree with histograms and "show the bad cells". Live cell-count estimate. |
| Physics | Steady and transient incompressible flow, laminar / RANS (k-ω SST, k-ε, Spalart-Allmaras) / LES; energy as a passive scalar and as compressible or buoyant flow; conjugate heat transfer with fluid and solid regions; porous media; MRF. |
| Materials | Built-in constant and polynomial catalogue; CoolProp; Cantera for gas mixtures; REFPROP through CoolProp when the user has it installed. |
| Units | Full unit systems (SI, mm-based, imperial) and unit-checked expressions in every numeric field. |
| Boundary conditions | Engineer-facing types (velocity inlet, mass-flow inlet, pressure inlet/outlet, wall with thermal options, symmetry, cyclic, coupled interface) compiled per dialect. Persistent colour tint by assigned type. |
| Engines | OpenFOAM Foundation 14: `foamRun` (incompressibleFluid, fluid), `foamMultiRun` (CHT), and `coupledSimpleFoam` CPU (flow + passive energy) built from source by the helper; GPU variant flagged. Attach to the user's existing installation; no OpenFOAM shipped. |
| Monitors | Residuals, report definitions (surface and volume integrals, forces, mass and energy balances), probes, stopping criteria as tree objects. Live plots. |
| Post | Scenes with displayers (surfaces, slices, iso-surfaces, streamlines, vectors), probes, image and CSV export, "open in ParaView". |
| Execution | Local: WSL2 on Windows, native Linux. No limits on cells, cores or concurrent jobs. |
| Automation | Python API, Python console, journal recording and replay, `stokes` CLI with JSON output, local HTTP/JSON-RPC server with OpenAPI, headless server mode. |
| Design exploration | Parametric studies over any parameter with a design table, DOE sampling (Latin hypercube, Sobol, factorial designs), plots and export (ADR-0016); surrogates, sensitivity and optimisation with Pareto fronts follow, free until decided otherwise (decisions log A4). |

### v1 — paid features

| Feature | Why paid |
|---|---|
| Remote execution: SSH hosts (schedulers and containers follow in v1.5) | Professional infrastructure. |
| Fluent engine through PyFluent | Users with Fluent licences get Stokes' workflow on Fluent. |
| Embedded LLM assistant and the MCP server | Agent-driven workflows are the premium tier; the plain API and CLI stay free. |
| Reports (HTML, PDF via print) | Productivity for professional use. |
| Shape optimisation (ADR-0017): CAD-parametric shape studies, adjoint sensitivity maps, adjoint shape and topology optimisation through adjoint-capable engines | The premium optimisation tier; the incumbents sell these as add-ons. |

### v1.5

ESI OpenFOAM dialect; two-phase VOF with phase change (Lee model); radiation;
species transport; parametric sweeps and design tables (paid); SLURM/PBS and
container hosts (paid); report templates; existing-case import (reflect a
foreign OpenFOAM case into the tree).

### v2

Parametric-part generators (a fin-core generator ported from your finmesh
code, with no employer design data); shared material and BC libraries; review
links; macOS through a container host.

### Non-goals (for the foreseeable future)

- Sketch-based CAD modelling. Stokes imports, prepares and builds simple
  primitives; it does not replace the CAD system.
- Shipping OpenFOAM, a WSL image or any engine binaries.
- Native CAD formats (SLDPRT, PRT, CATPart) and commercial kernels or
  translators. OCCT only, ever.
- Browser or cloud delivery. Stokes is a native desktop application; customer
  CAD never leaves the machine unless the user points a runner at their own
  remote host.
- Writing our own general-purpose mesher or flow solver outside the existing
  coupled-solver line.
- Supporting foam-extend.

## What to take from whom

| Product | Take | Leave |
|---|---|---|
| **Shapr3D** | Adaptive context toolbar that offers the next sensible tool from the selection; command search; stationary corners (sidebars at the top corners, modes bottom-left, view islands bottom-right); orientation cube with unit, grid and appearance popovers; filled, high-contrast icons; one-click switch between work and visual modes. | Modelling-first scope; Parasolid dependency. |
| **ANSYS Fluent** | Outline tree with Setup / Solution / Results; Named Selections; report definitions with convergence conditions; expressions; the task-based watertight-geometry meshing workflow; journaling. | Hundreds of equal-weight panels, no hierarchy of importance. |
| **Siemens STAR-CCM+** | Continua / Regions / Boundaries separation; parts-based meshing as an operations pipeline; derived parts; scenes with displayers; stopping criteria as objects. | Java macros; tree depth that hides essentials. |
| **Engys HELYX** | The commercial precedent: a proprietary GUI over a GPL core. Multi-region workflow, parametric runs. | — |
| **SimFlow** | A clean linear beginner workflow. | Cell-count limits in the free tier (Stokes has none). |
| **SimScale** | Face selection by click, box and feature angle; the "assigned faces" list per boundary. | Transient highlight that hides what is assigned. |
| **ParaView** | The post idiom users know; "open in ParaView" as the escape hatch. | Pipeline complexity for routine results. |
| **Claude Code and MCP clients** | Tools with typed schemas, streaming progress, journals that replay; agents as first-class users. | — |
| **The earlier post-processor attempt (your code, 2026-06 to 08)** | Numbers in a GUI-free core shared by UI, REST, MCP and agent, validated against the solver to 5–6 digits; an LLM action whitelist with names snapped to real case objects and a deterministic executor; the variable × look gallery, tune drawer, `Ctrl K` palette and drag-on-legend range; a colormap-coloured streamline logo mark; stroke icons at 24 px. | Two UIs with two token sets; LOD point clouds beside a surface mode; `eval` on model text; silent `except` blocks; a 2 GB one-file executable; agent results as static PNGs instead of scene state; latency judged from benchmarks instead of the real UI path. Nothing from it is copied. |
| **fingen (your code)** | Stepper with status dots; live mesh estimate; compiled-case preview; persistent BC tinting; job state machine with `done_unconverged`; convergence by energy balance; run bundles; WSL staging on ext4 with heartbeat liveness; the OF14 multi-region case writer; the PyFluent driver; finmesh. All portable, PyQt5 imports become PySide6. | employer design data, the internal component library, study results. |

## Success metrics (targets)

| Metric | Target |
|---|---|
| Cold start to an interactive empty project | under 3 s |
| STEP import, 500 faces / 20,000 faces | under 2 s / under 20 s with progressive display |
| Picking latency at 5 M visible triangles | under 16 ms |
| Orbit at 10 M visible triangles on a discrete GPU / in reduced-quality mode on an integrated GPU | 60 fps / 30 fps |
| Boundary surfaces of a 10 M-cell mesh visible after meshing finishes | under 10 s |
| STEP to first converged steady run, user time | under 30 min |
| Model edit through a tool call (API, MCP, HTTP) | under 200 ms round trip |
| An LLM agent completes the tutorial through MCP unaided | yes |
| A new user from Fluent completes the tutorial unaided | 45 min |
