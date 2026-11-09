# Linux readiness: what is done, what is not

Written 2026-11-09. Windows (native, FoamWin) was the platform driven end to end in 2026-10/11;
Linux is a v1 target (decision B1). **This file now records a real Linux run**, not a plan: every
number below was measured on the host described here, and anything not run says so.

## The host and the toolchain

| Item | Value |
|---|---|
| OS / kernel | Linux x86_64, kernel 7.0.0-38-generic, 4 vCPU, 7.1 GiB RAM, `/home` on ext4 |
| Display | Wayland session with XWayland (`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`); no DRM render node for Chromium |
| OpenFOAM | **Foundation 14**, source build 20261008, user-space at `$HOME/opt/openfoam14` |
| MPI | **OpenMPI 5.0.10** (`$HOME/opt/of-deps/usr/bin/mpiexec`) |
| cfMesh | **not 1.2.0**: upstream produces inverted meshes against modern OpenFOAM. Built the Foundation-13 port (`Ultravis66/cfMesh-OpenFOAM13` at `2e36fe7359dd8bbe76ca126c008f884a40ff38fc`) against 14 with two fixes, into `$FOAM_USER_APPBIN`/`$FOAM_USER_LIBBIN` |
| Python | 3.13.18 (venv created with `uv`; the system Python is 3.14) |
| Node | 24.11.0 |

The cfMesh fixes are in `.github/scripts/patch-cfmesh.py`: `<vector>` in
`boundaryLayerCells.C`, and element-wise `reduce` where 1.2.0/13 used
`maxOp<labelList>()`, which OpenFOAM 14 has no `max(List, List)` for.

## Gates on this checkout

| Gate | Result |
|---|---|
| `ruff check .` | **pass**, 721 files |
| `ruff format --check .` | **pass**, 721 files |
| `mypy` | **pass**, 344 files. Two Linux-only fixes were needed (see item 2) |
| `pytest -q` | **pass**, 5,009 passed, 93 skipped, 2 xfailed in 647.8 s |
| `tools/check_forbidden_imports.py` | pass |
| `tools/check_ip.py` | pass |
| `tools/check_no_foam_content.py` | pass |
| `tools/audit_licenses.py` | pass, 117 packages |
| `tools/audit_js_licenses.py` | pass, 76 packages |
| `pip-audit` | no known vulnerabilities (the editable project itself is not on PyPI) |
| web `npm run typecheck` | pass |
| web `npx vitest run` | pass, 179 files / 2,334 tests |
| web `npm run build` | pass |

The first Linux `mypy` run failed on six Windows-only attribute accesses
(`ctypes.windll`, `ctypes.WinDLL`, `ctypes.get_last_error`,
`subprocess.CREATE_NO_WINDOW`). They are now looked up through `vars(ctypes)`/`getattr`, so one
source type-checks on both hosts.

## 1. End-to-end run — done

A **synthetic swept elbow STEP** (three faces: two circular end caps and one toroidal wall), imported
through `geometry.import`; no customer geometry.

| Step | Measurement |
|---|---|
| STEP import | 1.3 s (1 solid, 3 faces, 450 triangles) |
| cfMesh polyhedral (`pMesh`, 5 mm) | **54,183 cells**, 0 failed checks, max non-ortho 63.11°, 13.5 s |
| cfMesh tetrahedral (`tetMesh`, 5 mm) | **196,968 cells**, 0 failed checks, max non-ortho 62.99°, 10.1 s |
| `foamRun` serial, k-omega SST, 500 iterations | 128.7 s; residuals Ux 1.0→1.1e-4, Uy 1.0→7.3e-5, Uz 1.0→4.5e-4, p 1.0→2.4e-5, k 1.0→1.1e-2 |
| `foamRun` on **4 ranks** | decomposePar → `mpiexec --oversubscribe -n 4 foamRun -parallel` → reconstructPar; 103.3 s; residuals match serial to three figures (Ux 1.09e-4, p 2.38e-5, k 1.10e-2) |
| Reconstruction | `0/`, `400/`, `500/` written in the parent case; `run.fields` returns `surfaces: ok`, `slice: true`, U max 1.476 m/s, p range −0.111…0.703 m²/s² |
| Server | `python -m stokes.server --port 8777` serves `http://127.0.0.1:8777/?token=…`; HTTP 200 |

Both runs ended **`done_unconverged`** at 500 iterations with k above the 1e-3 target. That is the
same outcome the Windows host recorded, and the reason the default target was lowered to 1e-3; it is
a setting, not a Linux defect.

**Differences from the Windows findings**

