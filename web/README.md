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
