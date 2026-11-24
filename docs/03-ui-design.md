# 03 · UI design

*Revised 2026-10-02 for ADR-0013: a web front end (React + three.js) in a
frameless Chromium window. `web/DESIGN.md` is the implementation contract
reviewers check against; this document is the product-level description.*

## 1. Principles

Taken from Shapr3D, Onshape and SimScale (sources in docs/01):

- **The viewport is the page.** Panels float over a full-bleed 3D view as
  glass cards with a 12 px inset; nothing is docked to an edge except the top
  bar and the output drawer.
- **Stationary corners, adaptive middle.** Modes bottom-left, view controls
  and the view cube bottom-right, the simulation tree top-left, properties
  top-right; a context toolbar appears next to the selection.
- **One accent.** Teal for selection, active state, primary action and focus;
  categorical colours only for patch types (inlet red, outlet blue) and
  status (green, amber, red).
- **Keyboard-first.** `Ctrl K` commands, `1`–`6` stages, `F` fit, `Ctrl Z/Y`
  undo and redo, arrows in the tree, Enter to edit, Esc to clear.
- **Four themes**: dark (the reference), light, Cupertino, a macOS-inspired
  light theme with vibrancy panels, an Apple-blue accent and a segmented stage
  control, and Ubuntu, the Yaru look with an orange accent and Ubuntu Sans.
  `auto` follows the OS between dark and light; `cupertino-auto` and
  `ubuntu-auto` follow it inside their family.
- **Every UI action is an API command**; the console shows them as they
  happen, and the same commands are what MCP and the CLI expose.

## 2. Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ◈ STOKES / project ●   [Geometry][Mesh][Physics][Solve][Results]   ⌕ ⌘K ☼ ▶ Run  – ▢ ✕ │
├──────────────────────────────────────────────────────────────────────────────┤
│ ┌ SIMULATION ──┐                                             ┌ PARAMETER ──┐ │
│ │ ⌕ filter     │                                             │ name  ●     │ │
│ │ ▾ geometry   │           viewport (three.js)               │ Dimension   │ │
│ │ ▾ physics    │        ┌ Assign · Isolate · Hide ┐          │ Value   ƒ   │ │
│ │   parameters │        └────── context ──────────┘          │ = 1996 Pa   │ │
│ └──────────────┘                                             └─────────────┘ │
│ [Section][Isolate][Measure][Mesh ▾]                     [⊡][⟂][⚙]  view cube │
├──────────────────────────────────────────────────────────────────────────────┤
│ Console · Log · Residuals · Reports                                        ▴ │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Region | Contents |
|---|---|
| Top bar (48 px, drag region) | logo and wordmark, project menu (new, open, import STEP, save; native file and folder dialogs when hosted), connection dot, units, engine and licence badges, stage chips, command palette button, appearance toggle (dark ↔ light inside the chosen style, Stokes, Cupertino or Ubuntu), Run, window controls when hosted (traffic lights at the left in Cupertino) |
| Tree panel (left, resizable) | filter, tree with type icons, display-unit values and expressions, status dots, hover `⋯` menu, keyboard navigation |
| Properties panel (right, resizable) | type and editable name, path, status; schema-generated fields with unit chips and expression evaluation; patch card with Assign condition, Hide, Isolate; a part's source, units, triangles, faces and size as read-only text; empty-state hints |
| Value panel (ADR-0022 §7.1) | the one form for assigning a condition, opened from Assign condition: a 360 px card with the condition kinds grouped by family (a colour dot each; an unavailable kind is disabled and names the milestone that brings it), the boundary's defaulted unique name, the target line, and the kind's parameters in the shared `SchemaFields`; Enter assigns in one undo step, Esc cancels |
| Viewport | PBR parts under an environment light, fading ground grid, hover lift, accent outline on selection, animated fit, view modes, hidden and isolated patches, view cube; imported parts (one mesh per solid, the face under the pointer tinted on hover) and a progress line on the top edge while a STEP file imports |
| Context toolbar | Assign condition (opens the value panel; Show boundary once the patch has one), Isolate, Hide, Rename; it sits above the selected patch's projected bounds, never on the patch (below the bounds when there is no room above) |
| Islands | bottom-left modes (Section, Isolate, Measure, Mesh view mode); bottom-right Fit, Perspective/Ortho, Display settings |
| Output drawer | Console with a command line (history, autocomplete, collapsible JSON), Log (events), Residuals (uPlot, log scale with one grid line per decade, a legend of line swatches above the plot; demo traces until a run exists), Reports; at the right end of the strip, the set-up status readout (a state dot and one line, ADR-0022 §11.1) that lists the findings when clicked |
| Command palette | fuzzy search over every API command and the window's own actions (each with its current chord), "Customise workspace", "Reset workspace" and "Move <panel> to <place>"; recents; schema-generated parameter form |
| Toasts | bottom centre, results and errors, undo action on deletes |

