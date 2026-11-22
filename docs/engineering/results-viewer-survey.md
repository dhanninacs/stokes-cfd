# The results viewer: what EnSight, Tecplot, ParaView, CFD-Post and Fluent offer, and what Stokes lacks

*2026-11-20. The research behind the results-viewer modernisation (branch `feat/results-viewer`).
The earlier gap table for reports and plots is `results-postprocessing-gaps.md`; this one is about the
viewer itself: its look, options, legends, selections and variables.*

## Sources

Vendor documentation and release notes (EnSight 2025 R1/R2 user manual and release notes, ParaView
5.12–6.1 release notes and colour-mapping reference, Tecplot 360 2022–2026 R1 pages, CFD-Post 2025 R1
reference, Fluent user guide pages, Simcenter STAR-CCM+ documentation), gathered on 2026-11-20. Where a
detail came from general product knowledge and no current page confirmed it, it is not relied on here.
Nothing is copied from any product: this is a list of capabilities and layout ideas.

## How the leading viewers are laid out

| Product | Structure | What it gets right |
|---|---|---|
| EnSight | Parts list (tree, derived parts nested under their source, name filter), Variables list, Feature panel per operation, Quick Actions on the selected part | Every visible thing is a part with its own colour-by; palette per variable with levels, scale (linear/log), banded/continuous, range "to current viewport", limit fringes, undefined colour; interactive hover probe; 2025 R1 defaults with ground plane, shadows and new lights |
| ParaView | Pipeline browser with eye toggles, Properties panel, Color Map Editor, MultiBlock Inspector | Rescale to data / visible / all timesteps / custom; log scale; above/below-range and NaN colours; discretise into N table values; legend position presets and drag, label format; hover tooltips; "Fast" perceptual default and warm-grey background in 6.0 |
| Tecplot 360 | Zones with per-zone style; plot sidebar of layers (mesh, contour, vector, shade, edge); slices, iso-surfaces and streamtraces as derived objects | Nice contour levels picked from the range; banded or continuous; publication-quality XY plots; translucency per zone |
| CFD-Post | Outline tree (cases, user locations, plots), Details view with Geometry / Color / Render / View tabs | Range Global / Local / User; linear or log; undefined colour; one place per object for what it is and how it is drawn |
| Fluent | Results tree (Surfaces, Graphics, Plots, Scene, Animations, Reports) | Contours with node values, global range, auto range, clip to range, banded/smooth; graphics effects (ambient occlusion, reflections, shadows) |
| STAR-CCM+ | Scenes > displayers > parts | Separates *what geometry* (parts) from *how it is drawn* (displayers); clamped range marked on the legend |

The common shape: **a list of what is shown** (parts, each with an eye toggle, colour-by and
representation), **a colour map editor with range modes**, **a legend with position, format and
levels**, **a hover readout**, and **derived quantities precomputed** (vorticity, Q-criterion, wall
shear, y+, total pressure).

## Stokes today (before this branch)

Measured on the solved duct bend (`.local/video-projects/bend_*`) through the running app.

- **Looks** (Guided): Surface, Slice, Iso, Stream, Vectors, Clip; parallel slices; iso of p, T and |U|;
  streamlines through the volume from the inlets (lines or tubes); a velocity lattice of arrows.
- **Colour**: nine maps (viridis, magma, turbo, greys, aurora, ember, ocean, cool-warm, ice-burn) with
  reverse; smooth or 8/16 bands; contour lines on slices; range Auto (1–99 %), Fit (to what is shown),
  Locked, Manual; a diverging map is centred at zero.
- **Legend**: a fixed card at the top left with the field title and units, five ticks, double-click to
  type an end, print size. Its *placement* setting exists in the model but is never used on screen.
- **Fields**: `U` (as |U| and three components), `p`, and `T` on the thermal solver; mean and
  turbulence fields when the case wrote them; field functions from the calculator.
- **Picking**: a Probe that answers one value per click; a Line with a plot.
- **Reports and plots**: surface integrals, XY on surfaces, histograms, history, functions (the
  Analyse block); an HTML report; image export at print resolution with a settings sidecar; saved views.
- **Full mode**: the Results tree (Surfaces, Graphics, Plots, Reports) with its panels.

Defects the results walk found (`web/walk_results_gui.mjs`, 2026-11-20): colouring by a velocity
component (U_x, U_y, U_z) shows **no legend at all**.

## Gaps, ranked by value

Value is what a CFD engineer loses without it, weighted by what the user asked for first: the look,
the options, the legends, the selections and every variable.

