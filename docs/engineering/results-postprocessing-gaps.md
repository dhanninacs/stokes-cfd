# Results post-processing: Stokes against Fluent and Discovery

*2026-11-07. What ANSYS Fluent 2026 R1 and ANSYS Discovery offer for results, what Stokes had, what this
change adds, and what is still missing.*

## Sources

There is no Fluent or Discovery user manual on the reference PC: `C:\Program Files\ANSYS Inc\v261` has only the
help viewer and online-help maps. The catalogue below comes from:

* the Fluent 26.1 settings API tree, `fluent/fluent26.1.0/cortex/pylib/flapi/generated/solver/settings_261.py`
  (one class per setting, with the TUI names: `results.graphics.contour`, `results.report.surface_integrals`,
  `solution.report_definitions`, `results.custom_field_functions` …);
* the Fluent GUI form files `fluent/fluent26.1.0/cortex/resources/{results,pathlines,surfacedefs,Report}.fdl`
  ("Use Global Range", "Clip to Range", "Use Node Values", "Use Log Scale");
* string tables in Discovery's `Disco.UI.ResultsModule.dll`, `Disco.Post.dll` and `Live.Post.dll` (tool and
  command names only; Discovery's layout and its probe UX are not evidenced locally and are marked *inferred*).

Nothing was copied from either product: this is a feature list, not an implementation.

## Gap table

Legend: **had** = in Stokes before this change; **new** = added here; **gap** = still missing.

| Area | Fluent / Discovery term | Stokes before | Now |
|---|---|---|---|
| Contours, filled | `graphics.contour filled`, colour map, levels (`colormap size`) | Surface and Slice looks, smooth or 8/16 bands | had |
| Contours, lines | `contour_lines`, filled off | Full mode's slice switch only | **new** in Guided: Lines on the slice, at the band edges, neutral colour over the fill; `results.contour_lines` for any patch or slice (API, MCP) |
| Range | Auto range, Global Range off, Clip to Range, min/max | Auto (1–99 %), Locked, Manual | **new** Fit: area-weighted robust range of what is on screen (`results.range`). Clip to range: gap |
| Node vs cell values | `node_values` | point values only | gap (the field cache holds point values) |
| Log scale, number format | `colormap log_scale`, `format` | format and digits in Full mode | log scale: gap |
| Vectors | `graphics.vector` scale, skip, colour by | volume lattice, on a slice, scale, density | had; colour by another field: gap |
| Pathlines / streamlines | `graphics.pathline` release from surfaces, colour by any field, step, pulse | volume trace from inlets, rake, coloured by speed | colour by another field: gap (needs the host to sample it); pulse: gap |
| Particle tracks | `particle_track`, injections | none | gap (no DPM in the engines) |
| Iso-surfaces | `iso_surface` | p and T on the host | had |
| Planes, lines, points | `plane_surface`, `line_surface`, `rake_surface`, `point_surface` | slices, Line (two clicks), probe | had |
| Iso-clip | `iso_clip` | none | gap |
| XY plot along a line | `xy_plot` on a line surface | Line plot, CSV, PNG | had |
| XY plot on a surface | `xy_plot` with `plot_direction` | none | **new** `results.surface_plot`: x, y, z or radius from the surface's own axis, several surfaces as series |
| Histogram | `plot.histogram` | mesh quality only | **new** `results.histogram`, area-weighted |
| Surface integrals | `report.surface_integrals`: area, area- and mass-weighted average, facet/vertex min/max/avg, flow rate, mass flow, sum, integral, standard deviation, uniformity index (area, mass) | pressure drop inlet→outlet; monitor table of flow rate and mean p | **new** `results.surface_integrals`: all of these on any patch or the slice, per surface and net, units, CSV |
| Volume integrals | `report.volume_integrals` | volume min/max of scalars | gap |
| Forces and moments | `report.forces`, `report_definitions force/drag/lift` | none | gap (needs a `forces` function object at compile time) |
| Pressure drop | `pressure_drop` | legend readout | had |
| Expressions, custom field functions | `custom_field_functions`, `named_expressions` | none | **new** field calculator on the expression grammar (units checked); `results.function_save/_list/_remove` as project nodes; a function reports, plots and colours like a field |
| Report definitions vs iteration | `report_definitions` + `report_plot` / `report_file` | the solver's flow rate and mean p tables (`run.monitor`), Full mode only | **new** Guided History tab: each table against iteration or time, last value and whether it has settled. User-defined report definitions written into the case: gap (compile `functions`, owned elsewhere) |
| Transient results | solution animation, time step selector, `animations.playback` | `run.time` (any written time), Case card only | **new** Guided Time row: slider over written times, step, Play (waits for each frame), Loop; History labels its axis in seconds for a transient run |
| Turbulence and mean fields | any solver field, `UMean` from `fieldAverage` | `U`, `U_mag`, `p` (`T`) only | **new** the agent reads `UMean` (+ `UMean_mag`), `pMean`, `kMean`, `nutMean`, `TMean`, `k`, `epsilon`, `omega`, `nut` when present, with units |
| Transient write history | `purgeWrite`, autosave | `purgeWrite 2` in every writer | **gap, blocking**: a transient run keeps only its last two time directories, so playback has two frames. The compile `controlDict` must keep every written time for transient runs |
| Animation export | `solution_animations`, `videoexport`, Discovery `SaveAnimationToFile` | PNG export of one frame | gap |
| Scenes, saved views | `scene`, `views save_view` | `view.save` (camera and looks) | had |
| Quick probe, pinned probes | Discovery (*inferred*), Fluent probe | Probe on click, one value | pinned probes: gap |
| Charts, dashboard | Discovery `ResultCharts`, monitor dashboard | residuals, Line, now the Analyse tabs | had / new |
| Units | Fluent units per quantity | SI throughout; p/ρ shown as m2/s2 with Δp in Pa | report units are SI; per-quantity unit choice: gap |

## What was wrong with the pipe picture

The Flanged pipe's mid-plane slice looks one colour because the flow is nearly uniform: at Re ≈ 120 000 a
laminar set-up gives a plug flow whose boundary layer is a cell or two thick (the outlet's radial profile is
flat at 2.0–2.2 m/s and drops to 0 only in the last 3 mm, `results.surface_plot axis=radius`). The legend's
auto range runs from the wall's no-slip 0 to 2.24 m/s, so the whole core falls in one band. **Fit** spreads the
map over the slice's own robust range (1.28–2.07 m/s on that run) and shows the developing core; Lines, the
XY plot and the histogram show the same thing in numbers.

## Measured on the Flanged pipe sample

| Quantity | `results.surface_integrals` | Solver function object |
|---|---|---|
| Inlet volume flow rate | −0.005643 m³/s | −0.0056430 (`patchFlowRate`) |
| Inlet area-weighted p | 0.15159 m²/s² | 0.15139 (`patchAverage`, face values) |
| Outlet flow rate | 0.005635 m³/s | 0.0056430 (point values, 0.15 %) |

Every command answers in under 20 ms on that case's 56 k cells, except `results.surfaces` (0.3 s, it loads
every patch once).

## Follow-ups

1. Keep every written time for transient runs (`purgeWrite 0`, or a user setting) in the compile
   `controlDict`, so the Time row has every frame.
2. Pathlines coloured by any field: the host's tracer samples the asked field along each line.
3. Report definitions written into the case (`surfaceFieldValue`, `forces`) from a `report_definition` node, so
   any surface integral is plotted against iteration while the run goes.
4. Volume integrals and forces/moments.
5. Animation export (frames to PNG/MP4) from the Time row.
