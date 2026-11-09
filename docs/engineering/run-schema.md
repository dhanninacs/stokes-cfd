# Runs: records, commands, executors and the field results

**Format 1** · ADR-0023 (accepted) · records: `stokes/run/records.py` · store:
`stokes/run/store.py` · executor: `stokes/run/executors.py` · field reader and codecs:
`stokes/run/fields.py` · commands: `stokes/api/commands/run.py` · routes:
`stokes/server/app.py` · tests: `tests/test_run_commands.py`, `tests/test_run_store.py`,
`tests/test_run_fields.py`, `tests/test_results_routes.py`

This document is the contract between the Python core, the agent, the desktop and the viewport for
**solving a case and getting the results back**. It covers the run record and its states, the `run.*`
commands, the executor, the `STKFLD1` field artifact, the results cache and the `/api/results/*`
routes.

**Status.** T-214b built the records, the commands and the executor. **T-215** built the agent's
`extract.fields` and the `STKFLD1` layout. **T-216** built the desktop half: the reader, the cache,
`run.fields` and the routes. T-218 draws them.

All values in commands, events and results are **SI**: lengths are metres, velocity `m/s`, and
incompressible `p` is **kinematic** (`p/rho`, `m2/s2`).

## The run record

A run is an **operation with a lifetime, not a parameter of the model** (ADR-0023). It is a plain
dataclass, not a node: undo must not resurrect it, and `run.start` adds no undo step.

Every record is written to `<runs_dir>/<run_id>/record.json` **after every change**, because a run
outlives a session — the desktop is closed, the agent keeps going, and the user reopens the project.
`<runs_dir>` is `<project folder>/runs` for a saved project and a per-session scratch folder
otherwise, so an unsaved project still has a working run list and moving it on Save as moves the
runs.

| field | type | meaning |
|---|---|---|
| `id` | string, `ID_RE` (`r_` + 8 hex) | the run id; **the only thing that may name its cache file** |
| `kind` | string | `simulation` today |
| `name` | string | unique in the project: `duct`, then `duct 2` |
| `state` | enum, below | |
| `stale` | bool | the model has moved on since the run |
| `engine`, `host` | string | what ran it, and where |
| `job` | string | the agent's job id; every event is matched against it |
| `mesh_result`, `mesh_basis`, `mesh_job` | string | the mesh it solved |
| `basis` | string | what makes reuse and staleness decidable |
| `settings_id` | string | the settings node it was built from |
| `iterations`, `converged`, `last_residuals` | int, bool, object | the report's answer |
| `monitors` | object | `{patch: {flow_rate, p_mean}}`, plus `T_mean` with the energy equation (`patchAverage(...,fields=(pT))`), the last value of each function object |
| `fields` | array of string | **which fields the run has** (T-216) |
| `slice` | bool | **whether a slice was cut** (T-216) |
| `timings` | object | `{"seconds": …}`, the job's own wall time |
| `error` | string | `code: message`, or empty |
| `created`, `updated` | string | ISO-8601 UTC |

A record over `MAX_RECORD_BYTES` (256 KiB) is written **without** its residual history and with
`"truncated": true`, rather than refused: the agent's `monitors/residuals.jsonl` is the authority for
the history, and losing the whole record to save it would be worse.

### States

| state | meaning |
|---|---|
| `queued` | the job is created, the solver has not started |
| `running` | the solver is working |
| `cancelling` | a cancel was asked for and the process has not gone yet |
| `done` | it finished and the report says it converged |
| `done_unconverged` | it finished and did not converge. **Not a failure** |
| `failed` | a step exited non-zero, or the report says `fatal` |
| `cancelled` | a cancel reached it |
| `interrupted` | the agent lost the job, so the outcome is unknown |
| `skipped` | the host cannot run it: no OpenFOAM, or no solver. **Recorded, not refused** (ADR-0024) |

`done` vs `done_unconverged` is **the report's call, not the job's**: many real runs stop at the
iteration limit with residuals still falling, and calling that `done` would tell a user something the
solver never said. `successful` is `done` or `done_unconverged`, and it is what the reuse path
branches on.

A machine without OpenFOAM is a fact about the machine, so `run.start` records the run as `skipped`
rather than refusing it: a journal recorded where OpenFOAM exists then replays where it does not,
reproducing the same project state.

## Commands

