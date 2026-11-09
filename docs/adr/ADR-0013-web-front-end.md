# ADR-0013 · Web front end in a bundled Chromium window; three.js viewport

**Status:** accepted · 2026-10-02 · supersedes ADR-0002 (UI framework) and ADR-0004 (viewport renderer)

## Context

The first shell built on Qt Widgets with a VTK viewport worked but looked
like a Qt application, not like Shapr3D, Onshape or SimScale. The owner asked
for a modern, properly rendered interface and proposed three.js. The product
decisions that remain untouched: one Python API behind every client
(ADR-0010), licence-clean dependencies, engines as separate processes, and a
native desktop window on Windows and Linux.

## Decision

1. **The front end is a web application**: TypeScript, React 19, Vite,
   Tailwind 4, zustand, lucide icons, uPlot for live plots, three.js for the
   viewport. It lives in `web/` and is built to `web/dist`.
2. **The front end is a client of the API only.** It talks JSON-RPC over a
   WebSocket to `stokes.server` (FastAPI + uvicorn, in-process with the
   session) and fetches geometry as packed binary buffers. It holds no model
   state; every edit is an API command and arrives back as an event.
3. **The desktop host is a frameless Qt window with QtWebEngine** (bundled
   Chromium from PySide6, LGPL), so rendering is identical on Windows and
   Linux and the window integrates with the OS. A QWebChannel bridge gives
   the page window move, resize, minimise, maximise and close. The same page
   runs in any browser against `python -m stokes.server`, which is the
   headless free tier of ADR-0010.
4. **Viewport**: three.js renders interactive-size data (CAD tessellations
   and mesh surfaces up to a few million triangles) with environment-lit PBR,
   render-on-demand and a capped pixel ratio. For larger data the plan is
   hybrid: the agent extracts and decimates with VTK, and above a size
   threshold renders server-side and streams frames (the ParaView-web model).
   VTK therefore moves to the agent and post-processing side; it is no longer
   a desktop dependency.
5. **Design contract**: `web/DESIGN.md` is the specification reviewers check
   against; `docs/03-ui-design.md` stays the product-level description.
6. **Tooling**: Node 24 and npm are development dependencies only; the
   shipped product contains the static bundle. JavaScript dependencies go
   through the same licence allow-list as Python ones.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Keep Qt Widgets and invest in styling | A large design-system effort for a look that still reads as desktop toolkit; the owner rejected the result. |
| Qt Quick (QML) panels beside VTK | Better than Widgets, but still a niche stack with weak tooling and no ecosystem of modern components. |
| Electron or Tauri as the host | A second runtime and packaging story; QtWebEngine is already in the audited Qt dependency and keeps Python as the host. |
| pywebview on the OS webview | Lighter, but WebKitGTK on Linux renders differently and lags WebGL/WebGPU; identical rendering matters for a product. |
| A browser-only product | Loses native window integration, file dialogs and the offline desktop story the decisions require. |

## Consequences

- Two languages in the repository: Python for everything that computes and
  TypeScript for everything that is seen. The gates grow a JavaScript licence
  audit and the IP gate scans `web/src`.
- The API-first rule now has teeth: the UI cannot reach the model except
  through commands, which also makes every UI action available to MCP and
  the CLI by construction.
- Large-data rendering needs the hybrid path before M2 meshes arrive; the
  budget table in docs/02 gains a "triangles rendered client-side" line.
- The milestone M0 step 5 is redefined: shell prototype = this web front end
  in the Qt host, reviewed against `web/DESIGN.md`.