**Pick modes** (ADR-0021 3.1; Edge and Point by ADR-0022 §15.4, enabled in T-060): the Modes island's
pull-up offers **Body**, **Face**, **Edge** and **Point**. Body and Face pick tessellated bodies and
B-rep faces; Edge and Point pick B-rep topology, which is fetched lazily for the visible parts while
one of those modes is on (ADR-0022 §15.2; at most three parts at a time, the hovered one first,
released when the mode is left). A mesh-only part has no B-rep, so it has no edges or points to pick
and says so rather than inventing ids. In Edge mode a click that meets no edge picks nothing rather
than the face behind it; the picked edges and vertices are drawn as one accent line object and one
marker object each for hover and for the pick.

Measure (T-098, T-113) targets an element: a **face**, an **edge** or a **vertex**. Two parallel
faces report their normal gap, two non-parallel faces report that they meet, and edges and vertices
use a closest approach.

Keyboard: `1`–`6` stage, `F` fit, `O` projection, `I` isolate, `H` hide,
`V` view mode, `Esc` clear, `Ctrl K` palette, `Ctrl Z`/`Ctrl Y` undo and redo,
`Ctrl S`/`Ctrl Shift S`/`Ctrl O`/`Ctrl I` project; in the tree, arrows navigate, `F2`
renames, `Delete` deletes with an Undo toast. The full list is in
`web/DESIGN.md` §3 "Keyboard".

### Layout presets (ADR-0015)

The arrangement above is the *Stokes* preset. Users rearrange it in Customise
mode (`Ctrl Shift C`) or pick a shipped preset in Display settings → Layout;
presets change the arrangement only and keep the theme, shortcuts and viewport.
Users save their own and share them as JSON. Details: `web/DESIGN.md` §5 and
`docs/engineering/workspace-schema.md`.

| Preset | For users of | What it arranges |
|---|---|---|
| Stokes | this design | Tree left, properties right, output drawer folded, stage chips in the top bar. |
| Classic | STAR-CCM+ | Tree with properties beneath it on the left, output on the right, modes top left. |
| Ribbon | Fluent | The stages as a ribbon with a task strip under the top bar, tree left, properties right, output open. |
| Workflow | SimScale | One tree in setup order with one level open, no stage control. |
| Minimal | anyone who wants the viewport | The viewport, the palette and a floating properties card; the tree, the modes and most of the top bar hidden. |

## 3. Workflow stages (unchanged in substance from the first draft)

Geometry (import, prep, tags) → Mesh (mesh model, estimate,
quality) → Physics (continua, materials, regions and boundaries, compiled
case preview) → Solve (engine, numerics, controls, stopping criteria, live
monitors) → Results (scenes, derived parts, reports, compare) → Explore
(studies and design exploration, ADR-0016). The stage
chips switch the tool palette and the default panel contents; every stage is
reachable at any time and status dots show what is stale. The chips and the
tree navigate each other: choosing a stage (a chip, or `1`–`6`) opens its
groups (Geometry → /geometry, Mesh → /mesh, Physics → /physics and /regions,
Solve → /solver, /monitors and /runs, Results → /scenes and /runs, Explore →
/studies), folds the
other top-level groups and scrolls the first of its own into view, and leaves
what was opened or folded below the top level as the user left it; the tree is
never reduced to the stage, and the load opens it to `tree.defaultExpanded`
levels (two, as before, unless the workspace says otherwise).

### 3.1 The guided-meshing stepper (the Mesh stage)

