# OpenFOAM-14 tutorial sweep through Stokes (2026-11-01, laptop, native FoamWin)

Owner's ask: *do the checks with all the tutorials present in OF14; check if GUI, engine and
everything works as intended; include the most popular tutorials.*

## Set-up

- Tutorials: the OpenFOAM-14 source tree's `tutorials/` (`Y:\src\OpenFOAM-14\tutorials`, 260 cases
  with a `system/controlDict`), copied to `<FoamWin install>\tutorials-of14`. The
  FoamWin install itself ships only two (`incompressibleFluid/cavity`, the legacy icoFoam cavity).
- Engine: FoamWin OpenFOAM-14 (native Windows, MinGW), `<FoamWin install>`.
- Driver: `sweep_tutorials.py` + `run_sweep.ps1` (session scratchpad; to be moved to `tools/`),
  own `STOKES_HOME` so the GUI's recent list stays clean. Per case: `tutorial.open` (new project +
  `case.import`), `case.get`, `run.start case=` for the catalogue's `ready` cases, polled with
  `run.status` up to 240 s then `run.cancel`, then `run.fields` and the log tail. One JSON line per
  case in `FoamWin\sweep-out\sweep.jsonl`, `summary.md` at the end, projects under
  `FoamWin\sweep-out\projects\`.
- Order: the popular list first (cavity, pitzDaily, pitzDailyLTS, airFoil2D, TJunction, cylinder,
  channel395, planarPoiseuille, mixerVessel2D, pipeCyclic, hotRoom, buoyantCavity, shockTube,
  squareBend, aerofoilNACA0012, damBreakLaminar, capillaryRise, waterChannel, heatedDuct,
  counterFlowFlame2D, legacy cavity/elbow/flange), then every other case.

## Catalogue (260 cases)

`tutorial.catalogue` on this install: **ready 117**, missing_module 54, missing_library 21,
missing_utility 18, coded 26, needs_recipe 23, unverified 1. (The FoamWin build has no
multiphaseEuler, solidDisplacement or compressibleMultiphase solver modules; `coded` is the lint's
refusal of `#codeStream`/`coded*`; `needs_recipe` is an Allrun line the parser does not take.)

## Findings so far (updated as the sweep runs)

1. **Long project paths break case import.** With the project under the session scratchpad (a
   ~150-character folder) `case.import` failed with `CaseStoreError: the case could not be copied
   into the project: [Errno 2] No such file or directory` — the copy plus `cases/<id>/...` exceeds
   Windows' 260-character limit. A user with a deep Documents path will hit this. Fix: `\\?\`
   extended paths in `stokes/cases/store.py` (or a documented check with a clear message naming the
   limit) — not done yet.
2. **A case that ran to its endTime reports `done_unconverged`.** `incompressibleFluid/cavity`
   (foamRun, 2000 iterations, endTime reached, fields written at t = 10) comes back
   `done_unconverged`, and the status bar says "Run · it 2000 · not converged", because
   `RunStore.finish` takes `converged` from the residual report, which for a transient or a case
   with no `residualControl` never says yes. For an imported case "reached endTime" is the success
   criterion; the state and the words should say *finished* (and *converged* only when the case
   has a convergence control). Not done yet (`stokes/run/store.py finish`, the foamRun parser).
3. **The guided layout does not know a case.** Opening a tutorial project in Guided mode shows the
   Geometry card's "Drop a CAD file here", the strip's steps unlabelled, Mesh "0 cells · quality
   not met"; the case card lives in the Full-mode Details pane only. The guided flow needs a
   **Case** path: Case (source, solver, recipe, settings) › Run › Results, with the case's mesh in
   the viewport after its mesh step. Design in `RESULTS-OVERHAUL`'s wake; not done yet.
4. **VTK reader noise on `0/` fields.** The agent's field extraction logs
   `vtkOpenFOAMReader … Error reading line 41 of …/0/U: Unexpected input while parsing dimensions
   array` for every `0/` field of an OF-14 tutorial (the `#includeEtc "caseDicts/setConstraintTypes"`
   line VTK cannot expand); the written times read fine. Harmless for the result, loud in the log;
   the extractor should skip time 0 or silence the reader for it.
