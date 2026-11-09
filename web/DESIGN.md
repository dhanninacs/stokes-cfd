# Stokes front end — design specification

The target: a window that an Onshape, SimScale or Shapr3D user recognises as
modern and professional in the first second, and that stays legible at 60 fps
with a 20 M-triangle model in it. This file is the contract between the
reviewer and the implementers. Deviations need a reason in the PR.

## 1. References and what we take

| Reference | Take | Leave |
|---|---|---|
| Shapr3D | floating islands over a full-bleed viewport; adaptive context toolbar next to the selection; stationary corners (modes bottom-left, view controls bottom-right); filled, high-contrast icons; one accent colour | modelling toolset |
| Onshape | calm grey/blue professional palette; dense but aligned left list; crisp 1 px dividers; view cube with click-to-snap; keyboard-first | ribbon toolbar |
| SimScale | tree with status icons and inline "+" actions; property panel that appears on selection with assigned-faces list | blue-heavy chrome |
| Fusion / nTop | glass panels, soft shadows, rounded 12 px cards | heavy gradients |

## 2. Layout (reference 1440 × 900)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ◈ STOKES / demo_pipe ●  [Geometry][Mesh][Physics][Solve][Results][Explore]  ⌕ Commands ⌘K ☼ ▶ Run – ▢ ✕ │ 48 px, drag region
├─────────────────────────────────────────────────────────────────────────────┤
│ ┌ SIMULATION ─────┐                                           ┌ PARAMETER ─┐│
│ │ ⌕ filter        │             full-bleed viewport           │ dp_dyn     ││ panels float 12 px
│ │ ▾ geometry      │     gradient background, fading grid      │ Dimension  ││ from the edges,
│ │   ▾ parts       │     PBR parts, outline on selection       │ Value  ƒ   ││ radius 12,
│ │ ▾ physics       │        ┌ Assign boundary · Isolate ┐      │ = 1996 Pa  ││ glass + shadow
│ │     parameters  │        └── context toolbar ────────┘      └────────────┘│
│ └─────────────────┘                                                         │
│ [Body ▾][Paint][Lasso][Section][Isolate][Measure][Shaded ▾] [⊡][⚙]  (cube)  │ islands├─────────────────────────────────────────────────────────────────────────────┤
│ Console · Log · Residuals · Reports                                       ▴ │ drawer 36 px / 240 px
└─────────────────────────────────────────────────────────────────────────────┘
```

Rules:
- The viewport is the page. Panels float over it with a 12 px inset and never
  touch each other or the edges. The view cube's 128 × 128 corner stays free.
- The default editing surface is the Details pane beside the tree. Mesh,
  Conditions, Solve, Results and Properties pages render there for the selected
  node; they are not repeated as tabs in the right dock. The right dock stays
  available for optional help or reference panels, and the bottom drawer holds
  console, log, residuals and reports.
- One accent colour (teal) for selection, active chips, primary button,
  focus rings. Blue is reserved for "outlet"-type information, red for
  "inlet" and errors, amber for stale, green for ok.
- Themes are a style family times an appearance, selected by `data-theme`
  on `<html>`: the **Stokes** family, **dark** (the reference look; do not
  re-colour it) and **light**; the **Cupertino** family, **cupertino**
  (light) and **cupertino-dark**; and the **Ubuntu** family, **ubuntu**
  (light) and **ubuntu-dark**, the Yaru look. The URL and `--theme` accept
  the six names plus `auto`, `cupertino-auto` and `ubuntu-auto`, which follow
  the OS appearance inside their family. The top bar flips the appearance;
  Display settings chooses Style and Appearance. Tokens live only in
  `styles.css`, `themes/cupertino.css` and `themes/ubuntu.css`; no hard-coded
  colours in components except the categorical patch palette. A custom theme
  (the workspace's `theme`, §5) is one of the six as its base plus inline
  overrides of those tokens on `<html>`, which win over every stylesheet block.
- **Cupertino** is inspired by Apple's website and macOS without using any
  Apple name or asset. Light: `#e8e8ed` page (a shade below the web's
  `#f5f5f7`, because a desktop window is not a web page), vibrancy panels
  `rgba(250,250,252,0.66)` (blur 24 px, saturate 180 %, 0.5–1 px hairline
  borders), text `#1d1d1f` with secondary `#6e6e73` and tertiary `#86868b`,
  accent `#0071e3` (hover `#0077ed`), system status colours (`#34c759` ok,
  `#ff9f0a` warn, `#ff3b30` danger), viewport radial `#f1f1f5` → `#d6d6dd`.
  Dark: `#1c1c1e` page, graphite panels `rgba(44,44,46,0.72)`, text
  `#f5f5f7` / `#a1a1a6` / `#7c7c81`, hairlines `rgba(255,255,255,0.09)`,
  accent `#0a84ff` (hover `#3395ff`), status `#30d158` / `#ff9f0a` /
  `#ff453a`, viewport radial `#2b2b30` → `#121214`. Both: radii 14 px
  panels / 10 px inputs, soft diffuse shadows, generous whitespace, a focus
  ring `0 0 0 4px` of the accent at 35–40 %. The stage chips become a macOS
  segmented control instead of a filled accent pill: track
  `rgba(118,118,128,0.14)` light / `0.24` dark, 2 px padding, no border,
  9 px radius around 7 px segments (concentric), idle labels `#424245` /
  `#a1a1a6`, raised selected segment `#fbfbfd` / `#636366` with a 0.5 px
  ring and a 1 px shadow. The primary button is a plain blue pill without
  glow; the logo mark is the app icon, as in every theme (see Top bar);
  section headers are 11 px / 600 in sentence case, untracked.
  Selection and menus follow macOS, not the web. A selected tree row is a
  quiet grey (`rgba(0,0,0,0.07)` / `rgba(255,255,255,0.08)`) until the tree
  has the focus, then solid accent with white text and icons, a white ring
  on its status dot and no accent bar. A menu has a 10 px radius, 5 px
  padding and 24 px rows (5 px radius); the row under the pointer or the
  keyboard fills with the accent and turns its text, icon and key cap
  white. Status chrome is text, not badges: 12 px muted words between
  hairlines, no fills or icons, a 7 px connection dot without halo, and a
  grey dot (`#c7c7cc` / `#636366`) for a node that is fine.
  Smaller habits: tooltips are light in the light appearance, scrollbar
  thumbs show only while the pointer is over their area, switches use the
  accent, a select ends in an accent chevron tile. The `--vp-*` viewport
  tokens are dark ink on the light floor and light ink on the dark floor,
  because `viewport/theme.ts` reads plain hex and applies its own alphas.
  When hosted, window controls render as traffic lights (`#ff5f57`,
  `#febc2e`, `#28c840`) at the top-left with a dark glyph per colour on
  hover, grey (`#d1d1d6` / `#4a4a4e`) while the window is not the active
  one (`host.ts` sets `data-active` on `<html>`); the right-hand controls
  hide.
