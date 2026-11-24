# ADR-0025 · Native Windows engine hosts and MPI execution

**Status:** accepted · 2026-10-21 · Owner requested native FoamWin integration and multiprocess meshing/solving. Amends ADR-0024's Linux-only local host and serial execution scope; relies on ADR-0001, ADR-0008 and ADR-0013.

## Context

FoamWin provides native OpenFOAM Foundation 14 utilities and an MS-MPI backend.
The local Stokes runner previously refused Windows, and solver configuration
rejected every core count except one. The owner requested real parallel jobs
and editable core counts in both mesh and solver settings.

## Decision

1. Keep FoamWin external: no GPL source, library import or bundled engine in Stokes.
2. A Windows local host uses an explicitly configured FoamWin environment handshake,
   `STOKES_FOAMWIN_ENV`, pointing to `of14-env.ps1`. Capture a filtered environment
   once and execute trusted utility paths using argument lists.
3. `cores` accepts integers from 1 to 256. One retains serial execution.
   Parallel snappyHexMesh and foamRun cases write a decomposition dictionary,
   decompose, execute the requested ranks, and reconstruct outputs for existing readers.
4. Job steps carry a validated rank count, rather than arbitrary launcher arguments.
   The agent owns the MPI launcher and `-parallel` flag. Ordinary utility argument
   allowlists continue to apply.
5. blockMesh stays serial because the utility has no parallel mesh generation mode.
   The interface states this when blockMesh is selected.
6. Windows processes belong to kill-on-close Job Objects so cancellation, timeout
   and desktop shutdown include MPI children. Linux keeps its process-group path.
7. Solver and mesh core counts are separate saved settings, each available under
   Execution in both dedicated panels and Properties. Configuration remains undoable.
8. snappyHexMesh runs from the job's parent with a relative `-case <job id>` on
   native Windows. Older FoamWin builds fail to open a `featureEdgeMesh` through
   an absolute drive-letter case path; the identical case reads its feature
   meshes and meshes cleanly when the case is named relatively. This is a
   workaround for an engine defect, not a Stokes behaviour: the recorded step
   arguments do not change, and no other utility is affected. The repaired engine
   recognises drive-absolute `fileName` paths, so an absolute `-case` with
   relative feature names also meshes; the workaround is retained for runtimes
   without that repair. Feature-file entries themselves stay relative to
   `constant/geometry`, as `refinementFeatures` prefixes that directory.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Expose a core field without changing jobs | It would save a number without running parallel work. |
| Accept arbitrary mpiexec command strings | It bypasses the agent's argument validation and ownership. |
| Require WSL for native FoamWin | It does not satisfy the owner's Windows runtime request. |

## Consequences

Native jobs require a valid Foundation 14 FoamWin installation. Parallel jobs
also require the MPI-enabled build and Microsoft MPI. Missing prerequisites
produce actionable errors rather than a serial fallback. Geometric simple
decomposition avoids adding optional graph-partitioning dependencies; workload
balance may vary with geometry. Native execution, cancellation, reconstructed
results and GUI edit/undo behavior require separate integration checks.
Native snappy meshing keeps the relative-`-case` workaround (decision 8) for
runtimes without the drive-absolute `fileName` repair. A repaired engine build
was verified with an absolute `-case` and relative feature names; absolute
feature-file entries remain unsupported because `refinementFeatures` always
prefixes `constant/geometry`. Remove the workaround only after the minimum
supported engine recognises drive-absolute paths and the feature-path rule is
documented for users.
