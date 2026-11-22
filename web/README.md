# web — the Stokes front end

React + TypeScript + three.js, built with Vite, served by `stokes.server` from
`web/dist` and shown in the Qt/Chromium host (`python -m stokes.app`) or any
browser. It is a client of the Stokes API over WebSocket JSON-RPC; it holds
no model state of its own (CLAUDE.md §1 rule 4).

```bash
cd web
npm install                 # once
npm run build               # -> dist/, what the server serves
npm run typecheck           # tsc only
npm run dev                 # Vite on 127.0.0.1:5173 with hot reload
```

Run against a live server:

```bash
# in another shell: start the API server (prints a URL with its token)
python -m stokes.server --port 8740
# development page (hot reload), pass the API origin and token:
#   http://127.0.0.1:5173/?api=http://127.0.0.1:8740&token=<token>
# the desktop window, loading the Vite dev server instead of dist/:
python -m stokes.app --dev
# screenshots for review (the Qt host grabs the page after it settles; Chromium needs
# 15-20 s to start on a slow laptop, so give it 20000 ms).
# Themes: dark, light, cupertino, cupertino-dark; auto and cupertino-auto follow the OS.
python -m stokes.app --theme cupertino --screenshot shot-cupertino.png --exit-after 20000
# A capture of something other than the opening state: --setup runs a JavaScript file in the page
# once it loads, driving the stores. The app adds ?drive=1 for that run, which is what exposes
# window.__stokes; examples live in .mailbox/shots/.
python -m stokes.app --screenshot prepare.png \
  --setup ../.mailbox/shots/prepare.js --exit-after 25000
```

Design contract: `DESIGN.md`. Folder map:

```
src/api        WebSocket client, API types
src/state      zustand store (the only client-side state); parts.ts: part nodes, mesh revisions,
               import progress and patch names as pure functions over the tree and geometry.* events
src/viewport   three.js: scene.ts (manager: sources, picking), faces.ts (the tint on the face under the
               pointer), camera.ts (rig, animated fit, ortho),
               environment.ts (studio lighting), materials.ts, grid.ts (fading shader grid), shadow.ts
               (contact shadow), outline.ts (full-resolution selection outline), gizmo.ts (view cube),
               theme.ts (palette from CSS tokens), geometry.ts (binary parser, transport v1 and v2),
               parts.ts (PartsController: loads the sample or every part node's mesh by id),
               ImportProgressLine.tsx and viewport.css, Viewport.tsx
src/panels     tree, properties, output drawer, command palette
src/app        top bar, islands, toasts, context toolbar, Qt host bridge
src/components small shared UI pieces
src/util       formatting helpers
public/        served as is at the site root: favicon.png, the app icon artwork (a copy of
               stokes/app/assets/stokes.png) that the page links and the top-bar logo mark shows
```

## GUI walks and the walkthrough recording

The `walk_*.mjs` scripts drive the app through a real browser (Playwright) against a running server
and print every problem they see: page errors, failed requests, alerts, a card that stays open. Each
makes its own NEW projects in the folder it is given and writes a screenshot per action to
`../.local/walk/<name>/`.

```bash
# a server with a home of its own, so the walks do not touch your recent list or layout
STOKES_HOME=../.local/walk-home python -m stokes.server --port 8777      # prints the URL with its token

cd web
node walk_inventory_gui.mjs     "<url>" "<projects folder>"   # every card's controls
node walk_build_gui.mjs         "<url>" "<projects folder>"   # every sketch and build tool, about 35 minutes
node walk_lattice_gui.mjs       "<url>" "<projects folder>"   # the Lattice tool
node walk_fast.mjs              "<url>" "<projects folder>"   # Fast mode: the panel, the plane, edits while it runs (needs a GPU)
node walk_flow_gui.mjs          "<url>" "<projects folder>"   # sketch > fluid > boundaries > mesh > run > results
node walk_optimise_step_gui.mjs "<url>" "<projects folder>"   # the Optimise step in both modes
```

Three environment variables: `STOKES_WALK_CHANNEL=chrome` (or `msedge`) drives an installed browser
where Playwright's own has not been downloaded; `STOKES_WALK_SCHEME=dark` asks for the dark
appearance; `STOKES_WALK_VIDEO=<folder>` records the page there as an .mp4, with an `ffmpeg` on the
PATH (or `STOKES_WALK_FFMPEG`).

`record_bend_walkthrough.mjs` is the recorded walkthrough: a 90 degree duct bend from a sketch with
two named dimensions to an optimised shape (gradient check, cycles, Apply, the final solve), with a
caption per step. It writes `marks.json` beside its screenshots; `cut_walkthrough.mjs` then runs the
waits fast:

```bash
STOKES_WALK_CHANNEL=chrome STOKES_WALK_SCHEME=dark STOKES_WALK_VIDEO=out/video \
  node record_bend_walkthrough.mjs "<url>" "<projects folder>" out --cycles=5 --speed=0.3
node cut_walkthrough.mjs out/video/walk-<n>.mp4 out/marks.json bend-walkthrough.mp4 --wait=6
```

Two servers must not share a home while a walk runs: the workspace layout is one file per home, and
switching Guided and Full in one window rearranges the other.