- **Ubuntu** is the Yaru look, read from this machine's installed
  `Yaru`/`Yaru-dark` gresources rather than invented. Light: `#fafafa` page,
  `#ffffff` raised fills and text fields, `#3d3d3d` text with secondary
  `#6d6d6d` and disabled `#9c9c9c`, borders `#c7c7c7`, viewport radial
  `#fafafa` → `#d9d9d9`. Dark: `#2c2c2c` page, `#272727` raised fills and
  text fields, unfocused `#2f2f2f` and `#343434`, text `#f7f7f7` with
  secondary `#b5b5b5` and disabled `#929292`, borders `#181818`, viewport
  radial `#343434` → `#181818`. Both: the classic Yaru accent, Ubuntu orange
  `#e95420` on `#ffffff` text (hover `#f0693a`), status `#109b26` ok /
  `#f99b11` warn / `#c7162b` danger, 8 px panel and 6 px control radii, flat
  buttons without a gradient or glow, and Ubuntu Sans / Ubuntu Sans Mono with
  system fallbacks. Yaru also ships blue `#0073e5`, purple `#7764d8`,
  magenta, red, yellow, olive, sage, prussiangreen and wartybrown accents,
  reachable through a custom theme's `--accent`. Tooltips stay dark in both
  appearances. The traffic lights and the macOS habits above are Cupertino's
  alone.
- Density: 13 px base, 28 px tree rows, 32 px inputs, 8 px spacing unit,
  12 px panel padding.

## 3. Components and behaviour

### Settings discovery and progressive disclosure
- The simulation tree footer provides a visible Settings button and an expandable Tree guide.
  The guide explains stages, selection, Details and keyboard navigation without taking space from the tree when closed.
- Mesh and solver settings show one category at a time, chosen from a compact selector.
  Show all sections offers an accordion overview; changing category or presentation never changes project values.
- Search settings matches section names, field labels, help and parameter names, including controls in closed
  sections. Matching sections open while searching; clearing search restores the selected category.
  Invalid fields remain visible through filtering and their sections open immediately.
- Section explanations live inside the expanded body to keep narrow Details panes legible.
  Application Settings explains where to find simulation-specific settings in the tree.
- Details resolves both stored nodes and synthetic outline pages, so selecting Solve → Controls
  opens solver settings even before a solver settings node exists.
- Solve → Controls opens Run limits directly, Methods and Flow model open Model, and Run opens monitoring.
  Configuration pages omit progress, residuals and job logs. Solver and mesher catalogues use compact selects;
  unavailable alternatives have a separate explanation disclosure. Energy belongs in Model, and gravity and
  operating pressure have a Reference conditions category.

### Opening screen and empty geometry
- With no open project, show a dedicated welcome screen: a short workflow introduction, New project and
  Open project actions, recent projects, and optional examples and tutorials. Tutorials are collapsed by default.
  Examples open only after the user chooses them. The viewport, editing panels and output drawer do not mount
  behind this screen; workspace preferences remain in their existing store.
- The project viewport displays project geometry only. Never fetch the demo pipe as a fallback while a project
  is empty, an import is pending, or an import fails. Existing imported geometry can remain visible while another
  file imports; partial new geometry appears through the normal geometry events.

### Top bar
- Logo mark + wordmark, project menu (New, Open, Import geometry… `Ctrl I`, Save,
  Save as; the host's native file and folder dialogs when there are any, else
  an in-app path prompt), status cluster: connection dot, display units,
  engine and licence badges fed by `engine.info` and `license.info`.
  Import geometry… needs an open project: it starts the import worker
  (`geometry.prepare`) before the picker opens, runs `geometry.import`
  without `wait` and toasts `Importing <file>…`; progress and the part come
  through the `geometry.*` events the viewport follows. The picker's filter
  comes from `geometry.formats`, asked once without a project, with the six
  formats as the fallback; a format the file does not declare units for
  (BREP, STL, OBJ, 3MF) asks for them first, starting from the Default
  import unit of Settings (`mm` until the user changes it).