5. Popular ids that are not in the OF-14 tree under those names (the sweep lists them): `elbow`,
   `backwardFacingStep2D`, `motorBike`, `nozzleFlow2D`, `LES/pitzDaily`, `circuitBoardCooling` (it
   is `multiRegion/CHT/...`), `damBreak/damBreak`, `legacy/basic/potentialFoam/cylinder` — see the
   id map below once resolved.

6. **"could not write the run record"** is logged at the start of every sweep run (and was seen on
   the CAD pipe too). The message carried no error text because the run store logged a plain
   `extra={...}` dict the JSON formatter drops; fixed (`fields(...)`, commit d22f831), so the next
   run shows the OSError. A direct repro (open the swept pitzDaily project, `run.start case=`,
   cancel) did not log it, so it is tied to the open-then-run-at-once path of `tutorial.open`.

7. **FoamWin: `fvConstraints` and `fvModels` tables are empty.** `channel395` and `squareBend`
   fail at once with `Unknown constraint type meanVelocityForce` / `limitPressure` — `Table of
   fvConstraints is empty`: on the MinGW build the solver module does not load
   `libfvConstraints.dll`, so nothing registers. **Verified workaround**: `libs
   ("libfvConstraints.dll" "libfvModels.dll");` in `system/controlDict` makes squareBend run
   (Time = 51 s after 90 s). 58 tutorials have `system/fvConstraints`, 91 have `constant/fvModels`;
   FoamWin ships `libfvConstraints.dll` but **no `libfvModels.dll`** (only the `.a`/`.def`), so the
   fvModels cases need the FoamWin build fixed, the fvConstraints ones need the case planner to add
   the `libs` line on a FoamWin host (`stokes/run/external.py`; the lint already allows `libs` the
   install has). Not done yet.
   Interim tally at 73/260 (old code for the runs): of 46 `ready` cases, 15 ran to the end, 10 hit
   the 240 s cap, 9 were refused at import (finding 9), 12 failed — every `foamRun exited 1` read
   so far is one of three FoamWin library-load faults: `Table of fvConstraints is empty`
   (channel395, squareBend, squareBendLiq(Steady), fluid/cavity, fluid/pitzDaily,
   annularThermalMixer, ductSecondaryFlow: fixed by the `libs` line), `Table of fvModels is empty`
   (climbingRod: needs `libfvModels.dll`, which the build lacks), `Unknown surfaceTensionModel type
   liquidProperties` (compressibleVoF damBreak, depthCharge2D: another library the VoF module does
   not load on Windows — see the DLL list below).
   FoamWin's `platforms/*/lib` has 111 DLLs; the `.a` archives with **no DLL beside them** are
   `libfvModels`, `libinterRegionFvModels` and `libOSspecific` — so every tutorial with a
   `constant/fvModels` (91 of 260) fails on this build until `libfvModels.dll` is built. For the
   owner of the FoamWin build.
8. `run.log` of a failed case run is empty (`log.tail step="solve"` finds nothing for a failed
   step), so the sweep's `log_tail` and the GUI's Run card show nothing where the FATAL ERROR is;
   the job folder's `log.s2_foamRun` has it. The log tail should fall back to the failed step.

9. **Catalogue "ready", import "refused".** Seven cases so far (`fluid/angledDuct*`,
   `compressibleVoF/angledDuct|ballValve|sloshingTank2D`, `incompressibleFluid/mixerVessel2D`)
   are `ready` in `tutorial.catalogue` and refused by `case.import`: their Allrun runs `blockMesh
   -dict system/<name>` and that dictionary holds `#codeStream`/`codeInclude`/`$file!entry`, which
   the catalogue's coded check does not look at (it lints the standard files only). The catalogue
   should lint every file the recipe names, so the start screen does not offer a case that cannot be
   opened. Not done yet (`stokes/cases/catalogue.py`).

Fixed during the sweep (engine side, commit 9510cec and after): finding 2 — an imported case that
reached its `End` is `done` (the strip says "Finished in N iterations"; "Converged" only when the
solver said so); finding 7 — `add_windows_libs` names `libfvConstraints.dll` (and `libfvModels.dll`
where the install has it) in the staged controlDict on a Windows host, verified: `squareBend`
re-run goes from "exited 1 in 10 s" to running until the 180 s cap; finding 8 — `run.log` falls
back to the step the run stopped in.

