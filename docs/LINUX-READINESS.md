# Linux readiness: what is pending

Written 2026-11-09. Windows (native, FoamWin) is the platform that has been driven end to end in
2026-10/11. Linux is a v1 target (decision B1) and the code is written to be portable, but **nothing
below has been run on a Linux machine from this work**; the only Linux evidence is CI, which runs the
Python and web test suites on `ubuntu` as well as Windows (`.github/workflows/ci.yml`). Read this as
a checklist, not a status report.

## What is probably fine (portable code, covered by CI tests only)

- The headless core (`stokes.units`, `model`, `api`, `cli`, `server`), the web front end, the
  agent protocol, the mesh/compile/engines layers, project persistence and crash recovery.
- The desktop host sets the Wayland `app_id` and prefers the Ubuntu theme family when no theme was
  ever chosen (`stokes/app/__main__.py`, `stokes/app/icon.py`).

## Pending, in order of what blocks a Linux release

1. **An end-to-end run on a real Linux host.** Import the elbow STEP, mesh (cfMesh polyhedral and
   tet), run foamRun serially and with `cores > 1`, reconstruct, view results. None of the Windows
   findings (SST/tet divergence, symmetry-plane flatten, residual target 1e-3) has been reproduced on
   Linux OpenFOAM 14. Expect different numbers; re-check the tet outlet non-orthogonality finding.
2. **Engine attachment.** `docs/TASKLIST.md` still lists "OpenFOAM Foundation 14 on WSL2 or Linux" as
   open. Linux needs the equivalent of `STOKES_FOAMWIN_ENV`: probing `$FOAM_INST_DIR`, `which
   foamRun`, module files (`docs/05-openfoam-integration.md`). Not built as a documented, tested path.
3. **Linux runner and agent.** `LocalLinuxRunner` (jobs under `~/.stokes/jobs/<id>`) is designed in
   `docs/05` and `ADR-0005`; the Windows runner got native job supervision (Job Objects), the Linux
   side needs the equivalent process-group supervision and kill-on-close behaviour, plus MPI
   (OpenMPI/MPICH, not MS-MPI) decomposition for `cores > 1`.
4. **Flatten step for the polyhedral and tet meshers** (`agent/stokes_agent/flatten.py`) was written
   and verified against FoamWin only. Check it on Linux (paths, `surfaceFeatureExtract` and
   `cartesianMesh`/`pMesh` invocation, line endings).
5. **Geometry import speed work** (parallel helpers, async cache writes, pack file). Tuned on NTFS,
   where per-part files are pathological. Re-measure on ext4/xfs before keeping the `CACHE_WRITERS=4`
   and pack choices; the 4,500-part budget in `tests/perf/` should be run there.
6. **Desktop shell and rendering.** Frameless QtWebEngine window: focus, HiDPI/fractional scaling,
   Wayland vs X11, GPU fallback (the 30 fps reduced-quality mode). ADR-0002 asked for this to be
   prototyped on both platforms in week one of M0; the Linux half is not recorded as done.
7. **Packaging (M8).** AppImage or tarball, a `Stokes.desktop` file (the basename the app already
   claims), GPG signing, bundled web `dist`, optional bundled engine. Nothing is built.
8. **CI for the engine tests.** The `engine` marker is skipped by default; a Linux runner with
   OpenFOAM 14 would make those tests real. Without it, Linux engine behaviour is untested.
9. **Docs.** An install and quick-start page for Linux, and a Linux column in the tutorials'
   "attach an engine" step.

## Not Linux-specific but still open (from `docs/PRODUCTION-READINESS.md`)

Installer and signing certificate, trademark search, release version, FoamWin release verification,
remaining tet-mesh quality items.

## Suggested order

Item 1 first (it finds the real problems), then 2 and 3 together, then 4 and 5, then 6 and 7. Item 8
turns the result into a regression gate.
