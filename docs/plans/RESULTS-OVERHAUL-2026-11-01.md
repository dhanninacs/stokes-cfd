# Results overhaul: publication-level post-processing in the present GUI (2026-11-01)

Owner's ask (2026-11-01): *the results need a massive overhaul; the present one is not up to the
task; take notes and inspiration from the previous Stokes post-processor (`E:\CFD\TEMP\Chandra\A2A`)
and apply the post-processing improvements within the present GUI; we should be able to produce
publication-level post-processor images after this.* House rule for all of it: **a few words on the
control, the sentence in the tooltip.**

## 1. Where we stand

What the present tree already has (Full mode, right slot, `web/src/panels/FieldsPanel.tsx` and friends):
field colouring with smooth or banded colour and a lockable range (`fieldsModel.ts`), slices at any
plane with their own field (`SlicesSection.tsx`, `viewport/sliceLayer.ts`, section caps and drag),
iso-surfaces (`IsoSection.tsx`, `isoLayer.ts`), arrows on the slice (`ArrowsSection.tsx`), glyphs,
streamlines (`StreamlineSection.tsx`, `streamlineLayer.ts`), a probe (`ProbePanel.tsx`), monitors
with CSV export and the pressure drop (`monitorsModel.ts`), an HTML report (`ReportSection.tsx`,
`/api/results/report`), residual plot, LOD, fade, one colour map (`state/colormap.ts`), a legend
(`viewport/FieldLegend.tsx`), a frame capture (`scene.ts` `capture`, used by the review harness).
The guided Results card (`guided/steps/ResultsCard.tsx`) exposes a sliver: Velocity/Pressure chips,
the mid-plane slice switch, arrows, Probe, Report.

What A2A had that we do not (survey 2026-11-01, `stokes_pyside6/`): the **variable + Look** model
(Geometry, Contour, Slice, Streamlines, Iso-surface, Iso-volume, Vectors as tiles with live
thumbnails: "pick a variable, pick a look, done"), a **legend with draggable range handles** and
double-click to edit, three legend styles and number formats, **14 colour maps** with reverse,
banded/continuous and 2-64 levels, auto 1-99 % range, **units per quantity** (Pa/kPa/bar/psi, K/°C/°F,
m/s/mph; `fluent_viz_ntop.py:32-107`), N parallel slices and a custom normal, clip planes and a
cutaway box, iso-volume (threshold band), a derived-variable calculator (vorticity, Q, Cp…),
**saved views** that travel across cases, a command palette, **one-click publication export**
(render + legend at chosen size, white or black background), orbit GIF, PDF contact sheet, surface
reports (area/mass-weighted averages, flow rates) validated against Fluent.

What neither has and the owner will want for publication work: an interactive plane widget, plot
over a line, time-step playback for transient runs, log-scale colouring, side-by-side compare,
forces/Cd/Cl for OpenFOAM, CSV/VTK export of cuts.

## 2. Target: what "publication level" means here

A figure a reviewer accepts without asking for a redo. Concretely, from the guided Results card,
in under a minute:

1. **The right view by default.** Lengthwise slice through the flow (done 2026-11-01: the default
   plane's normal is the mesh's shortest axis), the fluid body alone, walls faded, camera fitted
   and orthographic for 2-D cases, edges off, smooth shading, no grid or triad in the export.
2. **A legend that reads like a journal's.** Field title with unit, 5-7 ticks in a chosen format
   (fixed, scientific, significant digits), a range the user can drag or type, optional banded
   levels, placed where the figure wants it (right, bottom, floating), fonts at print size.
3. **Colour maps that are perceptually right.** Viridis-class default for scalars, a diverging map
   for signed fields (pressure about zero, vorticity), a cool-warm option, A2A's bespoke maps,
   reverse, and the same table for the surface, the slice, the iso and the legend.
4. **Export at print resolution.** PNG at 1x/2x/4x of the viewport (or a chosen pixel size), with
   the legend, title and scale bar composited, white or transparent background, and the camera,
   field, range and look written into the PNG's metadata and a sidecar JSON so the figure can be
   remade. SVG for the legend alone.
5. **Looks, not panels.** Surface · Slice · Iso-surface · Streamlines · Vectors · Clip as one row of
   tiles in the card; each tile has two or three controls (the ones a figure needs) and a "More…"
   that opens the Full-mode section for the rest.
6. **Saved views.** Camera + look + field + range + legend as a named view in the project; "Export
   all views" makes the figure set for a paper in one go.
7. **Plots that match.** Residuals (have), monitors (have), **plot over a line** drawn in the
   viewport (new), with the same fonts and colours as the figures and a CSV beside every plot.

Not in this overhaul: time playback (transient cases are the tutorial sweep's business first),
compare, forces. They go on the backlog with a line each in §6.

## 3. Design

### 3.1 The guided Results card becomes the Looks card

```
6  Results                                   ▾
   [Velocity] [Pressure] [more ▾]            ← field chips, as now
   Surface  Slice  Iso  Stream  Vectors  Clip ← look tiles, one active, icon + word
   ── controls of the active look ──
   Slice:  axis X Y Z  ·  position ──●──  ·  [+ parallel]   (tooltip: drag the plane in the view)
   Iso:    value ──●──  ·  count 1 3 5
   Stream: seeds  line | sphere | inlet  ·  count 50 200 500
   Vectors: every 1 2 4  ·  scale ──●──
   Clip:   axis  ·  position  (shows the surface colouring inside)
   ── legend ──  Range [auto] [min][max]  ·  Map [viridis ▾]  ·  Bands [smooth 8 16]
   [Probe] [Plot over line] [Export image…] [Saved views ▾]
```

Each look maps onto the stores that exist (`results.view`, `slices`, `iso`, `arrows`, `streamlines`),
so Full mode's sections keep working and show the same state. New model files, all pure and tested:
`panels/lookModel.ts` (which look is active, its two-or-three controls, how they write the existing
stores), `panels/legendModel.ts` (range mode auto/locked/manual, map, bands, format, placement),
`panels/exportModel.ts` (size, background, what to composite, the sidecar JSON).

### 3.2 Colour maps

`state/colormap.ts` grows from one table to a registry: `viridis` (default), `magma`, `coolwarm`
(diverging, centred on zero when the range spans it), `turbo`, `greys`, plus A2A's `aurora`, `ember`,
`ocean`, `iceburn` (`fluent_viz_v2.py:88-117`, 5-6 hex stops each; copy the stops, not the code).
Every consumer (`fields.ts` tint, slice, iso, legend gradient, arrows by speed) reads the active map
from the results view; `tintFor(values, min, max, bands, map)`. Reverse is a flag on the view.

### 3.3 Legend

`FieldLegend.tsx` gains: drag handles on the bar's ends that write `lockRange` (A2A's `_LegendBar`),
double-click on a tick to type the value, a format menu (auto / fixed n / scientific / significant
n) in `legendModel.ts`, a placement choice (right/bottom/floating, remembered per project), a print
size toggle (fonts 1.5x, thicker bar) that the export uses. The drop readout shows Δp in Pa when the
material density is known (`Δp = ρ·Δp/ρ`, density from the fluid region's material; the kinematic
value stays in the tooltip).

### 3.4 Export

