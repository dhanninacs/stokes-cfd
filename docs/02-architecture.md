# 02 · Architecture

*Revised 2026-10-02 after the decisions in docs/00. Decisions are recorded in
`adr/`; this document is the map.*

## 1. System overview

Stokes is one API with many clients, one agent, and engines it attaches to.

```
+------------------------------------------------------------------------------------------+
| STOKES DESKTOP  (proprietary · Python + C++ kernels · Windows / Linux)                    |
|                                                                                          |
|  clients   web front end (React + three.js) in the Qt/Chromium host or any browser        |
|            assistant panel | `stokes` CLI | MCP server | any JSON-RPC client              |
|                              \            |            /                                  |
|  server    FastAPI: WebSocket JSON-RPC over the API, events, binary geometry, web/dist    |
|  api       ONE Python API: typed commands, queries, events; tool schemas from node schemas |
|  model     project tree + JSON schema + units/expressions + commands + dependency graph   |
|  geometry  OCCT (via OCP): import, tessellate, primitives, booleans, prep, selections     |
|  native    C++ (nanobind): id-buffer picking, feature-angle growth, LOD                   |
|  mesh / physics / compile (dialects + Fluent writer) / engines / post / license / llm     |
|  run       runners: local-wsl · local-linux · ssh (paid) · scheduler, container (v1.5)   |
+------------------------------------------|-----------------------------------------------+
                                           | agent protocol (socket)
+------------------------------------------v-----------------------------------------------+
| STOKES AGENT  (proprietary · pure Python + VTK · runs where the engines run)              |
|  probe installs · build helper (in-house solvers from source) · staging · supervision     |
|  heartbeat · log parsing · VTK extraction (surfaces, slices, probes, reports) · cache     |
|        |  argv + files + stdout only (no linking, no headers)                             |
|        v                                                                                  |
|  USER-OWNED ENGINES (never shipped by Stokes)                                             |
|  OpenFOAM 14 (Foundation) · snappyHexMesh · cfMesh · blockMesh · Gmsh                     |
|  coupledSimpleFoam built from source at a pinned tag (+ CUDA variant when available)      |
|  ANSYS Fluent through PyFluent (paid feature; the user's own Fluent licence)              |
|  Hosts: WSL2 distro on Windows · native Linux · remote Linux over SSH (paid)              |
+------------------------------------------------------------------------------------------+
```

Why this shape:

- **One API, many clients** (ADR-0010). The UI is not special. Whatever a
  panel does, a tool call can do, so LLM agents, scripts and the CLI are
  first-class from day one.
- **The GPL boundary is a process boundary** (ADR-0001). Everything GPL is a
  child process exchanging files and text.
- **The data boundary is the agent** (ADR-0005). Time directories of a
  20 M-cell case never cross the WSL or SSH bridge; extracted surfaces and
  numbers do.
- **Engines are attached, not shipped** (ADR-0011). Stokes probes what the
  user has and builds the in-house solvers from source where needed.

On native Linux with a local engine the agent runs in-process through the
same API; on Windows it runs inside the WSL distro.

## 2. Package layout and responsibilities