Done during the sweep (GUI side, commits 4284a6f, d22f831): the guided strip and cards know a
`foam_case` project — Geometry says "cavity · incompressibleFluid", Fluid/Boundaries "From the
case", Mesh "Made by the run" with only the cell-edges switch, no CAD drop hint over the results.
Verified on the swept cavity project in the browser. Still to do for cases: a Case card (recipe
steps, settings editor) in guided mode, the `done_unconverged` wording (finding 2), results of a
case opening on the Results step with the field shown.

## Results of sweep 1 (11:45-15:11, old engine code for the runs; `FoamWin\sweep-out\summary.md`)

260 cases swept. Catalogue: ready 117, missing_module 54, coded 26, needs_recipe 23,
missing_library 21, missing_utility 18, unverified 1. Of the **117 ready** cases:

| outcome | cases | meaning |
|---|---|---|
| ran to its end | 34 | recorded `done_unconverged` (finding 2; `done` with the fix), fields read for 28 |
| hit the 240 s cap | 34 | still running when cancelled (motorBike, channel-type LES, capillaryRise…) |
| failed | 23 | 11 are FoamWin's empty fvConstraints/fvModels tables (finding 7), 2 `surfaceTensionModel liquidProperties` (same family), 1 `createBaffles` step, 1 `financialFoam`, 2 `icoFoam cavityClipped/cavityGrade` (mapFields steps), 2 `XiFluid` with `dynamicCode` (needs wmake — FoamWin has no compiler, and the lint lets `dynamicCode` through: **finding 10**), the rest to read in sweep 2 |
| refused at import | 23 | catalogue "ready", importer refuses (finding 9: `#codeStream`/`$file!entry` in `-dict` files, `mixerVessel*`, `angledDuct*`, `sloshingTank*`, `rotor2D*`, `windAroundBuildings`) |
| driver error / stuck | 3 | one `queued` never started, two Python errors in the driver |

Every non-ready case opened into a project (`tutorial.open`) with the catalogue's reason; none
crashed the server. The whole pass took 3 h 26 min.

**Sweep 2** (16:45, same driver, the fixed engine: `libs` for fvConstraints, step-keyed reports,
`run.log` of the failed step) is running into `FoamWin\sweep-out3\`; its table replaces the one
above when it ends.

## Results of sweep 2 (16:45-20:10, fixed engine; `FoamWin\sweep-out3\summary.md`)

Same 260 cases, same catalogue counts. Of the **117 ready** cases:

| outcome | cases | sweep 1 | meaning |
|---|---|---|---|
| ran to its end | 34 | 34 | fields read for 28; **still recorded `done_unconverged`** (finding 11 below) |
| hit the 240 s cap | 43 | 34 | the 11 fvConstraints/fvModels failures of sweep 1 now run (squareBend, channel395, hotRoom*, …) and most reach the cap |
| failed | 14 | 23 | 1 `createBaffles` (TJunctionFan), 1 `financialFoam`, 2 `icoFoam cavityClipped/cavityGrade` (mapFields), 2 `XiFluid` `dynamicCode` (finding 10), 3 `compressibleVoF` + 2 `incompressibleVoF` (climbingRod, cavitatingBullet: `surfaceTensionModel liquidProperties` family, to confirm from `run.log`), porousBlockage, parcelInBox, simplifiedSiwek (to read) |
| refused at import | 23 | 23 | finding 9, unchanged |
| import error | 1 | — | `fluid/forwardStep`: `[WinError 5] Access is denied` copying the case (a file held open; retry) |
| no steps to run | 2 | 3 | `porousSimpleFoam`/`rhoPorousSimpleFoam` `angledDuctExplicit`: `ExternalRunInvalid: has no steps to run` (the Allrun is a `runApplication` on a solver the catalogue maps to nothing) |

**Finding 11 — `ended` never reaches the record (the finding-2 fix was incomplete).** The parser
does report `ended: true` on the solver's `End` line and `solver_report` now looks at the last
step's report, but it builds the step names with `str(s)` over `status["steps"]`, and the agent's
status keeps the steps as **records** (`{name, state, rc, …}`, `jobs.py` ~713), so no name ever
matches a key of `reports` and the empty report wins: every finished case is still
`done_unconverged`, with `iterations` from the history fallback (cavity: 2000). Fix: take
`s["name"]` when a step is a record. (Commit after this note.)