- **Settings** (T-178): a modal dialog, not a panel. It opens from Project →
  Settings… (the last entry, after a separator; no project needed), `Ctrl Shift ,`
  (`app.settings`; `Ctrl ,` stays on Display settings), the palette, and the
  last row of the Display settings popover ("All settings…"). A vertical tab
  list on the left (`role="tablist"`, arrow keys), the section on the right,
  one "Done" button; Escape closes only the top dialog, so Export, Import,
  Save preset, Reset and the notices open over it. The sections are Appearance
  (style, appearance, custom theme, reduced motion, ground grid), Units (the
  open project's display units, an undo step; the default import unit),
  Mouse (the scheme, and the left-drag orbit switch for Select / Orbit),
  Workspace (customise scope, layout presets, save, export, import, Customise,
  Reset) and About (version, Python, platform, Stokes home, workspace files,
  licence, engine, the third-party notices). A section that has no reader is
  absent, not disabled. What persists where: the workspace document's
  `preferences` section (mouse scheme and switch, default import unit) is the
  user's, written with `scope: "user"`, never in a project file or a preset,
  and cleared by Reset workspace; the grid is `viewport.grid`; the display
  units are the open project's; reduced motion is this device's. The Modes
  island's Mouse menu and `Shift N` change the scheme for the session only; the
  dialog is what makes it last. The table the dialog renders is
  `app/settingsSections.ts`.
- The logo mark is the app icon artwork itself, used everywhere: `web/public/favicon.png`, a
  copy of `stokes/app/assets/stokes.png` (the 256 px twin of `stokes.ico`, the icon of the desktop
  shortcut, the window and the taskbar), drawn at 28 px. It keeps its own rounded tile and
  colours in every theme, dark, light and both Cupertino: the `.logo-mark` box has no background,
  shadow or token, so the tile is never doubled. The wordmark is text and follows the theme. This
  is an owner-approved exception to the frozen look (dark is not re-coloured; Cupertino has no
  tile of its own). Replace the PNG and the `.ico` together.
- Stage chips centred: active chip filled accent; keyboard `1`–`6`. A chip
  opens that stage's groups in the tree and folds the others; selecting a
  tree node moves the chip.
- Right: Commands (opens the palette, shows `Ctrl K`), appearance toggle
  (dark ↔ light inside the current style; Display settings chooses Style and
  Appearance), Run (primary pill with gradient and soft glow), window
  controls when hosted.
- Compaction is measured, not breakpointed: engine names and licence
  labels vary, so `fit()` in `TopBar.tsx` clears `data-compact` on the bar
  and takes these steps in order while a cell overflows, writing the ones it
  took to `data-compact` (`styles.css` hides what each names): licence badge,
  engine pill, Commands label, wordmark, units pill, idle chip labels, the
  `Ctrl K` hint, then the project name (24 → 12 characters). A step is
  skipped when it would only free room in the cell that already fits. The
  left cell is measured by `scrollWidth` (its end padding keeps 16 px before
  the chips), the right one by its first item's edge, because it packs to the
  end. It runs on a change of theme, hosted state, project name, units,
  engine, licence or stage, on a resize of the bar, when `data-theme`
  changes and when fonts load. A pass costs about 2 ms plus 1–2 ms per step
  taken.
- Window controls: minimise, maximise, close at the right in dark and light.
  In Cupertino they become traffic lights at the top-left and the right-hand
  set is hidden; the glyphs appear on hover, as on macOS.
- Whole empty bar area drags the window; double-click maximises.

### Tree panel (left, 300 px, resizable 240–420)
- Header "SIMULATION" with filter and a "+" that offers *Parameter*.
- Rows: chevron, type icon, name, value (muted, right-aligned, truncated with
  tooltip), status dot. Parameters show the display-unit value and, when an
  expression exists, `·` plus the expression.
- Hover reveals a `⋯` menu (Rename, Delete, Add parameter; the value steps
  aside and the status dot gives way while it shows); right-click does
  the same. Keyboard: ↑↓ move, →← expand/collapse, Enter opens properties
  focus, F2 rename, Delete removes (with undo toast); rename, delete and the
  row menu are the workspace's `tree.*` chords, which the menu hints show.
- A part or tag row (ADR-0021) carries two controls: the 12 px colour
  swatch between the type icon and the name, and the visibility eye between
  the value and the status dot. The swatch stays on show. The eye shows on
  row hover, while the row's menu is open, on the selected row while the
  tree holds the focus, and always while the node is hidden or its card is
  open. The eye writes `{visible}` through `geometry.style` (one undo step,
  the selection unchanged); the swatch opens `StylePopover`, the 220 px card
  that edits `color` and `opacity` on that same command. The name gives way
  first: it stops at `calc(62% - 18px)` so it never runs under a control.
- A group that holds parts carries the eye alone: no swatch and no card,
  because it owns no colour or opacity. Its eye reads the parts beneath
  (`EyeOff` only when every one of them is hidden), and it hides all of
  them in one `geometry.style` step, as that command defines it. A group
  without parts is a plain row.
- Selection tint with a 3 px accent bar (Cupertino: section 2); selecting a
  tag or a part highlights the matching patch in 3D, and vice
  versa.
- Many parts (ADR-0019): rows have a fixed height of 28 px and only those near
  the visible window are drawn, each with `aria-posinset` and `aria-setsize`; the
  selected row, the one being renamed and the one whose menu is open stay
  mounted wherever they are.

### Properties panel (right, 320 px)
- Header: type label, editable name, path, status (a dot and a word, no
  fill); failed nodes show the error message in a red card.
- Quantity fields: value in the display unit with the unit as a clickable
  chip (switches display unit for that field); an expression shows in mono
  with `ƒ` and the evaluated `= value unit` line below; errors inline in red;
  success flashes the field green for 400 ms. Enter applies, Esc reverts.
- Patch selection card: colour swatch, kind, triangles, vertices (and faces
  for an imported solid), and an
  **Assign condition** action that opens the value panel (ADR-0022 §7.1) and
  creates `/regions/fluid/boundaries/<patch>` when it does not exist
  (**Show boundary** once it does); Hide and Isolate are icon buttons beside
  it.
- Value panel (ADR-0022 §7.1): a 360 px `.glass` card anchored under the
  context toolbar (docked at the right of the viewport when there is no
  anchor), never restyled — the operation card's surface, 12 px radius and
  tokens, with `SchemaFields` for the fields and exactly one accent (the
  primary button). Left column: the condition kinds by family, a 12 px dot in
  each kind's colour; a kind the catalogue cannot offer is disabled and names
  the milestone that brings it. Right column: Name, the target line
  ("12 faces of 3 bodies · Replace") and the kind's `when`-filtered
  parameters. Enter assigns, Esc cancels; a refusal keeps the server's message
  under the field.
- A `part` node shows what the import found as read-only text above its
  editable Colour and Visible: source file, solid number, the units the file
  declared, triangles, faces and the size of its bounding box in the project's
  length unit (the corners in the tooltip).
- Empty state (nothing selected): a quiet hint card with three shortcuts; the
  key caps are the workspace's current chords for the palette and Fit.
- One `SchemaFields` component renders every field, driven by the node's JSON
  schema (the panel's `include` filter decides which properties are drawn; a
  `ref` select lists the nodes of `x-ref-type`, and a three-number `array3`
  row converts each entry to the display unit); the fields keep their own
  edit state and the panel keeps only the server's refusals.

### Viewport
- three.js, environment-lit PBR: walls brushed metal (metalness 0.6,
  roughness 0.35), flanges darker, inlet/outlet slightly translucent patches
  with a faint emissive tint. Background radial gradient from tokens.
- Ground grid that fades with distance (shader), sitting just under the part;
  soft contact shadow is a bonus. The workspace's `viewport.grid` (on by
  default) shows or hides the grid, its axes and the floor tint; the contact
  shadow stays.