| command | params | mutates | result |
|---|---|---|---|
| `run.start` | `host?`, `wait?`, `force?`, `run_id?` | no | `{run, state, job, basis, reused, name}` |
| `run.cancel` | `run?` | no | `{cancelled}` |
| `run.status` | `run?` | no | the record, plus the live step and fraction |
| `run.list` | — | no | `{runs: [...]}`, newest first |
| `run.log` | `run?`, `lines?` (1..5000) | no | `{step, lines, truncated}` |
| `run.residuals` | `run?`, `since?` | no | `{iterations, time, fields, continuity, loading}` |
| `run.fields` | `run?` | no | `{run, time, times, fields, slice, surfaces}` |
| `run.time` | `run?`, `time?` (s) | no | `{run, time, fetching}`; `run.changed` follows |
| `run.monitors` | `run?` | no | `{run, monitors: [{name, size}]}`: the `.dat`, `.xy` and `.csv` tables under the job's `postProcessing/` |
| `run.monitor` | `run?`, `name` | no | `{run, name, columns, values: {column: [floats]}, truncated}` |
| `run.clear` | `run` | yes | `{removed}` |

`run?` means the current (newest) run when omitted. **`run.start` adds no undo step and
`run.cancel` is not a mutation**: a user who pressed Solve right after changing a velocity wants
`history.undo` to undo the velocity, and cancelling is not something anyone wants to *undo*.

### `run.fields`

```json
{
  "run": "r_1a2b3c4d",
  "time": 1000.0,
  "times": [0.0, 500.0, 1000.0],
  "fields": [{"name": "U", "units": "m/s", "min": 0.0, "max": 1.2}],
  "slice": true,
  "surfaces": "ok"
}
```

`fields` is every field present on **at least one patch**, in the artifact's order. `min`/`max` are
over **every patch and the slice** — for `U` over the **magnitude**, never its components, because a
legend's ends have to describe the colour scale the browser applies. `surfaces` is `"ok"` when the
cache file is readable and `"missing"` when it is not (a cleaned cache, or a run recorded before the
fetch). `times` is every time the case wrote; `time` is the one the fields are from.

### `run.time` and imported cases (T-310)

`run.time {run?, time?}` reads a finished run's fields at another written time, on a worker thread:
the nearest time the case wrote is used, and omitting `time` goes back to the latest. The run's
cached planes and iso-surfaces are dropped and cut again at the chosen time when next asked for.
It is not a mutation: which instant is on screen is a view. Refused with `run.not_finished` before
the run has finished.

A run of an imported case (`of14.external`) reads **every scalar and vector field the case wrote**
(at most 64), not a fixed `U`/`p`, and each field's unit comes from the `dimensions` of its own
file at that time, including OpenFOAM 14's named sets (`[velocity]`, `[kinematicPressure]`,
`[length*velocity]`); a set the reader does not know gives an empty unit rather than a guess.
`U_mag` takes `U`'s unit. A model run reads exactly the fields it always did.

**It never goes to a host.** A finished run's fields were fetched once, when the job ended; asking
again on every query would make a legend's numbers depend on whether the agent is still up.

`run.residuals` returns a value per iteration with **`null` where the solver did not report that
field** — one entry per iteration, so a chart's x and y stay aligned. `p` is solved twice per step
and a laminar run has no `k`, so a missing field is a missing key rather than a shifted index.

**A reopened project holds no history in memory.** The first `run.residuals` for a finished run with
a job reads the agent's `monitors/residuals.jsonl` back on a worker thread and answers
`loading: true` with no blocks until it is in; the front end asks again every two seconds.

## The executor

`FoamRunExecutor` (`of14.incompressibleFluid`) writes the case, stages it beside the mesh result,
starts `foamRun`, and turns the agent's events into `run.*` ones. `copy_from` carries the
`constant/polyMesh` out of the finished mesh job, so the agent never re-meshes and the two cannot
silently disagree about the mesh.

The listener does no work: `_on_event` runs on the runner's **event thread**, so it maps and
forwards. Reading the agent's files is a round trip, so it happens on a worker thread once the job
is over. A final `job.state` from the wire does **not** end the run — `store.finish` does. The record
stays non-final while the worker reads `job.status`, the history and the monitors, because
`settle`/`run.status` stop at `record.final` and a reader arriving between the two calls would find
`iterations` right and `converged` still at its default `False`.

**The agent's file is the authority, the events are the live view.** Events are rate-limited, so
once the job is over `monitors/residuals.jsonl` is read back and replaces the in-memory history:
that is what makes a finished run's chart whole rather than the part that survived the socket.

### The fetch after a run (T-216)

Between the terminal state write and `run.finished`, `store.finish` asks the host for the run's
fields and keeps them in the cache. This is the one seam every production path goes through, it runs
on the collect **worker thread** (a round trip must not touch the session thread), and the event
therefore goes out with the record already carrying `fields` — a listener that acts on `run.finished`
sees a complete run.

**A failure to fetch never changes the run's state.** The solve worked and its fields exist on the
host; reporting the run as `failed` because a *fetch* failed would tell the user something untrue
about their solve. The record stays `done` with `error = "run.extract: …"`, `fields = []`, the bad
file is removed, and `run.failed_extract {run_id, code, message}` is emitted.

