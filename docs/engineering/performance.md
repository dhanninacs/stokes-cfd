# Performance

Speed is goal one. This document holds the budgets, the rules that keep them,
and how to measure. `CLAUDE.md` §5 is the summary.

## Budgets (tests in `tests/perf/`)

| Operation | Target | Test |
|---|---|---|
| `import stokes.api` (headless core) | < 300 ms cold | `test_import_time.py` |
| `import stokes.cli.main` | < 400 ms cold | `test_import_time.py` |
| App start to an interactive empty project | < 3 s | M0 shell, `test_app_start.py` (ui) |
| API command round trip for a model edit (`parameter.set`) | < 200 ms, typical < 5 ms | `test_api_latency.py` |
| Expression evaluation with 10 references | < 1 ms | `test_units_perf.py` |
| Project save/load with 10 k nodes | < 500 ms each | `test_model_perf.py` |
| STEP import, 20 k faces | < 20 s, first parts visible < 2 s | M1 (tessellation share now: `test_geometry_tessellation.py`; the OCCT read dominates, decisions log G4) |
| Latin hypercube 1000 × 10 plus a polynomial fit | quadratic < 80 ms, cubic < 800 ms | `test_explore_perf.py` |
| Face pick at 5 M triangles | < 16 ms | M1 |
| Feature-angle growth over 20 M triangles | < 300 ms | M1 |
| Orbit at 10 M triangles | 60 fps discrete GPU; 30 fps reduced-quality | M0/M1 |
| Boundary surfaces of a 10 M-cell mesh visible after meshing | < 10 s | M2 |
| Compile a case | < 1 s | M3 |
| Residual stream update | 10 Hz without UI stutter | M3 |

A budget test fails CI when the measured time exceeds the target by more than
25 % on the CI runner's calibrated baseline (the harness scales targets by a
machine factor measured at session start, so laptops and CI agree on
pass/fail).

## Principles

1. **Measure first.** No optimisation without a profile (`py-spy`,
   `cProfile`, `tracemalloc`, VTK timers) attached to the PR.
2. **The main thread paints.** Nothing else. Workers, the agent and asyncio
   loops do the work and post results.
3. **Lazy imports.** Qt, VTK, OCP, the pint registry and numpy-heavy modules
   load when first used. The import-time test keeps this honest.
4. **Buffers, not objects.** Geometry and fields move as numpy arrays or
   memoryviews; C++ kernels take `nb::ndarray` views without copies; the agent
   sends float32/uint32 blobs.
5. **Batch and coalesce.** VTK pipeline changes are applied together and
   rendered once per frame; tree updates are batched per command; the agent
   coalesces log and monitor events to 10 Hz.
6. **Level of detail everywhere.** LOD actors during interaction, decimated
   artifacts first and full ones later, progressive tessellation per body.
7. **Cache by content.** Tessellations, artifacts and compiled cases are
   keyed by content hash; re-opening a project recomputes nothing it already
   has.
8. **Startup is sacred.** No network, no probing, no engine detection on the
   start path; those run after the window is interactive.

## Rules by layer

### Model and API

- Commands touch only the nodes they name; no full-tree traversals in hot
  paths. Indexes by id and by path are maintained incrementally.
- Expression recomputation follows the dependency graph, not the whole tree.
- Journals append; they never rewrite.

### Geometry (OCCT)

- Tessellate per body in workers with linear and angular deflection chosen
  from the body size; coarse first, refined later.
- Face ids are assigned once at import and stored with the tessellation.
- Booleans and healing run in workers with timeouts; the UI shows progress.

### Viewport (VTK)

- One actor per part or patch; per-face colours as scalar arrays, never
  duplicated geometry for highlights.
- Picking through the id buffer in `stokes.native`; VTK's cell picker only
  as a fallback.
- `vtkRenderWindow.Render()` is called from a single coalescing timer, not
  from every event.
- Reduced-quality mode: LOD always on, no edges, one light, smaller picking
  buffer.

### Agent and engines

- The agent never reads a time directory into Python lists; VTK readers
  produce arrays that are sliced and decimated before transfer.
- Log tailing uses incremental reads with a remembered offset; parsers are
  anchored regexes on single lines.
- Only STL and the compiled case cross the Windows–WSL bridge (measured at
  about 21 MB/s); everything else stays on the engine host's disk.

### Qt

- Model/view for the tree with batched `dataChanged` signals; no per-node
  widgets.
- Inspector forms are built from schemas once per node type and reused.
- No blocking dialogs on the startup path.

## Measuring

```bash
# import time (shows the slowest imports)
python -X importtime -c "import stokes.api" 2> import.log && sort -t'|' -k2 -n import.log | tail -20

# CPU profile of a command
python -m cProfile -o out.prof -m stokes.cli.main set demo.stokes /physics/parameters/U_in value "3 [m/s]"

# sampling profile of the running app
py-spy top --pid <pid>

# budgets
pytest -q -m perf
```

Attach the numbers to the PR. A PR that changes a hot path without numbers is
sent back.