- Hover: subtle emissive lift. Selection: a crisp accent outline (a custom
  full-resolution pass composited onto the canvas; `OutlinePass` was rejected
  for being half-resolution, additive and invisible on light themes) plus
  emissive lift.
- Camera: orbit with damping, zoom to cursor, animated fit on load, on `F`
  and on view-cube clicks; perspective/orthographic toggle.
- View cube (three.js ViewHelper), bottom right by default; clicks snap and
  animate. It is drawn in the corner the workspace puts it in (§5) and not at
  all when the workspace hides it.
- **Scale crosshair**, at the middle of the top edge. Not a corner: the
  islands own the four corners and their offsets move as panels open and
  collapse, so a corner scale gets covered on a narrow window — the first
  attempt did exactly that. The top centre is contested by nothing, and
  centring on `left: 50%` needs no runtime offset. A vertical centre line with
  an arm either side and a tick at each outer end, the length under it: `10 mm`,
  `2 m`, `1.5 km`. The arms together span what the stated length actually
  measures on screen, so the drawing is the length and the label is its name.
  Measured **at the orbit target** and saying so in its tooltip, since a
  perspective view has no single scale; exact everywhere under the orthographic
  camera. A soft drop shadow keeps it off the grid. It never rotates, never
  clips against the model, and takes no pointer. Its unit ladder is
  mm / m / km / µm with no centimetre, so a millimetre-scale model keeps
  millimetres instead of being silently converted.