| Package | Responsibility | Depends on |
|---|---|---|
| `stokes.model` | Node types, JSON schema, validation, commands with undo/redo, dependency graph, dirty flags. Pure Python. | units |
| `stokes.units` | Quantities, unit systems, display formatting, the expression engine (ADR-0012). | pint, lark |
| `stokes.api` | The one public surface: commands, queries, event subscriptions, journal recording. Stable and documented; tool schemas generated from node schemas. | model |
| `stokes.server` | FastAPI app: WebSocket JSON-RPC over the API with event push, token check on every call, binary geometry transport, static `web/dist`; `ServerThread` runs it in-process on a loopback port; `python -m stokes.server` is the headless mode. | api |
| `web/` (TypeScript) | The front end: React panels (tree, properties, output drawer, command palette, chrome) and the three.js viewport; a client of the API only (ADR-0013). Contract: `web/DESIGN.md`. | server (over WebSocket) |
| `stokes.mcp` | MCP server exposing API commands and queries as tools and resources (paid flag). | api |
| `stokes.cli` | `stokes` command: every API verb, JSON output, journal replay, project and run management. | api |
| `stokes.llm` | Assistant: provider abstraction (Anthropic, OpenAI, Gemini, OpenAI-compatible local servers), tool-use loop over the API, conversation storage, key storage in the OS keychain (paid flag). | api |
| `stokes.geometry` | OCCT import and healing, tessellation, persistent face identity, primitives and booleans, prep operations, named selections, STL export per selection. | model, OCP |
| `stokes.native` | C++ via nanobind: id-buffer picking, triangle adjacency, feature-angle growth, connected components, LOD decimation, spatial hashing. | — |
| `stokes.mesh` | Mesh model nodes, drivers (snappyHexMesh, cfMesh, blockMesh, Gmsh), cell-count estimator, quality mapping from checkMesh. | model, compile |
| `stokes.physics` | Catalogue of models, materials (built-in, CoolProp, Cantera, REFPROP-via-CoolProp), boundary-condition types with compatibility rules. Declarative tables. | model, units |
| `stokes.compile` | Dictionary AST, printer, parser; case writers per dialect (`of14`, later `esi`); the Fluent writer that emits PyFluent settings calls; override application; compiled-case preview. | model, physics, mesh |
| `stokes.engines` | Engine definitions: capability predicate over the model, dictionary additions, log parsers, validation cases, build recipes. | compile |
| `stokes.run` | Runners (local-wsl, local-linux, ssh; scheduler and container later), job state machine, queue, monitors, agent client. | engines, agent protocol |
| `stokes.post` | Server-side extraction with VTK (surfaces, slices, iso, streamlines, probes) packed for the viewport; reports (paid), run comparison (paid), exports. | run, VTK |
| `stokes.app` | The desktop host: frameless Qt window with QtWebEngine, QWebChannel bridge (move, resize, window buttons, native file and folder choosers, text files read or written only at paths the user picked in those choosers this session, 1 MB cap), in-process server start-up in the workspace's theme, screenshot capture for reviews. | server, PySide6 |
| `stokes.license` | Licence file parsing and Ed25519 verification, feature flags; the free tier runs with no licence file. | cryptography |
| `agent` (separate distribution) | `stokes-agent`: install probing, build helper, staging, supervision, log parsing, VTK extraction, artifact cache. Pure Python + VTK + numpy. | VTK |
| `engine-helper` | Build recipes executed by the agent: clone pinned tags of the in-house solvers, resolve PETSc and Eigen, run `Allwmake` against the detected OpenFOAM, record versions. | — |

Rules:

- **Nothing above `stokes.api` imports Qt, VTK or OCCT** except `viewport`,
  `geometry` and `ui`. The model, units, compile, engines and run packages
  are headless and testable without a display.
- **Every user-visible action is an API command.** A panel that needs a new
  action adds it to the API first; the UI, CLI, MCP and HTTP get it for free.

## 3. The API and its clients (ADR-0010)

- Commands are typed objects (`geometry.import`, `selection.grow`,
  `mesh.generate`, `run.start`, `post.slice`) with JSON schemas derived from
  the node schemas; queries return JSON; events stream progress and state.
- **Tool generation**: the MCP tool list and the OpenAPI document are
  generated from the same command registry, so they never drift from the UI.
- **Agents have full control** (L3). No confirmation prompts; an optional
  "confirm destructive actions" setting exists and is off by default.
- **Headless mode**: `stokes serve --project x.stokes` runs the API without a
  window; the agent and engines work exactly as from the UI.
- **Assistant panel**: a chat client of the same API inside the app, with a
  provider picker (Anthropic, OpenAI, Gemini, local OpenAI-compatible) and
  tool calls shown as undoable actions in the tree.
- **Journals**: every command is appended to a journal; a journal replays
  through the API, which makes tutorials, regression tests and agent
  demonstrations the same artifact.
- **Session extensions**: a subsystem keeps its per-session service (geometry
  meshes, design tables, the workspace) in `Session.extensions` and follows the project
  through optional `project_opened`, `project_saved` and `project_closed`
  hooks, which `project.new`, `.open`, `.save` and `.close` call after the
  state has changed and before they emit their event; a hook that fails is
  logged and never fails the command.

## 3b. The workspace layer (ADR-0015)