## The field artifact (`STKFLD1`)

Written by the agent's `extract.fields` (T-215), read by `stokes/run/fields.py` (T-216):

```
bytes[8]  b"STKFLD1\0"
u32       header_length (JSON space-padded to a multiple of 4)
header JSON, then arrays at header offsets measured FROM THE START OF THE DATA SECTION
```

Per patch, per field **in header order**: `f32[components × points]`. Then the slice's
`positions f32[3P]`, `triangles u32[3T]`, `edges u32[2E]` at `slice.offset`, then its fields.

* **Patch order and `points` are the surfaces artifact's** (T-215's one invariant). The desktop
  tints the vertices it already drew, so an array in a different order is a wrong picture, not a
  visible failure.
* **A patch with no array has an empty `fields` dict — never zeros.** An `empty` patch has no `U`,
  and a zero there says the wall is at rest.
* **`min`/`max` are over the stored array**, for `U` over the magnitude.
* **`extract_fields` always emits a slice section**, treating `slice_plane=None` as the default
  plane, so **`slice.triangles == 0` means the plane missed the mesh** — a `slice: false` run, not a
  malformed artifact.

**The buffer is untrusted** — it came over a connection and sits in a cache file — so `read_header`
checks the magic, version 1, the sizes and **every offset and count against `len(data)`** before
anything is sliced, refuses a header over 16 MiB, and raises `MeshFormatError`. It is deliberately
the same style and the same limit as `stokes.mesh.surfaces.read_header`, so there is one set of rules
to remember.

| function | returns |
|---|---|
| `read_header(data)` | the header plus `data_start` |
| `field_array(data, header, patch_index, field)` | a **view**: `(points,)` or `(points, 3)`; `SLICE_INDEX` names the slice |
| `slice_arrays(data, header)` | `(positions (P,3) f32, triangles (T,3) u32, edges (E,2) u32)` |
| `field_payload(values, field, units, vmin, vmax)` | the client codec |
| `slice_transport(data, header, color)` | transport version 2, one group, `kind: "mesh"` |
| `slice_edges(data, header)` | the T-210 edge codec |

### The field codec

`u32 header_length`, JSON padded with spaces to a multiple of 4, then `f32[components × count]`:

```json
{"version": 1, "field": "p", "units": "m2/s2", "components": 1, "count": 25000,
 "min": -1.2e-04, "max": 3.4e-02}
```

`components` is the array's own shape rather than a table, because the header a client reads has to
agree with the bytes beside it. The client (T-218) validates it the way `parseMeshEdges` does.

**`min`/`max` are the RUN'S GLOBAL RANGE for that field, not the payload's own range.** A legend shared
across the patches needs one scale, so the codec carries the global ends, and the difference is visible
in the data: `GET /api/results/field/<run>/<walls>?field=U_mag` reports `"max": 0.157644` while **every
value in the payload is `0`**, because the walls are no-slip and the fastest flow is at the outlet.

**So do not scale a per-patch colour by these.** The same global range per field is on `run.fields`, which
is where a legend should read it; if a patch's own range is ever wanted, that is a new pair of keys
(`patch_min`/`patch_max`) rather than a change to these. Measured and settled with the T-218 author on
2026-10-15, who confirmed the viewer takes its colour range from `run.fields` and parses the codec's
`min`/`max` without using them.

The slice's two payloads are **byte for byte the shapes the mesh routes already serve**:
`slice_transport` is the same transport version 2 as `/api/mesh/{id}` and `slice_edges` is the same
edge codec as `/api/mesh/edges/{id}`, so the viewport has one reader for patches and slices alike,
and neither codec is duplicated or changed.

## The results cache

A run's fields live at **`<cache>/results/<run_id>.stkfld`**, where `<cache>` is
`<project folder>/cache/geometry` (`stokes.geometry.shapes.cache_dir`) — the mesh cache's sibling, so
the fields travel with the project. A project with no folder uses the session's scratch folder.

* `run_id` must match `ID_RE`, and the resolved path must be inside `<cache>/results`: the id
  becomes part of a path, so it is validated first and neither the folder nor the file may be a
  symlink. This is `MeshService._surface_path`'s rule, for the same reason.
* The file is written by `fetch_artifact` through a `.part` name and renamed only when its size and
  sha256 match, so a reader sees a complete, verified file or no file. **A failed fetch leaves
  nothing behind.**
* **`project_opened` fetches nothing**; a missing cache file is an answer (`surfaces: "missing"`,
  and the routes 404), not an error.
* **`project_saved` copies `<old cache>/results/*.stkfld`** into the new cache — on the first save
  too, out of the scratch folder. Without it a project moved to a new folder shows every finished run
  as `missing`, and the host the fields came from may be gone. Only a name this store could have
  written (`<run id>.stkfld`) is copied.
