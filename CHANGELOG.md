# Changelog

All notable changes to Stokes are recorded here. The format follows Keep a
Changelog; versions follow semantic versioning.

## Unreleased

### Changed

- Documentation: the README is rewritten with the elbow case in nine screenshots (geometry, boundaries, two meshes, residuals, velocity, temperature, streamlines, Full mode) in one orientation, plus montages of the three themes in dark and light and of nine colour maps, install guides for Windows (`docs/INSTALL-WINDOWS.md`, FoamWin) and Linux (`docs/INSTALL-LINUX.md`), a usage tour (`docs/USAGE.md`) and a Buy me a coffee link (`.github/FUNDING.yml` adds GitHub's Sponsor button).
- Found by running Fluent's mixing-elbow example (water, 0.4 m/s at 20 °C and 1.2 m/s at 40 °C, realizable k-ε, energy on) through every mesher from the Guided screens:
  - **A lost inlet no longer gives a silent wrong answer.** cfMesh's Cartesian mesh at 5 mm cells dropped the 25 mm hot inlet (a patch with 0 faces), checkMesh said ok, and the run solved one cold stream (293.4 K at the outlet against the 296.3 K energy balance). The Mesh card now says which inlet or outlet has no faces, and Run refuses with `solve.boundary_not_meshed` until the mesh has it.
  - **Parallel heat-transfer runs work on Windows.** decomposePar stopped at "Unknown patchField type compressible::alphatJayatillekeWallFunction"; the agent now loads the generic patch fields for decomposePar and reconstructPar on Windows (a fixed library it names itself; `-libs` stays refused from clients).
  - **Tetrahedral and other high non-orthogonality meshes solve.** Above 70 degrees the case uses `limited corrected 0.33`, a second non-orthogonal corrector, upwind for the kinetic energy term, a cell-limited velocity gradient and tighter under-relaxation; the elbow's tet mesh (79.8 degrees) had diverged to a negative temperature at iteration 67. An ordinary mesh's case and run basis are unchanged.
  - **Energy runs are steadier** (fluid writer `of14-fluid/6`): every equation is under-relaxed, the turbulence fields included (k-omega SST had gone to NaN in its first omega solve), and each energy case carries a `limitTemperature` constraint at Fluent's own limits (1 to 5000 K) so a first-iteration energy overshoot no longer stops the run with "Negative initial temperature". Earlier energy runs show as out of date.
  - The Mesh card says what "quality needs a look" means (each check above its limit), and shows real progress for every mesher (only blockMesh had step names, so the others showed an indeterminate bar).
  - Boundaries can be renamed in place (double-click the name; the tag, and so the mesh patch, follows), and two of a kind show their names ("cold_inlet", "hot_inlet") instead of a cut-off "Inlet · inl…".
  - The Guided card stops above the viewport toolbar when the Console drawer is open (the Fluid card's Next button was under it), and the Mesher choice is wide enough to read.
  - The view cube is no longer cut in half by the section plane a Results slice turns on.
  - A reopened tutorial shows its mesh after a page reload too (the tree could arrive after the runs).
  - **k-omega SST converges on the polyhedral mesh.** cfMesh's `pMesh` left the symmetry plane bent along the edge where a pipe wall meets it (49 points up to 3.3 mm off, with 5 mm cells), and SST diverged from those cells; the same case converged on the snappy and hex-dominant meshes. The polyhedral type now meshes a planar symmetry face as `symmetryPlane`, flattens it with cfMesh's `improveSymmetryPlanes` and sets it back to `symmetry` (driver `cfmesh/2`); SST then converged. The agent allows the two new steps, with `foamDictionary` pinned to that one edit of `constant/polyMesh/boundary`.
  - **A bent symmetry plane is reported.** After every mesh, each planar symmetry patch is measured from its own points (`meta.symmetry_flatness`); one with points more than 2 % of a cell off the plane makes the mesh "needs a look" with a sentence saying how many points, how far, and that a run can diverge there. checkMesh passes such meshes: the tetrahedral one (2.8 mm off) said "Mesh OK" and its run diverged. The Guided Mesh card now shows the mesh's warnings.

- Tutorials solve on several cores: the Run card's **Cores** (the solver settings' `cores`) now applies to an imported case. A tutorial whose Allrun is already parallel runs its parallel steps on that many MPI ranks; a serial one with a single `foamRun` solve is decomposed, solved over MPI and reconstructed by Stokes; `decomposeParDict` is written for the core count. Multi-region cases, legacy solvers and meshing stay on one core, and the run says why. Measured: pitzDailySteady on 4 ranks converged in 293 iterations (285 serial). On Windows the generic patch fields library is loaded for a decomposed run, without which decomposePar stopped on pitzDaily's unused `0/v2`.

- Standard views are easier to pick: a row of **Front / Back / Left / Right / Top / Bottom / Iso** buttons sits against the view cube (hidden with it), the cube's negative axes are labelled -X, -Y, -Z in their axis colours, and the seven views have default shortcuts Alt+1 to Alt+6 and Alt+0 (Iso).
- A tutorial's settings can be changed in Guided mode: its Fluid, Boundaries and Run cards show the case's own flow model, viscosity, boundary values and types, end time, time step and write interval, edited with the same `case.set` overrides (and revert) as the Full-mode case panel.
- The Guided Mesh card has a **Mesher** choice (blockMesh, snappyHexMesh, the cfMesh types), as the Full-mode Mesh panel does.
- The tutorial list now says **coded** for cases whose Allrun copies runtime code in from `resources/` (angledDuct, mixerVessel2D, sloshingTank, ...), which the import refuses; before they were listed as ready. A `#calc` the import can evaluate no longer makes a case coded in the list.
- A refused tutorial leaves nothing behind: `tutorial.open` closes the project it made, removes its new folder and drops it from Recent (`project` is null in the reply).
- A tutorial's mesh is drawn on every step once it is built (Mesh, Run, and Results before a field is chosen), and after reopening the project; Show mesh cells draws its cell edges. A tutorial has no wall patches, so its mesh is the results slice, which was drawn only in Results with a field chosen and so never appeared after a mesh-only Generate.
- The measure readout says "Distance between the two picked points" for two clicks on one face, instead of "two vertices".

- Tutorials open on the Mesh step with a **Generate** button. A case from the start screen has no CAD, so Guided mode now lands on Mesh, and Generate runs only the case's own mesh steps (blockMesh, createZones, transformPoints, ...; `run.start {case, mesh_only}`, a run record of `kind: mesh`) and shows the mesh before anything is solved. Run then solves it; a full Run still builds the mesh too.
- A tutorial that cannot be imported no longer leaves an empty project open: the start screen stays and says which file and why. Before, the reason was a toast that vanished over a blank project ("nothing loaded").
- ballValve (both versions) and other tutorials now import: plain-arithmetic `#calc` in a case's copy is replaced by its value (ADR-0033; anything else is still refused), a case's own function files (`system/wallsSurface`) count for `#includeFunc`, and the allow-lists accept `potentialFoam -pName`, `transformPoints -pointZone` and `createNonConformalCouples <patch> <patch>`.
- Transient runs gain three controls in Solve settings → Transient: **Adaptive time step** with **Max Courant** (`adjustTimeStep`/`maxCo`, starting from Time step), **Write every (time)** in physical seconds (`adjustableRunTime`, or `runTime` with a fixed step; zero keeps writing every N steps), and **Time-averaged fields** from **Average from** (`fieldAverage` writes UMean, pMean and nutMean or kMean, and TMean with energy). Off by default and ignored for steady runs, so no existing case or result changes. Checked on FoamWin with a WALE LES: the step grew from 0.0006 s to 0.002 s under Co 0.8, fields were written every 0.01 s, and the averages were written. Results does not list the averaged fields yet.
- Every turbulence model in the Flow model list now runs on the incompressible solver. The incompressible case writer only knew laminar, so k-ε, k-ω SST and every other model the list offered (including Guided mode's two Turbulent choices) was refused when the run started; it now writes the model's `momentumTransport`, initial fields, schemes and solvers from the same module the thermal solver uses, and writes transient cases (PIMPLE, Euler) for the LES, DES and SAS models that need one. Laminar steady cases are written exactly as before. Also fixed: `SpalartAllmarasIDDES` named a delta OpenFOAM 14 does not have (`IDDESDelta`), `dynamicLagrangian`'s `flm`/`fmm` fields had the wrong dimensions, and `DeardorffDiffStress` is offered for the incompressible solver only (OpenFOAM 14's compressible form stops on its first step). Checked by running every model on both solvers on FoamWin.
- Stop keeps what the run solved. Pressing Stop on a running solve now asks the solver to write the iteration it is on and end cleanly (every case carries OpenFOAM's `stopAtFile` function, and the agent's new `job.stop` creates the file it watches), so the run finishes as a result: its fields can be shown in Results and a new Resume button (`run.resume`) continues it from that iteration on the same mesh (`startFrom latestTime`, the newest time folder copied in by the agent) up to the Iterations limit. Before, Stop killed the solver and everything since the last scheduled write was lost. A run with no solver going yet is still cancelled. New: `run.stop`, `run.resume`, agent `job.stop` and `job.times`, `copy_from.latest_time_from`; run records carry `stopped` and `resumed_from`.
- Choosing a turbulence model (k-ε, k-ω SST, …) now clears the "needs the turbulence models: choose them in Physics → Flow model" note on every inlet and outlet, as turning energy on already did for the energy equation. The note stayed after a model was chosen, so turbulence looked unconnected although the case was written and solved with it.
- Measure works again and reads like a CAD measure. A pick on a curved face (a pipe's barrel) and a flange printed "—": the barrel was treated as the tangent plane at the click. Curved faces are now measured as surfaces, by the exact minimum distance over their triangles, and the dimension line is drawn between the two closest points rather than the two clicks. A Measure panel beside the view picks the target (Vertex, Edge, Face, Body; the edge or vertex under the pointer is highlighted before the click), names the two elements, and lists the distance with dX, dY and dZ, the angle between two planar faces or straight edges, each element's own length, area or volume, and a vertex's X, Y, Z; Clear starts again. Two parallel faces' dX/dY/dZ now describe the normal gap (the line used to lie along the first face).
- The Guided Mesh card takes the number of cores snappyHexMesh runs on (the mesh settings' own Cores). Before, only Full mode showed it, and the Run card's Cores is the solver's, so a mesh made in Guided mode always ran on one core.
- Four more mesh types, chosen from the Mesher list (ADR-0032): Hex-dominant (cfMesh cartesianMesh), Tetrahedral (cfMesh tetMesh), Polyhedral (cfMesh pMesh) and Poly-hexcore (cartesianMesh then polyDualMesh). Each meshes the fluid part's own volume, keeps the boundary names and types, takes the local sizes and wall layers already set (the tetrahedral type refuses layers, which this cfMesh build cannot grow cleanly), and runs as a background job with progress and cancel; Cores sets cfMesh's OpenMP threads. The agent now allows cartesianMesh, tetMesh, pMesh and polyDualMesh.
- Results reports and plots in the style of Fluent and Discovery (`docs/engineering/results-postprocessing-gaps.md`). The Results card has an Analyse block: **Report** gives surface integrals on any boundary or the slice — area, area- and mass-weighted average, volume and mass flow rate, min/max, standard deviation and the uniformity index, per surface and net, with units and a CSV (`results.surface_integrals`); **XY plot** plots a field against x, y, z or the radius from a surface's own axis, so an outlet shows its velocity profile (`results.surface_plot`); **Histogram** shows how much of a surface's area is at each value (`results.histogram`); **History** plots the solver's flow-rate and mean-pressure reports against the iteration or time; **Functions** saves your own fields as expressions over the run's fields with units checked (`p_total = p + 0.5 * U_mag^2`), kept with the project, and they report, plot and colour the model like any field (`results.function_save`, `_list`, `_remove`, `results.evaluate`).
- The Range row has **Fit**: the colour map is spread over the area-weighted range of what is on screen (`results.range`), so a slice whose core flow is nearly uniform no longer draws in one colour because the walls are at 0. The Guided card has contour **Lines** on the slice at the band edges, and `results.contour_lines` draws them on any surface through the API.
- Transient results: the Results card has a **Time** row when a run wrote more than one time — a slider over the written times, step back and on, Play (each frame shown once it has been read) and Loop. A transient run still keeps only its last two written times (`purgeWrite 2`), so playback has two frames until that changes.
- Results show the time-averaged and turbulence fields when a run wrote them: `UMean` (with its magnitude `UMean_mag`), `pMean`, `kMean`, `nutMean`, `TMean` on the thermal solver, and `k`, `epsilon`, `omega`, `nut`, each with its units. They are in the field list's "more" menu and every report, plot and function can use them; a run without them shows exactly what it did.
- A Guided card taller than the window scrolls in place instead of running off the bottom.
- Vector arrows are lifted off the dark end of their colour map, so the slow bulk of a flow no longer draws as black specks on the viewport.
- Guided mode now sets up the physics a real case needs, so nobody has to switch to Full mode for it. The Fluid card has Flow (Laminar, Turbulent k-ε, Turbulent k-ω SST) and Heat transfer (Off or On; On also picks the thermal solver), the Boundaries card takes an inlet temperature when heat transfer is on (beside the speed when you assign an inlet, and under each inlet in the list), and the Run card takes the number of cores beside Iterations. These are the Solve settings' own values, so the two modes agree.
- A left drag orbits from anywhere in the view by default (the "Orbit" mouse scheme, ADR-0021 amended). Before, a drag on the empty background drew a selection box, so a first-time user could not rotate the model and a stray box changed the faces picked in the Boundaries step, while the hint said "Drag to orbit". A click still selects; Shift-drag draws the box; the Lasso button now also works under this scheme; middle- or right-drag pans. CAD, Blender and Select / Orbit stay in the Mouse menu, and a scheme you saved is kept.
- From the mixing-elbow walkthrough (Fluent tutorial case, 2026-11-04): New project accepts a name with spaces ("mixing elbow" is saved as `mixing_elbow`); the Boundaries card describes each unassigned face ("Face 1 · flat · 39.2 cm²", `boundary.uncovered describe=true`) and names two boundaries of one kind ("Inlet · inlet_2"); with the energy equation on, an inlet's temperature shows beside its speed instead of under Advanced; the Mesh card names the step that is running (it said surfaceFeatures through a whole snappyHexMesh); the mesh steps' Boundary layers row shows the layer count instead of "off".
- snappyHexMesh's background grid has exactly the base cell size and is anchored at the part, so the part's bounding planes lie on cell faces: a symmetry plane is now meshed flat (the elbow's was bumpy by about 2 mm, which slowed the flow on it and stalled the residuals) and the meshed volume of the elbow is within 0.2 % of the CAD (was +2.5 %).
- A run the agent refuses at start is recorded as failed with the reason, instead of staying "Run · starting" with a Stop button; such a record left by an older version reads "interrupted" when the project opens. A run shows "Run · it N" as soon as its solver runs (it stayed "starting" for a whole serial solve).
- Streamlines and vectors fill the volume and are drawn correctly (ADR-0030 amendment 1). The Stream look starts its lines on every inlet (the host spreads them over the inlet faces and moves them into the fluid) and no longer turns the mid-plane slice on, whose cut clipped every line on its near side; the walls fade instead, and lines and arrows are depth-tested so they have a front and a back. The Vectors look samples the velocity on an even lattice through the fluid (`run.vectors`, the agent's `vector.sample`), with a Where choice to put the arrows back on a slice. Arrows on a slice are spread evenly over the whole cut (all its regions) instead of crowding into the fastest flow, and an arrow's length is proportional to its speed (it was its place in the min..max range, floored at 15 %). The Streamlines section in Full mode traces through the volume first too.
- The simulation tree's stage groups and their folders show what they hold (Geometry, Mesh, Physics, Solve, Results, Explore with the top bar's stage icons; Materials, Regions and Boundary conditions with their own) instead of the same folder icon on every row.
- A signature look built on flow. Each recent project and example card without a saved thumbnail draws its own streamlines, potential flow around its monogram, with the flow angle and lift taken from the project's path, so no two cards look alike; hovering a card makes its lines flow in the accent colour. While a run is going, Stop in the top bar is the run's live readout: the accent fills it as the run advances, a sparkline shows the worst residual falling, and the iteration count ticks. The rail between the steps in the top bar fills once a step is done. The viewport backdrop has a soft studio vignette and a fine grain under the model; the render and its exports are unchanged.
- A visual pass over the app (web and Windows window alike). Buttons are 8 px rounded rectangles with a faint fill, and only the primary action stays a pill. A row of choices in the guided cards (field, axis, range, bands, material) is now one segmented control. The step strip in the top bar stays on one line: the open step's hint moves to its tooltip, so the strip no longer changes size when you change step, and finished steps show a soft check instead of a solid green disc. The Settings dialog has a title bar, a sidebar and a footer separated by hairlines. Recent-project cards show their monogram on a raised tile and lift on hover. Command descriptions in the palette no longer show Markdown marks.
- Streamlines: a Rake seed (between the two points picked with the Line tool; off until they are picked) and a Style choice, Lines or Tubes (tubes have a width in the model, 0.4 % of its diagonal).
- Streamlines are traced through the solved case's 3-D volume by the run's host (`stream.trace`, `run.streamlines source=volume`, ADR-0030): lines leave the mid-plane and run the length of the model, colour by speed, and each says why it stopped. The Stream look falls back to the old trace on the slice when the host cannot trace. Seed generation (line, plane, sphere, patch) is in `stokes.post.seeds`. The surface tracer takes up to 20,000 steps and no longer ends a line after 2,000.
- The Mesh card's How fine is a slider with Coarse, Medium and Fine marked on it, and a box to type a cell size (millimetres, or with cm or m). The slider is a log scale from half as coarse as Coarse to twice as fine as Fine; a typed size that would give an absurd cell count is refused.
- In the Cupertino themes the flow strip no longer shows as a grey slab with a shadow on the top bar.
- The viewport asks for a redraw when the streamlines change (the lines were in the scene but the frame was not redrawn). Not yet confirmed in the Windows app.
- Double-click the title of the results legend to switch it to print size (larger text and bar), and again to go back.
- Double-click the top or bottom value of the results legend to type that end of the colour range; the range switches to Manual.
- An imported OpenFOAM case's files can be edited as text in the app: Edit text beside the file list opens the whole file. The text is checked by the same rules the agent applies when a case is staged, so code directives and unknown libraries are refused with the line; a saved text replaces the key edits of that file, can be reverted to the copy, and each save is one undo step (`case.file_read`, `case.file_write`, `case.file_revert`).
- Opening a project in a folder whose path is long (over 180 characters on Windows) shows a short warning, because deep case files can hit the 260-character limit. `project.new` and `project.open` return `path_warning`.
- Gravity and the operating pressure show in the solver settings only with the energy equation on.
- A batch of part meshes is read with eight threads, which brings the cold load of 4,500 parts inside its budget on Windows.
- The mass-flow inlet runs: it is written as `flowRateInletVelocity` with `massFlowRate` and `rhoInlet` (keywords from the OpenFOAM 14 `squareBend` tutorial), with zero-gradient pressure and the same temperature condition as a velocity inlet. It is refused with a turbulence model, which needs a speed at the inlet.
- Condition kinds the solver cannot write yet (mass-flow inlet, pressure inlet) are shown greyed out with the reason in the Assign menu, the value panel and a boundary's Condition list, and `setup.check` reports a boundary that holds one (`setup.kind_unsupported`) before you run. `boundary.kinds` takes an `engine` and says which kinds it supports.
- The opening screen now prioritises New/Open project and recent work, with examples and tutorials kept separate. It no longer mounts a hidden viewport. Empty projects and geometry imports never display the demo pipe as a fallback.
- Mesh and solver editors show one settings category at a time, with cross-category search and an optional overview. Solver selection is a compact dropdown; Solve Controls opens configuration separately from run monitoring.
- Mesh and solver settings now include search, expandable optional sections, beginner explanations and an essential-view shortcut. Rejected fields open automatically. The simulation tree includes navigation help and a visible app Settings entry.
- Imported cases can run the installed `setFields`, `setWaves`, `boxTurb`, `dsmcInitialise`, and `mdInitialise` utilities under the external profile, with bounded argument validation. The profile also accepts `subsetMesh -noFields` and `reconstructPar -noLagrangian` or `-newTimes`.
- An imported case keeps the files no solver reads (a tutorial's `validation/` plots, its README, helper scripts) in an `extras` area of its copy: shown by `case.files`, never linted as dictionaries and never staged. Before, such a shell script was linted as a dictionary and refused the whole case (found by the tutorial sweep).
- A new gate, `tools/check_no_foam_content.py`, fails when the OpenFOAM banner or a copied OpenFOAM dictionary is found under `stokes/`, `agent/`, `web/` or `native/`, so no tutorial content can ship by accident (ADR-0029 decision 2, T-318). It runs in CI beside the IP gate.

### Fixed
- Linux engine attachment and multi-rank runs work from a sourced shell. `FOAM_INST_DIR` that names the parent of the installs is scanned, `which foamRun` names an install, and the caller's `LD_LIBRARY_PATH`/OpenMPI prefixes and `USER` survive the bashrc capture; before, `etc/bashrc` alone left every utility at exit 127 with `libmpi.so.40` missing, and OpenFOAM built a `~/OpenFOAM/-14` user prefix. OpenMPI 5 gets `--oversubscribe` for `cores > 1` (it counts physical cores, so 4 ranks on a 4-thread host were refused). Measured: a 54,183-cell elbow solved on 4 ranks and reconstructed in 103 s; see `docs/LINUX-READINESS.md`.
- A Linux desktop with no WebGL 2 (QtWebEngine blocklists Mesa llvmpipe) now says so in the viewport instead of leaving a black canvas: "3D view unavailable on this display", the driver's reason and what to check. Every panel, command and status line keeps working. Verified on X11 and Wayland with `--screenshot`. **`python -m stokes.app --software-webgl` (or `STOKES_SOFTWARE_WEBGL=1`) restores the 3D view on such a machine** by running WebGL on SwiftShader with software compositing; verified rendering the elbow on both X11 and Wayland. It is opt-in because it costs frames where a GPU works.
- The default residual target is now 1e-3 (it was 1e-4), for a new project's solver settings and for a run whose settings carry none. A solve stops when every field's initial residual falls below it, so default runs now stop earlier; the elbow's runs sit near 2e-3 after 500 iterations and were never reaching 1e-4. A project that already stores a target keeps it, and the Run settings still change it.
- The tetrahedral and hex-dominant meshers now flatten a planar symmetry plane as far as the cells allow. cfMesh left their points off the plane along the edge where a wall meets it, up to 1.2 mm with 5 mm cells on the elbow, and `improveSymmetryPlanes` inverted cells on both. A new agent step (`flatten`, `stokes_agent/flatten.py`, runs before `checkMesh`) moves each point onto the plane by as much of its move as its neighbouring cells allow, keeps every cell valid, rewrites only the points it moved, and never fails the mesh job; its report is in the step's log. Mesh jobs may take this one action and nothing wider (a patch name only). On the elbow tet mesh 72 of 76 points go fully onto the plane in 2 s, `checkMesh` still says "Mesh OK", and the mesh warning drops from 70 points up to 1.1 mm to 4 points up to 0.58 mm. The app's own realizable k-epsilon case on the tet mesh, which died at iteration 113 with a negative temperature (a runaway that starts in cells beside the plane), now runs its 500 iterations: peak speed 2.1 m/s, temperature 293 to 313 K, energy residual 1.7e-3 and slowly falling, as on the other meshes. A fixed 79.75 degree non-orthogonality at the outlet edge remains, and the Mesh card reports it (`docs/TASKLIST.md`).
- Large STEP imports are faster, and saved projects open cold in a fraction of the time. On the 4,500-solid, 52 MB pin grid (24-CPU host, unloaded) an import takes 41 s instead of 75 s, and a saved project of it opens cold in 1.5 s instead of 49.5 s. Both tessellation passes now run in helper processes that start before the file is read (`stokes/geometry/helpers.py`; `STOKES_GEOMETRY_HELPERS` sets how many, 0 turns them off), the per-part cache files are written on four threads (more made them slower), and the import writes the project's mesh pack so the first open reads one file instead of 4,500. A helper that fails hands its batch back to the worker, so an import never depends on one. The 26.5 s OCCT read is now the floor; see `docs/engineering/performance.md`.
- Meshes made by other meshers are kept and can be viewed (ADR-0034, amending ADR-0024's "latest only"). Generating keeps one result per mesher and replaces only that mesher's previous one. The Mesh card shows a "Mesh to use" picker when more than one finished mesh is kept. It chooses the mesh the viewport draws and the one Run solves on, and moves the Mesher setting with it. `mesh.use result=<id>` does the same from the API, and `mesh.list` marks the result in use. Going back to a mesher whose mesh is still current takes the kept mesh without meshing again. Before this, each Generate deleted the previous mesher's mesh, so only the last one could be seen.
- Autosave: unsaved changes are kept in a recovery copy beside the project file (`recovery.json`, never written over `project.json`). The copy is written once a minute while there are unsaved changes, through `project.autosave`. After a crash or a server restart, the next open shows "Unsaved changes from … were kept" with Restore (`project.recover`) and Discard (`project.discard_recovery`). Saving or closing without saving removes the copy. Before this, a restart before Ctrl+S lost the whole set-up. The design is recorded as an assumption, U3 in `docs/00-open-questions.md`.
- The Run button no longer sticks on "Starting… Working on the last request" after a run diverges. The diverged run's monitors were NaN, which the server wrote as bare `NaN` (not JSON), so the browser dropped every run list that held it. The server now sends non-finite numbers as `null`. A reply the browser cannot read now fails its request instead of leaving it waiting. "Starting…" now covers only the start request itself.
- The mesh and run progress lines now name the step that is running. The agent announced only the first step's start, so a step that had just ended stayed on the line until the next one ended. A parallel snappyHexMesh run read "decomposePar" from start to finish.
- A boundary's marker is now drawn on its faces. The centroid of a non-convex face lies off it, so the mixing elbow's L-shaped symmetry plane had its marker floating in the empty corner. `tag.anchor` now also returns `position`, the point on the faces nearest the centroid, which the viewport draws at. `centroid` is unchanged, since the run snapshot reads it.
- A project opened in a hidden or minimised window no longer shows a screen filled with colour, which looked like a camera inside the part. The boundary markers were sized for a 1-pixel-high canvas, 28 times the height of the view. They are now skipped while the canvas has no height and resized whenever the canvas is. A fit also stays a fit when the canvas or the free area beside the cards changes after it, until you move the camera. Before this the opening view stayed framed for the wrong area, and the elbow spilled off the bottom.
- A left drag now rotates the model under every mouse scheme: under CAD, Blender and Select/Orbit the left button was bound to nothing, so only the middle button turned the view. In the Results stage a left drag always orbits, and entering Results keeps a view you have already turned (it still frames the slice).
- A "vtkOutputWindow" window no longer pops up on the desktop when a run finishes: the agent now silences VTK's Windows message window before reading a case.

- Selecting synthetic outline pages such as Solve → Controls now opens the corresponding Details page instead of leaving the previous checklist on screen.
- On Windows, checking whether an agent process is alive no longer sends a console interrupt.
- Imported shared blockMesh dictionaries now bring along bounded local includes and lint their contents; a missing or unsafe include is refused during import.
- Imported tutorials can copy a bounded set of shared resources with a single filename wildcard; no shell is run.
- A tutorials `resources` folder that links outside the tutorials tree is refused.
- Imported recipe removals before the first step remove the intended staged path.
- Monitor files in FoamWin's Windows-encoded function-object folders can be listed and read.
- Indented multi-region solver time and execution lines now update run progress without combining residuals from different regions.
- Legacy DSMC and molecular-dynamics runs report time-step progress.
- The Start screen no longer logs "no project is open" in the Console on launch. Its Tutorials card header now shows the whole folder's case and ready counts while a filter is typed, and the out-of-date note about case import is gone. `tutorial.catalogue` returns the unfiltered counts as `totals`.
- The New project dialog no longer says "Enter a name." before anything is typed; the Start screen's dialog has the Browse… button the project menu's has; and the "will be created at" line breaks only after a folder separator and follows a chosen folder (`project.defaults` takes `folder`).

### Added

- 2D runs (ADR-0031). The Mesh settings and the Guided Mesh card have a 2D switch with Planar or Axisymmetric and a plane picker (XY, XZ, YZ); `mesh.configure` takes `two_d`, `plane`, `thickness` and `wedge_angle`. blockMesh meshes one cell across the plane with an `empty` `frontAndBack` patch, or, axisymmetric, a wedge (default 5°) about the plane's first axis with `wedge` front and back and a collapsed `empty` axis; snappyHexMesh meshes a slab part and `extrudeMesh` (now on the agent's allow-list) turns it into one cell with `empty` front and back. Those patches are the mesher's: their names are reserved, and every `0/` field writes `type empty;` or `type wedge;` on them whatever boundary names them. Solver settings are unchanged. In Results a 2D project is shown face-on in an orthographic view with rotation off. Checked on FoamWin: a planar channel (pressure drop 1.09 × plane Poiseuille at Re 50, with the entrance region) and a backward-facing step on snappyHexMesh both converge (`tools/e2e_two_d.py`).
- Picking faces a boundary already holds is warned before the assign: the context toolbar, the Assign menu, the value panel and the selection card name the boundary (decisions log G10: allowed while preparing, refused by the case compiler), and an assign that overlaps says so. The Conditions panel lists, per part, the faces no boundary holds ("box · 4 of 6 faces"), and a click picks exactly those. New read-only queries `boundary.overlap {members}` and `boundary.uncovered {part?}`.

- A run's own monitor tables, the `postProcessing/` files its function objects wrote (forces, probes, spectra), are listed by `run.monitors` and read as columns by `run.monitor`. The case card plots each column against time. The agent gains a read-only, bounded `job.outputs` listing (T-311).
- The CLI runs tutorials headless: `stokes tutorial list|open` and `stokes case import|settings|set|reset|diff|run|results` (T-316). For example `stokes case import demo.stokes --tutorial incompressibleFluid/cavity`, `stokes case set demo.stokes /cases/cavity system/controlDict endTime "1 [s]"`, `stokes case run demo.stokes /cases/cavity`, and `stokes case results demo.stokes --time 0.5`. In Git Bash, set `MSYS_NO_PATHCONV=1` so a node path such as `/cases/cavity` is not rewritten into a Windows path.
- An imported OpenFOAM case has its own card in Details: what it is and where it came from, its solver and recipe, the Allrun lines that are not run and why, a file-by-file settings editor (click a value, type, Enter; known physical keys take a unit; an edited value shows what it was and reverts in one click), Run case with its live state, and the written time the Results show. After a run the case's own mesh is drawn and coloured by any of its fields in the Results stage (T-313 to T-315). Checked in the browser on the install's cavity: edit `endTime`, run, and |U| on the lid and the cavity at 10 s.
- A tutorial opens from the Start screen in one click: each row of the Tutorials card opens that case in a new project of its own (`tutorial.open`), named after it and never over an earlier copy. The case appears in the tree under Solve > OpenFOAM cases, a row that only projects with imported cases show. A case the safety check refuses still opens its project and says which file and line (T-312).
- The results of an imported OpenFOAM case show every field the case wrote (`k`, `epsilon`, `nut`, `alpha.water`, ...), not just `U` and `p`, each in the unit its own file declares, OpenFOAM 14's named dimension sets included. `run.fields` lists every written time, and `run.time` shows a finished run's fields at any of them; planes and iso-surfaces follow the chosen time (T-310). Verified on the install's cavity: `U`, `p`, `k`, `epsilon` and `nut` at 0.25 s and at 10 s.
- `run.start case=<node>` runs an imported OpenFOAM case on the local install or a host: every file of its copy with the edits applied, and its recipe's steps, under the agent's new `external` job profile, which judges each file and step against the agent's own read of the install. Version 1 runs on one rank (decompose and reconstruct steps are left out and listed). The install's `incompressibleFluid/cavity` runs end to end on FoamWin with an `endTime` edit, and its residuals reach `run.residuals` (T-309).
- An imported OpenFOAM case's settings can be edited without touching its copy. `case.set` records an edit as one undo step: a typed token, or a quantity with its unit for known physical keys (`endTime`, `deltaT`, `nu`, `rho`), which is written in SI. `case.reset` drops edits. `case.settings` shows a file as it will be staged, `case.diff` shows every edit as a unified diff, `case.preview` lists or shows the staged files, and `case.boundary` reads the patch-by-field grid of the initial conditions. Unedited files stage byte for byte, and an edited file changes only in the edited values (writer `of14-external/1`, T-308).
- `case.import` copies an OpenFOAM 14 case, from a tutorial id or a folder, into the project as a read-only, content-addressed `foam_case` node under `/cases` (one undo step, journaled with its snapshot hash); every copied file must pass the external lint profile, and a refused case adds nothing. The run steps come from the case's Allrun through a restricted parser that never runs a shell and lists the lines it does not understand, or from the default `[blockMesh, application]`. `case.get`, `case.files` and `case.remove` read and remove imported cases (ADR-0029, T-306/T-307).
- The Results panel has an Iso-surface section: choose `T` or `p`, type a value in the field's unit and press Show iso; it shows `ready` with the triangle count, follows a `cutting` answer through `run.iso_ready`, and explains out-of-range, too-large and host-unavailable refusals. A ready surface is drawn in the viewport, coloured by its value on the results colour map, with the walls faded as for a slice; a new value replaces it, "Show in viewport" switches it off and on, Hide takes it out, and the viewport's top line reads `iso T = 300 K, N triangles` while it is shown.
- `run.iso_surface` cuts an iso-surface of `p` or `T` from a finished run, at a value given with its unit; `GET /api/results/iso/{run}?field=&value=` serves it to the viewport. Values outside the field's range and surfaces over two million triangles are refused.
- The energy equation can be enabled on the `of14.fluid` engine: steady, laminar, constant properties, with gravity and an operating pressure (ADR-0027). Verified natively on a mixing elbow to 300 iterations (temperature within the inlet range, outlet mean within 0.1 K of the mixing balance), with residuals not yet converged to 1e-4.
- The kOmegaSST turbulence model runs with the energy equation on the `of14.fluid` engine: inlets take intensity with a viscosity ratio or a length scale, walls use wall functions, and the energy wall uses the Jayatilleke function (ADR-0028). `solve.check` still refuses kOmegaSST without the energy equation. The native elbow run with kOmegaSST completes 300 iterations, but residuals stay above 1e-4 with 0, 3 or 5 wall layers (ADR-0028).

### Fixed

- The agent's dictionary lint refuses `#include "geometry./x"` and similar spellings with a trailing dot, space, other case or `:stream` on a reserved folder (`geometry`, `polyMesh`), which Windows opens as the reserved folder itself; it was accepted before. This is the only change to the current lint profile.
- The external lint profile decompresses a gzipped surface under `constant/geometry/` to the end and refuses it if it expands past the 8 MiB limit, instead of checking only its first 4 KiB, and accepts a quoted `"libs"` key with the same probed-library check as a bare `libs`.
- With nothing selected on the Results stage, the Details pane shows the step card with a hint to select a field, slice, residual or report, instead of the whole Results page. A selected row still shows only its own settings.
- `run.iso_surface` accepts a value only the interior of the volume reaches: the range is now each field's min and max over every cell, measured by the agent when it extracts the fields and reported by `run.fields` as `volume` (ADR-0026 amendment 2).

- Opening the Capping row under Mesh shows the capping card (Close openings, or the existing cap operation's page) instead of the Mesh panel.
- The pressure of an energy run is labelled `p` in Pa in the field list, the tree, the legend and the monitors table, not `p/rho`.
- A finished run in a reopened project shows its residual chart again: `run.residuals` reads the history back from the run's host (answering `loading` meanwhile) instead of reporting it unavailable.
- Energy runs (`of14.fluid`) with gravity not normal to the pressure outlet no longer blow up: the outlet holds the set pressure at its centroid and a hydrostatic profile across its faces (ADR-0027 step 6). Energy runs on two MPI ranks are verified.
- The simulation tree shows Capping under Mesh, after Global size; the row was built but the default tree never listed it.
- Residuals name the energy equation: OpenFOAM's `e` (or `h`) reads as "Energy (e)" in the residual legend and the final-residuals table.
- An agent iso-surface cut no longer fails on packing (the surface has no plane) or on its artifact name (`T` and `1e+05` were not valid names).
- Mesher selection uses a compact dropdown in shared Mesh/Properties settings, preserving unavailable choices with reasons and saved values.

- CAD opening/import and Fit to View centre the projected model in the available dock-free area, including visible region bodies. Late-mounted panels update the opening fit; repeated perspective and orthographic fits do not accumulate offsets.

- Saved layouts that put Mesh, Conditions, Solve, Results or Properties in the right dock while Details sits beside the tree now hide those duplicate editors on load. The migration keeps dock widths and unrelated panels; layouts without the tree-and-Details arrangement keep their chosen editor placement.
- Tree names yield space to fixed action and status columns in narrow panes; shared SVG and menu icon sizing is consistent across desktop and browser hosts.

- Solver and mesh settings share labelled, collapsible groups in their panels and Properties, with mesher and layer-mode applicability.
- Properties uses validated configure commands for canonical settings; both mesh and solver Execution groups expose editable core counts.
- Disabled boolean and box controls now respect job and connection locks. Guided mesh settings use buffered, unit-aware fields and refresh after model edits.
- Stage navigation opens the matching outline section while preserving nested folder choices.
- Geometry Check distinguishes pending, disconnected and failed requests, refreshes after edits, rejects stale replies and offers a direct retry.
- Virtual folders no longer announce an undefined status.
- A guided mesh task page no longer repeats the generic next-step message beneath it.
- Mesh task headings follow the selected page, and task queries stop when the model is idle.
- Made the Sizing page component import explicit so Windows resolves the form instead of its similarly named helper.
- snappyHexMesh works on native Windows: older FoamWin builds cannot read a feature-edge mesh through an absolute drive-letter case path, so the agent runs that utility from the job's parent with `-case <job id>` (the same case meshes when named relatively). The repaired engine recognises drive-absolute `fileName` paths (`D:/` and `D:\`) while preserving drive-relative `D:foo`; an absolute `-case` with relative feature names now meshes too. The workaround remains for runtimes without the repair, feature names stay relative to `constant/geometry`, and the recorded step arguments and every other utility are unchanged.

### Added

- Native Windows engine hosts and MPI execution (ADR-0025): FoamWin discovery through `STOKES_FOAMWIN_ENV`, kill-on-close Job Object supervision, and 1–256 core counts saved separately for meshing and solving.
- MPI case plans for snappyHexMesh and foamRun: decompose, run the requested process count and reconstruct normal mesh/result files. blockMesh remains serial.

- **The Quality page.** The Mesh stage's task 8 shows the verdict line, the three quality histograms
  with the project's limits drawn on them, and the statistics — including the seven cell types
  checkMesh reports. The distributions are computed on the host after the mesh finishes (the result
  is done first), so the page shows them pending, ready or failed without ever holding the mesh back.
- **The Boundary layers page.** Every wall's layer set-up in one place, a one-call "Add 3 layers on
  every wall", per-wall coverage after a mesh, and a y+ helper that turns a target y+ and the flow
  conditions into a first-layer height (`mesh.layer_height` reads the fluid region's material at its
  reference temperature). The page says layers are *requested* until a mesh measures them.
- **Mesh quality distributions on the host** (the backend). A bounded ASCII polyMesh reader and the
  install's own quality definitions as numpy: non-orthogonality, skewness (boundary faces included,
  as checkMesh reports it) and aspect ratio, validated against checkMesh's own logs on four fixtures
  to the printed digits. The checkMesh parser now reads the seven cell-type counts. The service
  fetches the distributions as a follow-up task, after the result is finished — never holding it.
- **The Surface page, and a sharp-edge setting.** The Mesh stage gains its Surface page: the staged
  surfaces with their size against the staging limit, and a `feature_angle` setting (default 30°) that
  decides which sharp edges the mesher keeps — reaching both the feature extraction and the curvature
  refinement, where 30° leaves the dictionaries byte-identical to before.
- **The Sizing page, and surface sizes that work.** `surface_size` is no longer a dead setting: it
  feeds the snappy surface refinement behind a body's own size and a per-boundary size (each empty =
  inherit). A Sizing page shows the level each size really achieves per body and per boundary, and an
  honest cell-count range before anything runs — calibrated on the pipe and the flanged-pipe STEP,
  and warning with advice when the estimate clears the cell limit.
- **The guided-meshing stepper.** The Mesh stage's Details pane always shows the nine meshing tasks
  and what is next — a one-line summary in the pane's header, the full list as the first block of its
  body, collapsible by preference — with the first three task pages working: the geometry check with
  its fixes, the per-body role choice (one call, one undo step for a multi-selection), and the
  boundaries page with "Keep as a wall". `Alt+↑/↓` moves between tasks.
- **Boundary layers in the snappy mesh.** The mesh settings gain the layer set-up (count, mode —
  final-ratio or first-height — expansion, minimum thickness, feature angle); a body and a wall can
  each override it (empty inherits, 0 turns layers off, n asks for n, walls only). After meshing, the
  result reports per wall how much of its wall got its layers, and a wall under 80 % coverage warns
  with the wall named. A project that never asks for layers gets exactly the dictionary it always got.
- **A start screen's backend (T-179).** `project.recent` lists recently opened projects with whether
  each still exists and has a thumbnail; `project.recent.remove` clears a row without touching the
  folder; `project.browse` lists folders under two roots (symlinks and hidden names skipped); and
  `project.preview.set` stores a validated `preview.png` beside the project.
  `GET /api/project/preview?path=…` serves it with an `ETag`, so a start screen redraws without
  re-fetching a picture that has not changed. Contract in `docs/engineering/project-schema.md`. The
  screen itself is the front end's.
- **Slices and section-plane contours.** Results can cut the model at any plane (an axis and a position, or the Section view's plane), with show/hide, |U| or p, banded fills and contour lines (5 to 20 levels) per slice. With the Section view on in Results, the cut face is painted with the chosen field. A finished mesh now opens its results and patch rows in the tree, and the Mesh panel offers Show mesh / With cells when the mesh isn't on screen.

- **The Settings dialog (T-178).** Project > Settings… (`Ctrl+Shift+,`) opens Appearance, Units, Mouse, Workspace and About. The mouse scheme and the default import unit are remembered per user, never in a project or a preset, and Reset workspace clears them. The Display settings popover gains "All settings…". Escape in a dialog opened over another now closes only the top one.

- **The viewport context menu (T-172, T-173).** Right-click in the viewport opens a menu for a body, a face, the pick or empty space (Isolate, Hide, Name, Assign, Material, Prepare, Related, Rename, Copy name and path, Delete, standard views, Display, Mouse, Grid). A right-drag still pans, and in Edge, Point, Paint and Measure mode the right-click leaves the selection alone. The menu is checked under real presses, keys and four theme families by `check_context_menu` in the interaction harness.

- **Menu primitive and context target (T-170, T-171).** Menus open at a point, close on an outside scroll, wheel or window blur, support nested lists, keep disabled rows reachable by arrow keys with their reason announced, and take focus on open. A pure context target and action context, plus `enabled(ctx)`, `label(ctx)` and `group` on the action table, prepare the viewport context menu; new actions: Show all hidden bodies, Select parts of the same file, Select every face of the body, Clear the pick.

- **A `preferences` workspace section, user-only (T-176).** The mouse scheme (`cad`, `paraview`,
  `blender`, `toggle`) and the default import unit (`mm`, `cm`, `m`, `in`, `ft`) now have a home in
  the user's workspace document. They are **never** in the project file and **never** in a preset:
  `workspace.set scope=project` refuses them with `workspace.user_only`, and applying a preset leaves
  the user's scheme alone.
- **`app.info` and `app.notices` (T-177).** `app.info` reports the version, the Python and platform
  it runs on, and where the workspace files live — without the machine name or the user name.
  `app.notices` returns the third-party notices, from the packaged wheel when there is one and from
  the repository otherwise, so About works in a shipped build.

- **Serving field results to the desktop and the browser (T-215, T-216).** A finished run now fetches
  its fields into `<project cache>/results/<run id>.stkfld`, remembers them on the run record, and
  serves them: `run.fields` reports the names and their ranges, `GET /api/results/field/{run}/{patch}`
  is one float32 array in its own codec, and `/api/results/slice/{run}` and `.../slice-edges/{run}`
  are the cut plane as the existing transport v2 and edge payloads. A reopened project serves them
  from the cache with no host; a fetch that fails leaves the run `done` and says so in `error` rather
  than failing it. Contract: `docs/engineering/run-schema.md`.

- **The Solve panel, Run and Stop, and live residuals (T-217).** The top bar's and the ribbon's Run start a
  run (`run.start`) and become Stop while it goes; when the case is not ready Run is disabled and says why in
  the server's words (`solve.check`). A Solve tab beside Mesh holds the state and progress, a log-axis residual
  chart with a legend and the convergence target, the result, the job log and the solver settings
  (`solve.configure`, one undo step per edit). The Residuals tab draws the run's own residuals; the Log tab has
  an Events | Job choice; the drawer strip shows Mesh and Run readouts. `done_unconverged` reads "finished, not
  converged", `interrupted` is never a spinner. Names and shapes of the commands and events are one module,
  `web/src/panels/runApi.ts`. `tools/fake_run.js` plays the server half for the harness and for captures.

- **The mesh in the viewport (ADR-0024, T-211).** In the Mesh stage the boundary patches of the mesh are
  drawn in a matte `mesh` look and the geometry steps aside (a view filter: no part parameter changes).
  Display has **Mesh surface** and **Mesh with cells** (the cell edges, in the feature-edge colour).
  `SceneManager.setSourceVertexTint` gives a source a colour per vertex that survives a re-set source,
  the hook for fields on patches. `tools/mesh_demo_project.py` makes a project with a synthetic finished
  mesh, so the look can be captured without OpenFOAM.

- **The agent job supervisor and wire (ADR-0024, T-144a, T-202b).** `agent/stokes_agent/jobs.py` runs each
  step as the leader of its own process group, so cancel stops the whole group, and streams `log.lines`
  events and parser events such as `monitor.residual`. Each step's flags are checked against a per-utility
  allow-list (`flags.py`); `agent/stokes_agent/lint.py` refuses dictionary code such as `#codeStream` and
  `#calc`, and includes that escape the case.

- **The local runner and the host probe (ADR-0005, T-144c).** `stokes/run/local.py` gives a blocking
  `LocalRunner` that hosts the agent in-process. `host.probe` (`stokes/api/commands/host.py`) runs
  `probe_host` (`agent/stokes_agent/probe.py`); `refresh` probes again instead of reusing the stored result.

- **The OpenFOAM 14 case writer and dictionary printer (ADR-0008).** `stokes/compile/foam.py` prints
  OpenFOAM dictionaries from typed values (words, quoted strings, tuples, lists, dimensioned values,
  includes). `stokes/compile/of14/` holds the Foundation 14 writers, with `boundaries.py` for boundary entries.

- **The blockMesh driver and mesh commands (ADR-0024, T-145a, T-145b, T-146).** `stokes/mesh/drivers/blockmesh.py`
  builds the case and runs `blockMesh` and `checkMesh`. `stokes/api/commands/mesh.py` adds `mesh.meshers`,
  `mesh.configure`, `mesh.generate`, `mesh.cancel`, `mesh.status` and `mesh.log`. `agent/stokes_agent/parsers/checkmesh.py`
  reads the quality numbers, and `stokes/mesh/service.py` marks a result stale when its inputs change.

- **The Section view (T-065b).** `web/src/viewport/sectionCaps.ts` fills each cut in its body's colour.
  `web/src/app/SectionPopover.tsx` adds Invert, which keeps the other half, and a drag handle that moves the
  cut. `web/src/viewport/sectionPick.ts` makes picks land on the kept side of the cut.

- **Edge and vertex topology picking (ADR-0022 §15).** `web/src/viewport/topology.ts` decodes the topology
  data, and `topologypick.ts` keeps a stable candidate order so Alt + wheel cycles through occluded picks.
  Double-clicking an edge selects its whole loop (`web/src/viewport/wire.ts`); Ctrl-double-click removes it.
  A face pick does not yet expand to its bounding edges in Edge mode: the record carries each edge's faces, but the inverse conversion is not built.

- **checkMesh quality on the mesh result (T-146, T-208).** Every mesh job now runs `checkMesh` after
  `blockMesh`; the agent parses its log and the result carries the numbers, with `ok` = OpenFOAM's
  verdict **and** the project's thresholds, and a `warning` status when a real mesh misses the bar.
  Contract: `docs/engineering/mesh-schema.md` §Mesh quality.

- **`mesh.generate`, `mesh.cancel`, `mesh.status`, `mesh.log` (T-145b, T-207).** The command
  that meshes: it builds the case, sends it to a host as a job and keeps one result node. A result is
  not an undo step; a change makes it **stale** rather than re-meshing; a host without OpenFOAM is
  **`skipped`**, so a journal replays anywhere. `tools/mesh_slice.py` builds and meshes the duct
  from scratch. Contract: `docs/engineering/mesh-schema.md` §Generating a mesh.

- **The blockMesh driver (T-145a, T-206).** A box snapshot, the tag-to-side mapping, the
  `blockMeshDict`/`controlDict` writer and the mesh basis. The duct (0.5 x 0.02 x 0.02 m at
  2 mm) meshes to 25,000 cells and passes `checkMesh`. Contract:
  `docs/engineering/mesh-schema.md` §blockMesh.

- **Mesh hosts: `local`, and `host.list` / `host.probe` (ADR-0005, T-144c).** The agent runs
  in-process on Linux, behind a blocking `LocalRunner`; the registry remembers what a host reported so
  asking twice does not source the OpenFOAM environment twice. `SIGTERM` now runs the shutdown path, so
  a killed server does not leave a solver behind. Contract:
  `docs/engineering/mesh-schema.md` §Hosts.

- **The mesh model and its first commands (ADR-0024 draft, T-143).** `mesh_settings`,
  `mesh_result` and `mesh_patch` node types, the four-mesher catalogue, and `mesh.meshers`,
  `mesh.configure` and `mesh.list`. Sizes are quantities in metres, never snappy "levels".
  Contract: `docs/engineering/mesh-schema.md`.

- **The Ubuntu (Yaru) theme family: `ubuntu`, `ubuntu-dark` and `ubuntu-auto`.** Light and dark
  use the exact Yaru palette read from the installed gresources (`#fafafa`/`#2c2c2c` surfaces,
  `#e95420` orange accent, `#109b26`/`#f99b11`/`#c7162b` status), Ubuntu Sans and Ubuntu Sans Mono
  lead the font stacks, and the family follows the Ubuntu desktop's radii and flat buttons. The
  Style picker now offers Stokes, Cupertino and Ubuntu; the workspace, `--theme` and `?theme=`
  accept the new names. On Linux the Ubuntu family is preferred when no theme has ever been chosen,
  so a fresh window matches the desktop; a theme the user did choose still wins.

### Fixed

- **The packed mesh cache could serve an old mesh for good after its source file changed (T-101
  review).** An entry was stamped with the source file as it stood when the pack was written, so
  bytes made from an older file were vouched for as fresh; reopen after editing a STEP file and the
  old mesh came back, again and again. An entry now carries the stamp its own bytes were made from,
  the same verdict the per-part file gives. A part with no source (an operation's output) is covered
  by dropping the pack when such a part is rewritten. The pack layout is version 2: a version-1 pack
  reads as absent and is written again from the per-part files, so no project needs a migration.
- **Opening a project rewrote its whole pack and read every part file every time.** The pack is now
  written only when it lacks a part, and a per-part file is opened only for a part neither memory nor
  the old pack holds. The "11x" in `docs/engineering/performance.md` is the cache read alone, and the
  doc now says so.
- **A reader could keep a save from replacing the pack on Windows**, and a rewrite by another process
  was never noticed. The reader now stays away while the pack is replaced, and the store refreshes the
  header once per restore.
- **Every content change cost one full id-pass render (T-103).** The box-select programs are now
  compiled once per GL context and batch mode, not after every change.
- **On Linux the desktop shell filed the window under the interpreter, not under Stokes.** The
  Wayland `app_id` the compositor receives was measured as `"python3"`, so a dock or taskbar had
  nothing to match `Stokes.desktop` against and showed a generic entry. `stokes.app.icon` now sets
  the desktop file name at start-up (`set_desktop_file_name`, called beside the Windows taskbar
  identity before the first window exists); the compositor receives `Stokes`. The `.desktop` file
  itself is M8 packaging and is unchanged.
- `tests/test_geometry_pack.py`: the pack had no test; it now has 18 (round trip, damaged packs,
  stale source, rewrite invalidation, the held-handle replace, refresh, and the store's rebuild).
- **Measure can only measure between arbitrary picked points (T-113).** B-rep edge and vertex
  topology is now read from the shape: `stokes/geometry/topology_brep.py` enumerates edges and
  vertices in `TopExp.MapShapes` order (ADR-0022 §15.1), samples each edge as a bounded polyline and
  records its true B-rep length, centre, endpoints, tangent and curve type, plus each vertex's degree
  and incident edges, all in metres. A mesh-only part is refused with a message rather than given
  invented ids, and a versioned bounded codec (`topology_transport.py`) carries the record to the
  lazy endpoint. Measuring Face/Edge/Vertex itself still needs that endpoint and the pick paths
  (T-057+); this is the data they read, so T-113 remains open.
- **No part could report its B-rep edges and vertices, and none could be re-matched (T-055).** The
  worker now answers a `topology` job for one part — loaded through the shape provider, so a source
  file and an operation output both work — writes a versioned `<part>.topology` record whose sidecar
  is the commit, and the client reads it back as one header plus one float32 frame.
  `stokes/geometry/topology_fingerprints.py` adds per-edge (true B-rep length, centre, endpoints,
  tangent, curve type) and per-vertex (position, degree, incident edge lengths) fingerprints with
  the face matcher's one-to-one, ambiguity-aware matching, so two identical holes stay unmatched
  rather than being guessed between. The `tag` member schema is unchanged: typed edge/vertex
  members are T-056.
- **The set-up had no flat view of its own (T-048).** A **Conditions** panel lists each region with
  its boundaries beneath (proxy swatch, name, kind label, primary value in display units and a
  derived status dot — the same derivation the tree uses, from ADR-0022 D2.10), then the unassigned
  face count and the `setup.check` findings with a Fix button. It is registered through the workspace
  registry's plug-in route and placed as a default tab beside Properties in the right slot, in every
  shipped preset.
- **Setting up a flow by hand meant several operations and conditions in sequence (T-050).** The
  Prepare menu now leads with **External flow…** and **Internal flow…**: External flow opens the
  enclosure with a velocity field and an "Add default conditions" switch, and Internal flow runs the
  extraction and then takes the inlet and outlet caps in an Openings step — both then call
  `setup.defaults` in one undo step. They reuse the existing operation card, so there is no separate
  wizard.
- **Bodies could not be given a material (T-045).** The context toolbar gained **Material ▾**: the
  project's materials in a fluid and a solid section, the five most recent names (a per-browser
  convenience), and **Browse library…**, which searches `material.library_search` with provider chips
  and adds a record. An existing material is one `region.assign` (one undo step); adding from the
  library is two, and its toast says so.
- **There was no way to find the other faces like the ones picked (T-051).** The context toolbar
  gained **Related ▾** with the six relations the server supports (same area, coplanar, parallel,
  coaxial, tangent, same colour). A click adds the matches to the pick and a Shift-click replaces it,
  per ADR-0022 §6.4, and the result is reported as "4 faces on 2 parts". The angle relations' tolerance
  is labelled in radians because that is what the server takes, despite the ADR describing them in
  degrees. The hover preview is not wired yet.
- **A boundary had no marker in the viewport (T-046).** Each boundary now draws one glyph at the
  anchor `tag.anchor` reports — an instanced mesh per shape on its own render layer, so the outline,
  picking, shadow and batching never see it — pointing **inward for an inlet and outward for an
  outlet** and keeping a constant 28 px on screen as the camera moves. The catalogue's own `glyph`
  governs the shape, including its literal `"none"` (which the `wall` kind uses) for a kind that
  draws nothing. Hover, click-to-select and the `view.glyphs` toggle are not wired yet.
- **The tree showed `region` and `boundary` nodes as anonymous groups (T-047).** They now have
  proper rows: a region or boundary takes the **swatch and eye of the tag it references** (so the same
  tag cannot show two colours), a boundary shows its kind label and its primary value in the project's
  display units, and a plain group from before typed boundaries is marked as one rather than hidden.
  Each row's status dot is **derived** per ADR-0022 D2.10 — `failed` for a missing tag or invalid
  kind, `stale` for ids referring to an earlier B-rep, `warning` for a check finding — never stored.
- **The docs described the pre-ADR-0022 boundary model (T-052).** The UI design contract, the OpenFOAM
  integration note, the geometry schema and `web/DESIGN.md` now describe what exists: one `boundary`
  type with a condition `kind` (the boundary table gained the missing `pressure_inlet` and is marked
  as the intended M3 mapping, not a built compiler), tags that carry edges and vertices as well as
  faces with one shared basis and the differing numbering, the lazy topology endpoint, the four pick
  modes and element-aware Measure, the value panel and the set-up status readout.
- **Assigning a condition had no panel: the kind menu opened a small dialog (T-044).** Choosing a
  kind now opens the value panel of ADR-0022 §7.1 — a 360 px card listing the condition kinds by
  family with a colour dot each (an unavailable kind is disabled and names the milestone that brings
  it), with the boundary's defaulted unique name, the target line, and the kind's `when`-filtered
  parameters in the same `SchemaFields` the Properties panel and operation card use. Enter assigns
  (`boundary.assign`, one undo step), Esc cancels, and a refused value keeps the server's message
  under the field.
- **Measure could not target an edge or a vertex, and the set-up state was invisible (T-113,
  T-049).** The measure tool now takes a B-rep edge or vertex under the pointer (a vertex wins over
  an edge at a corner) and falls back to the face plane and then the surface point, so a mesh-only
  part still measures as before. The output drawer's strip shows the set-up readiness — a dot in the
  state's colour and one line such as "Set-up · needs an outlet · 12 faces unassigned" — from a
  debounced `setup.check`, which the front end had never called; clicking it lists the findings.
- **Edges and vertices could not be picked or seen (T-058, T-060).** The viewport now resolves a
  click in Edge or Point mode in screen space over the part's topology record, draws the hovered and
  picked edges and vertices as one accent line object and one marker object per set (batched across
  parts, rebuilt only when the pick changes, so body/face mode and the orbit path do no extra work),
  and offers all four pick modes in the mode pull-up. A click in Edge mode picks nothing rather than
  the face behind it, and an edge pick can no longer be assigned as a whole-body region — which it
  could have been, silently, before this change.
- **A tag could hold edges and vertices but nothing consumed them, and no part could report its
  topology (T-059, T-058 groundwork).** `OpContext` resolves a tag's `edges`/`vertices` to real B-rep
  topology, and the `cap` operation gained a `loop` tag parameter: capping a named opening closes
  just that one rather than every opening, and refuses with a message when the tag's edges lie on no
  closed loop. The client gained the topology record decoder, `fetchTopology` (404 meaning "this part
  has no B-rep"), and the lazy fetch lifecycle of ADR-0022 §15.2 — fetched only while Edge or Point
  mode is on, at most three parts at a time, the hovered part first, released on leaving. The edge and
  vertex *hover and selection overlays* are the remaining part of T-058.
- **Measure could only report a distance between two clicked surface points (T-113).** It now
  measures between *elements*: a B-rep face, edge or vertex. Two parallel faces report their normal
  gap (which a point-to-point measure cannot express), two non-parallel faces report that they meet,
  and edges and vertices use a clamped closest-approach. Face measurement is wired into the measure
  click today; picking an edge or a vertex still needs T-058's screen-space topology pick, so a
  click on one still measures its surface point rather than inventing a target the user did not pick.
- **The pick could only hold bodies and faces (T-057).** `PickMode` gained `edge` and `point`, and
  the live pick holds edge and vertex ids per part beside faces (ADR-0022 §15.4). `convertPick`
  follows the ADR's conversion ladder and refuses to invent a conversion it cannot compute. A new
  pure module (`web/src/viewport/topologypick.ts`) adds the screen-space edge/vertex hit tests of
  §15.3 — a device-pixel tolerance, nearest-first ordering with depth and stable-id tie-breaks, and
  the Alt + wheel cycle — with 28 tests. The scene wiring and the topology fetch lifecycle are T-058.
- **A tag could not hold edge or vertex members (T-056).** `tag` members now carry optional
  `edges` and `vertices` B-rep id ranges beside `faces`, sharing the member's one `basis`
  (ADR-0022 §15.1). The change is additive: pre-existing member text parses to exactly the same
  members it did before, a face-only tag's stored JSON is byte-for-byte unchanged, and every
  face-only reader is unaffected. `faces` is still required in stored text (an edge-only member
  writes `[]`), a basis may accompany any explicit id set but not a wholly whole member, and
  `difference` refuses to complement an edge or vertex set whose count it does not have rather than
  guessing. Re-matching a tag across a re-import drops edge/vertex ids and reports how many, because
  their fingerprints are not archived yet.
- **`GET /api/geometry/topology/{node_id}`** serves one part's edges and vertices through the
  versioned topology codec, cache-first and generated in the worker otherwise (ADR-0022 §15.2). The
  route is registered before the `/api/geometry/{name}` catch-all; a part with no B-rep (a
  mesh-only import, or a derived part whose operation has not run) is a 404 so the client shows
  "no edges" rather than inventing them.

### Changed

- **A reopened project loads its meshes from one file instead of 4,500 (T-101).** The T-096 profile
  put 84 % of a cold request in the per-file `open()`, and left the cause open: an antivirus filter
  driver would have made it deployment guidance rather than a code change. It is not — the cost is
  charged at `CreateFile` and our code chose to pay it 4,500 times. On this machine the measurement
  is unambiguous: **Defender's real-time protection was off during every run**, a metadata-only
  `stat()` costs 72 % of a full read, a directory scan that opens no file is 12× cheaper, and
  opening one file and slicing it costs 4.2 µs a part against 630 µs an open. Each project now keeps
  `cache/geometry/meshes.bin`, one pack laid out like the `adjacency.bin` archive already beside it,
  and reading a part is one `seek`+`read` on a handle the project holds open. **`read_cache` over all
  4,500 parts falls 5,732 → 465 ms (11.5×; 15.9× in a busier session), and the real server's cold
  transfer 3,558 → 2,248 ms.** The pack is additive: every part is still written as `<id>.mesh`, an
  existing project opens unchanged and gains a pack the first time it is activated, the pack is
  preferred and the per-part file answers on a miss, and a truncated, version-mismatched or
  out-of-bounds pack reads as absent rather than as valid. Design and numbers in
  `docs/engineering/performance.md`.

### Fixed

- **A reopened project read every mesh cache file twice (T-096).** When the first client connects,
  the server activates the geometry store, which starts a background `restore` pass that walks every
  part and loads its cached mesh — while the viewport's own batch requests are doing the same reads
  for the same parts. On the 4,500-solid benchmark a single page load made **5,620 to 5,710 cache
  reads for 4,500 parts**: about 1,150 of them were the same file opened twice, and the two threads
  held their first and last read over the identical window, so neither was warming anything for the
  other. `_load_cache` now claims a part under the store lock and a second caller waits for the
  reader instead of reopening the file: **4,500 reads, 0 duplicates**, and the restore pass stops
  contending after about 5.5 s instead of running the whole load. Measured with
  `tools/bench_read_timeline.py`; the wall-clock effect is below this machine's noise floor, and the
  profile behind it (84 % of the cold request is one `open()` per part) is in
  `docs/engineering/performance.md`.

### Added

- **Tooling: where the cold 4,500-part geometry load spends its time (T-096).**
  `tools/bench_batch_server.py` profiles `/api/geometry/batch` per request from inside the server
  process (route wall clock, store, cache read, decode, index lookup, with the requests in flight
  varied); `tools/bench_cache_read.py` separates the file read from the decode and compares one
  handle against 4,500; `tools/bench_read_timeline.py` timestamps every cache read by thread during a
  real page load; `tools/bench_single_cache.py` measures what a single-handle cache would save
  (3.9 s → 0.9 s cold) without changing any stored format.

- **Measure: point-to-point distance in the viewport (T-098).** `view.measure` (`M`, and the Modes
  island's Measure button, which now shows its active state) turns the tool on, and each click that
  meets a face records the surface point under the pointer: two of them give the distance between
  them. The measurement is drawn in world space — a line with a marker at each end, in the accent
  colour — so orbiting and zooming leave it exactly where it was measured, and the figure
  (`30 mm`, `2 m`, `1.5 km`: the scale bar's own unit ladder and formatter) floats at the projected
  midpoint, kept inside the viewport and clear of the top strip the scale bar and the import line
  share. A third click starts a new measurement at the point it picked, so two unrelated distances
  are never chained through a point nobody clicked; `Esc` leaves the mode and takes the measurement
  with it. View state only: no API command, no journal record, nothing to undo.

- **The 4,500-part project opens about twice as fast (T-096).** The viewport asked the server for
  meshes two batches at a time while it applied the replies, so it spent most of the load with either
  the server or the page idle. Four batches are now in flight, which is what the transfer bench
  measures with. On the 4,500-solid benchmark the window from the tree arriving to the last mesh
  drawn went from **23.5 s and 18.4 s** to **11.1 s and 12.8 s**; the server does the same total work,
  the replies simply overlap. Measured with the loader's own marks (`window.__stokesLoad`), and the
  correction that made it visible is in `docs/engineering/performance.md`: this time was previously
  recorded as 14 s of *browser* apply, which the instrumentation showed was really the page waiting on
  the server.

- **Default conditions for the two flow templates (T-039).** `setup.defaults` turns a prepared
  body — an enclosure output or an extracted fluid — into a ready set-up in ONE undo step: the tag
  names the operation gave it become the conditions (`inlet` a velocity inlet, `outlet` a pressure
  outlet, `far_field` a slip wall, `symmetry`/`ground`/`walls` their own, the extraction's other
  `caps` no-slip walls), the picked inlet and outlet faces become tags, every face no tag holds
  joins a `walls` wall, and the body's region takes its material or a built-in `air`/`water` made
  for it. `stokes setup defaults <project> <target> external --velocity '2 [m/s]'` is the same
  command from the CLI; the result names the boundaries with their tags, how many faces became
  walls, what was already there and what to know.

- **A scale crosshair at the top of the viewport (T-097).** The viewport had no scale at all, so a
  20 mm part and a 20 m one looked the same until you opened the tree and read the bounds — a bad
  gap in a CFD tool, where the model's size is the first thing to check. A vertical centre line with
  an arm either side and a tick at each outer end now sits at the **middle of the top edge**, with
  the length under it (`10 mm`, `2 m`, `1.5 km`). The arms together span what that length genuinely
  measures on screen, so the drawing IS the length rather than decoration beside a number, and the
  length is a round value from the 1-2-5 series. It is measured at the orbit target and says so on
  hover, because a perspective view has no single scale; under the orthographic camera it is exact
  everywhere. Its unit ladder is mm / m / km / µm with no centimetre, so a millimetre model is not
  silently converted. The top centre is used deliberately: the four corners belong to the islands
  and their offsets move as panels open and collapse, so a corner scale gets covered on a narrow
  window.
- **The 4,500-part viewport benchmark, and what it measured (T-089).** `tools/bench_scene.py build`
  tessellates `pin_grid(50, 45)` into a project with its mesh cache, so the benchmark needs no STEP
  file, and `measure` serves it, drives headless Chrome over the DevTools protocol and prints the
  orbit, box, lasso, hover and click figures — naming the WebGL renderer in every run, because a
  SwiftShader line is a regression baseline and never the budget. Run on the owner's Intel Iris Xe
  (the real GPU, not software): still frame p50 **12.76 ms** (30 fps budget met, 60 fps discrete-GPU
  budget not), face box 33 ms and both lassos 0.2 ms — and a **wide body box 588 to 1,262 ms against
  the 50 ms budget**, scaling with the number of bodies picked rather than the box area, which is
  recorded in `docs/engineering/performance.md` as the one M1 budget this fails. The click
  comparison landed the same way: the id buffer is 307.6 ms at p95 against the raycaster's 77.0 ms,
  so `idPickEnabled` stays off by default.
- **Selectable mouse schemes, so the view can always be rotated (ADR-0021 3.4, amended).** The
  bindings shipped as three.js's web defaults and a left drag on empty space drew the selection
  band, so the only way to orbit was to press on a part: a user reported "the left click only
  allows drag and selection now, there's no way to rotate the view". Rotation is now a choice, in
  the Modes island's **Mouse** menu, and every scheme rotates from anywhere in the viewport:
  **CAD** (default) rotates on the middle button and pans on the right, leaving the left for
  selecting and the band, as FreeCAD and SolidWorks have it; **ParaView** rotates on the left and
  moves the band to Shift; **Blender** rotates on the middle and pans on Shift+middle;
  **Select / Orbit** keeps the middle rotating and adds a switch for the left drag. The band keeps
  its meanings — plain replaces, Shift adds, Ctrl removes — Alt or the Lasso toggle still draws the
  lasso, and `Shift N` walks the schemes.
- **A capture setup hook (`--setup PATH`, T-088).** `python -m stokes.app --screenshot shot.png
  --setup script.js` runs that JavaScript in the page once it has loaded, before the shutter. The
  host could never drive the interface — the bridge goes from the page to the host and never back —
  so every review screenshot so far showed only the opening state: no open menu, no card, no
  progress line, no selection. The run adds `?drive=1`, which is what exposes the stores
  (`window.__stokes`) for that load only; the app is otherwise unchanged. Examples live in
  `.mailbox/shots/` (`prepare.js` opens an operation card; `pick-and-run.js` runs one for real).
- The M1 test set and its gate (ADR-0020 decision 9; T-034): `tests/m1_fixtures.py` builds ten
  synthetic STEP files — an assembly of 24 named solids, a filleted bracket with mounting holes, a
  hole grid, a body with one internal void and one with two, a thin-walled part and a box each with
  a 0.01 mm sliver step, an open surface model, an open pipe tee, and a housing of fused bodies —
  plus the open surface again as IGES, each written with OCP into `tmp_path` at test time, so no
  binary and no third-party licence enters the repository (CLAUDE.md §6 and §8). `tests/test_m1_gate.py`
  (marked `slow`) takes every file through `geometry.import` → `geometry.check` → one repair where
  the file needs it (`cap`, `slivers` or `sew`) → `enclosure` or `extract_fluid` → a tag over the
  fluid domain → `geometry.export_stl`, then replays the session's journal into a fresh project
  through the real `stokes replay` CLI path and compares the two STL files byte for byte; the
  per-file wall-clock goes to `tests/perf/results.jsonl` as a measurement, with no budget
  (`tests/perf/test_setup_check_perf.py`'s format). Measured 2026-10-05: 8.9 s to 24.0 s per file
  headless, 280 s for all eleven runs and their replays, every replay byte-identical. The owner's
  interactive five-minute run from `python -m stokes.app` remains outstanding.
- Operation cards, the Prepare menu and seed picking (ADR-0021 decisions 7 and 8, ADR-0022 7.3;
  T-029): **Prepare ▾** on the context toolbar (shown when the pick holds something) and on the
  Geometry task strip opens the geometry operations — Enclosure…, Extract fluid…, Boolean ▸ (Fuse,
  Cut, Common) and Repair ▾ (Heal, Sew, Close openings, Unify faces, Drop slivers, Remove faces,
  Fix orientation, Make solid) — as a data list whose entries are disabled with a reason when the
  pick does not fit or the type is not built yet. An entry opens the operation card, a floating
  glass card at the right of the viewport: its title is the type's label, its form is the node
  schema through `SchemaFields` (quantities in the display unit, enums as selects, switches, `json`
  members as "n faces of m bodies"), and its inputs and faces come from the pick it opened with.
  Run calls `operation.create`; the card then shows the run's progress text and a Cancel that
  calls `operation.cancel`, `geometry.op_done` closes it and selects the first output, and
  `geometry.op_failed` shows the message in its red error line (a cancel, `geometry.cancelled`,
  says itself in the quiet line: the user asked for it). The viewport's progress line shows
  an operation under way as well as an import, an operation node with status `stale` offers Run in
  its row menu, and the extract-fluid card's `seed` field has a **Pick in view** button: a one-shot
  point pick (crosshair, Esc cancels) that writes the point 1 % of the part's bounding-box diagonal
  inside the face. `selection.clear` (Esc) leaves the point pick first, then the card, then the
  pick. The four actions `prepare.enclosure`, `prepare.extractFluid`, `prepare.boolean` and
  `prepare.repair` are registered with no default chords.
- Mesh-only import (ADR-0021 decisions 1.2 and 1.3; T-017): `geometry.import` reads STL (binary
  and ASCII, one body per `solid <name>` block), OBJ (`v`/`f`, `o`/`g` bodies, fan-triangulated
  faces, negative indices, other keywords ignored) and 3MF (`3D/3dmodel.model` or the path
  `_rels/.rels` names, `<build>` items, components resolved to depth 8 with their 3x4 transforms,
  the model's `unit`). Such a part has `family: "mesh"` and no B-rep: its faces are the regions of
  the triangulation between feature edges (`meshfile.split_faces`, 30°), so a box gives six and a
  fine cylinder three. `units` stays the import's for STL and OBJ (default mm) and comes from the
  file for 3MF, which refuses the parameter. Untrusted input is bounded: a binary STL's record
  count must match its size, a 3MF member may hold no more than 512 MB uncompressed and 2 GB in
  total and its XML may not declare a DTD, and a NaN or infinite coordinate is refused. B-rep-only
  commands refuse a mesh-only part with `geometry.mesh_only`: the operations (`operation.create`)
  and `geometry.export_step`; `geometry.check` reports `brep: {checked: false, reason: "mesh-only
  part"}` and still checks the tessellation. `geometry.export_stl`, tags and the viewport work as
  on any part.
- Geometry operations, the framework (ADR-0021 decision 8; T-024): `operation.create(type,
  inputs, params, auto_run, wait)` adds an operation node under `/geometry/operations` as one undo
  step that also hides the parts it reads, and runs it in a worker; its outputs are derived parts
  (`derived`, `basis`, `units: mm`) with meshes, added as the run reports them. `primitive` (box,
  cylinder, sphere) and `boolean` (fuse, cut, common, with `fuzzy` and `simplify`) are built in
  this entry; the other ten types are declared here with their parameters and arrive with T-025 to
  T-028 (all twelve are built now — see those entries). Tags follow
  OCCT's history onto the outputs, and the faces they cannot follow are listed in the run's report.
  Changing a parameter, or re-importing an input, makes the operation stale and runs it again
  300 ms after the last change (`auto_run`); undo and redo give the same ids and tags; a journal
  replays it, waiting for each run. Also `operation.run`, `operation.cancel`, `operation.delete`
  (with or without the outputs), `operation.list`, `operation.info` and `operation.types` (the
  cards' schemas); events `geometry.op_progress`, `geometry.op_done` and `geometry.op_failed`;
  errors `geometry.op_invalid`, `geometry.mesh_only`, `geometry.op_failed`. Every part's B-rep is
  now cached beside its mesh (`cache/geometry/<part>.brep` with a sidecar), written by the import
  and by the operations, and loaded by the shape provider the worker jobs use; parts gain `basis`
  (the B-rep version tags compare, written when the file is read) and `derived`. Contract:
  `docs/engineering/geometry-schema.md` ("Operations").
- The `enclosure` operation (ADR-0021 decision 6.2; T-025): a box, cylinder or sphere around the
  bodies with six per-side margins, relative by default (`factors` multiples of the bodies' extent
  along each axis, or the absolute `margins`), a `flow` direction naming the `inlet` and the
  `outlet`, `symmetry` planes through the bodies' bounding-box centre that keep the positive half,
  `ground` for a floor at the bodies' lowest point, `subtract` (true) for the enclosure cut by the
  bodies, `fuzzy` and `tag_walls`. The output is a derived part whose faces are tagged `inlet`,
  `outlet`, `symmetry`, `ground`, `far_field` and `walls`, and the run's report carries the volume,
  the bounding box and the blockage ratio, with a warning above 1 %. `operation.types` says
  `built: true` for it; contract: `docs/engineering/geometry-schema.md` ("Operations").
- The `extract_fluid` operation (ADR-0021 decision 6.1; T-026): `cavity` mode takes the inner
  voids of the closed bodies it reads — every shell of a solid other than its outer shell, made a
  solid and oriented so its volume is positive, skipped below `min_volume` — and gives one derived
  part per cavity (`<input>_fluid`, `_fluid_2`, …); a body with no cavity at all, or none above
  `min_volume`, fails as `geometry.error` "no closed cavity in …". `region` mode collects every
  face of the inputs, the faces of the `caps` tag and, with `auto_cap`, a planar face on each
  closed free-edge loop (a loop no plane fits is reported in the run's report and warning, and
  left to the `cap` operation), makes the closed volume with `BOPAlgo_MakerVolume`, and gives the
  one solid that holds `seed` as `<input>_fluid`; when no solid does, the run fails with
  `geometry.seed_outside` and lists every candidate's volume and centroid, in the error's
  `details.candidates` and in the report. The faces the volume maker adds are the output's
  `cap_tag` tag (`caps` by default), so the ends of a pipe come out ready to be named inlet and
  outlet; tags the user made on the body's faces follow the history onto the fluid. Contract:
  `docs/engineering/geometry-schema.md` ("Operations").
- Paint select (ADR-0022 decision 6.5, T-043): `P`, or the Paint toggle in the Modes island, turns
  the canvas into a brush. A left drag paints instead of orbiting — the camera is held still for the
  whole stroke — and every face the pointer crosses joins the pick as the highlight follows it;
  Ctrl-drag takes faces off again. Body mode paints whole bodies the same way, and a press on empty
  space paints nothing and orbits nothing. It rides the hover pick that already exists, so it needs
  no id buffer and adds no work per face of a mesh. `Esc` leaves the mode first and clears the pick
  only on a second `Esc`; switching Body/Face keeps paint mode, which is not persisted. The bulk
  store action behind it, `pickMany`, is the one T-032's box and lasso will apply through.
- The four repair operations (ADR-0021 decision 5; T-027): `heal` fixes a body's topology with
  `ShapeFix_Shape` to `tolerance`, drops the edges below `small_edges` with `ShapeFix_Wireframe`,
  makes the edges' 2D curves agree with `BRepLib.SameParameter_s` and clamps what the result may
  carry to `max_tolerance` (`ShapeFix_ShapeTolerance`), reporting what the fixers' status flags say
  they did and the largest tolerance before and after. `sew` runs `BRepBuilderAPI_Sewing` over every
  input and turns each closed shell of the result into a solid, reporting the free edges before and
  after — counted across the faces of a compound, because `geometry.check`'s own count reports 0 for
  an unsewn compound of loose faces and tells the user nothing (the batch 4 review's finding).
  `orient` makes every solid forward with `BRepLib.OrientClosedSolid_s` and every loose shell
  coherent with `ShapeFix_Shell`, reporting the solids it flipped and the faces it reoriented.
  `solidify` sews the inputs and asks `BOPAlgo_MakerVolume` for the volume their faces bound, one
  output per solid (`<input>_solid`, `_solid_2`, …), and fails as `geometry.op_failed` "no closed
  volume could be built" when they bound none. Faces follow the sewing tool's `Modified` and the
  fixer's re-shape context, so a tag on a loose face reaches the solid it became; contract:
  `docs/engineering/geometry-schema.md` ("Operations").
- Box and lasso selection (ADR-0021 decisions 3.3 and 3.4, T-032): a drag on empty space draws a
  dashed accent rubber band; releasing it picks every body with a pixel inside (or, in Face mode,
  every face), through the same `pickMany` the paint stroke uses — Shift adds, Ctrl removes, plain
  replaces. `Alt`-drag, or the new Lasso toggle beside Paint, draws a freehand path instead and
  tests each pixel against it. What the pointer is over is answered by an offscreen id buffer: the
  part layer alone is drawn into an RGBA8 target, R,G naming the part and B,A the face
  (`web/src/viewport/idbuffer.ts`), one read-back per gesture end and no raycast per part. Each part
  mesh carries a per-vertex `faceId` attribute built from the transport's per-triangle face ids
  (`groupFaceIds`, +2 bytes a vertex) and each batch an instance-id to part-index texture, so a
  batched part is named as exactly as one drawn on its own; hidden and isolated-away parts are
  outside the pass and translucent ones are drawn. A patch past 65,535 faces logs once and loses
  face picking above that ordinal. The probe gains `idPick`, `idReadMs` and a switch (`idPickEnabled`)
  that moves click picks onto the buffer; it is off by default, because the benchmark that decides
  whether the buffer is the faster path has not been run.
- The other four repair operations (ADR-0021 decision 5; T-028): `cap` ("Close openings") finds
  the closed free-edge loops of its inputs and closes each with a planar face where a plane fits
  through the loop and with a `BRepFill_Filling` patch where none does (`planar_only` leaves those
  open), skips the loops above `max_size`, sews the caps on with the T-027 sewing tool and makes a
  solid of the closed shell. The caps become the tag `<input>_caps` (kind boundary), so the user
  renames them inlet and outlet and `extract_fluid` takes that tag as its `caps` — one test chains
  the two. `unify` merges the faces that lie on one surface with `ShapeUpgrade_UnifySameDomain` at
  a linear and an angular tolerance, keeping the faces a `keep` tag names apart (their edges go
  into OCCT's keep map with them: a face alone in that map is still merged on this OCP). `slivers`
  drops the sliver faces and edges below `size` with `ShapeFix_FixSmallFace` and
  `ShapeFix_Wireframe`, and, with `holes`, the inner wires below `size²` with
  `ShapeUpgrade_RemoveInternalWires`. `defeature` ("Remove faces") removes the picked faces with
  `BRepAlgoAPI_Defeaturing` and unifies what they left behind, composing the two histories with
  `BRepTools_History.Merge` so a tag follows both steps. Contract:
  `docs/engineering/geometry-schema.md` ("Operations").
- Analysis set-up, the check (ADR-0022 decisions 5.1 to 5.4, 2.7 and 2.10): the read-only
  `setup.check` answers with `{state, next, counts, findings}` — `not_defined` (no region holds a
  body), `incomplete`, `stale` (a region or boundary tag is stale or missing and nothing else is
  wrong) or `ready` — and with the sixteen findings of the contract's table, each carrying its
  stable code, its severity, a sentence, a suggestion, the nodes it is about and, where one exists,
  the fix that mends it. `uncovered_faces` and the faces `setup.overlap` reports come from the
  members' ranges, never expanded to face ids (4,500 parts); `setup.stale_tag` reads the staleness
  the session knows, so a CLI without meshes makes no such finding; `setup.needs_physics` stays
  information until the physics models exist (M3). The check never writes to the tree, emits
  nothing and is not journaled. CLI: `stokes setup check <project>`, which prints the answer and
  exits 1 while the state is `incomplete`. Contract: `docs/engineering/setup-schema.md`.
- Two read-only geometry queries over tags and picked faces (ADR-0022 decisions 8.1 and 6.4):
  `tag.anchor(tags)` (default every tag) returns one anchor per tag in metres — the area-weighted
  centroid and mean normal of the member faces, the summed area, and the diagonal of the bounding
  box of the faces' centroids, with `partial` when a member part has no mesh yet — for one glyph
  per boundary; `tag.related(members, relation, parts, tolerance)` returns the faces like the
  picked ones, the picked faces excluded, over every part with a mesh by default: `same_area`
  (2 %), `coplanar` (0.1 % of the part's bounding-box diagonal plane offset, angle fixed at
  0.035 rad), `parallel` (0.035 rad between open faces' mean normals), `coaxial` (the axis fitted
  from the picked facets' area-weighted normal covariance, 2 % of the picked radius; a pick whose
  smallest eigenvalue exceeds 5 % of the largest is refused, `tag.invalid`, and a pick whose
  facets share one direction wraps no axis and finds nothing), `tangent` (the 30°
  feature-angle flood of `tag.region`) and `same_color` (the `tag.by_color` parts of the picked
  faces' colours). Both read the per-face fingerprints and facets the session already holds, are
  not journaled, and change nothing; contract in `docs/engineering/geometry-schema.md`.
- Analysis set-up, the commands (ADR-0022 decisions 2.8, 2.9, 3 and 4): `boundary.assign` gives a
  condition kind and its values to picked faces, a tag, a boundary or a pre-ADR plain-group
  boundary in one undo step, making what is missing (the tag, the region `fluid` with its tag and
  `boundaries` group) and giving the tag the kind's colour; a second assign on a tag re-kinds its
  boundary instead of adding one. `boundary.set`, `boundary.delete` and `boundary.list` (tag,
  primary value, findings, derived status); `region.create`, `region.assign` (bodies join the
  region of their material; a fluid material on a solid region is refused,
  `materials.kind_mismatch`), `region.set`, `region.delete` and `region.list` (bodies, boundaries,
  faces no boundary covers). Every command is one journal record with the ids it chose, so a
  replay builds the same tree. References are guarded: `node.remove` refuses a node another refers
  to (`model.referenced`), `tag.delete` refuses a tag in use unless `force`, which deletes its
  boundaries and regions in the same step, and `parameter.set` takes `null` to clear an optional
  reference (`ParamSpec.required` marks the ones that may not be null). `tag.list` names each
  tag's boundaries and regions and warns of faces two boundary tags share (`tag.overlap`);
  `material.list` names each material's regions. Plain groups become typed in place with the new
  `ConvertGroup` model command. CLI: `stokes boundary assign|set|delete|list|kinds`,
  `stokes region create|assign|set|delete|list`, `stokes tag delete --force`. Contract:
  `docs/engineering/setup-schema.md`.
- The tree's style controls (T-022, ADR-0021 decision 4.3): a part or tag row shows its 12 px
  colour swatch between the type icon and the name, and its visibility eye between the value and
  the status dot. The eye shows on row hover, while the row's menu is open, on the selected row
  while the tree holds the focus, and always while the node is hidden or its card is open; it
  writes `{visible}` through `geometry.style` in one undo step and leaves the selection alone. The
  swatch opens `StylePopover`, the 220 px card that edits `color` (150 ms after the typing stops)
  and `opacity` (once per gesture) on that same command. A group that holds parts carries the eye
  alone — no swatch, no card — because it owns no colour or opacity: it hides every part beneath in
  one step. The row menu gains `Hide` / `Show` and `Colour and opacity…`, so the keyboard reaches
  both. The name gives way first: it stops at `calc(62% - 18px)` so it never runs under a control.
- Selection additions (T-042, ADR-0022 decisions 6.1, 6.2, 6.3, 6.7 and 11.2): switching Body/Face
  keeps the pick — the faces become the bodies that own them, the bodies become every face of the
  part (a part whose face count the transport does not carry is dropped) — and keeps the active
  member while it survives, instead of clearing. Clicking escalates: in face mode a double-click grows
  the face to its smooth tangent chain (`tag.region`, the same call Grow makes) and a triple-click
  takes every face of the body; in body mode a double-click takes the parts of the same import group,
  the clicked part first, and a triple-click adds nothing. Shift and Ctrl apply to the escalated set as
  they do to a click. `Alt` + wheel on the viewport cycles the candidates under the pointer (distinct
  bodies, or distinct faces), the highlight follows the one it stands on, a click picks it, and moving
  more than 4 px returns to the nearest; the wheel without `Alt` still zooms. The name of what the
  pointer is over — the patch, "face 12 of part_3" in face mode, and the `{label, index, count}`
  readout — goes to the store for the drawer strip (T-049 draws it). The palette gains
  `selection.previous` ("Previous selection", no default chord), which brings back the pick before the
  last change; the last ten are kept.
- Analysis set-up, the data model (ADR-0022 decision 2): the condition catalogue
  `stokes.physics.conditions` with eight kinds (velocity, mass-flow and pressure inlets, pressure
  outlet, wall, symmetry, cyclic, and the fluid-solid interface, which arrives in M5), each with
  its family, the `boundary` parameters it uses and when, their rules, the solver fields it fixes,
  the physics its parameters need, its tag colour and its viewport glyph. New node types beside
  `tag`: `region` (kind, tag, material) and `boundary` (kind, tag, and the union of every kind's
  parameters, stored in SI, so a change of kind keeps the node and its expression references).
  `boundary.kinds` returns the catalogue with each parameter's JSON schema, without a project. The
  commands that assign conditions and materials follow. Contract: `docs/engineering/setup-schema.md`.
- Geometry import (T-016, ADR-0021 decisions 1.1, 1.2 and 1.4): `geometry.import` accepts STEP,
  IGES and BREP through one reader entry point (`read_source`), with `geometry.import_step` as its
  alias so old journals replay; the `part` node gains `family` (`brep` | `mesh`, default `brep`);
  `units` (default mm) names the length of a BREP file, and STEP and IGES files, which declare
  their own units, refuse it; a reader's notes (an unknown IGES unit flag) reach the parts as
  `meta.warnings`; `geometry.formats` lists the formats and the picker's file filter;
  `stokes geometry import` takes every format and `--units`. An IGES file without solids gives
  its loose faces as one `faces` part, not six (they are not sewn: that is a repair), and
  `geometry.check` and `geometry.export_step` read a BREP part in the units it was imported in,
  so an inch BREP exports at its own size.
- Face and body picking (ADR-0021 decisions 3.1, 3.2 and 3.5): the Modes island starts with a
  `Body` / `Face` control and the `selection.mode` action toggles it (no default chord); then a
  click replaces, Shift-click adds and Ctrl-click toggles, in either mode. The store holds
  `pickMode` and `picked { bodies, faces }`, while `selectedPatch` stays the active member, so the
  tree, the properties card and Isolate and Hide keep working; Esc clears the pick. Picked faces
  are tinted together by one `SelectionHighlight` overlay (it borrows a single group's buffers and
  copies across groups and parts), picked bodies beside the active one are tinted whole, and in
  face mode the context toolbar anchors to the picked faces' union. The `?debug=1` probe adds
  `pick()` and `pickMs`.
- Naming picks and drawing tags (ADR-0021 decisions 2.2, 3.5 and 4.1): while faces are picked, or
  more than one body, the context toolbar leads with **Name…**, which asks for a tag name and runs
  `tag.create` on the pick (a `boundary` from faces, sent as ranges; a `region` from whole bodies),
  then selects the new tag row and keeps the pick; a name the server refuses stays in the dialog with
  its message. **Grow** replaces a face pick with the region the new read-only `tag.region (members,
  angle_deg = 30)` returns (the feature-angle flood of `tag.grow`, touching no tag, not journaled);
  **Clear** empties the pick. The palette lists `selection.name` and `selection.grow` (no default
  chords). Visible tags draw their faces in their own colour at their opacity, lit like the part,
  through the ADR's switch to a per-vertex RGBA8 `tint` attribute on every part geometry, mixed in by
  the look's shader and copied into the batches (`batchable()` now wants position, normal and tint):
  one overlay mesh per tag, measured first with one tag over every face of the 4,500-part
  benchmark, doubled the triangles drawn and added 10.6 ms to the GPU frame against a 1 ms budget;
  the tint draws the same calls and triangles as no tag. Where tags overlap the one lower in the
  tree shows.
  A tag with `visible: false` hides its faces (`scene.setHiddenFaces` filters the drawn index, own
  mesh and batch copy alike, so they are neither drawn, picked nor outlined; whole-part members hide
  nothing). The scene keeps both per part and applies them to every mesh a part gets, coarse or fine.
  Selecting a tag row picks its members (`tag.members`), and `patchForNode` gives tags no patch. The
  probe's `perf()` reports `tagTriangles` and `hiddenTriangles`.
- Materials slice 2 (ADR-0018): CoolProp (pure fluids, mixtures, incompressible liquids and solutions), Cantera (ideal-gas mixtures as JANAF) and REFPROP providers behind one protocol in a persistent worker process (CoolProp holds the GIL and takes several seconds to import); library search with transport-coverage flags; `fit.py` with the contract's candidate order and 1 % tolerance, background fits with progress, cancel and undo; `material.providers`, `refit`, `set_state`, `cancel_fit`; `add_from_library` with composition, basis, phase, range and tolerance; templates from the built-in catalogue; a library property edited by hand is marked detached; the `materials` extra.
- M1 slice 1 (ADR-0020): `named_selection` nodes with face-range members, per-face fingerprints and re-matching after a re-import, feature-angle growth over the tessellation, 13 `selection.*` commands with undo and journals, `geometry.check` (tessellation checks) and `geometry.export_stl` (one solid per selection, in metres); contract in `docs/engineering/geometry-schema.md`.
- B-rep checks and STEP export (ADR-0020 decisions 6 and 7), both run in the geometry worker (`stokes/geometry/brep.py`, job kinds `check_brep` and `export_step`). `geometry.check` reads each part's B-rep as well and reports validity (`BRepCheck_Analyzer`), free edges (`ShapeAnalysis_FreeBounds`), unclosed shells, and edges or faces under a micrometre, under a `brep` key, and it shows the result on the part's status dot (`warning` on findings, back to `ok` when clean). `geometry.export_step` writes the chosen parts, named, to a STEP file in metres.
- Face adjacency is built at import (ADR-0020): the geometry worker welds each tessellation batch once and the store writes one `adjacency.bin` per project at the end of the import (`stokes/geometry/adjacency_cache.py`), so `tag.grow` reads it instead of building. `selection_cache` uses an entry whose face and triangle counts match the mesh and builds adjacency otherwise. On a 300-solid fixture the build is 0.09 s, reading the archive for `grow` is 0.08 s against 0.18 s for the lazy build, and the import time is unchanged within noise (about 20 s, dominated by the STEP reader).
- `named_selection` is a built-in node type now: `register_builtin_types` registers the light `stokes.geometry.model` beside the study and material types, so every project knows it without the API loading its commands (ADR-0020). `tools/check_forbidden_imports.py` permits `stokes.model` to import that one module and keeps the rest of `stokes.geometry`, which imports OCP, out.
- `stokes selection VERB`: the `selection.*` commands from the command line, with one verb per command (`create`, `add`, `remove`, `set`, `invert`, `grow`, `rematch`, `rename`, `delete`, `list`, `members`, `by-color`, `by-name`). `--project` opens a project, a verb that changes it is saved after the command, and list or object arguments are JSON text: `stokes selection create walls --project demo --members '[{"part": "/geometry/parts/cube", "faces": [3]}]'`.
- ADR-0020: geometry selection and operations model (faces by part and face id, selections as nodes, operations as parametric nodes with shape history, GPU id-buffer picking).
- A `json` parameter kind: `ParamSpec` values are JSON-compatible data stored as is, deep-copied into undo snapshots and compared by value; `parameter.set` takes a JSON value and quantities and expressions do not apply. The optional `max_bytes` caps the serialised value (64 KB by default). `named_selection.members` moved from JSON text in a patterned string to this kind, so the schema describes it as structured data; projects saved before the change read back unchanged because their `members` text is itself JSON. The front-end types carry the kind and the inspector shows the value read-only, formatted.
- A `warning` node status (ADR-0020 decision 6): the model accepts it beside `ok`, `stale`, `failed` and `new`, the tree and the Properties card draw its dot, and the colour is the theme's existing warning token in every theme (dark, light, Cupertino light and dark). `geometry.check` now shows its result on the part's dot: a part with findings becomes `warning`, and a clean one goes back to `ok`.
- Old names keep working after a rename (ADR-0021 decisions 2.3 and 2.4): commands and node types take aliases (`CommandRegistry.alias`, `NodeTypeRegistry.alias`), and `Session.execute` replaces old command names, parameter names and node paths with the current ones before it validates, so journals record current names; `schema.commands` lists each command's old names in `aliases`. The tables are in `stokes/api/legacy.py` and stay empty until the tags rename fills them. Projects are now saved as schema version 2: a version-1 project is migrated when it opens (renamed node types; `/geometry/named_selections` becomes `/geometry/tags`), and earlier Stokes builds refuse a version-2 project. Rules in the Compatibility section of `docs/engineering/geometry-schema.md`.
- `geometry.style` (ADR-0021 decisions 4.1 and 4.2): one command styles many parts, tags or groups of parts at once — `visible`, `color` and the new `opacity` on `part` (`number`, 0 to 1, default 1; readers treat an absent value as 1 and clamp to [0, 1]) — as one undo step and one journal record, with a `node.changed` per changed node and parameter. Nodes already in the target state come back unchanged, and a call that changes nothing pushes no undo step. Hiding the 4,500 parts of the benchmark tree is one step, measured at 0.25 to 0.28 s on the development laptop (not a budget yet).
- Per-body opacity in the viewport (ADR-0021 decision 4.4): a part's `opacity` below 1 draws it
  translucent, in tenths (from 0.95 a part draws opaque, and at 0 it still shows at 0.1), in both
  render paths. A batched part moves to the batch of its opacity bucket, whose key gains
  `|o<bucket>` (an opaque part keeps the key and the batch it had), drawn through a translucent
  clone of the kind's look (`transparent`, no depth writes) with the opaque batches' kept draw
  list, so nothing is sorted per frame. Below the batching threshold, and while it is highlighted,
  a part draws through a translucent clone of its own look. Translucent parts stay pickable and keep
  their contact shadow, but no longer hide the selection outline, so the outline of a part behind
  glass shows whole. Their shaders are compiled in the background after a load: compiled at first
  use, they held the frame of the first opacity change for 0.7 s. The `?debug=1` probe's `perf()`
  adds `translucentBatches`. Orbit figures with 50 and 500 translucent parts of the 4,500-part
  benchmark are in `docs/engineering/performance.md`.

- **Many-part rendering (ADR-0019, decisions 1 to 5).** From 100 parts on, the viewport draws parts
  through `THREE.BatchedMesh`, one batch per look, so the draw calls follow the number of looks
  and not the number of parts; colour and visibility are per instance. On the 4,500-part
  benchmark (20,250 faces, integrated GPU, other agents loading the CPU) an orbit now runs on the
  GPU timer at a median of 10.6 ms per frame and a p95 of 16.6 ms, so the 60 fps orbit gate is
  met, against 250 ms before. **The look fixes have landed**: a batched part looks as it does
  drawn alone. Below 100 parts nothing changes.
- `/api/geometry/batch`: the meshes of many parts in one request, as a binary frame. The layout is
  written down in `_batch_frame` in `stokes/server/app.py` and read by `readBatch` in
  `web/src/api/client.ts`; change both together. All 4,500 parts transfer in 7.9 s cold and
  0.23 s warm (26.2 MB) over loopback, where replies are uncompressed, and in 13.5 s cold and
  1.73 s warm (2.82 MB) for a remote client, where they are gzipped at level 6; one request per
  part took about 113 s.
- The tree scales with the part count (ADR-0019, decision 6): virtualised rows (4,513 rows in the
  DOM become 26), node event patches instead of whole-tree reloads, `node.tree` built in short
  slices and WebSocket per-message deflate turned off. `node.tree` of 4,514 nodes fell from 38 to
  149 ms to 9 to 25 ms, the longest event-loop stall from 169 to 269 ms to 30 to 45 ms, and
  expanding the 4,500-part group from 9.3 s to 60 ms.
- `units.convert`: converts a number between units and returns the factor and offset of the
  conversion, so a caller can convert more values (`25 degC` is `298.15 K`, factor 1, offset
  273.15; `0 m` in `mm` has factor 1000). They are read off a one-unit step, so a temperature keeps
  its offset and a tiny value keeps its factor. An unknown unit, units of different dimensions or
  unit text over 64 characters fail with the error code `units.convert`.
- `stokes units convert VALUE FROM TO` prints that result as JSON.
- `stokes material VERB`: the `material.*` commands from the command line, including `list`,
  `create`, `library-search`, `library-info` and `evaluate`. `--project` opens a project, which
  `list`, `create` and `evaluate` need and the library queries do not (`stokes material
  library-search water`); `evaluate` takes `--temperature` and `--pressure` as a number or a
  quantity such as `25 degC`.
- `tools/bench_viewport.py`: headless benchmark of geometry import and mesh transfer, so load
  time can be compared before and after a change. Note it sends `Accept-Encoding: gzip`; without
  that it measures a transfer the browser never makes.
- The viewport exposes a read-only `__stokesScene` hook (`perf()`, `measureRender()`,
  `measurePick()`, `projectAllPartBounds()`) reporting frame cost, draw calls and pick latency.
  Frame cost is time inside a render, not a `requestAnimationFrame` interval, so it is not masked
  by the display's refresh rate.
- M0 foundations: repository skeleton, agent constitution (`CLAUDE.md`) and
  engineering references, CI gates (lint, types, tests, forbidden imports, IP
  gate, licence audit, pip-audit).
- `stokes.units`: quantities with dimension registry, three display unit
  systems, and a unit-checked expression language (`25 [mm]`, `U_in * 1.5`).
- `stokes.model`: project tree with node schemas, parameter nodes, commands
  with undo/redo, expression dependency tracking, JSON persistence.
- `stokes.api`: command registry with generated schemas, `Session`, events,
  journals with replay.
- `stokes` CLI with JSON output over the same commands.
- `stokes.license`: Ed25519 licence files and feature flags (free tier needs
  no file).
- `stokes_agent`: length-prefixed msgpack protocol, asyncio server and
  client, host probe.

### Changed

- **The selection tint is drawn instanced, not copied, and the pick no longer scans every patch
  (T-095, the box-select budget).** Two costs sat on a wide box release, and both had to go for the
  50 ms budget:
  - `Viewport.tsx` looked for each picked body's patch by scanning **all 4,500 patches**, so a
    3,625-body pick was about **16 million iterations**. It is now one index of the patches and a
    lookup per body.
  - The tint copied every picked triangle into one world-space geometry — transform each vertex,
    dedup through a `Map`, then walk the result again for the bounds. A wide body box is 3,624
    bodies and **565,992 triangles**, which alone measured 417 to 458 ms. The bodies already have
    world matrices, so the tint is now one `InstancedMesh` per group of picks sharing a geometry and
    a face filter: one geometry, one matrix per body, no vertex touched on the CPU. The pick
    signature is a rolling hash instead of a 3,624-name string, and a repeated gesture reuses the
    instance buffers.

  A wide body box over the 4,500-part benchmark went from **556–626 ms to 19–27 ms** across five
  consecutive runs, against the 50 ms budget. A face pick that filters triangles still borrows or
  copies. The overlay's own rebuild is a further 25–70 ms, but it runs in the frame after the
  release, and `tools/bench_scene.py` now reports it separately with a `selection_fresh` stamp so a
  stale figure can never be misread as the release cost.
- **`geometry.check` runs its B-rep pass through the shape cache, as a background job, and reports
  face ids (T-031, ADR-0021 decisions 5 and 8.3).** The tessellation findings stay immediate; the
  B-rep pass is one job per batch of parts, started off the calling thread, so a check over many
  parts returns at once and its result says `brep: {checked: false, reason: "running"}` until the
  job has reported (pass `wait: true` to have the command wait, as `geometry.import` does). Each
  part loads through the shape provider, so a **derived part of an operation is now checked** —
  the gap T-024 left, where such a part silently came back `checked: false` — and a fresh cache
  costs no source read. Progress arrives as `geometry.check_progress {node_id, index, count}`; on
  completion `meta.check.brep` holds the findings, the `basis` they belong to and `at`, the part's
  status follows as before, and `node.changed` is emitted; a part the job could not check emits
  `geometry.check_failed`. `brep` gains `free_edge_faces`, `small_face_ids` and `small_edge_faces`
  (the part's own face numbering, each capped at 1,000 ids with `truncated: true`), so a UI can
  highlight what a mesher would choke on. `force: true` checks again even when the stored findings
  are already for the part's current version, and `geometry.cancel_check(part?)` stops a running
  check. Free edges are counted across the part's faces — `ShapeAnalysis_FreeBounds` reports 0 for
  an unsewn compound of loose faces (T-027's measurement) — while the operations' own
  `check_shape` summary keeps the old count. Contract: `docs/engineering/geometry-schema.md`
  ("Checks").
- **`geometry.export_step` writes the node names, through the shape cache (T-030, ADR-0021
  decision 8.3).** Every exported part is loaded by the shape provider, so one call mixes parts and
  groups of any number of source files with the derived parts of operations — an operation's output
  part can be exported at last, and a part of one BREP file imported in another unit no longer
  blocks the export. The file names each solid as its **node** names it, so renaming a part changes
  the exported product name; each shape is scaled from the cache's millimetres to metres; and a
  fresh cache means the source file is not read at all (the worker reports the job's
  `source_reads`/`cache_hits`). A mesh-only part is still refused with `geometry.mesh_only`, and a
  part with neither a cached B-rep nor a readable source is `geometry.unreadable`, naming the part.
  The command's result is unchanged (`{file, solids, names, units: "m"}`). Contract:
  `docs/engineering/geometry-schema.md`.
- **Unit tests for the front end (T-040, ADR-0022 decision 14).** `npm test` in `web/` runs
  Vitest over the pure helpers: the pick rules, which moved out of `store.ts` into
  `web/src/state/pick.ts` (the `Picked` type, `NO_PICK`, the body and face toggles and the active
  member after a change) and keep their names, so every import of `../state/store` is unchanged; the
  tree row helpers of `nodeRules.ts`; the stage map of `stageMap.ts`. Node environment, no DOM and no
  React; the files end in `.test.ts` and `tsconfig.json` excludes them from the build. 52 tests.
- **Import geometry in the UI (T-018, ADR-0021 decision 1.1).** The project menu, the Geometry
  stage of the ribbon, the command palette and the action descriptions say "Import geometry…" and
  run `geometry.import` (`geometry.import_step` stays as its alias). The file picker's filter
  comes from `geometry.formats`, asked once without a project, and falls back to the six formats
  (STEP, IGES, BREP, STL, OBJ, 3MF) when the server has no such command. A file whose format
  declares no units (BREP, STL, OBJ, 3MF) asks for them in a dialog before the import, `mm` to
  start with. The action id `project.importStep` and its `Ctrl I` chord do not change, so saved
  workspaces keep working.
- **Named selections are tags (T-015, ADR-0021 decisions 2.1 and 2.2).** The node type `tag`, the
  path `/geometry/tags`, the 13 `tag.*` commands, the parameter `tag`, the event `tag.changed` and
  the error codes `tag.invalid` and `tag.stale` replace their `selection` spellings; the CLI verb
  is `stokes tag` (`stokes selection` stays as a hidden alias); labels read "Tag" and "tags", and
  `geometry.export_stl` takes `tags` (`selections` still accepted). Old journals and projects keep
  working through the alias tables in `stokes/api/legacy.py`. The `tag` node also gains `opacity`
  (default 1.0).
- `tag.members` is validated when it is written (T-013): `parameter.set` (and `node.add` with a
  `members` value) must now parse the value to the members structure the `tag.*` commands produce,
  a list of `{part, faces, basis}` objects or the JSON text of that list which the commands store,
  and refuse anything else with a `validation` error that names the problem (the model hook:
  `ParamSpec.validate`, run on write; undo restores a value as it was, unchecked). The old form,
  members as the JSON text of the list, still loads, and the tag commands keep their own check
  when they read, for values written before this one began.
- The server compresses responses with gzip, at level 6, for clients that are not on loopback only
  (a remote browser needs it; over loopback the compression costs more than it saves).
  `--compress-loopback` turns it on for loopback clients too, for a browser behind an SSH tunnel.
  Mesh payloads are float arrays and compress about nine to one.
- The IP gate (`tools/check_ip.py`) also fails on a user's home folder path, in its Windows, Linux
  and macOS spellings, whatever the private blocklist holds. A placeholder such as a folder name in
  angle brackets passes.

- The Explore design table takes its SI-to-display factors from `units.convert` instead of a
  hand-written table in `web/src/panels/explore/format.ts`. Each pair is fetched once and cached
  (`conversions.ts`), so the server's unit registry is the only source of truth; a pair that has not
  arrived yet keeps its SI value and label. The numbers on screen do not change, and
  `tests/test_units_explore_pairs.py` guards that the command returns the factor and offset the
  removed table carried for every pair.
- The Modes island's two pick buttons and its "Mesh" button become two pull-up menus of the same
  shape (T-053): an icon, the current value and a chevron, then a menu that opens upward with a
  check on the current item. The pick menu lists Body, Face, Edge and Point; Edge and Point are
  disabled with "Coming soon" and stay out of the `pickMode` type, so `selection.mode` still toggles
  body and face. The display menu's button shows the current display mode's name with its own icon
  and the menu heading reads "Display", so the control no longer reads as the CFD mesh. No action or
  workspace id changes; `web/DESIGN.md` §Islands describes both.

- The Properties panel now draws all of its fields through one presentational `SchemaFields`
  component (`web/src/panels/SchemaFields.tsx`, T-041), driven by the node's JSON schema: the
  schema's `x-kind`/`enum`/`type` picks the field kind, the panel's `include` filter decides which
  properties are drawn, a `ref` select lists the nodes of `x-ref-type` (with a null option), and a
  three-number `array3` row converts each entry to the display unit and writes back in SI; the
  stateful `QuantityField` is gone and the panel's look is pixel-identical. The panel's edit
  contract is unchanged by the move: a refusal is dismissed by typing or Esc, a refused Enter can
  be repeated, enum and bool show the choice before the server answers, a value typed with its own
  units is normalised on the way back, a failed expression keeps its warning hint, and a required
  `ref` no longer offers "None".

### Fixed

- **The icon documentation no longer claims a shortcut that nothing creates.** `stokes.app.icon` said
  the desktop shortcut "carries the same value in its `System.AppUserModel.ID` property, so a pinned
  shortcut and the running window share a button". Nothing in the repository writes that property —
  no `WScript.Shell`, no `IShellLink`, no `.lnk` — and the shortcut on the development machine does
  not have it, so the claim was false as shipped and hid a real symptom: pin that shortcut and the
  pinned button does not join the running window and can show the interpreter's icon. The docstring
  now says what the code does (the process names itself `Stokes.Desktop` at start-up, which is what
  gives the running window its own button) and what a pinned one needs; `docs/HANDOVER.md` §7
  documents the icon, the identity, how to make a shortcut by hand and the Explorer icon-cache
  gotcha. No behaviour changed.

- **The app no longer promises features "in milestone M1".** Section view and Measure are offered in
  the Modes island, the ribbon and the command palette, and pressing either answered "… arrives with
  milestone M1" — a placeholder written before M1 was planned in detail. M1 has shipped without
  either of them and neither is scheduled, so the message sent you looking for a milestone already
  behind you; they now say they are not built yet. The Run button said the engine arrives in M2, but
  the engine (`foamRun`) is **M3** — `stokes/engines`, the roadmap and the residuals panel all said
  M3 already, so this made the app contradict itself. `stokes workspace`'s action list, its contract
  in `docs/engineering/workspace-schema.md`, and the `engine.info` docstrings were corrected with it.

- **The import line and the scale no longer print through each other (T-097).** Both live at the
  middle of the top edge, and an import printed "Reading thing.stl…" with the scale's own figure
  struck through it. The import line belongs against its 2 px track on the top edge, so the scale is
  the one that steps aside, and only while a file is being read. `tools/test_interactions.py` now
  measures both boxes with the import state forced on — the first attempt at that check passed
  vacuously, because the label is empty when nothing is importing and an empty box cannot overlap
  anything.

- **A journal replay re-runs an operation as often as the session that wrote the journal did
  (T-092).** A journal carries no timing, so a replay cannot tell from the file that a burst of
  changes arrived inside one debounce window. It settled after every record and forced the pending
  re-run each time, so a three-change burst ran the operation **four** times against **two** in the
  original — the same answer from twice the worker time. Worse, it could not simply wait for the
  debounce: walking the burst takes longer than the 300 ms window, so each timer fired during the
  *next* record and forced its own run anyway. `replay` now holds the debounce across a stretch of
  change-only records (`parameter.set` / `parameter.remove`) and settles once at the end, with the
  first change settled on its own as the recording session did. Anything that is not a change-only
  edit ends the stretch, so every record still sees the work it depends on. The M1 gate's
  byte-identical replay across all eleven files still passes.
- **A project save no longer fails when the file is briefly locked (T-094).** On Windows the final
  atomic rename fails with a `PermissionError` while anything holds `project.json` open, and an
  antivirus scanner, the search indexer or a backup agent does exactly that for a few milliseconds
  after a write. That surfaced as `model.persistence: cannot write …\project.json`, failing a save
  the user asked for. The rename now retries with a short backoff, preserving the point of the
  temp-file-and-rename pattern — a reader never sees a half-written project — instead of giving up
  on the first momentary lock. A lock that never clears is still reported, with the file named.
- **Slivers no longer deletes a body that has no slivers (T-093).** On a part whose faces all read as
  spots at the fixer's precision, `ShapeFix_FixSmallFace`, `ShapeFix_Wireframe.FixSmallEdges` and
  `ShapeFix_Shape` each reported success while the last one answered with a shape holding **0 faces,
  no shell and no volume** — and the operation returned that as a finished "slivered" part, after
  which the geometry worker died on the empty shape and the user saw a failed operation. A repair
  may drop slivers; it may not drop the model, so a guard over the whole fixer chain now keeps the
  input body and warns when the fixers would have removed every face. Found by the new
  `tests/test_operations_smoke.py`; two regression tests in `tests/test_operations_repair2.py`.
- **The M1 review fixes.** Remove faces now sends the faces the pick held, so the entry can run at
  all; a second Run can no longer create a duplicate operation in the window between the create
  reply and the run's first event, and a create reply that lands after the card was reopened
  attaches to the card that asked; the box works again as ADR-0021 3.4 defines it (a plain drag on
  empty space, Shift adds, Ctrl removes, Alt or the toggle draws the lasso) with a press on a patch
  still orbiting; the STEP writer puts the process-wide working unit back after it writes; and the
  JavaScript licence audit fails on an installed tree with no packages instead of passing vacuously.
- **An operation that reads a tag its own run also writes no longer ends `stale`.**
  `cap` fills a pipe tee's openings under a tag its run created, and `extract_fluid` reads that
  tag as its `caps`; the extraction carried the tag too and added a member for its own output,
  which the next signature hash saw as a change: the run ended `stale`, and the next edit started
  a needless re-run that failed with "not an input of this run". A referenced tag's members are now
  kept to the ones on the parts the run reads — the same set the worker accepts — so neither the
  run's own output nor a downstream operation's (the `orient` after the extraction writes the tag
  too) reaches the plan. A member the user adds on a part the run reads still counts, so editing
  that tag still makes the operation `stale`.
- **Assign applies to exactly what is picked, as one undo step (T-061).** Where nothing is picked,
  the assign is one `boundary.assign` onto the boundary the selected patch already has (the demo's
  own boundaries keep their names and are re-kinded in place), or onto the part whole when the patch
  is that part's only patch. It no longer makes `/regions/fluid/boundaries/<patch>` through separate
  `node.add` commands first, which left a plain-group boundary behind after one Undo. A pick that
  the server cannot take — the sample part has no node in the project — is refused with a reason and
  never widened to the whole patch under it. `web/src/panels/assignTarget.ts` holds the decision
  and `web/src/panels/assignTarget.test.ts` covers it.
- Undoing the first write of a parameter a node never held now takes the parameter off again. A project
  saved before `part.opacity` has parts without it, and the first `geometry.style` opacity on such a
  part could not be undone: undo wrote `None`, the model refused it, and the step was lost from the
  undo stack.
- **STEP imports no longer die with an access violation (`0xC0000005`).** The reader read a
  label's name with `FindAttribute` into an out-parameter made in Python, and on OCP 8.0.1 that
  call faults the process, with no Python exception, on any label that has no name attribute. The
  solid sub-labels of a part whose solids are coloured one by one have none, and the reader asked
  each of them for a name. `ReadFile` and `Transfer` were always fine. The reader now asks
  `IsAttribute` first, for names and for colours, and takes a part's sub-shape labels with one
  `GetSubShapes` call per part, matched to the solids with `IsSame`. **Per-solid colours are
  kept.** A solid's own name is used where its sub-label has one; OCCT's writer leaves solid names
  empty, so files it writes bring back the part's name.
- Importing the 4,500-solid benchmark fell from 298 s to about 190 s (168 s on an earlier, quieter
  run; the figure moves by tens of seconds with the load on the laptop). The `FindSubShape` lookup
  that ran once per solid was the largest cost of the walk, and one `GetSubShapes` call per part,
  about 25 µs each, replaces it.

## Unreleased (continued)

- First shell: `python -m stokes.app` opens the Qt Widgets window with the stage bar, rail, tree, schema-driven inspector, VTK viewport with a sample part, assistant and context placeholders, output dock; dark and light themes follow the OS.

- Front end rebuilt as a web application (ADR-0013): React + three.js in a frameless QtWebEngine window, served by the new FastAPI WebSocket JSON-RPC server (`stokes.server`); the Qt Widgets shell was removed. Added a JavaScript licence audit gate.

- Front-end polish pass. Viewport: PBR materials under an environment light, contact shadow, fading grid, hover lift and accent outline, view cube, animated fit, view modes, hidden and isolated patches. Tree: keyboard navigation, inline rename, row menus, Undo toast on delete. Properties: schema-driven fields with unit chips, expression errors and a patch card (Assign boundary, Hide, Isolate). Output drawer: console command line with history and autocomplete, event log, residuals (uPlot). Command palette with recents and parameter forms. Chrome: project menu, context toolbar, resizable and collapsible panels, tooltips, first-run hint, full shortcut set.
- Cupertino style family, in light and dark: macOS-inspired vibrancy, hairlines, system blue and traffic-light window controls. Themes are now Style (Stokes, Cupertino) × Appearance (dark, light); `auto` and `cupertino-auto` follow the OS. The Stokes dark and light looks are unchanged. The top bar compacts by measurement, so the project name stays legible whatever the engine or licence label.
- API: `license.info` and `engine.info`; `history.state` adds stable `undo_kind` and `redo_kind`; node summaries carry `structural`; the skeleton groups can no longer be removed or renamed.
- Desktop host: native folder dialogs for Open and Save as; window-state changes reach the page; the window fits the primary screen's work area and reports its size on `--screenshot`.
- Review fixes (panels and viewport): the stage chips and the tree navigate each other (a chip opens its groups and folds the rest; selecting a tree node moves the chip; viewport picks never change the stage); the context toolbar sits above the selected patch; Residuals with decade-only grid, line-swatch legend and theme-token series colours; status badges as dot plus text; no native selects or checkboxes; tabular numerals; AA contrast for secondary text; the palette highlights whole runs; the gizmo matches the floor axes.
- STEP import (ADR-0014): `geometry.import_step` / `list` / `info` / `cancel_import` / `prepare` and `stokes geometry import` create `part` nodes meshed in a worker process (one part per solid; a multi-solid STEP imports as a group of parts), with progress events, mesh transport v2 with face ids at `/api/geometry/{node_id}`, live event delivery from background threads, and a per-project mesh cache.
- Workspace v1 (ADR-0015): `stokes.workspace` with the versioned workspace document, five presets (Stokes, Classic, Ribbon, Workflow, Minimal), user and project merge, and the `workspace.*` commands with `workspace.changed` events; contracts in `docs/engineering/workspace-schema.md` and `theme-tokens.md`.
- Import STEP… in the project menu (`Ctrl I`) with the host's native file picker; the import worker is pre-started when the dialog opens.
- Imported parts in the viewport (ADR-0014): every `part` node is drawn from its mesh by node id (mesh transport versions 1 and 2), one mesh per solid, a coarse mesh replaced in place by the fine one; the sample part shows only while the project has no part; a part's `color` and `visible` apply without a new mesh; hovering tints the face under the pointer from the face-id buffer and a click selects the solid; a progress line on the viewport's top edge ("Reading part.step…", "Meshing 3 of 12 (coarse)…") and toasts report imports, cancels and failures; the Properties panel shows a part's source, solid, units, triangles, faces and size as text.
- `stokes.explore`: samplers, surrogates, sensitivity, Pareto utilities and optimisation drivers for design exploration (ADR-0016); scipy joins the core dependencies and the optional `explore` extra adds pymoo, SALib and scikit-learn.
- Explore v1 (ADR-0016): the `/studies` group with `study`, `variable`, `response`, `objective` and `constraint` node types; the `function` engine that evaluates design points from parameter expressions without a solver; 13 `study.*` commands and `stokes study` subcommands; design tables as `points.jsonl` and CSV beside the project; the demo project gains a velocity sweep.
- Explore, the sixth stage, in the front end: its chip opens `/studies` and folds the other groups, selecting a study node moves the chip to it, and the tree gives studies, variables, responses, objectives and constraints their own icons. The Explore panels come later.
- Workspace v1 in the front end (ADR-0015): panels, islands and top-bar items placed from the workspace document (slot layout with tabs, splits and float cards), Customise mode (`Ctrl Shift C`), layout presets with Undo, export and import, a theme editor with live preview and custom token sets, the stage ribbon, and shortcuts from the workspace keymap.
- Explore stage UI (ADR-0016): the Design table (selection, generate, update, add point, clear, CSV export, virtualised rows), the Plots panel (response against input, scatter matrix, parallel coordinates, Pareto), the Study card in Properties with forms for variables, responses, objectives and constraints, and five `study.*` actions in the palette; the Design table and Plots are built-in workspace panels placed by every preset.
- Front-end integration: the command palette lists window actions with their chords, Customise, Reset and "Move <panel> to <place>"; project chords are rebindable through the workspace keymap and tree actions follow rebinding; the view cube follows its island corner or hides, `viewport.grid` toggles the ground, custom tokens reach the 3D view; `linkStage` and `defaultExpanded` are honoured; workspace Export… and Import… use the native dialogs through a picked-path bridge; `python -m stokes.app` starts in the workspace's theme.
- Materials slice 1 (ADR-0018): `material` and `property` node types with a Fluent-parity method catalogue (45 methods; non-Newtonian laws in slice 5), evaluation over T and p grids, consistency checks, nine `material.*` commands with undo and journals, and a referenced built-in catalogue of 20 records: 11 fluids fitted from CoolProp 8.0.0 within 1 % and 9 solids with two public citations each; seven new dimensions.
- Desktop launch: an application icon (`stokes/app/assets/stokes.ico`); started without a console (a shortcut to `pythonw -m stokes.app`), the app writes its output and logs to `~/.stokes/logs/app.log` instead of failing.
- One icon everywhere: the Qt application and window take their icon from `stokes/app/assets/stokes.ico`, and on Windows the process names itself `Stokes.Desktop` (`stokes.app.icon`), so the taskbar and Alt-Tab show the Stokes mark, not the icon of the interpreter or of another Python program, and a shortcut with the same `System.AppUserModel.ID` shares the button; a missing icon file or a refused call is a logged warning, never a failed start. The page has a favicon (`web/public/favicon.png`, a copy of `stokes/app/assets/stokes.png`), and the top-bar logo mark is that artwork in every theme, in place of the drawn waves on a themed tile (an owner-approved exception to the frozen look, `web/DESIGN.md`).
- Session extension hooks (`project_opened`, `project_saved`, `project_closed`): Save as carries study design tables, workspace overrides and mesh caches without a prior command; `geometry.import_progress` sends a "read" event with count 0 the moment an import starts; a dimension change resets a node's dynamic quantities for study variables and constraints as for parameters, and undo restores them; the first WebSocket client is registered before the geometry store activates, so no start-up event is lost.
- `THIRD-PARTY-NOTICES.md`: every bundled Python and JavaScript component with its licence, the LGPL notices for Qt, OCCT and moocore, the dual-licence elections and the attributions FreeType and the IJG require.
- Decisions: ADR-0014 STEP import, ADR-0015 customisable workspace, ADR-0016 design exploration (parametric studies, DOE, surrogates, sensitivity, optimisation with Pareto fronts), ADR-0017 shape optimisation (CAD-parametric and adjoint, premium), ADR-0018 materials (method catalogue, library fits from CoolProp, Cantera and REFPROP, compile mapping; contract in `docs/engineering/materials-schema.md`, sources in `docs/research/materials/`).