- **Measure** (`M`, the islands' ruler button): click two points on the model and
  the distance between them appears on the line between them — `319 mm` at the
  midpoint, an accent line with a marker at each end. It rides the same one-shot
  point pick the operation cards use for a seed, so a click measures instead of
  selecting and a click that misses the model simply waits for one that does not.
  A third click starts a **new** measurement at the point it picked rather than
  continuing from the old second point: the first point of a measurement is
  always one the user clicked. The line and the markers live in world space on
  the scene root, so orbiting, zooming and re-rendering do not disturb them, and
  the label follows the projected midpoint, pulled inside the viewport and kept
  clear of the top strip the scale bar and the import line share. The figure goes
  through the same unit ladder as the scale crosshair, so the two can never
  disagree about what a length looks like. Nothing about a measurement is model
  state: it is not saved, journalled or undone, and Escape leaves the mode.
- Render on demand only; pixel ratio capped at 2; resize without flicker.
- View modes from the store: shaded, shaded with edges, wireframe; hidden
  and isolated patches honoured.
- Imported parts (ADR-0014): every `part` node of the tree is drawn from its
  mesh at `/api/geometry/{node_id}` (many at a time through `/api/geometry/batch`),
  one mesh per transport group. From 100 parts on (ADR-0019) parts are drawn in
  batches, one per look (a part's kind, not its colour), on a white base material with
  the part's colour per instance, so the draw calls follow the number of looks and
  not of parts; below 100 each stays its own mesh. A hovered or selected part is
  drawn by its own mesh, its instance hidden, until its highlight has faded, so the
  lift and the outline look as they do on a part drawn alone. The sample part is
  shown only while the tree has no `part` node; the first part replaces it. A mesh
  arrives coarse and is replaced in
  place by the fine one without a blink. The part's `color` replaces the file's
  colour and `visible` hides the part, both without a new mesh. A patch is named
  after its part node, made unique in tree order with `_2`, `_3`, so the tree and
  the viewport agree. A part that arrives re-frames the view only while the user
  has not orbited, panned or zoomed since the last frame.
- Faces: the face under the pointer is tinted with an accent overlay built from
  the triangles that carry its face id (the part's own buffers plus a small index
  of its own, nothing per face up front). The Modes island chooses what a click
  picks: in body mode the part, the solid, as for any patch; in face mode the
  face. Click replaces, Shift-click adds and Ctrl-click toggles, across bodies
  and faces; the pick is drawn as one accent overlay (a single group's borrowed
  buffers, or copies across groups and parts), the active member keeps its
  outline, and Esc clears it. Switching Body/Face keeps the pick: the faces
  become the bodies that own them and the bodies become all their faces.
  Clicking twice escalates: in face mode the double-click grows the face to its
  smooth tangent chain (`tag.region`) and a triple-click takes every face of the
  body; in body mode the double-click takes the parts of the same import group,
  the clicked part first; a triple-click adds nothing there. Shift and Ctrl apply
  to the escalated set as they do to a click. Paint (`P`) turns the canvas into
  a brush: the cursor is a crosshair, a left drag paints instead of orbiting —
  the camera is held still until the button comes up — and every new face (face
  mode) or body (body mode) the pointer crosses joins the pick as the highlight
  follows it; Ctrl-drag takes them off instead. A press on empty space paints
  nothing and orbits nothing. `Esc` leaves paint mode first; a second `Esc`
  clears the pick. Switching Body/Face keeps paint mode. A drag on empty space —
  a press that starts on no patch — draws a dashed accent rubber band instead of
  orbiting (a press on a patch still orbits, so the camera keeps the gesture the
  cursor was over); releasing it picks every body with a pixel inside, or every
  face in face mode, through the same bulk path the paint stroke uses: Shift
  adds, Ctrl removes, plain replaces, and an empty plain box clears the pick as a
  click on nothing does. **The mouse scheme** — the Modes island's Mouse menu, ADR-0021 3.4 as
  amended — decides which button rotates, because no single convention covers both audiences.
  `CAD`, the default, rotates on the **middle** button and pans on the **right**, leaving the
  left one for selecting and the band; `ParaView` rotates on the **left** anywhere, so the band
  needs `Shift`; `Blender` rotates on the middle and pans on `Shift`+middle; `Select / Orbit`
  keeps the middle rotating and the right panning and adds a switch for the left drag. **Every
  scheme rotates from anywhere in the viewport** — that is the rule the amendment exists for.
  `Shift N` walks the schemes. `Alt`-drag, or the Lasso toggle beside Paint, draws the
  freehand path instead and tests each pixel of its bounds against it. The
  rectangle and the path are drawn on a 2D overlay over the canvas, in the accent
  colour, dashed, never in the scene. `Alt` + wheel walks the
  candidates under the pointer — distinct bodies, or distinct faces — the
  highlight follows the one it stands on, a click takes it, and moving the
  pointer more than 4 px goes back to the nearest; the wheel without `Alt` still
  zooms. The name of
  what the pointer is over — the patch, "face 12 of part_3" in face mode, and
  the candidate line "2 of 3 under the pointer" — is kept for the drawer strip
  (T-049 draws it). Tags: every visible tag under /geometry/tags
  draws its faces in the tag's own colour at the tag's opacity, lit like the
  part (its metalness and roughness), with no geometry of its own: the colour
  is a per-vertex tint the part's look mixes in (ADR-0021 3.5), so a tag adds
  no draw call or triangle. Where tags overlap, the one lower in the tree shows;
  theme changes do not recolour them. A tag with its eye off hides those faces,
  so they are neither drawn, picked nor outlined (a tag of whole parts hides
  nothing per face: the part's own eye is for that). Selecting a tag row in the
  tree picks its members, so the accent shows them.
- Import progress: a 2 px accent line on the viewport's top edge and one line of
  plain text under it, "Reading part.step…" (the line sweeps while the file is
  read) and then "Meshing 3 of 12 (coarse)…" (it fills over the coarse and the
  fine pass); it fades out when the last part is final. A failed import is an
  error toast, a cancelled one an info toast, one toast for all its parts. The
  same line shows a geometry operation under way (`geometry.op_progress`) as
  "Enclosure: cutting 1 body out of the enclosure 60 %", the newest of the
  imports and the operations; an operation node with status `stale` has **Run**
  in its tree row menu (`operation.run`).

### Context toolbar
- Appears 12 px above the top edge of the selected patch's projected bounds
  (below the bottom edge when there is no room), clamped to the viewport:
  Assign condition (Show boundary once assigned) · Prepare ▾ · Isolate · Hide ·
  Rename. Fades in 160 ms.
- While faces are picked, or more than one body, Name… leads (accent text)
  ahead of Assign condition, and Grow (faces only) and Clear follow it. Name…
  asks for a tag name and creates the tag under /geometry/tags from the pick (a
  `boundary` from faces, a `region` from whole bodies), selects its tree row and
  keeps the pick; a name the server refuses shows under the field and the
  dialog stays open. Grow replaces the pick with the faces reached across
  smooth edges (`tag.region`); Clear empties the pick. The sample part's faces
  have no ids, so its pick never makes a tag.
- Prepare ▾ (`prepare.enclosure`, `prepare.extractFluid`, `prepare.boolean`,
  `prepare.repair`; no chords) offers the geometry operations of ADR-0021 8 as
  a data list: Enclosure…, Extract fluid…, Boolean ▸ (Fuse, Cut, Common),
  Repair ▸ (Heal, Sew, Close openings, Unify faces, Drop slivers, Remove faces,
  Fix orientation, Make solid). An entry the pick does not fit is disabled with
  a reason in its tooltip (Boolean needs two bodies; Remove faces needs picked
  faces; an operation type the server has not built yet says so). Picking one
  opens the **operation card**: a floating glass card of the properties
  panel's kind (320 px) at the right of the viewport, one at a time, titled with
  the operation's label. Its form is `SchemaFields` over the type's parameters
  (quantities with the unit chip, enums as selects, switches, a `json` member
  list as "n faces of m bodies"), its inputs and faces come from the pick the
  card opened with, and its `seed` field carries a **Pick in view** button that
  turns the viewport into a one-shot point pick (crosshair; Esc cancels). Run
  calls `operation.create`; the card then shows the run's progress line and a
  Cancel that calls `operation.cancel`. `geometry.op_done` closes the card and
  selects the first output, `geometry.op_failed` puts the server's message in
  the card's red error line (a cancel, `geometry.cancelled`, says itself in the
  quiet line instead: the user asked for it), and Esc closes the card (then the
  pick, on a further Esc). The same entries are the Geometry ribbon's
  **Prepare ▾** task.

### Islands
- Bottom-left pill group: pick mode ▾ (Body, Face; Edge and Point are
  listed disabled with "Coming soon"), Paint, Lasso, Section, Isolate, Measure,
  display ▾
  (the current display mode). Both ▾ buttons show an icon, the current value
  and a chevron and open their menu upward with a check on the current item.
  Paint (`P`) is a toggle that fills with the accent while it is on, and Lasso is
  one too, with no chord of its own — `Alt`-drag asks for a lasso regardless.
  Measure (`M`) is a toggle on the same terms — accent while it is on, and it
  turns Paint off, since both want the left button to mean "draw on the model".
  Section is the one button left saying "Coming soon".
- Bottom-right pill group: Fit (`F`), Perspective/Ortho, Display settings.
  Tooltips carry keyboard hints.

### Output drawer
- Collapsed 36 px strip with tabs; expanded 240 px, drag handle to resize.
- Console: results and errors, command line with ↑↓ history and command-name
  autocomplete, JSON pretty-printed and collapsible, Clear button.
- Log: events only. Residuals: uPlot, log scale with a decade-only grid, a
  legend of line swatches above the plot (a click toggles a series), demo
  traces until a run exists. Reports: placeholder text.

### Command palette
- `Ctrl K`. Fuzzy filter over name and description, recent commands first,
  `pro` badge for gated commands, parameter form generated from the input
  schema with required markers; Enter runs; results in the console and a
  toast. Besides the API commands it lists the window's own actions with the
  chord each has now, "Customise workspace", "Reset workspace" (it asks first)
  and "Move <panel> to <place>" for every panel; with nothing typed the list
  is sectioned (Recent, Commands, Actions, Layout).

### Toasts
- Bottom centre, max 4, auto-dismiss (3 s info, 6 s error), dismiss button,
  a 3 px bar in the kind colour on the left edge.

### First-run hint
- One card above the islands, once: "Drag to orbit · right-drag to pan ·
  scroll to zoom". It goes away after 30 s, on dismiss, or at the first
  drag, scroll or fit.

### Motion and accessibility
- 160 ms ease-out for panels and menus; none on data updates; respect
  `prefers-reduced-motion` (the OS setting or the Display settings switch,
  one rule in `app/prefs.ts`).
- Every control has an accessible name; focus rings use the accent; the tree
  is keyboard-operable.

### Keyboard
- Global, not while typing in a field: `1`–`6` stage · `F` fit · `O`
  perspective/orthographic · `I` isolate · `H` hide or show the selection ·
  `V` cycle view mode · `P` paint select · `S` section (soon) · `M` measure
  (soon) · `Esc` clear the selection and close the palette (leaving paint mode
  first) · `Ctrl K` palette · `Ctrl Z`, `Ctrl Y` or `Ctrl Shift Z` undo and
  redo · `Ctrl S` save · `Ctrl Shift S` save as · `Ctrl O` open · `Ctrl I`
  import a geometry file.
- Tree: arrows, `Home`, `End`, `PageUp`, `PageDown` move; `←` and `→`
  collapse and expand; `Enter` edits the first field; `F2` renames; `Delete`
  deletes with an Undo toast; `Shift F10` or the context key opens the row
  menu.
- Properties: `Enter` applies, `Esc` reverts and a second `Esc` returns to
  the tree.
- Every shortcut is also on the control's tooltip and its `aria-keyshortcuts`.
  These are the defaults: the chords are the workspace's (§5), and the tooltips,
  the menus and the hints of the empty properties panel show the current ones.
  The palette also carries the actions without a chord: `selection.name`,
  `selection.grow` and `selection.previous`, which brings back the pick before
  the last change (the last ten are kept). `Alt` + wheel on the viewport cycles
  the selection candidates under the pointer and is not a rebindable action.

## 3b. Craft, and the generic look we refuse

Reference points praised for design, and what each lends us:

| App | What we take |
|---|---|
| Linear | restraint: one accent, hairline 1 px borders, weight and spacing for hierarchy instead of colour; keyboard affordances everywhere; a thin status line; subtle surface gradients with faint grain |
| Raycast | the command palette as a crafted object: generous row height, mono for identifiers, muted descriptions, section labels, recents; crisp kbd chips |
| Figma | calm light UI: precise panel edges, dense but aligned property rows, segmented controls, numeric inputs with tabular figures |
| Vercel (Geist) | monochrome palette with a single accent; typographic rhythm; Geist/Geist Mono as the voice |
| Things 3, macOS Sonoma | the Cupertino theme: vibrancy, hairlines, soft diffuse shadows, system colours used sparingly, SF-like type |
| Spline, Womp | the viewport as a product render: studio lighting, soft contact shadow, a floor that fades, materials with real reflections |
| Arc | personality in one place (the logo mark and the accent) rather than everywhere |

The generic look (what reviewers must flag): purple-to-blue gradients;
glassmorphism on every surface; identical drop shadows on every card; glow
on buttons; rounded-everything with inconsistent radii; Inter 13 px with no
hierarchy; emoji or mixed icon sets; muddy translucency over busy content;
toasts and badges in saturated colours; "AI" sparkles; centred hero text in a
tool UI; placeholder copy that reads like marketing.

Rules that follow:
- Typography carries hierarchy: three sizes (11 / 13 / 15), two weights
  (450, 600), uppercase 10.5 px tracked labels for sections, tabular numerals
  for every number, a monospace face for identifiers and expressions. Fonts
  must be OFL or similar: Inter, Geist, Geist Mono, JetBrains Mono.
- Surfaces: at most two elevation levels. Floating panels get one tuned
  multi-layer shadow; nested elements use hairlines, not shadows.
- Radii: 12 px panels, 8 px controls, pills only for chips and the primary
  action. Nothing else is rounded. `.btn` is an 8 px control with a faint
  `--track` fill (`.btn.ghost` has none); `.btn.primary` alone is a pill.
- A row of mutually exclusive choices is one segmented control (a recessed
  `--track` with a raised segment), never a row of separate pills: `.segmented`
  in panels and `.guided-chips` in the guided cards.
- The guided step strip in the top bar is one line in every state: a 16 px
  status mark (done is `--ok-soft` behind an `--ok` check, not a filled disc),
  the label, and the hint only in the tooltip, so the strip keeps its size
  when the step changes and never pushes the project name into compaction.
- Colour: the accent appears in at most three places on a screen at rest
  (active chip, selection, primary button). Status colours are 7 px dots and
  thin bars, never filled badges.
- Motion: 120–200 ms, ease-out, only on enter/exit and state change.
- Empty states say what to do in one line; no illustrations.
- No native form controls: a select is `select.field` inside `.select-wrap`
  with a drawn chevron, a boolean is the Switch.
- The viewport reads as a studio render: three-point environment lighting,
  contact shadow, fading floor, reflections on metal; never a flat grey
  Lambert look.

### Signature: the flow language

The one place Stokes allows itself personality (§3b, "Arc") is flow itself, drawn by the app's own physics
rather than decoration:
- **Project cards** without a saved thumbnail draw `flowArt` (`app/flowArt.ts`): streamlines of potential flow
  around a cylinder, the card's monogram (or an example's icon) on a round tile where the cylinder is. The free
  stream's angle (±16°) and the circulation (up to 70 % of 4πUR) come from a hash of the project's path, so
  each card is its own and stays the same. Quiet `--text-faint` lines at rest; on hover or focus the lines take
  the accent and flow (dashes moving downstream). Reduced motion stops the flow, not the colour.
- **The live Run pill**: while a run is active, Stop is a pill whose accent-soft fill grows with
  `progressFraction` (a soft band sweeps while the fraction is unknown or still 0, the start-up), with a 40 × 14 sparkline of the worst
  residual over the last 120 iterations (`state/residualSpark.ts`, log scale) and the iteration count in
  tabular figures. The bar's fit pass reruns when the pill appears and when it grows. Checked on a real
  two-rank solve of the elbow case on Windows (2026-11-04): fill, count and sparkline follow the run, and Stop
  returns the plain Run pill.
- **The step rail**: the 14 × 2 px connector after a done step fills with `--ok` at 70 %, running into the
  accent where it reaches the open step.
- **The studio backdrop**: under the transparent canvas, `.viewport-bg` stacks a static SVG grain (9 % alpha),
  a vignette (`--vp-vignette`, black 28 % in the dark themes, ink 8 % in the light ones) and the theme's radial
  floor. Nothing in the WebGL render changes.

## 4. Acceptance checklist (reviewer)

- [ ] First impression at 1440 × 900 dark and light: no overlaps, aligned
      edges, consistent radii and spacing, no default browser styling.
- [ ] Viewport: part reads as metal with depth, grid fades, no z-fighting,
      selection outline crisp, view cube usable, 60 fps orbit.
- [ ] Tree ↔ viewport selection works both ways.
- [ ] Editing `U_in` to `3` (bare number) updates `dp_dyn` to 4491 Pa; an
      invalid unit shows an inline error; undo (`Ctrl Z`) restores.
- [ ] Command palette runs `parameter.list`; console shows the result.
- [ ] Drawer resizes; residual chart redraws on resize and theme change.
- [ ] Light theme is as finished as dark.
- [ ] No console errors; build (`npm run build`) and typecheck clean.

## 5. Customisation (ADR-0015)

The Stokes preset (sections 2 to 4) is the reviewed default and draws the
window those sections describe. Everything a user can arrange is data in the
workspace document (`docs/engineering/workspace-schema.md`), and every change is
a `workspace.*` command, the same one the CLI, MCP and the assistant use. The
console shows the command and the journal records it; folding a panel is the one
quiet write (the journal still has it). The front end is `web/src/workspace/`.

### What moves

| Part | Places | Notes |
|---|---|---|
| Panels: Simulation tree, Properties, Output | left, right, bottom, float, hidden | Several panels in one slot show as tabs on one glass card, or as a split: stacked cards with a handle in each 12 px gap (left and right), side by side (bottom). Widths and heights clamp to each panel's limits in the registry and to the window (the viewport keeps 360 px). |
| Island groups: Modes, View, View cube | bottom left, bottom right, top left, top right, hidden | Left to right in each corner, 8 px apart, clear of the side slots. The view cube is a 128 px box in its place of the row; the viewport draws it there and not at all when it is hidden. |
| Top-bar items (eleven) | their order, shown or hidden | Items before the stage control go in the left cell, the rest in the right cell. Window controls exist only when hosted; Cupertino draws them as traffic lights at the left. |
| Stage control | chips, ribbon, none | The ribbon is a band under the top bar: the stages as tabs, then a task strip with that stage's actions (the ones the context toolbar, the islands and the project menu offer). The number keys switch the stage in every style. |
| Tree | group order, hidden groups, stage mapping, link, levels open | The mapping decides which groups a stage opens; with the link off, stages and the tree do not move each other. |
| Shortcuts | any chord per action | Tooltips and `aria-keyshortcuts` show the first chord of each binding. |
| Theme | a built-in theme, or a base plus token overrides | See the theme editor below. |

### How

- **Customise mode** (`Ctrl Shift C`, Display settings → Customise…, or the
  palette): a dashed outline and a grip chip on every panel, island group and
  top-bar item. Dragging a chip shows the drop zones: the side bands, the bottom
  band and the viewport (a float card where it lands) for panels, the four
  quadrants for islands, the gaps between top-bar items (an accent caret). A drop
  writes the whole changed section with one `workspace.set`. Clicking a chip, or
  Enter on it, opens its menu: Move to…, tabs or split for its slot, fold, and for
  the stage control its style. The toolbar holds the scope (this project or all
  projects; a project needs a folder), a Hide target, the hidden parts (each with
  a menu to bring it back), the values the workspace files had wrong, Reset
  workspace… and Done. Escape leaves. Outside this mode nothing moves.
- **Edges and handles** resize as before; the size is written when the gesture
  ends, and a double click removes the override.
- **Folding**: the drawer's chevron, a side slot's icon strip (when the document
  folds it, at any width; below 900 px left and 1100 px right the window folds
  it for the session, as before), the grab bar of a float card.
- **Writes go where the value comes from**: outside Customise mode a change of a
  value the project overrides is written to the project, so it shows.

### Layout presets

Display settings → Layout applies one; the toast offers Undo. Shipped presets
carry the arrangement (layout, islands, top bar, tree) and keep the user's theme,
shortcuts and viewport. *Save current as…* stores the whole workspace as a user
preset; *Export…* and *Import…* move JSON text (Copy; Download in a browser, or Save as…
in the hosted window, which writes only the file the user chose in the native
dialog; paste or Choose file… to import, size-limited to 256 KiB).

| Preset | For users of | Arrangement |
|---|---|---|
| Stokes | this document | Tree left, properties right, output drawer folded, stage chips. |
| Classic | STAR-CCM+ | Tree over properties on the left (split), output on the right, modes top left. |
| Ribbon | Fluent | Stage ribbon with a task strip, tree left, properties right, output open. |
| Workflow | SimScale | One tree in setup order, one level open, no stage control. |
| Minimal | anyone | The viewport, a floating properties card, the palette; tree, modes and most of the top bar hidden. |

### Theme editor

Display settings → Theme editor…: a card beside the viewport with a base (Style
and Appearance), a title, the main colours (accent, background, panels, text,
secondary text, viewport top and bottom, grid lines) as a swatch with the
system colour picker and a text field, and the corner radius. Every change
previews at once. The accent derives its family (stronger, soft, ink, fill, glow,
text on accent, and the Cupertino focus halo) so the chrome stays consistent; the
panel colour also sets the opaque panel (top bar, drawer); the picker keeps a
translucent token's alpha. Values are checked against the kinds of
`docs/engineering/theme-tokens.md` before *Save as custom theme* writes the whole
theme with `workspace.set path=theme`. Reset returns to the base's own values;
the copy button puts the theme JSON on the clipboard. Style and Appearance in
Display settings write a built-in theme (Auto follows the OS); replacing a custom
theme that way offers Undo.

### Limits and rules

- Reset workspace and the command palette are always reachable: the palette
  keeps a chord (the server restores `Ctrl K`), Customise mode has its own chord,
  and Display settings opens with `Ctrl ,` when the View island is hidden.
- The launch theme (`--theme`, `?theme=`) wins over the workspace's theme for the
  session until the theme is changed, so review captures stay comparable.
  Without `--theme` the host puts the workspace's theme in `?theme=` (a custom
  theme: its base), so the first paint is the right one and nothing flashes.