* **`run.clear` removes the cache file**, because the artifact can be megabytes and a run the user
  removed must not leave one behind.
* At most `FIELD_LRU` (2) files are held in memory as `memoryview`s, keyed by path and stamped with
  `(size, mtime_ns)` so a file replaced under us is read again rather than served stale.

## Routes

| route | payload |
|---|---|
| `GET /api/results/field/{run_id}/{target}?field=p&plane=…` | the field codec |
| `GET /api/results/slice/{run_id}?plane=…` | the slice as transport version 2 |
| `GET /api/results/slice-edges/{run_id}?plane=…` | the slice's cell edges (the T-210 edge codec) |

`target` is a `mesh_patch` node id or the literal word `slice`.

### `?plane=` — any plane, on demand

Every results route takes an optional `plane=ox,oy,oz,nx,ny,nz` (metres, six finite numbers, a
non-zero normal). **Omitting it is the run's own default mid-plane**, which is the cut the `.stkfld`
already carries, so a client that has never heard of planes is unaffected.

A named plane the run's artifact does not hold is **cut on the host once and cached** at
`<cache>/results/slices/<run id>/<plane hash>.stkslice` — the same artifact format, with no patches
and one slice section. `MAX_CACHED_PLANES` (16) are kept per run, **least recently used first**, and
`run.clear` removes the whole folder. At most `MAX_CONCURRENT_CUTS` (2) cuts compute at once across
the server; identical concurrent requests for one plane share a single computation.

**The cache key is the plane rounded to six significant digits and with its normal normalised.** Both
halves matter: a plane arrives as text from a client that may be dragging a handle (`0.30000001` and
`0.3` are one plane), and a direction has no magnitude to the cutter (`(0,0,1)` and `(0,0,7)` are one
plane). `stokes/run/planes.py` owns that identity, and the route and the cache both use it, so they
cannot disagree about what "the same plane" means.

**A plane applies to the slice and to nothing else.** `?plane=` on a named patch is a 400: a patch
has its own geometry, and ignoring the parameter would look exactly like a plane that did nothing.

Every route calls `check(request)` first, is a plain `def` (Starlette runs it on a **worker thread**,
so packing a payload never touches the event loop), and answers `Cache-Control: no-store`.

**Path parameters are validated against `ID_RE` (or `slice`) before any lookup**, which is what keeps
a path parameter out of a filesystem path; the store's own `fields_path` validates the id again on
the way to the file. A literal `/` in the value never reaches a handler at all — it splits the path,
so the router answers 404 before any parameter is validated.

| status | meaning |
|---|---|
| 200 | the payload |
| 400 | an unknown **field name**, a missing `field` parameter, a malformed path parameter, a malformed `plane`, or `plane` on a patch target |
| 401 | a missing or wrong token |
| 404 | an unknown run or patch node, a run with no slice, or a missing cache file. **A plane request is more specific**: `results.slice_empty` when the plane misses the model, `results.host_unavailable` when the plane is not cached and the host cannot be reached to cut it |

**400 vs 404 is deliberate**: 404 is "this run or patch is not here", while an unknown field name is
a request that cannot be answered as stated — a 404 there would read as "this run has no fields",
which is a different and false claim.

A patch is mapped from its node **by name, falling back to the node's `index`**: a rename must not
silently serve a different patch's array.

## Security checklist (`docs/engineering/security.md`)

* **New paths**: `<cache>/results/<run id>.stkfld`, built from an `ID_RE`-validated id and refused
  unless it resolves inside `<cache>/results`, with symlinked folders and files refused. Writes go
  through `fetch_artifact`'s `.part`-then-rename, so no partial file is ever visible.
* **New routes**: three, all behind `check(request)`, all with `Cache-Control: no-store`, all sync
  (worker thread), all validating their path parameters before any lookup.
* **New decoder**: `read_header` for `STKFLD1`, which validates the magic, the version, the sizes and
  **every offset and count against `len(data)`** before slicing, caps the header at 16 MiB, and
  raises `MeshFormatError`. It is the same contract as the `STKMSH1` reader.

### Monitors (T-311)

`run.monitors` asks the run's host for the files its function objects wrote (the agent's
read-only `job.outputs`: no links, at most 6 folders deep and 500 files) and keeps the tables.
`run.monitor` reads one (the first MiB) and returns its columns: the last `#` comment line names
them, a value in parentheses becomes one column per component (`U_x`, `U_y`, `U_z`), a row that
does not read as numbers is skipped, and past 20,000 rows every n-th is kept. The case card draws
each value column against the first. Checked on the source tree's `incompressibleFluid/boxTurb16`:
its energy spectrum `graphs/<time>/Ek.xy` as `k` and `E(k)`.