The Mesh stage's Details pane always shows the nine guided tasks at the top — **the one-line
collapsed form in the pane's fixed header, the full stepper as the first block of the scrolling
body** — so a long stepper never pushes the page off-screen and a wheel at the end of the pane
never chains to the tree beside it (the independent-scroll rule, 1430). The tasks come from the
`mesh.tasks` command and every Mesh-stage page reads the same answer; the reply is re-asked when
the tree or the mesh changes, debounced 300 ms, and never polled while idle. A task row (in the
stepper or under the Mesh section of the tree, for the tasks without a node of their own) opens
the task's page; `Alt+↑/↓` moves between tasks. `preferences.mesh.stepper` ("shown", default |
"hidden") hides the full block — never the line, which is what "always shown at the top" means
(ASK Q2). The first three task pages act: Geometry check (the precheck's findings, each with its
Fix), What each body is (one `mesh.body_role` call per choice, one undo step for a
multi-selection), Boundaries (the patches the mesh will have, and "Keep as a wall" for the faces
without one). Task 4 (Sizing), task 5 (Surface), task 6 (Boundary layers) and task 8 (Quality)
have their own pages; tasks 7 and 9 show the Mesh panel's own sections until their packages
land.

### 3.1.1 The Quality page (task 8)

"How good the cells are, where the bad ones are, and what to change." The verdict line keeps the
Mesh panel's words; the distributions arrive AFTER the result is done (the M-506 follow-up), and
the page draws the three states: **pending** ("Working out the distributions…" — the verdict and
statistics are ALREADY shown from checkMesh), **ready** (the three histograms: non-orthogonality
in degrees, skewness, aspect ratio on a log axis, each with the project's limit as a vertical
rule labelled "limit 65°" and the bars past it in the warning token), **error** (checkMesh's
numbers only, with the reason). Statistics carries the counts and the seven cell types (zero rows
hidden). Clicking a bar lights its faces in the viewport (the viewer half, with the faces route);
the worst-faces list fits the camera. No suggestions section until M-510.

## 4. The tree

STAR-CCM+ structure with Fluent names (decision H3): Geometry (Parts,
Operations, Named Selections) · Mesh · Physics (continua, Materials,
Parameters) · Regions (Boundaries, Interfaces) · Solver (Engine, Numerics,
Controls, Initialization, Stopping Criteria) · Monitors & Reports · Scenes &
Plots · Runs. Selecting a node in the tree moves the stage chips to the stage
whose groups contain the node's path (under /runs the current stage stays when
it already owns /runs) and leaves the tree as it is. Picking a patch in the
viewport reveals its tree row but never moves the stage: viewport picks are
cross-stage work, such as assigning a boundary while in Physics.

Many parts (ADR-0019): rows are 28 px and only the ones in view, plus a few either
side, are in the DOM; the selected row, the one being renamed and the one whose
menu is open stay drawn wherever they are, and every row carries `aria-posinset`
and `aria-setsize`. The tree is fetched once and kept current from the node events,
which carry the node summaries, applied in one update per animation frame.

## 5. Visual tokens

Defined once in `web/src/styles.css`: Inter at 13 px; 8 px spacing unit;
12 px panel radius, 8 px inputs, pills for chips; glass panels
(`backdrop-filter` blur, 1 px border, soft shadow); dark background `#0b0d10`
with a radial viewport gradient, light `#eef1f5`, Cupertino `#e8e8ed`
(light) and `#1c1c1e` (dark), Ubuntu `#fafafa` (light) and `#2c2c2c` (dark);
accent `#19c0a8` (dark) / `#0f9d8a` (light) /
`#0071e3` and `#0a84ff` (Cupertino light and dark) / `#e95420` (Ubuntu both
appearances); motion
160 ms ease-out with `prefers-reduced-motion` respected. Full token values
and the Cupertino and Ubuntu rules are in `web/DESIGN.md` §2.

## 6. Technology (ADR-0013)

React 19 + TypeScript + Vite + Tailwind 4 + zustand + lucide + uPlot;
three.js for the viewport; QtWebEngine host with a QWebChannel bridge for the
frameless window; FastAPI WebSocket JSON-RPC to the Python API. Build with
`npm run build`; review captures with `python -m stokes.app --screenshot`.

## 7. Lessons carried over from fingen

1. A stepper with status dots makes a non-linear workflow legible.
2. Show the cost before the click: live cell-count estimates.
3. Show the compiled case before the run.
4. Tint assigned boundaries persistently; list what is unassigned.
5. `done_unconverged` is a real state; show it in amber everywhere.
6. Judge convergence by balances and report stationarity, not residuals.
7. Stream logs, do not poll; heartbeats across the WSL boundary.
8. Keep runs immutable and reproducible.
9. Protect scarce resources (Fluent seats, GPUs) with explicit semaphores.