`viewport/exportImage.ts`: renders the scene off-screen at the chosen scale with `scene.capture`
(already one task with `renderFrame`, no `preserveDrawingBuffer`), hides the grid, triad, scale
bar and pick highlights for the frame, composites the legend (rendered to a canvas from the same
gradient and ticks), an optional title, and the scale bar; writes PNG with `tEXt` metadata
(`stokes:view` JSON) and offers the sidecar `.json`. Background: viewport, white, transparent.
Sizes: 1x, 2x, 4x, or W×H. One button in the card, a small dialog with those four choices and
"Remember". Later: SVG legend, PDF contact sheet from saved views.

### 3.5 Saved views

`state/views.ts` + `panels/SavedViews.tsx`: a named list in the project document (schema change:
`project.views`, Python side `stokes/model/views.py`, commands `view.save`, `view.list`, `view.apply`,
`view.remove`), each holding camera, look, field, range, map, bands, slice/iso/stream settings,
legend placement. "Export all" runs 3.4 over the list.

### 3.6 Plot over a line

Two clicks in the viewport (or two probe points) make a line; the server samples the field along it
(`/api/results/line/{run}?from=&to=&n=` via the agent's `extract` on the internal mesh, cached like
planes); the card shows a small plot (same component family as `ResidualPlot`), with CSV and PNG
export. Lines are saved with views.

### 3.7 Defaults that make the first picture right

On the first show of results: fluid body only, solids faded (fade exists), slice on with the field
if the mesh is 3-D, surface colouring if 2-D (one cell thick), camera fitted to the fluid, orthographic
when the shortest axis is under 5 % of the longest, legend right, viridis, smooth, auto 1-99 % range
with the 0-100 % range one click away ("Full range" in the range menu).

## 4. Briefs (for Sonnet builders; Opus reviews each before merge)

- **R1 Colour maps + legend model** (pure TS + tests): registry, `tintFor` with map, diverging
  centring, legend format/placement model, drop in Pa. No UI beyond the legend's menus.
- **R2 Looks card** (guided): tiles, per-look controls wired to the existing stores, "More…" to the
  Full sections, defaults of §3.7. Driven in the browser on pipe4 and on a 2-D tutorial (cavity).
- **R3 Export image**: `exportImage.ts`, the dialog, metadata, sidecar; a test that the composite's
  pixels at the legend's corners match the gradient; a browser check that the PNG opens at 4x.
- **R4 Saved views** (Python schema + commands + TS list): with journal/undo semantics as other
  project writes; "Export all".
- **R5 Plot over line**: agent sampler + route + card plot + CSV.
- **R6 Legend handles**: drag/double-click on the legend writing the range; print-size toggle.

Order: R1 → R2 → R3 (the three that make a publication figure), then R6, R4, R5. Each brief lists
the files above, the acceptance in §5 and the GUI text rule.

## 5. Acceptance (what the owner checks)

1. pipe4: open Results, press Slice, Export at 4x white → a 5600-px-wide PNG with a lengthwise
   velocity slice, viridis, legend right with "|U| (m/s)" and 6 ticks, no grid/triad; the sidecar
   JSON re-applies the view.
2. cavity (tutorial): 2-D, surface colouring, orthographic, pressure with coolwarm centred on zero.
3. Streamlines from the inlet on pipe4 coloured by speed; Iso at 0.015 m/s; Vectors every 2 on the
   slice; each exported in one press and each readable at print size.
4. Save three views, export all, get three PNGs named after the views.
5. Plot over the pipe axis: a parabolic-ish profile with a CSV of the samples.
6. No sentence on the card; every control has a tooltip.

## 5a. First browser pass over R1-R3 (2026-11-01 14:50, pipe3, 1400×900)

Works: the Looks card (field chips + "more ▾" with U_x/U_y/U_z, six tiles, Slice active by
default on the 3-D pipe, Axis/Position/Add parallel, Range/Map/Bands rows, Probe/More…/Export…/
Report…); the Y axis cuts the pipe lengthwise and colours it; Export… opens the dialog (1x/2x/4x/
W×H, viewport/white/clear, legend, scale bar, title, settings file, remember) and a 2800×1632 PNG
came out at 2x.

To fix next (R4 polish, before saved views):
- **First-show camera**: after `firstShowDefaults` the view sat zoomed into the outlet end; the
  fit should frame the fluid body after the slice is on.
- **Slice edge speckle**: the lengthwise slice is drawn with its cell-edge lines (black dashes);
  for a figure the edges must be off unless asked (`sliceLayer` edges default, or hide them in the
  export frame).
- **Axis none-selected** on open: the active axis is not derived from the run's default plane
  normal; show it.
- **Position label "0 mm"** reads as "not set"; show the coordinate along the axis ("y = 0 mm")
  or the fraction.
- ~~**Iso is disabled for velocity**~~ Done 16:05 (49d6c9e, 4b5abba, 3615568): the agent derives
  `U_mag` from `U` before the contour, the route parses a value in m/s, the card lists speed first;
  a teal tube at 0.288 m/s on pipe3 in the browser.
- **First connect still 10-20 s** on pipe3 with the loop idle (py-spy): the tutorials card no
  longer asks for the catalogue (e64d40f), so what remains is client-side load or the geometry
  activation's GIL time - measure next (the Windows app opens with `--project`, so users see it).
- **Streamlines still viridis** (R1 note) and `MAX_SEEDS` 40 vs the card's 50/100/200.
- **One-cell-thick cases**: the server should say a mesh is 2-D (an `empty` patch, or one cell
  along an axis) so the cavity comes up surface + orthographic (acceptance 2).
- **Long project paths** (Windows 260): a slice at a chosen plane is cut and then not cached
  (ENOENT), so the card shows nothing; the verification projects were moved to a short folder.
  See GUIDED-MODE-STATUS.
- **The legend overlaps the scale bar** in Full mode at the top centre (place it left, under the
  strip, as in Guided).

## 5b. Second browser pass, after R4a (17:00, pipe3)