The interface's own arrangement is data: a versioned JSON *workspace*
(theme or custom token set, slot layout and panel placement, island corners,
top-bar items, tree organisation and stage mapping, shortcuts, viewport
defaults, default engine). `stokes.workspace` holds the schema, defaults,
shipped presets, merge of user and project documents, validation and
persistence (`~/.stokes/workspace.json`, overridden by the project's
`<name>.stokes/workspace.json`); `workspace.*` commands expose it and
`workspace.changed` events announce changes. In the front end,
`web/src/workspace/` keeps a registry of panels, islands and top-bar items
and a slot layout engine that places them according to the document;
Customise mode edits the document through the same commands the CLI, MCP and
the assistant use. The Stokes preset is the reviewed design; the layer makes
every other arrangement possible without conditionals in the core UI.

## 4. The project model

### Tree

A typed, addressable document. Every node has a `type`, a stable `id`, a
`path` (`/physics/fluid1/turbulence`), parameters validated against the node
schema, and references to other nodes by `id`. The full tree is in docs/03 §4.

### Quantities and expressions (ADR-0012)

Every numeric parameter is a quantity with a dimension. Fields accept a value
with a unit (`25 mm`, `40 degC`, `2 L/min`) or an expression over named
quantities (`inlet.velocity * 1.5`, `0.5 * rho * U_in**2`). Expressions are
parsed by our own grammar, evaluated with unit checking, and stored as text
plus the evaluated SI value. The display unit system is a project setting.

### Commands and undo

All mutations go through `Command` objects that record their inverse. The
UI, the API, the CLI, the MCP server and journal playback all produce
commands, so undo is uniform and journals are command logs.

### Dependency graph and staleness

Each node declares what it derives from. Changing a named selection marks
the mesh, the regions and the runs that used it *stale*; the tree shows a
status dot. Compile refuses to run with unresolved staleness unless the
caller acknowledges it.

### Overrides

A recorded command on a generated dictionary path, applied last by the
compiler and shown in the compiled-case preview with a marker. Loose edits of
generated files are detected by hash and offered for import as overrides.

### Persistence (ADR-0006)

`<name>.stokes/` with `project.json`, `geometry/`, `runs/<run-id>/`, `cache/`
and a derivable SQLite index.

## 5. Threading and responsiveness

| Work | Where | Budget |
|---|---|---|
| Painting, input, tree, inspector, QML panels | Qt main thread | 16 ms per frame |
| Tessellation, STEP parsing, selection growth, LOD | `QThreadPool` workers (OCCT and `native` release the GIL) | cancellable, progress every 100 ms |
| Rendering | VTK on the main thread, LOD actors during interaction | 60 fps on a discrete GPU; 30 fps in reduced-quality mode on integrated graphics |
| API server, MCP server, assistant | asyncio loop on a separate thread, marshalled onto the main thread per command | under 200 ms per model edit |
| Meshing, solving, extraction, builds | agent process (local or remote) | streamed progress, cancel within 1 s |
| Log parsing, residual streams | agent | 10 Hz, coalesced |

Reduced-quality mode (B6): LOD always on, no edge rendering, one light,
lower-resolution picking buffer, surface-only mesh display. Chosen
automatically on integrated GPUs, switchable in the view island.

## 6. The main data flow

```
STEP / primitives ──> Parts (B-rep) ──tessellate──> display meshes (+ face ids)
                         │
                 prep operations (fluid extraction, enclosure, split, defeature, booleans)
                         │
                 Named Selections (faces, bodies) ──export──> per-selection STL
                         │                                         │
                 Mesh model ──compile──> snappyHexMeshDict | cfMesh dict | blockMeshDict | .geo
                         │                                         │
                 Regions + Boundaries + Physics ──compile──> OF14 case tree  |  PyFluent script
                         │                                         │
                 Run ──stage──> agent host local disk ──launch──> mesh tools, splitMeshRegions,
                         │                                        decomposePar, foamRun |
                         │                                        foamMultiRun | coupledSimpleFoam |
                         │                                        Fluent session (PyFluent)
                 Monitors <──stream── agent: log lines, residuals, report values, heartbeat
                         │
                 Results <──artifacts── agent: surfaces, slices, iso, streamlines, probes
                         │
                 Scenes, plots, reports (paid), run comparison (paid)
```

