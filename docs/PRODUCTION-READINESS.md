# Production readiness

**Checked 2026-11-09** on the Windows host with the FoamWin OpenFOAM 14 install, branch `windows`.
This is a record of what was run and what is open, not a promise: an item is only "verified" if the
command beside it was run and passed.

## Verified

| Check | Command | Result |
|---|---|---|
| Lint, format | `ruff check .` and `ruff format --check .` | clean, 713 files |
| Types | `mypy` | clean, 342 files (the browser review harnesses under `tools/` are excluded, see `pyproject.toml`) |
| Python tests | `pytest -q` | 4,789 passed, 0 failed, 265 skipped (tests that need an engine or a display) |
| Front-end tests, types, build | `npm test`, `npm run typecheck`, `npm run build` in `web/` | 2,334 tests pass, clean, builds |
| Layering, IP, OpenFOAM-content gates | `tools/check_forbidden_imports.py`, `check_ip.py`, `check_no_foam_content.py` | clean |
| Licences | `tools/audit_licenses.py`, `tools/audit_js_licenses.py` | 117 Python and 76 JS packages clean |
| Vulnerabilities | `pip-audit`, `npm audit --omit=dev` | none known |
| Performance budgets | `pytest -m perf` | 18 of 18 pass |
| Real end-to-end on Windows | the mixing-elbow walkthrough in the app: mesh (snappy, tet, polyhedral), 4-core solve with k-omega SST, results, autosave and restore | works; see `CHANGELOG.md` |
| CI | `.github/workflows/ci.yml` | runs all of the above on Windows and Linux, Python 3.13 and 3.14 (front-end tests added 2026-11-09) |

## Not production-ready: needs the owner or a toolchain this host lacks

1. **No installer exists.** ADR-0009 specifies a signed, Nuitka-compiled Windows build with an Inno
   Setup or WiX installer. This host has no Nuitka, no C compiler, no installer tool and no admin
   rights. Needed: a build machine with MSVC and Nuitka, and **an Authenticode (EV) signing
   certificate, which only the owner can provide**. The fallback in ADR-0009, PyInstaller, would need
   the owner's decision to ship unsigned.
2. **Trade-mark search for "Stokes"** (open question A3, the owner's task).
3. **Version.** The package is `0.0.1.dev0`. The first release number is the owner's call.
4. **FoamWin is not release-verified.** `docs/HANDOVER.md`: no `#codeStream` compiler in the package
   (U2), `libinterRegionFvModels.dll` not copied or tested, the packaged ZIP not rebuilt since the last
   tool changes. The Gmsh driver is not wired and cfMesh was built against an older OpenFOAM target.
5. **One M0 geometry performance gate is still open.** A cold open of a saved 4,500-part project is now 1.5 s (was 49.5 s). STEP import of the 4,500-solid, 52 MB pin grid is 41 s on a 24-CPU host (was 75 s) against a 30 to 40 s target; the 26.5 s OCCT read is the floor. Measured on this host, not on the owner's laptop.

## Known correctness gaps

- cfMesh tetrahedral and hex-dominant meshes bent the symmetry plane along the edge where a wall
  meets it (tet up to 2.8 mm with 5 mm cells). The agent now flattens it as far as the cells allow
  (`flatten` action, 2026-11-09) and the mesh still warns about what is left. On the elbow the
  app's own realizable k-epsilon case on the tet mesh, which died at iteration 113 with a negative
  temperature, now runs its 500 iterations (peak speed 2.1 m/s, 293 to 313 K, energy residual
  1.7e-3, not converged in the same way as the other meshes). What stays: 4 plane points up to
  0.58 mm off and a fixed 79.75 degree non-orthogonality at the outlet edge, both reported on the
  Mesh card (`docs/TASKLIST.md`). Polyhedral and snappyHexMesh meshes are not affected.
- The elbow solves stop at 500 iterations as "not converged" with residuals just above the 1e-4 target.
  The answers are consistent between meshes, so this is a setting to decide, not a defect.

## What a release still needs, in order

1. A build host with the toolchain and the signing certificate (items 1 and 3 above).
2. The two P0 geometry-performance items, or a decision to ship with the measured numbers.
3. A fresh FoamWin ZIP and its checksum after the last tool changes, then a clean-machine install test.
4. The tet mesher's 79.75 degree faces at the outlet edge, or a decision to ship it with the quality
   warning the Mesh card already shows.