- **Tet outlet non-orthogonality**: the Windows elbow's fixed 79.75° faces at the outlet edge did not
  reproduce on this geometry; both meshes peak near **63°**. The geometry differs (a smooth circular
  bend, not the mixing elbow), so this is not a contradiction — re-check on the mixing-elbow project
  before closing the Windows finding.
- **Symmetry-plane flatten**: covered separately in item 4 with a half-elbow that has a real z = 0
  symmetry face.
- **Residual target default 1e-3**: confirmed; the run still stops at its iteration limit because k
  sits at 1.1e-2, so "not converged" is honest.

## 2. Engine attachment — done

The checklist asked for the Linux twin of `STOKES_FOAMWIN_ENV`, probing `$FOAM_INST_DIR`,
`which foamRun` and a sourced bashrc. `STOKES_OPENFOAM_BASHRC` already existed; the paths around it
did not work without it. Fixed and tested:

- `FOAM_INST_DIR` is the **parent** of installs (`$HOME/opt` holding `$HOME/opt/OpenFOAM-14`); the
  old code appended `/etc/bashrc` to the parent. A directory that is not itself an install is now
  scanned one level down.
- `which foamRun` names an install (source-build and prefix layouts), so a shell that already ran
  `source …/etc/bashrc` needs no variable.
- The sourcing shell now inherits `LD_LIBRARY_PATH` and the OpenMPI prefixes. Measured: without
  them, `blockMesh -help` exits 127 with `libmpi.so.40: cannot open shared object file`; with them it
  exits 0.
- The sourcing shell now inherits `USER`. Measured: without it OpenFOAM builds
  `FOAM_USER_APPBIN=$HOME/OpenFOAM/-14/...`, so a cfMesh in `$HOME/OpenFOAM/<user>-14/...` was
  invisible.
- `host.probe` from a sourced shell (no `STOKES_OPENFOAM_BASHRC`) finds Foundation 14, `blockMesh`,
  `foamRun`, `cartesianMesh`, `pMesh`, `tetMesh` and OpenMPI.

Tests: four new cases in `tests/test_agent_engine_env.py`; 23 pass. The engine census test
(`tests/engine/test_host_probe.py`) now requires a resolved `cartesianMesh` to actually run, and
passes on this host.

## 3. Linux runner and agent — done, with limits

- Process-group supervision and kill-on-close: `tests/test_agent_jobs.py`,
  `test_agent_security.py`, `test_agent_job_protocol.py`, `test_agent_jobs_limits.py` and
  `test_run_local.py` pass on Linux (**182 pass**; the one test that needs a real engine skips without `STOKES_OPENFOAM_BASHRC`). The job-state events are
  the same ones the Windows runner emits; the engine job tests below exercise the real steps.
- **MPI**: OpenMPI 5 counts physical cores, not `nproc`. This 4-thread host refused four ranks with
  "not enough slots"; `--oversubscribe` launches them. `mpi_flags()` adds it only when the captured
  environment is OpenMPI (`OMPI_*`), never for MS-MPI or MPICH.
- `tests/engine/test_agent_jobs_engine.py` and `test_foamrun_job_engine.py`: **5 pass, 141.7 s** on
  Linux, including a real four-rank solve and process-group cancellation.
- What was not tested: the desktop closing mid-job (the `atexit`/SIGTERM path is covered by unit
  tests only), and OpenMPI on a host that needs a hostfile or a scheduler.

## 4. Flatten and the cfMesh driver — done

- **Polyhedral**: `improveSymmetryPlanes` + `symmetryType` ran on the half-elbow; 33,260 cells,
  0 failed checks, max non-ortho 64.9°.
- **Tetrahedral**: the agent's `flatten` step ran on the half-elbow: **1,781 patch points, 1 point
  off the plane by up to 0.426 mm, moved fully, 0 points left off**, 0.6 s; 106,268 cells, 0 failed
  checks.
- `tests/test_agent_flatten.py`: **14 pass** on Linux.
- `tests/engine/test_mesh_cfmesh_engine.py` (the flanged pipe, all four cfMesh fills plus a
  three-layer poly-hexcore): **5 pass, 112.3 s** — patch types, cell types and checkMesh verdict all
  as on Windows.
- Line endings and tool invocation: the meshDict/STL paths are ordinary POSIX paths; no CRLF
  problem appeared. `surfaceFeatureExtract` was not on the tested path; the app's cfMesh cases do not
  use it.

## 5. Geometry import on ext4 — measured, no change made

`pytest -q -m perf -s tests/perf/test_geometry_tessellation.py`, ext4, 4 vCPU, machine factor 1.298:

| Budget | Measured | Limit (target × factor × headroom) |
|---|---|---|
| both tessellation passes, 4,500 solids | **14,534 ms** | 18,087 ms |
| coarse pass | **3,771 ms** | 15,073 ms |
| fine pass | **7,580 ms** | 18,087 ms |

From the same full-suite run (all budgets passed): `geometry.batch_transfer_4500_cold` 2,188 ms
(limit 29,993), warm 924 ms (limit 4,499); `tree.node_tree_4500` 34.1 ms (limit 37.5 ms).

**Full 4,500-part import**: the same pin grid through `geometry.import` end to end took **39.95 s**
(worker 38.5 s, 4,500 parts, 26.9 MB mesh pack) on this 4-vCPU ext4 host. That is inside the 30–40 s
target, and faster than the 41 s recorded on the 24-CPU Windows host; the OCCT read remains the floor
and the helper passes plus the pack write are the reason the rest is small. **No change was made to
`CACHE_WRITERS=4` or the pack file**: there is no profile showing either is wrong, and the rule is to
measure first.

## 6. Desktop shell and rendering — partial

- **X11** (`QT_QPA_PLATFORM=xcb`): window maps at 1480×920, `_NET_ACTIVE_WINDOW` is the Stokes
  window, screenshots captured. The X11 plugin needs `libxcb-cursor0`, which this host lacked and
  which was extracted locally (no root); a packaged build must depend on it.
- **Wayland** (`QT_QPA_PLATFORM=wayland`): window maps and screenshots capture.
- **Fractional scaling**: `QT_SCALE_FACTOR=1.25` gives `dpr 1.25`, a 1823×1018 image from a
  1458×814 logical window, no clipping.
- **GPU fallback**: this host has no usable DRM render node, and QtWebEngine blocklists WebGL 2 on
  Mesa llvmpipe by default. The viewport shows a persistent "3D view unavailable on this display"
  card with the driver's reason (verified on X11 and Wayland) unless the app is started with
  `--software-webgl`, which adds `--use-angle=swiftshader --enable-unsafe-swiftshader
  --disable-gpu-compositing` before QtWebEngine starts. **With that switch the 3D view renders the
  elbow, the grid and the 20 mm scale bar** (X11 screenshot); the same flags also render on Wayland.
  `--disable-gpu-compositing` is what keeps the window visible: the SwiftShader flags alone failed to
  import the compositor's dma-buf. **Hardware-GPU 3D and the 30 fps reduced-quality mode are still
  unmeasured here** — this VM has only a software rasteriser.
- **Focus on Wayland** was not queryable with the tools on this host; only the X11 active-window
  check is recorded.

## 7. CI for the engine tests — added, not yet executed

`.github/workflows/engine-linux.yml` **is prepared on the local branch** (the push needs the OAuth
`workflow` scope, which the session's token did not have; it is the last local commit and a patch is
kept beside the worktree). It installs the OpenFOAM Foundation 14 apt pack on `ubuntu-24.04`, builds
the cfMesh port with the committed patch script, and runs `pytest -q -m engine tests/engine`. It is
manual + weekly because the install and build cost about ten minutes. **It has not run on GitHub
yet**, so treat it as added, not proven.

The same command was run on this host with the local source build and cfMesh:
**53 passed, 1 skipped, 349 warnings in 565.9 s**. The skip is
`test_energy_elbow_engine.py`, which needs a meshed elbow energy project
(`STOKES_ELBOW_ENERGY_PROJECT`); it is a data dependency, not a Linux failure.

## 8. Docs and packaging

An install and quick-start page is now [docs/engineering/linux-install.md](engineering/linux-install.md)
(OpenFOAM 14, the cfMesh build, MPI, the engine variables, the desktop dependency); the README
links it.

**Packaging (M8) is untouched.**

No AppImage/tarball, `.desktop` file, signing or bundled engine. This host cannot build or sign a
package (no root, no signing key). Unchanged from the Windows note.

## What remains

1. Run the new engine workflow once on GitHub and record the result.
2. Re-check the Windows tet outlet 79.75° finding on the mixing-elbow project (this synthetic elbow
   peaks at 63°).
3. Verify hardware-GPU WebGL 3D and the 30 fps reduced-quality mode on a Linux machine with a
   real GPU; software SwiftShader rendering is verified.
4. Linux packaging and signing (M8).
5. Confirm the 4,500-part import on a many-core Linux host as a comparison; it is already inside
   the target on this 4-vCPU host (39.95 s). No optimisation without a profile.
6. Wayland focus and fractional scaling on a real compositor (this session is XWayland-ish).