The Fluent path: the mesh comes either from Fluent Meshing driven through
PyFluent (watertight workflow from the exported STL) or from the OpenFOAM
mesh converted with `foamMeshToFluent`; setup is emitted as PyFluent settings
calls; results are read through PyFluent field data or an EnSight export that
VTK reads.

## 7. Performance budgets

| Operation | Target | Mechanism |
|---|---|---|
| App start | < 3 s | Nuitka-compiled, lazy imports, no network at start |
| STEP import 20 k faces | < 20 s, first parts visible < 2 s | progressive tessellation per body, workers |
| Face pick at 5 M triangles | < 16 ms | id-buffer picking in `native` |
| Feature-angle growth over 20 M triangles | < 300 ms | C++ adjacency and BFS, precomputed on import |
| Orbit at 10 M triangles | 60 fps discrete / 30 fps reduced-quality | LOD actors, one draw call per part |
| Boundary surfaces of a 10 M-cell mesh | < 10 s after meshing | agent extracts on the engine host |
| Residual plot update | 10 Hz, no stutter | agent coalesces, pyqtgraph ring buffers |
| Compile a case | < 1 s | pure dictionary generation, cached |
| API command round trip (edit) | < 200 ms | in-process API; marshalled to the main thread |

Enforced by benchmarks in `tests/perf/` in CI on a fixed dataset.

## 8. Error handling and observability

- Structured logging (JSON lines) in the desktop and the agent.
- Every run bundle keeps the model snapshot, compiled case or script, engine
  versions, full logs and timings.
- Typed, actionable errors (`MeshFailed`, `EngineMissing`,
  `LicenceFeatureMissing`, `UnitMismatch`), never a traceback in a dialog;
  the same error objects are returned to API, CLI and MCP callers.
- A "support bundle" button zips logs and the model (never the CAD unless
  ticked).

## 9. Security and privacy

- Offline by default; network calls only for user-initiated licence
  activation, the optional updater, and LLM providers the user configured.
- LLM provider keys live in the OS keychain; local OpenAI-compatible servers
  need none.
- The HTTP, MCP and agent servers bind to localhost or a Unix socket; remote
  only through an SSH tunnel; a per-session token is required.
- Remote runners use the user's SSH keys; Stokes stores no passwords.
- Licence verification is Ed25519 signature checking of a licence file
  against an embedded public key; the free tier needs no file.

## 10. Decision register

| ADR | Decision | Status |
|---|---|---|
| 0001 | GPL engines are separate processes; no linking, no headers, own dictionary code | accepted |
| 0002 | PySide6 shell + QML panels + VTK viewport; no GPL-only Qt modules | superseded by 0013 |
| 0003 | OCCT through OCP, and only OCCT | accepted |
| 0004 | VTK renderer, C++ id-buffer picking, LOD, reduced-quality mode | superseded by 0013 |
| 0005 | Desktop–agent split with one host-agnostic protocol | accepted |
| 0006 | `.stokes` directory, JSON tree, immutable run bundles, overrides as commands | accepted |
| 0007 | snappyHexMesh, cfMesh, blockMesh and Gmsh-as-a-process in v1 | accepted |
| 0008 | Foundation 14 dialect first; ESI in v1.5 | accepted |
| 0009 | Nuitka, signing, Ed25519 licence files with freemium flags; nothing shipped for engines | accepted |
| 0010 | One API behind UI, CLI, HTTP and MCP; assistant with pluggable providers; full agent control | accepted |
| 0011 | Attach to existing OpenFOAM; build in-house solvers from source; Fluent via PyFluent (paid) | accepted |
| 0012 | SI stored, full unit systems, unit-checked expressions everywhere | accepted |
| 0013 | Web front end (React + three.js) in a frameless QtWebEngine window over a WebSocket JSON-RPC server | accepted |
| 0014 | STEP import through OCP in a worker process; `part` nodes; mesh transport v2 with face ids; mesh cache | accepted |
| 0015 | The workspace: panel placement, islands, top bar, tree, themes, shortcuts and default engine are user data with presets, edited in the UI and through the API | accepted |
| 0016 | Studies as nodes: sweeps, DOE, surrogates, sensitivity, optimisation with Pareto fronts in `stokes.explore`; a `function` engine before real runs | accepted |
| 0017 | Shape optimisation is premium: CAD-parametric with every engine first; adjoint shape and topology optimisation and sensitivity maps through adjoint-capable engines (ESI `adjointOptimisationFoam`, Fluent) | accepted |
| 0018 | Materials: `material` nodes with `property` children and a Fluent-parity method catalogue; library materials fitted from CoolProp, Cantera and REFPROP (the library is the source of truth, the fit a cited, regenerable artefact); compile to Foundation 14's precompiled thermoTypes, tables or runtime-compiled forms; `material.*` commands and a Materials panel | accepted |