- A custom theme's tokens are inline properties on `<html>`. The top bar's
  measuring and the residual chart follow every token change; the 3D viewport
  re-reads its colours (accent, grid, floor) when the base changes and on
  `stokes:theme-tokens`, which goes out when only the tokens changed.
- Float cards do not push the islands or the context toolbar; the side slots do.
- There is no density control yet: the token contract has no density token.
- The project menu registers its four actions on the action bus
  (`web/src/app/actions.ts`) while it is in the top bar, so the keymap owns their
  chords like any other: rebinding `project.save`, `project.saveAs`,
  `project.open` or `project.importStep` takes effect at once. With the menu
  hidden they do nothing.
- Customisation keeps the accessibility rules: the DOM order follows the visual
  order (left, right, float, islands; the top bar in item order), every handle,
  tab and fold control has an accessible name, and everything a drag does is in
  the handle menus too.

## Workflow review clarifications

Stage navigation must open the displayed tree section in both groups and outline
layouts; virtual outline ids and real model group ids are different. Nested
folder choices survive stage changes. A folder with no status carries no status
announcement.

Geometry Check reports success only after the current request succeeds. Pending,
disconnected and failed checks must not read as clean results. It refreshes after
model edits, ignores superseded replies and offers a direct retry. A concrete
guided task page replaces the generic next-step checklist in Details.

The guided-meshing heading and highlighted task follow the same explicit page
selection. Task queries are debounced after relevant changes and stop while idle.


Settings use one field taxonomy across their dedicated panel and Properties.
Solver groups separate model, run limits, output, execution and result slice.
Mesh groups separate mesher, domain, sizing, sharp edges, boundary layers,
quality checks and execution. Advanced sections start collapsed; a new server
refusal opens its group. Collapsing preserves field drafts. Unknown schema
fields remain reachable under Other settings, while known inapplicable fields
stay hidden. Physical values retain schema quantity editors and unit chips.
Controls lock during the corresponding active job and while disconnected.

### CAD framing correction (2026-10-21)
Opening/import and Fit frame all visible CAD bodies, including region domains. This supersedes the earlier solids-only fit policy: visible geometry must not be cropped. The fit uses the measured free canvas rectangle and centres projected bounds after applying the camera pose. Dock mount/resize observation keeps that rectangle current; manual navigation is not continuously refitted.