| # | Gap | Leaders | Stokes now | Slice |
|---|---|---|---|---|
| 1 | **Every variable and the standard derived quantities**: vorticity (vector and magnitude), Q-criterion, wall shear stress, y+, total pressure, pressure coefficient, heat flux where the run has T | all five | U, p, T, components, user functions | 2 |
| 2 | **A field picker that scales**: grouped (Velocity, Pressure, Turbulence, Temperature, Derived, Functions), with units and a one-line meaning, searchable | EnSight Variables list, ParaView array combo with icons | two chips and a bare `more ▾` select of names | 1 |
| 3 | **Legend done properly**: placement honoured (corners, bottom, drag), tick count chosen from nice numbers, data min/max shown, a marker when the range clips the data, log scale, units written with powers, a legend for every field shown (components had none) | ParaView, EnSight, STAR-CCM+ | fixed card, five ticks, components missing | 1 |
| 4 | **Out-of-range and undefined colours**: clamp to the end colours (today) or leave blank (Fluent "Clip to Range", EnSight "Limit Fringes"); a colour for missing values | Fluent, EnSight, ParaView | clamp only | 1 |
| 5 | **What is shown, as a list**: each surface (every boundary, each slice, the iso-surface, streamlines, arrows) with an eye toggle, its colour-by, opacity and representation (surface, surface with edges, outline) | EnSight Parts, ParaView pipeline, STAR displayers | one look at a time; per-patch visibility only in the tree | 3 |
| 6 | **Hover readout**: the value and position under the cursor, continuously, without a mode | EnSight interactive probe, ParaView tooltips | Probe mode, one click at a time | 3 |
| 7 | **Pinned probes and value over time** | EnSight, ParaView | one probe value | 4 |
| 8 | **A clean default picture**: feature-edge outline of the model over the colours, soft lighting, the legend placed where a figure wants it | EnSight 2025 R1, ParaView 6, Fluent effects | good already (PBR viewport); no outline on results | 1 |
| 9 | **Range per time step vs fixed, and "over all times"** | ParaView, EnSight | the run's range at the shown time | 4 |
| 10 | **Draggable plane widget** | all | slider and axis chips | 5 |
| 11 | **Clip by box or sphere; threshold; iso-surface of any field coloured by another** | EnSight, ParaView, CFD-Post | plane clip; iso of p, T, |U| | 5 |
| 12 | **Animation export** (MP4/GIF of time, camera orbit) | all | Play in the app; a transient run keeps two times | 6 |
| 13 | **Side-by-side views with linked cameras; run A minus run B** | EnSight, ParaView, Tecplot | none | 6 |
| 14 | **glTF scene export; annotations (text, arrows)** | EnSight, ParaView | PNG and sidecar | 7 |
| 15 | **Surface LIC, animated particles** | ParaView, Tecplot 2026 R1, EnSight | none | later |

## Done on this branch (2026-11-20)

| # | Gap | What landed |
|---|---|---|
| 1 | Variables | Every stored field (including the time-averaged and turbulence fields, `alphat`), the velocity and vector components, built-in dynamic and total pressure and Cp, and agent-computed vorticity, Q-criterion, wall shear stress, y+ and wall heat flux, each with units. A wall-only field is "no value" elsewhere. |
| 2 | Field picker | Grouped, searchable, with symbols, units, meanings and the reason a variable is unavailable; Guided card and Full mode. |
| 3 | Legend | Variable title with powers in units; band-edge labels; caps and a data line when the range clips the data; component legends fixed. |
| 4 | Out of range | Clip to range (Full mode). An undefined value is drawn in the surface's own colour. |
| 5 | What is shown | A Show row on the Guided card (every boundary and slice). Per-object colour-by and opacity: open. |
| 6 | Hover readout | The value under the pointer, continuously, from the colours' own values. |
| — | Menus | A colour-map list drawn as gradients; Fit in both modes; the Probe panel's own button; Analyse answers follow the choices. |

Still open, in value order: pinned probes and value over time (7), range over all times (9), a draggable
plane widget (10), iso-surfaces of any field coloured by another (11; the iso route and the iso field
list are outside the results files), animation export (12; transient runs keep two times while the
case writer purges older ones), side-by-side views and run differences (13), glTF export and annotations
(14), LIC and animated particles (15). Streamlines coloured by another field need the agent's tracer.

## The order of work on this branch

1. **Legend, look and field picker** (gaps 2, 3, 4, 8): a grouped field picker with units and
   meanings in Guided and Full mode; the legend honours placement and draws nice ticks, the data's
   min and max, clipped-range markers and powers in units; out-of-range colouring choice; component
   legends fixed.
2. **Variables** (gap 1): derived quantities computed where the data is. Vorticity and Q-criterion from
   the volume gradient, wall shear stress and y+ at the walls, by the agent when it extracts fields
   (`agent/stokes_agent/derived.py`); total pressure and the pressure coefficient as built-in field
   functions on the server; each with its units in `run.fields`.
3. **Selections**: the list of what is shown (gap 5) and the hover readout (gap 6).
4. Then down the table.

Room for regions (multi-region conjugate heat transfer, planned on another branch): the field cache
and the surfaces are per patch, so a region is a grouping of patches and a field a region lacks is
absent rather than zero. The list of what is shown groups by region when patches carry one.