## 11. Reuse candidates from your own code

You own fingen, finmesh and the solver work (confirmed 2026-10-02). Code may
be ported; employer designs, the internal component library and study data may not.

| Source | Reuse as | Notes |
|---|---|---|
| `fingen/webapp/backend/wslbridge.py` | `stokes.run` WSL runner and the agent's staging rules | Measured facts: `\\wsl.localhost` staging on ext4, heartbeat liveness, no cross-boundary pids |
| `fingen/webapp/backend/jobs.py` | job state machine | add `extracting`, `cancelled`; keep `done_unconverged` |
| `fingen/webapp/backend/case_openfoam.py`, `scripts/of_coupled_cht_case.py` | `stokes.compile.of14` multi-region writer, coupled engine dictionary additions | strip design-specific defaults |
| `fingen/webapp/backend/results.py`, `energy_terms.py` | balance-based convergence and report KPIs | the pump-work caveat becomes a documented option |
| `fingen/webapp/backend/estimate.py` | live cell-count estimator | generalise from fin cores to snappy refinement levels |
| `fingen/webapp/backend/nonortho.py`, `surfaces.py`, `named_surface_stl.py` | quality mapping, per-selection STL export | — |
| `fingen/webapp/backend/fluentbridge.py`, `solver_fluent.py`, `meshing_fluent.py`, `fluent_import.py` | the Fluent engine (PyFluent driver, mesh import) | paid feature; licence-seat semaphore idea kept |
| `fingen/finmesh/` | a v2 parametric-part generator | port without the internal component vocabulary or design JSONs |
| `fingen/fingen_cadmesh.py` (PyQt5 + pythonocc) | nothing directly | PyQt5 and pythonocc are replaced by PySide6 and OCP; ideas only |
| coupledSimpleFoam repositories | the engine build recipe and validation cases | GPL stays in its own repos; Stokes clones tags |

Rule for every ported file: new licence header, no employer identifiers in
names, fixtures or comments, and a test that does not depend on any employer
dataset.

## 12. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| QML panels beside a native VTK widget (QQuickWidget compositing, focus, HiDPI) | UI jank or platform bugs | Prototype the shell in the first week of M0 on Windows and Linux; fall back to Widgets for any panel that misbehaves |
| User OpenFOAM installs vary (version, packaging, environment scripts) | Probing fails or compiles wrong dictionaries | v1 supports Foundation 14 only and says so; the probe reports exact findings; the build helper logs everything |
| cfMesh compatibility with Foundation 14 | A promised mesher does not build | Verify in M2; if it needs the ESI line, ship it with the ESI dialect in v1.5 and say so in the UI |
| OCCT STEP import on real-world CAD | Users blocked at step one | Healing pipeline, STL fallback, collect ten public STEP files for M1 tests |
| PyFluent API churn across Fluent releases | Fluent engine breaks | Pin a PyFluent version per Stokes release; support Fluent 2024 R2 SP05 and later as PyFluent states |
| LLM tool misuse with full control | An agent deletes or launches at scale | Journals make everything replayable and undoable; the off-by-default confirmation setting exists for cautious users |
| Persistent face identity across re-imports | Named selections break when CAD changes | STEP names, geometric signatures, user-confirmed remap |
| 9P / UNC bandwidth to WSL (~21 MB/s measured) | Big meshes take minutes to copy | Only STL crosses; mesh and results stay on ext4; the agent sends compact artifacts |
| snappyHexMesh layers and leaks | Users lose trust | Pre-flight checks, quality tree with "show bad cells", presets tested on the benchmark set |
| Part-time effort (evenings and weekends) | Calendar time stretches | Demo-first ordering in docs/06; defer Fluent, cfMesh and Gmsh if the demo slips |