- Axis chip lights (Z for the run's cached default plane), position reads `z = 0.5 mm`, `y = 0.1
  mm` after the switch; Export… on the card; the speed Iso draws a teal tube at 0.288 m/s.
- **The dark dashes on the slice are NOT edges, contour lines or arrows** (edges off, Vectors
  on/off changes nothing, Section on/off changes nothing): regular rows of short dark segments
  along the flow on a flat fill. That is lighting on sliver triangles with inconsistent winding
  (the cut's `drop_zero_area` leaves near-zero ones, and a lit single-sided material shades them
  dark). A figure's slice must be **unlit and double-sided** (the colour map is the information;
  shading hides it), which also removes the artefact. R4b.
- **First-show camera still frames the outlet end** on pipe3 after the "fit when settled" change:
  the fit is not the fluid body's box. R4b: fit to the fluid part's bounds (`sourceBounds`), then
  the slice.
- A section cut at z = 0 was found active on opening (the Section panel showed "Remove
  section") — from the earlier Clip look or the slice's cut; a fresh Results open should start
  with no section unless Clip is pressed. R4b.

## 5c. Third browser pass, after R4b (17:40, pipe3)

- **The dashes are gone**: the lengthwise slice is a clean flat fill (unlit, double-sided cuts).
- No section is active on a fresh Results open; the Section tool is quiet.
- **First-show camera still frames the outlet end.** A manual Fit frames the whole pipe, so the
  settled fit fires too early or something re-frames after it (the slice layer, or the mesh
  patches arriving). Next: log the fit calls and their targets from `looksControl.fitWhenSettled`
  and `Scene.fitSources` in the console, and fit once more when the last patch lands. R4c.
- The run's cached default slice on pipe3 is still the old z-normal disc (cut before 0613047); a
  new run, or `run.time` at the same time, re-extracts it lengthwise.

- **Acceptance 2 (cavity, 2-D) passes** (17:55): the swept `incompressibleFluid/cavity` opens its
  Results as Surface, orthographic, face-on (+Z), fitted to the square, velocity coloured, with
  epsilon/k/nut under "more ▾".

## 5d. Fourth browser pass, after R4c/R4d (18:20, pipe3)

- The first-show fit fires with the right targets (`[stokes] first-show fit`: 4 patches + the
  slice, `slice: true`) and is face-on to the slice: with the run's cached z-normal plane that is
  a disc seen along z (correct for that plane); after Axis → Y the lengthwise cut fills the view,
  seen from +y, fitted. **Acceptance 1's view is reachable in one press** once the run's default
  plane is lengthwise (0613047; this project's cut predates it).
- Polish for later (R4e): when viewing face-on, choose the camera's up so the longest in-plane
  axis is horizontal (the pipe stood vertical because its long axis is z); and re-fit when the
  Slice tile is pressed while already active.

## 5e. Fifth browser pass, after R4e (19:15, pipe3)

- **The figure view in one press**: Axis → Y gives the lengthwise cut lying across the screen,
  face-on, fitted, unlit fill, legend at the left. Acceptance 1's view, short of the cached
  default plane.
- Polish: the fit frames the whole viewport width, so the card covers the pipe's left end — the
  fit should respect the card's 312 px inset in Guided mode (the `ContextToolbar` already does).
- The app's console badge shows 12 errors on this project; read them in the next pass.

## 6. Backlog after this

Time playback for transient runs (the tutorial sweep decides how many we have), side-by-side
compare, forces/Cd/Cl from `postProcessing`, log-scale colouring, VTK export of cuts, orbit GIF, PDF
contact sheet, an interactive plane widget in the viewport (drag the slice; `sectionDrag.ts` is the
start).

## R1 done (2026-11-01)

Colour-map registry and legend model, `web/` only. New: `src/panels/legendModel.ts` (+ test).
Changed: `src/state/colormap.ts` (nine maps, `tintFor`/gradients take a map, reverse and the
zero-centring of diverging maps; `COLORMAP`, `colorAt`, `gradientCss`, `steppedGradientCss` unchanged
for viridis), `src/panels/fieldsModel.ts` (optional legend fields on `ResultsView`, percentile auto
range, `patchTint` takes a map), `src/panels/resultsApi.ts` (optional `p01`/`p99`),
`src/panels/FieldsPanel.tsx` (Map and Range menus), `src/viewport/fields.ts`, `sliceLayer.ts`,
`isoLayer.ts`, `arrowLayer.ts`, `FieldLegend.tsx`; tests `colormap.test.ts`, `isoLayer.test.ts`.
Open: the drop readout stays kinematic until the fluid density is read (`material.evaluate`);
streamlines are still viridis (`Viewport.tsx`/`scene.ts` `setStreamlines` need the map); the server
does not send `p01`/`p99` yet; legend placement and print size are state only (R2 draws them).

## R2 done (2026-11-01)

The guided Results card is the Looks card, `web/` only. New: `src/panels/lookModel.ts` (+ test, 58 cases),
`src/guided/looksControl.ts`. Changed: `src/guided/steps/ResultsCard.tsx`, `src/guided/guided.css`. Not touched:
`stokes/**`, `src/viewport/**`, `FieldsPanel.tsx`, `streamlineModel.ts`, `isoModel.ts`, `IsoSection.tsx`.

**Shape.** `lookModel.ts` is pure: `activeLook` reads the stores (iso shown > stream lines drawn > arrows on a slice >
slice > section > surface; Stream and Vectors outrank Slice because they stand on one), `activationActions` and
`controlActions` return `LookAction`s as data, `applyLookActions` is a reducer over the subset of the store the looks
touch (`results`, `slices`, `iso`, `streamlines`, `arrows`, `section`, `orthographic`, `hiddenNodes`) and leaves the
asks (`askIso`, `askStreamlines`, `fit`) as requests. `controlsOf` gives each look's controls as data (choice, slider,
button) and the card draws them without knowing what they mean. `looksControl.ts` writes the reducer's result in one
`setState` per press (so `viewport/fields.ts` engages and releases the slice's cut on a coherent state) and makes the
two server asks with the Full-mode sections' own commands and stores. `firstShowDefaults` (plan §3.7) is applied once
per project (a module `Set` keyed by project, as `GeometryCard` keeps `guessed`), after the scene knows the model's box.

**Wired.**
- Field chips, plus a "more" menu of the run's other fields by name; six look tiles (`aria-pressed`, disabled with a
  reason in the tooltip when the run cannot give the look); the active look's controls; Legend rows (Range
  Auto/Locked/Manual with Min/Max inputs when manual, Map = the whole registry, Bands Smooth/8/16) written to
  `results.view` through `legendModel`/`fieldsModel`; Probe, "More…" (`chooseMode("full")`, `arrangeFor("full")`,
  Results stage, `openPanel({panel: "results"})`), Report. Every `data-ws` hook of the old card is kept.
- Slice: axis X/Y/Z + position (0..1 along the axis, mm shown) + "Add parallel". The run's own mid-plane is the slice
  until a control moves it; the first move hands over to a slice of the list (`slices`) and turns the mid-plane off.
- Iso: value slider across the range of T or p (the field on the walls if it can be contoured) through
  `run.iso_surface`, same event/re-ask handling as `IsoSection`. Stream: seeds Line | Sphere | Inlet, count 50/100/200
  through `run.streamlines` on the run's slice; seeds are on the slice plane (`streamSeeds`, tested), the inlet's box is
  the mesh patch named like the `velocity_inlet` boundary. Vectors: every 1/2/4 (density 192/96/48) + scale. Clip:
  axis + position + Flip (`putSectionAt`/`setSectionInvert` semantics through the pure reducers).
- First show: fluid body only (solids hidden through `hiddenNodes`, the tree eye's own state), slice for a 3-D mesh,
  surface + orthographic for a sheet, view fitted.

**Not done, and why.**
- **Iso count 3 and 5** are listed but disabled ("One surface at a time for now"): the `iso` store holds one surface
  and the viewport's `isoLayer` draws one. Needs a list in the store and the layer (viewport: R3's neighbourhood).
- **Iso of speed** (acceptance 3, "0.015 m/s") is not possible: the server's `ISO_FIELDS` are `T` and `p`.
- **Stream count 500** became 50/100/200: the server draws at most 200 (`stokes/post/streamlines.py` `MAX_LINES`).
  Stream colours are still viridis (R1's open item: `setStreamlines` needs the map).
- **"One cell thick" cannot be known in the browser** (no per-axis cell count, no `empty`-patch type in the tree).
  `firstShowDefaults` takes `oneCellThick` when something can say it; the card passes none, so the box decides:
  under 5 % is a sheet. The OpenFOAM cavity tutorial (0.1 x 0.1 x 0.01 box, 10 %) is therefore treated as 3-D:
  slice on, perspective. Fix: the server adds `two_d`/cells-per-axis to `run.fields` or the case node; then pass it.
- **Plane drag in the view** does not exist, so Slice/Clip positions are sliders only.
- **Vectors "every N"** maps to the existing arrow density (the fastest 192/96/48 samples), not a literal every-Nth.
- **Fluid body only** hides the CAD solids; in the Results stage the mesh patches replace the geometry parts, so it may
  change nothing on screen for a case whose solids are meshed regions. Left as the brief says.
- Not driven in a browser (told not to start one): the visual layout of the six tiles at 300 px and the slider
  behaviours are checked by typecheck/build and the model tests only.

**Surprising.** `IsoSection` hides the iso on mount (its effect on `data?.run` runs at mount), so pressing "More…"
(Full mode mounts the Results panel) drops a running Iso look; the card then shows Surface. Pre-existing; left alone.
`activeLook` in a bare store reads Clip after leaving a slice only until `viewport/fields.ts` releases the slice's cut.

## R3 done (2026-11-01)

Publication image export, `web/` only. New: `src/panels/exportModel.ts` (+ test, 43 cases: settings, remembered
settings, pixel size and clamping, file name, sidecar, CRC-32 and the PNG `tEXt` chunk with read-back, the legend
bar's colours against `tintFor` and the CSS gradient, the layout rectangles for right/bottom/floating, title and
scale bar), `src/viewport/exportImage.ts` (`exportImage`, `download`, `downloadSidecar`, `currentExportView`),
`src/panels/ExportImageDialog.tsx` + `.css` (`openExportDialog()` for the guided card; mounted once in `App.tsx`).
Changed: `scene.ts` (`captureExport`, `maxExportSide`, `liveSize`, an `exporting` flag in `drawFrame`),
`camera.ts` (`freeArea` getter), `selection.ts` (`objects` getter), `FieldsPanel.tsx` (Image section: "Export image…"
beside Report).

**Wired**: scale 1x/2x/4x or W x H (clamped to the driver's limit and 80 Mpx, the dialog says "reduced"), background
viewport/white/clear, legend on/off at the view's placement (right and bottom take a strip so nothing is covered,
floating overlays on a panel), title, scale bar (bottom left of the model's rectangle), "Settings file" (sidecar
`.json`), "Remember" (`localStorage`, never the title). The frame leaves out the grid and axes, view cube,
selection outline and highlight, hovered/picked faces/edges/vertices, the measurement, the section handle and the
lift of a selected part; the camera's free rectangle is cleared so the model is centred. The PNG carries the sidecar in
`stokes:view` (ASCII-escaped JSON); name `<project>-<run>-<field>-<look>.png`. One task from draw to copy; still no
`preserveDrawingBuffer`.

**Not wired / open**: nothing has been run in a browser (no server was started), so the canvas calls in
`exportImage.ts` (gradient background, text fitting, `toBlob` at 4x) are checked by types and the layout tests only;
do the 4x check of §4. The contour lines of slices and the iso/stream/arrow layers use 1 px GL lines, so they are
thin at 4x (the edge lines are scaled; those are not). Streamlines are still viridis (R1 open item). The desktop
host can write text but not bytes, so the PNG is a browser download there and may not save; the `.json` goes
through the host's dialog. A perspective view's scale bar is measured at the centre (the dialog tooltip says so).
Tags/glyph overlays are not hidden for the frame (not drawn in the Results stage). Side find:
`captureThumbnail` restores the size with `canvas.width/height` (device pixels) instead of the CSS size, which is
wrong at a pixel ratio above 1; left alone.

## Review fixes for R1-R3 (2026-11-01)

From the Opus review of `039662d`, `bcbd4c7`, `ecf2b86`, `ea6a0a4`. `web/` only.

**Done as proposed.**
- *Full range, then Locked or Manual* froze the 1-99 % range, not the one on screen. `chooseRange` moved from
  `FieldsPanel.tsx` into `legendModel.ts` (so it is tested) and locks first, then clears `fullRange`.
- *Stream after a moved slice* now hides the slices of the list and puts the run's mid-plane on, the plane the server traces on.
- *First-show `hideSolids`* is gone from `firstShowDefaults` (it wrote the session-wide `hiddenNodes`); the action,
  its reducer and a test stay for the day it can target the Results stage's mesh regions. `bodies` is optional now and the card no longer reads them.
- *Pending first picture over a chosen look*: `press` and `act` add the project to `defaulted` first.
- *IsoSection* hides the iso only when the run CHANGES (`useRef` of the last run), and `looksControl.cancelIsoAsk()` (new, the old inline `isoSeq++`/`isoStop` pair) drops a cut still in flight.
- *Slice and Clip shared a `position` slider*: each control row is keyed `${look}:${id}`, so the draft cannot carry across looks.
- *Sidecar* records `rangeModeOf(view)`, the mode in force, not the stored flag.
- *Export legend* draws 6 ticks (`EXPORT_TICKS`); *`MAX_PIXELS`* is 33 M (was 80 M), commented with this review.
- *`captureExport`*: only the drawing buffer is sized for the frame; a lost context after the frame returns `"lost"`, which `exportImage` throws as `ExportMemoryError` and the dialog shows as an error toast ("Export failed: out of graphics memory, try a smaller size"), the dialog staying open; the restore is last and guarded (`paint` loop and the final redraw each in `try/catch`).
- Text: stale "Lock range" tooltip; the card's notes are a word or two with the sentence in the tooltip (`noLinesNote`, `isoRefusalWord`, `Report` takes a `tip`; the probe notes likewise); every `title=` on the card became the `Tooltip` component; `.looks-tile:focus-visible`; `tintFor` decides zero-centring once before its loop.

**Changed approach.**
- *Overlay*: the review said to call `sizeOverlay()` at the two sites too. It is a 2-D canvas sized from the CSS box times the pixel ratio, not the frame, so it costs nothing at 4x, and resizing it clears a marquee being drawn. Left out; only `getDrawingBufferSize` is called.
- *Tooltip on a row*: `Tooltip` showed on keyboard focus only when the wrapped element itself matched `:focus-visible`, so a label on a row div (the Looks card's slider and chip rows, as `FieldsPanel`'s fade row) never showed from the keyboard. It now tests the element that took focus (`e.target`); for a focusable trigger that is the same element as before.

**Not done.** The disabled-reason `title`s on the `Chips` buttons (`guided/controls.tsx`, shared by every card) are still native titles; wrapping them touches all cards. The Export dialog's other errors are still a line in the dialog (only memory is a toast).

## R4a done (2026-11-01)

Polish of the Looks card and its viewport layers, `web/` only, from the "To fix next" list of §5a. Nothing was run in a
browser (told not to start one): every item is checked by typecheck, build and tests, and the camera and legend items
need the owner's eye.

**Changed.**
1. *First-show camera.* `firstShowDefaults` now ends with `{kind: "fit", settle: true}`. The controller
   (`looksControl.fitWhenSettled`) polls every 250 ms and asks the scene's own fit (`requestFit`, the `F` key's action; no
   new camera path) once the model's box is the same for two looks in a row and the mid-plane slice (and any slice of the list)
   is in the scene; it fits anyway after 40 looks (10 s). The rule is pure and tested (`stepFitWatch`, `sameBox` in
   `lookModel.ts`). **Cause not measured**: I could not see the zoomed-in view, so the cause is inferred. The fit used the
   parts the scene held at the moment of the press, and the slice's geometry and the Results stage's mesh patches arrive
   after it (`flushContent` rebuilds the fit points from whatever parts exist). If pipe3 still opens zoomed after this, the
   next suspect is `Scene.fit` being aimed before the camera's own orbit target is updated: look there.
2. *Slice edges off.* The black dashes were the cell edges of the slice source, which the Display menu's "Mesh with cells"
   (left on from the Mesh stage) draws for every mesh source. There was no per-slice toggle, so one was added:
   `SliceSpec.edges` (default false; "Cell edges" switch in the Slices section), `results.view.sliceEdges` for the run's own
   mid-plane (absent is off; switch in the Results panel's Slice section), and `Scene.setSourceCellEdges(source, segments,
   own)`: an `own` source's edges ignore the mesh mode, so the walls can show their cells while the cut stays clean. The
   iso-surface draws no cell edges (its source never gets any), so nothing changed there. The Looks card's Slice look has
   no Edges control (two or three controls per look); the switch is in Full mode.
3. *Axis read back.* The card read the plane from the solver settings, which are empty for the default plane. `midPlaneOf`
   in `looksControl.ts` now falls back to the flat axis of the slice in the scene (`slicePlaneOf`'s own rule, the one the
   section cut uses), so the chip of the plane on screen lights. It is empty until the slice has arrived, and the card
   re-reads when the cut sets the section. The same helper now feeds `askStreamlines`, whose seeds used to fall back to a
   z plane for a default plane that is not z (so seeds on a lengthwise y slice were off the plane; not seen, found by reading).
   The brief's "slice header normal" is not on the wire (`run.fields` carries no normal): the slice's bounds are what the
   client has.
4. *Position label.* `y = 0 mm`, and the fraction ("50 % of the way along") in the tooltip; Clip's position the same.
5. *Streamlines use the colour map.* `streamlineSegments`/`streamlineLines`/`Scene.setStreamlines` take the map and
   `reverse`; `Viewport.tsx` passes `legendOf(results.view)`'s. The lines are still ranged over their own speeds (not the
   field's legend range). `MAX_SEEDS` (40) is **left as it is**: only Full mode's rake uses it, as its COUNT, so raising it
   would make that section draw 200 lines. The card's 50/100/200 go through `streamSeeds` (cap 200 = the server's
   `MAX_LINES`) and are not limited by it.
6. *2-D cases.* `RunFields.twoD?` (read from `two_d` when it is a boolean, else absent); the card passes
   `oneCellThick: true` to `firstShowDefaults` when it is true: Surface, orthographic, fit. Only `true` is acted on; false or
   absent leaves the box to decide. **Server side still to do**: `run.fields` does not send `two_d`.
7. *Legend placement in Full mode.* `.field-legend` starts at `top: 62px` (under the strip's row, as `.guided-stack` does)
   instead of 12 px. Guided is unchanged (the stack positions it statically).

**Not done / open.**
- Nothing here was seen on screen. In particular the legend's 62 px, the settled fit on pipe3 and the clean slice need a
  browser pass; the 2-D case also wants a face-on view (`Scene.fitFrom` along the thin axis) rather than the current
  direction, which would be a camera change I was told not to write.
- Slice cell edges lost their link to "Mesh with cells": a user who wants them on the cut turns on the slice's own switch.
- The Looks card's Slice look has no Edges control.

## R4b done (2026-11-01)

Second polish pass, `web/` only, from §5b. Nothing was run in a browser (told not to start one): the four items are
checked by typecheck, build and tests (146 files, 1925 cases); the dashes, the camera and the section need the owner's eye.

**Changed.**
1. *Cuts are unlit and two-sided.* `PartStyle.unlit` (new) is set by the three cut layers (`fields.ts` mid-plane, `sliceLayer.ts`
   slices and the section cap, `isoLayer.ts`); `Scene.setSource` builds such a source with `createLook(.., unlit)`, a
   `MeshBasicMaterial` (vertex colours through the same `tint` attribute, `DoubleSide`, `toneMapped: false`, the polygon offset kept)
   and never batches it (a batch's look is the lit PBR one). The mesh patches stay lit. No normals, so the sliver triangles cannot
   shade into dashes, and no specular, so the colours do not move with the view; no tone map, so the colours are the legend's.
   `SceneStats.unlitParts` counts them for the `window.__stokesScene` probe. Pick, outline and section caps use the same meshes
   as before. Tests: `materials.test.ts` (new), the recorder scenes of `fields.test.ts` and `isoLayer.test.ts` assert the style of
   mid-plane, list slice, cap and iso. **Not seen**: that the dashes are gone (cause inferred from §5b, not measured).
2. *Fluid fit.* `fitTargets.ts`: `fitSourcesFor` (the fluid's mesh patches, plus a slice that is on; before patches, the fluid part;
   else the scene's own rule) and `namesOfSources`; `Scene.fitSources(sources, from?)` and `sourceIds()`; `looksControl.fitNow` is
   used by the settled first-show fit and by every `fit` request of the card (the store's `requestFit`, the `F` key, is unchanged).
   The rig keeps those fit points until the next flush replaces them. Tests: `fitTargets.test.ts`.
3. *Clean section.* `staleSectionActions` (in `lookModel.ts`): every look but Clip clears the section when no slice that stays on is
   cutting (Surface and Iso always; Slice/Stream/Vectors when no slice shows) and asks `dropSavedSection` first. That request is
   `fields.ts` `forgetSavedSection`: the slice's cut remembered the user's section and gave it back when the slice went, which
   is how a stale z = 0 returned on Surface. `engageSliceCut(.., forget)` / `forgetSaved` in `sectionState.ts` are the pure
   parts; a forget asked before the cut engages is carried to the engage. `firstShowDefaults` does the same for the first
   show even when the look is already the active one. One existing test changed meaning: Surface after Iso no longer keeps the
   section a slice cut set.
4. *Face-on sheets.* The fit of a sheet's first show has `faceOn`; `faceOnDirection(box)` is +Z/+Y/+X along the thinnest axis (the
   `view.from.front/top/right` vectors of `viewDirections.ts`), read at the time of the fit; orthographic was already there.
   It applies to every first-show sheet (the server's `two_d` or a box under 5 %), not only `two_d`.

**Not done / open.**
- The slice's *own* cut (engaged by `fields.ts` whenever a slice is on, so the walls are cut at the slice) is still a section in
  the store, so the Section panel offers "Remove section" while a slice shows. If the owner meant that cut too, the next step is
  to stop engaging it when the walls are faded; not changed here.
- Pressing the look that is already showing does nothing, so it does not clear a stale section either.
- The fit points of `fitSources` are the shown parts only; a patch hidden in the tree is left out of the frame.

## R4c done (2026-11-01)

First-show camera, `web/` only. Nothing was run in a browser (told not to start one): the cause is read from the code, not
measured; typecheck, build and tests (146 files, 1932 cases) are green. A browser pass on pipe3 decides.

**What I found (two causes, both in the scene, neither in the slice layer).**
1. *The fit aimed at a stale centre.* `Scene.fitSources` (R4b) swapped the rig's fit POINTS to the fluid's patches but left
   the rig's CENTRE alone, and `frameView` aims the camera at that centre (`target = center`). The centre is only set by
   `flushContent` (`setContent`: the sphere of every part at that flush). The flush is throttled, and the Results stage's
   patches land in a batch after the slice, so the centre a settled fit used was the one of an earlier, smaller scene, off the
   body; the `F` key works because by the time it is pressed a flush has run with everything in. That is the outlet end.
   The slice layer calls no fit of its own (grep: the only fit callers are `Viewport` `fitRequest`, the gizmo, `frameContent`,
   `fitSources`).
2. *A later source re-framed.* `fitSources` leaves the camera untouched (`fit` clears `userTouched`), so `frameContent`
   ("a source came or went and the user has not taken the camera: fit") fired again on the next flush that had a new source,
   which includes the cut's arrival (slice, section cap, iso), with the ordinary rule.
   And the settled watch looked at the model's box (shown parts, slice included) and only waited for the slice, not for the
   patches, so it could settle before the last patch landed.

**Changed.**
- `CameraRig.setFitPoints(points, center?)`; `fitSources` passes the centre of the targets' own box. (Test in
  `cameraFitWiring.test.ts`: the same points aimed at the points' centre, and at the stale one without it.)
- `isCutSource` sources (slice, list slices, cap, iso) no longer set `sourcesChanged` when they arrive or go
  (`setSource`/`removeSource`), so a cut never re-frames. Patch and part arrivals still do, as before.
- `looksControl.wantedSourcesReady` now also needs every mesh patch the tree lists in the scene
  (`missingPatchSources` in `fitTargets.ts`, tested); a patch the server cannot give never lands, so the 40-look (10 s)
  limit of `stepFitWatch` still decides then. The box the watch compares is the union (`unionBox`, tested) of the fit
  targets' `sourceBounds`, not the model's.
- `console.debug("[stokes] first-show fit", {targets, bounds, sources, faceOn})` at the settled fit (silent at default
  levels; shows in the Verbose level).

**Not done / open.**
- If pipe3 still opens off the body, the breadcrumb says what was fitted: `targets` should list every `mesh:<id>` plus the slice,
  `bounds` the whole pipe.
- A patch that arrives after the first show (a second mesh result) still re-frames while the user has not touched the camera.

## R4d done (2026-11-01)

A slice is viewed face-on, `web/` only. Nothing was run in a browser (told not to start one); typecheck, build and tests
(146 files, 1936 cases) are green. The R4c breadcrumb showed the right targets, so the cause was the camera's DIRECTION
(the default oblique one, looking almost along pipe3's axis), which no fit from there can cure.

**Changed.**
- `viewFromFor(look, axis, twoD)` (`lookModel.ts`, pure, tested): for the Slice look it is the + side of the slice plane's normal
  (`directionVector`: X -> right, Y -> top, Z -> front, the same vectors as `faceOnDirection`); null (keep the direction) for
  Surface, Iso, Stream, Vectors and Clip, for no axis, and for 2-D (the sheet's own `faceOnDirection` rule, orthographic as now).
  The camera stays perspective for 3-D.
- `LookAction` `fit` has `slice?: boolean`: the fit looks at the slice that is on. Asked (settled) by the first show of a 3-D
  mesh (replacing the plain fit), by pressing the Slice tile, and when the Slice look's AXIS changes (a position move or
  "Add parallel" does not touch the camera). The first show keeps one fit only.
- `looksControl.ts`: the controller reads the slice's axis at the time of the fit (`lookSliceOf` on the store and the scene's
  plane), so the slice that is on then decides; `fitFromNow` falls back to the sheet's face-on, then to the camera's direction.
  The `[stokes] first-show fit` breadcrumb now also lists `slice` and `from`.

**Not done / open.** Pressing the Slice tile while it already shows still does nothing (so no re-fit). The axis fit settles on
the box of the fit targets, which hold the old slice until the new cut lands; the patches dominate that box, so the framing is
unaffected, but a browser pass on pipe3 (y-normal cut seen from +y, pipe across the screen) is the check.

## R4e done (2026-11-01)

Face-on views keep the long axis horizontal, and Slice re-fits, `web/` only. Nothing was run in a browser (told not to start one);
typecheck, build and tests (146 files, 1946 cases) are green. A browser pass on pipe3 (lengthwise cut from +y lying across the
screen; orbit away, press Slice, get the figure back) is the check.

**Changed.**
- `upFor(from, box)` (`viewDirections.ts`, pure, tested): for a `from` along an axis, world Y up puts X (from +/-Z), Z (from +/-X)
  or X (from +/-Y) across the screen; when the other in-plane axis of the fit targets' box is the longer one, up is turned onto
  the across axis (sign picked so the longer axis runs along its + direction). Ties, oblique `from` and no box keep [0,1,0].
  Pipe along z from +y gives up = +x.
- The camera takes it: `ViewState.up`, `CameraRig.fit(dir, up)` / `fitState(dir, up)`, `frameView` and `viewBasis` take an up axis;
  `Scene.fitSources` computes `upFor(from, box of the fit corners)`, so it covers the 3-D slice and the 2-D sheet's `faceOnDirection`
  alike. The up turns with the heading during the move and lands exactly; the orbit controls' own frame (`_quat`) turns with it
  so a drag from the figure view does not jump. A fit with a new direction and no up resets to world Y; a fit that keeps the
  heading keeps the roll.
- `activationActions("slice")` while Slice already shows is now `[fit {settle, slice}]` (was `[]`); the first show still has one fit.

**Open.** The orbit controls' `_quat`/`_quatInverse` are reached by a cast (private in three); the wiring test mocks OrbitControls, so
the real drag-after-fit behaviour is for the browser pass.

## R4f done (2026-11-01)

The fit keeps the figure clear of the guided card, `web/` only. No server, no browser; typecheck, build and tests (147 files, 1951 cases)
are green. A browser pass on pipe3 (face-on Axis -> Y: the pipe's left end beside the card, not under it) is the check.

**Changed.**
- `guided/guidedInset.ts` (pure, tested): `guidedInset(mode)` is `{left: 312, top: 52}` in Guided mode (the card column and the strip's row)
  and `{0, 0}` in Full. `GUIDED_CARD_INSET`/`GUIDED_STRIP_INSET` match `guided.css`.
- `Viewport.tsx`: the free rectangle the docks effect hands to `rig.setFreeRect` is `freeRect({left: dockEdge("left") + inset.left,
  top: inset.top, ...})`; the effect re-runs when the mode changes. The fit frame follows that rectangle (R3), so a fitted figure is
  centred in the space beside the card and under the strip. Full mode is unchanged (inset 0; its docks set the rectangle as before).
- `App.tsx`: the `ContextToolbar` left inset takes the same constant (was a literal 312).
- The export frame is unaffected: `captureExport` clears the rectangle (`setFreeRect(null)`) for its frame and puts back the one it read.

**Open.** The inset applies whenever Guided mode shows its stack (card and legend), even while no step card is open; the legend then
holds the column.

## R4 done (2026-11-01)

Saved views, in two commits: the model and commands (Python), then the web side. Nothing was run in a browser or against a
server (told not to start one): Python is checked by `tests/test_views.py` (12 cases), the web side by typecheck, build and
tests (150 files, 2007 cases); the menu, the camera restore and "Export all" need the owner's eye.

**Server.** `stokes/model/views.py` declares the `view` node type (`name`, `record`, `created`); `stokes/api/commands/view.py`
has `view.save {name, record}` (adds, or replaces by name, in place, as ONE undo step; the `/views` group is made inside the
first save, as `case.import` makes `/cases`, so undoing the first save leaves no group), `view.list` -> `{views: [{id, name,
created}]}`, `view.get {view}` -> the record, `view.remove {view}`; `view` is an id, a tree path or the name. All
`requires_project`. The node's tree name is generated from its id (`v_<hex>`; tree names are identifiers, a view's name is free
text up to 80 characters). Journal: `node_id` and `created` are recorded, so a replay finds the same ids. The record is kept as
written (a JSON object, up to 256 KB) and survives `project.save` / `load_project`. Test: save, list, get, remove, replace by
name, undo of a save, a replace and a remove, journal replay, save/load round trip, name checks.

**Web.** `panels/viewsModel.ts` (pure, tested, 42 cases): `recordOf` = `buildSidecar` + `stokes.extras` (the run's mid-plane slice
and its edges, the walls' fade, the visible slices in full, the section when no slice shows, the stream seeds); `readRecord`
(tolerant; a plain sidecar `.json` from an export applies too); `applyView` (what to write: the field, range, map, bands, legend,
slices, section, plus the Looks controller's actions for the iso, streamlines, arrows and projection, and the camera);
`viewSettled`; `exportAllNames`; name rules. `panels/viewsApi.ts` (the four commands, tested with a fake `execute`).
`panels/viewsControl.ts` (impure: reads the stores and the scene, applies in the Looks card's order, runs "Export all").
`guided/ViewsMenu.tsx` + `viewsMenu.css`: "Views ▾" beside Export… on the card: Save view… (the app's `PromptDialog`), one row per
view (press applies, × removes), Export all; the card's action row wraps so a fifth button drops to a second line. The card
changed in three lines (import, the `looks-actions` class, the component with the stream seeds and `defaulted`).
The camera is restored with a new `CameraRig.restore(state)` (target, direction, span, up; ends a framing move; marks the camera
as the user's so a later patch does not re-frame it; tested in `viewport/cameraRestore.test.ts`). The sidecar's camera gained an
optional `up` (`exportModel.ts`, `exportImage.ts` `cameraOf`, now exported), so a face-on view with the long axis across the
screen comes back exactly; files from before R4 have none and leave the up vector as it is.

**Export all** applies each view, waits (`SETTLE_DELAY_MS` 450 for the slice debounce, then `viewSettled` every 100 ms up to 20 s:
results read, no slice cut loading, the asked iso drawn at its value, streamlines not computing), runs `exportImage` with the
dialog's REMEMBERED settings (else 2x on white; never a title), downloads `<project>-<run>-<view name>.png` (names that sanitise
alike get `-2`, `-3`), waits 250 ms, and goes on; it stops at the first failure and says how many were written; the figure that
was on screen is applied again at the end.

**Not wired / open.**
- No outline row for `/views` (the outline files are in another agent's hands); the group is plain in the Full-mode tree.
- No `view.apply` command: applying is the front end setting its stores (the server has nothing to change).
- A view that has Clip's section and a slice is saved with the slice's cut as the only one (the section in the store is then the
  slice's own); its Clip half is lost.
- Streamlines are re-traced from the saved seeds, not stored; an iso is re-asked at its value. Both follow the server's answer, so
  a refused ask leaves the picture without it (the settle wait gives up after 20 s and exports anyway).
- Apply does not cancel a first-show fit already scheduled (`looksControl.fitWhenSettled`); pressing a view within the first ten
  seconds of the first picture could be re-framed. `onTouch` stops one not yet scheduled.
- `viewsControl.ts` and `ViewsMenu.tsx` have no unit test (they read the live scene and store); the model they call is tested.
- Several PNG downloads in a row may raise the browser's "download multiple files" prompt; the desktop host saves PNGs as a
  browser download only (R3's note).
- A field or iso the run lacks is skipped and named in a toast (`No T in this run`); the rest of the view is applied.

## R5 done (2026-11-01)

Plot over a line, in two commits: the server (Python), then the web side. Nothing was run in a server or a browser (told not to):
the Python side is checked by `tests/test_run_probe_line.py` (synthetic sheet, no OpenFOAM), the web side by typecheck, build and
tests (152 files, 2035 cases; one unrelated timeout under load, the file passes alone). The two clicks, the drawn line, the plot and the two downloads need the owner's eye on pipe4.

**Server.** `run.probe` takes `patch: "slice"` (as `run.streamlines` does) and `n` (int, default 0). With a two-point `polyline`
and `n >= 2` it samples `n` evenly spaced points, ends included (capped at `MAX_LINE_SAMPLES` = 1000), and answers `points` and
`along` (cumulative distance from the first point, metres) beside `values`; an `n` below 2, a point, or a longer polyline behave as
before. `_slice_field` was split into `_slice_cut` (also returns the slice's triangles) with `_slice_field` kept as its wrapper.
**Deviation from the brief's cache key `<run>|slice`:** the slice is NOT put in the triangulation cache. `stokes.post.triangulation.
triangulation_of(positions, triangles)` wraps the slice's OWN cells (no Delaunay, nothing to build, so no "preparing" for the slice),
which (1) leaves a hole or a concave edge of the cut outside (a Delaunay of the points fills the convex hull and would answer a number
there), and (2) cannot serve a moved slice from the old one's triangulation (a fixed key would). Outside the cut still refuses with
`post.probe_invalid` ("outside"); an empty slice and a vector field refuse with the reason.

**Web.** `panels/lineModel.ts` (pure, 26 cases): `samplePoints`, `lineCsv` (`distance_m,<field>[<units>]`, 6 significant digits),
`lineTitle`, `lineFileName` (`<project>-<run>-<field>-line.csv|png`), the two-click reducer (`pressLine`, `pickPoint`), the ask and
reply (`lineAsk`, `readLineReply`), `rayOnPlane` / `carryToPlane`, `lineStale`, and the plot geometry (`lineChart`: x in one length
unit for the whole line, y padded, a flat line still draws). The store gained `line` / `setLine` (view state, no journal;
`{a, b, n = 100, phase, along, values, field, message}` plus `patch` and `seq`), reset on a project change. `viewport/lineLayer.ts`
draws the thin accent segment and two end markers (hidden for an export frame; `scene.setPlotLine`). `guided/lineControl.ts` (impure)
runs the pick, the ask with the probe's "preparing" retry and a sequence guard, `reconcileLine` (a new field, new results or a moved
mid-plane slice asks again along the same line; the ends are carried to the moved plane), and the CSV save (the host's save dialog
when there is one, else a download). `guided/LineBlock.tsx` + `linePlot.css`: the **Line** button (`data-ws="guided-line"`) beside
Probe and, under the buttons, the plot (viewBox 280x132, at most 140 px) with CSV, PNG and Clear; the curve is the colour map's mid
colour. `guided/linePlotImage.ts` paints the PNG at 2x on white with `--font-sans` / `--font-mono`. The click rides the probe's pick:
`Viewport`'s `onProbe` hands it to `takeLinePick` while a line pick is armed (the probe's own readout does not run) and re-arms for B.
`PointHit` gained an optional `ray` so a click on the slice is carried to the slice's plane along the camera ray (the click lands on
whatever face the pick met). With the mid-plane slice on the line is read on `slice`; otherwise on the patch under the first click.
`ProbePanel.tsx` is untouched.

**Also fixed in passing.** The probe's pick is one-shot in the scene but the store flag stayed true, so the Probe button stayed lit and
needed two presses to arm again; `onProbe` now clears it. `view.probe` cancels a waiting line pick.

**Open.**
- **The line is NOT yet saved with views** (plan §3.6 says it should be). That wiring comes after the views review (`viewsModel.ts`,
  `viewsControl.ts` are under review and were not touched); the line state is `{a, b, n, patch}` in the store, ready to be added to
  the record.
- Only the run's mid-plane slice can be probed on the server. A slice of the list (the Position slider of the Slice look writes one
  and turns the mid-plane off) is not in the `.stkfld` cache, so with only such a slice on, the line is read on the patch under the
  first click (a wall), not on the slice. A sampler for the list's planes (the plane route) is the follow-up.
- Off a slice, a line must lie on one flat patch: a chord across a curved wall leaves the surface and says "Outside the model".
- `lineControl.ts`, `LineBlock.tsx`, `linePlotImage.ts` have no unit test (they read the stores, the scene and the canvas); the model
  they call is tested. No Python test reads a real run's slice cache (the join is `run.streamlines`'s own, `_slice_cut`).
- The line is hidden for an image export frame, like the measurement; a figure that wants it drawn would need an option in R3's dialog.
- The count `n` has no control on the card (fixed 100; the store holds it).

## R4 review fixes (Opus review, 2026-11-01)

**Changed.**
- `viewSettled` now waits for the field values too. `FieldsController` and `SliceLayer` publish their in-flight counts to a new
  `loading` store field (`state/loadingState.ts`: `{fields, cuts, sliceFields}`), and the wait needs all three at zero. A cut that is
  cached and `ready` no longer lets the next view's PNG be taken with grey walls.
- The waiting and the loop moved to `panels/viewsRun.ts` (no store, no DOM, tested with a virtual clock): `settleWith` (resolves to
  whether it settled), `ApplyGuard`, `exportAllWith`, `exportAllSummary`. `viewsControl.ts` wires the real stores and scene.
- A late camera restore is skipped when a newer apply has begun or the camera is no longer where the apply put it (`ApplyGuard`,
  `sameCamera`). A restore with no `up` in the record resets to world Y up (`cameraUp`) instead of keeping a turned one.
- Export all: a view whose field the run lacks is not written (it would show the field that was on screen); a view that timed out or
  skipped a setting is written and named. The toast is short (`2 of 3 exported · Pressure: No p in this run`, at most two notes then
  `+N more`); every line goes to the console.
- `applyParsed` calls the new `cancelSettledFit()` (looksControl) so a waiting first-show fit cannot override an applied view.
- File names compare in lower case (`Front` / `front`) and take `-2`, `-3` until the name is unused (a generated `X-2` cannot meet a
  real `X-2`).
- Views menu: x gives an Undo toast (`undoRemoveView`: `history.undo` only while the removal is still the newest step); the card is
  `inert` while Export all runs and the progress is read by a polite live region outside the card.
- Python side (`7eb0c51`): the `/views` group cannot be renamed; id errors are validation errors.

**Open.**
- The in-flight counts are per layer, not per request: a request that is queued behind the fetch cap is covered only because the
  running ones keep the count above zero.
- The restore guard compares the camera to the stamp the apply took; a user who moves the camera and moves it back to exactly that
  place within a part in 100 000 is treated as not having moved.
- `viewsControl.ts` and `ViewsMenu.tsx` themselves have no unit test (they read the scene and the DOM); the logic they call is tested.
- A view skipped for a missing field is still applied to the screen before it is skipped (the figure is put back at the end).
- A view still exports with a refused iso after the full 20 s wait; it is named `timed out`.

## R5 + R4 review fixes, driven in the browser (2026-11-01, 19:45 laptop)

On pipe3, view along the pipe at the mid-plane disc (the cached default plane): the Line button, two
clicks on the disc, the segment with its end markers, the plot of |U| along 50.8 mm (100 samples
read on `slice`), CSV (`distance_m,U_mag[m/s]`) and PNG (960x600) downloads, Clear. Two defects
found on the way, both in the point pick that the probe, Measure and the line share:

- `7c3f0bb` the pick met no part (walls edge-on, fluid hidden): the scene now falls back to the camera
  ray's crossing of the slice's plane while a line pick is armed (`setPointPlane`/`planeAt`).
- `61669b6` a press on empty space armed the marquee, whose `pointerup` returned before the pick.
- `21f7ab2` the section handle's generous pick sphere covered the whole disc from that direction, so
  every press began a section drag. A point pick now takes the press before the handle.

Export all after the review fixes (`0d1dbb1`): two views with different fields AND planes
("Lengthwise speed" = U on Y, "Pressure Z" = p on the default disc) gave two 2800x1632 PNGs whose
sidecars carry `U_mag` with the Y plane and `p` with the default plane; the screen came back to the
view that was on it.

Seen, not done: with the line plot open the card is taller and the legend under it runs under the
bottom toolbar (legend placement should move to the right, or the card should scroll); the Views menu
is anchored to the button, so its rows move when the card's height changes; the line is not yet saved
with views (extras.line, after the review's settle changes); only the run's mid-plane slice can be
probed, a Position-slider plane cannot.

## R5b: the line travels with a view (2026-11-01)

**Done.** A saved view keeps the plot line. `extras.line` is `{a, b, n, patch}` and is written only while a line is on screen (phase
`ready`, or `preparing` with both ends); it is absent otherwise, and older records have none. The rules are pure, in `viewsModel.ts`:
`lineSpecOf` (state to record), `lineSpecOfRaw` (a record read with care: finite ends, a named surface, `n` clamped to 2..1000),
`lineStateOf` (record to a `preparing` store line). `ParsedView.line` and `ViewApplication.line` carry it.
Applying (`viewsControl.ts` `applyParsed`): a view with a line sets the store's line (ends, patch, count; `preparing`) and calls
`askLine()`, which has its own retry while the surface is prepared; a view without one calls `clearLine()`, so a view never shows
another view's line. `viewSettled` takes the store's `line` and counts `preparing` as not settled; `ready`, `failed`, `outside` and
`idle` are settled, and the 20 s limit still ends the wait. Export all needed nothing: the line is hidden in export frames; it only
waits for the line so the next view is not applied over a pending ask.

**Open.**
- A line on a Position-slider plane is not probeable (see R5); a view with it comes back `failed` or read on a wall.
- A line on `slice` applied to a view with the mid-plane off fails to ask (words on the card); it is not hidden.
- `applyParsed` (the `setLine` and `askLine` wiring) has no unit test, like the rest of `viewsControl.ts`; the model and the settle wait are tested.

R5b driven in the browser (20:05): a view saved with the disc line, the line cleared, the view applied
from the Views menu: the line, its 100 samples and the plot came back (`patch: slice`, phase `ready`).
The wrapping stack (6e4d250) keeps the legend beside the card and the plot in view on a 900-px window.
