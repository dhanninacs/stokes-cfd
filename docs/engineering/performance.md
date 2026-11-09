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
| `node.tree` of a 4,500-part project (ADR-0019) | build < 25 ms; streamed in fragments, none longer than 6 ms; whole < 60 ms | `test_tree_perf.py` |
| Announcing 4,500 part changes (a node event with its summary each) | < 200 ms | `test_tree_perf.py` |
| Project save/load with 10 k nodes | < 500 ms each | `test_model_perf.py` |
| STEP import, 20 k faces | < 20 s, first parts visible < 2 s | M1. **Not met, but 75 s to 41 s** (2026-11-09, 4,500-solid 52 MB pin grid, 24 CPUs, unloaded): the OCCT read is 26.5 s (parse 4.9, translate 17) and is now the floor; both tessellation passes run in helper processes (`geometry/helpers.py`, 4.7 s for both on `test_geometry_tessellation.py`), first mesh 0.2 s after the read. See "Import and cold start at 4,500 parts" below |
| Cold open of a saved 4,500-part project | < 5 s | **MET**: 49.5 s to 1.5 s (2026-11-09) by writing the pack at the end of the import. See "Import and cold start at 4,500 parts" below |
| Latin hypercube 1000 × 10 plus a polynomial fit | quadratic < 80 ms, cubic < 800 ms | `test_explore_perf.py` |
| Face pick at 5 M triangles | < 16 ms | M1 |
| Box or lasso select across the 4,500-part model, after release | < 50 ms | **MET (T-095)**: body box **25.6 ms**, face box 17.2 ms, lassos 0.2 ms, all at 4,500 parts on the owner's Intel Iris Xe via `tools/bench_scene.py`. It was 556–626 ms before the fix; see "Box and lasso at 4,500 parts" below. **Single-machine, not reproducible from a clean clone** (T-108): the fixture is generated at test time and the figures come from one GPU — treat them as history, not as a budget you can assert here |
| Feature-angle growth over 20 M triangles | < 300 ms | M1 |
| Orbit at 10 M triangles | 60 fps discrete GPU; many parts use ADR-0019's batched path | M0/M1 |
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

## Recorded figures

### Where the 4,500-part load actually goes (T-096, measured)

`tools/bench_scene.py` reports about 36 s from the page arriving to all 4,500 parts drawn.

- `tools/bench_viewport.py`: the project **reopens in 0.59 s**, and a full warm transfer of 4,500
  parts takes **217 ms** (535 ms gzip) — 18 batch requests, 25.8 MB of meshes. (The cold and warm
  figures are separated further down; this line is the warm one.)
- `tools/measure_browser_load.py` (new, reading the loader's own marks through `window.__stokesLoad`)
  splits what is left:

| Stage | Cold run 1 | Cold run 2 |
|---|---|---|
| The tree arrived (the page knows what to fetch) | 5,038 ms | 4,267 ms |
| The first part's mesh was applied | +1,235 ms | +1,281 ms |
| Every part applied | 19,153 ms | 19,062 ms |
| **The window between the tree and the last mesh** | **14,115 ms** | **14,795 ms** |

**That window is NOT browser-side apply — corrected 2026-10-06.** The first reading of it concluded
"the page spends ~14.4 s turning those buffers into scene objects, which is about 3.2 ms per part".
That was wrong: it timed the window rather than the work inside it. Instrumenting the await showed
the page's own work is about 2.7 s and the rest is the page **waiting for the server**. See the next
section for the corrected table; the ~4.7 s before the tree is the page booting, the RPC round trip
and the first render.

**CORRECTED 2026-10-06: the browser is WAITING, not working.** The piece-by-piece timings above
stand, but the sentence that first followed them here — that the remaining time was the per-part cost
of building scene objects, and that the answer was "whether a part that will be batched needs its own
`BufferGeometry`" — was wrong. The loader was then instrumented at the one place that decides it: the
time spent **awaiting the server** for meshes (`fetchMs`, `batches`, `singles` in
`web/src/viewport/parts.ts`, on `window.__stokesLoad.marks`).

| Measured in the page, 4,500 parts | |
|---|---|
| tree arrives | 5.6 s |
| all meshes drawn | 29.5 s |
| window from tree to all meshes | **23.8 s** |
| **time spent awaiting the server** (summed over 2 concurrent requests) | **40.8 s ≈ 20.4 s wall** |
| batch requests / single requests | **18 / 0** |
| parse + apply + flush (the browser's own work) | ≈ 2.7 s |

So the batching works (18 requests, no per-part trickle), the browser's own work is small, and
almost the whole window is the browser **blocked on 18 geometry-batch replies**. The cost is on the
server side of that socket, and the earlier "3.2 ms per part APPLYING" attribution in this file and
on the board was measuring the wait, not the work.

The server side of it, measured with `tools/bench_viewport.py` (a fresh server, all 4,500 parts, 18
requests):

| Pass | identity (what the desktop host gets) | gzip |
|---|---|---|
| **cold** (first touch of every mesh cache) | **2.92 s** | 6.15 s |
| **warm** (caches already in memory) | **0.18 s** | 0.73 s |

Two things follow from that table. First, the cold path is **16× the warm path**: on a cold project
the meshes are not in memory and every part is read and decoded from its cache file. Second, gzip on a
loopback client would cost 2 to 4 times the transfer — already prevented, since
`SkipGzipOnLoopback` sends the desktop host identity bytes and the compression is only for a remote
client.

The cold figure (2.9 s) is still far below the ~20 s the page waits, and the difference was
**concurrency, not duplicated work**. `GeometryStore.activate` — the hook that would start a
background restore pass — is **never called** anywhere in the tree, so there is no second thread
reading the same caches, and that hypothesis (an earlier revision of this note) is withdrawn. What
there was: `MAX_BATCHES` in `web/src/viewport/parts.ts` kept only **2** batch requests in flight,
while `tools/bench_viewport.py` measures the cold transfer with **4**. The page's own work runs
between the replies, so with 2 outstanding the server sat idle while the page applied a batch, and
the page sat idle while the server built the next one.

**Corrected 2026-10-08 (T-096, second pass): the duplicate-work theory was right, and `activate`
IS called.** The paragraph above says `GeometryStore.activate` is called nowhere. It is:
`stokes/server/app.py` calls `asyncio.to_thread(store.activate)` for the **first WebSocket client**,
and `activate` starts `restore` on a `stokes-geometry-restore` thread, which walks every part node
and calls `_load_cache` for each — the very same per-file open the batch route does. The page opens
a WebSocket as it loads, so the two run at once. `tools/bench_read_timeline.py` counts and timestamps
every `read_cache` inside a real server driven by the real page:

| One page load, 4,500 parts | read 1 | read 2 |
|---|---|---|
| total cache reads | 5,620 | 5,710 |
| distinct ids | 4,500 | 4,500 |
| **ids read more than once** | **1,102** | **1,210** |
| restore thread: first → last read | 0 → 29,240 ms | 0 → 34,256 ms |
| request workers: first → last read | 522 → 29,241 ms | 684 → 34,259 ms |

The two threads interleave over the identical window, so it is not the restore warming anything for
the requests: it is both reading the same 4,500 files at the same time, about a quarter of them
twice. **Fixed**: `_load_cache` claims a part under the store lock and a second caller waits for the
first reader instead of reopening the file. Same tool, after:

| | before | after |
|---|---|---|
| total cache reads | 5,620 · 5,710 | **4,500 · 4,500** |
| ids read more than once | 1,102 · 1,210 | **0 · 0** |
| restore thread: last read | 29,240 · 34,256 ms | **5,383 · 5,667 ms** |

The duplicate opens are gone and the restore thread stops contending after about 5.5 s instead of
running the whole load. **The wall clock did not move** (see the profiles below): on this shared
laptop the load varies by more than the fix is worth, and the open is only part of the cold cost.
The fix is kept because it removes work that was provably wasted, not because it moved the number.

### What the cold batch request actually spends its time on (T-096, profiled 2026-10-08)

`tools/bench_batch_server.py` opens the per-request wall clock and the await boundaries inside
`/api/geometry/batch`. It patches the store read path, `Project.find`, `pack_part` and the ASGI
entry **inside the server process**, so the numbers are the shipped path's. 18 requests of 256 ids
(148 in the last), cold, one at a time:

| Phase | Total over 18 requests | Share of wall |
|---|---|---|
| `wall_ms` (what the page sees) | 7,468 ms | 100 % |
| `store_ms` (`GeometryStore.transport`) | 7,267 ms | 97 % |
| `cache_ms` (`read_cache`) | 7,076 ms | 95 % |
| — of which `unpack` (decode) | 789 ms | **11 %** |
| — of which **the file read** | **6,287 ms** | **84 %** |
| `find_ms` (the id → node index) | 10.5 ms | 0.1 % |
| source-file `stat` calls | 0 | — |

**The cold path is 84 % file open/read/close, 11 % decode, and effectively no index or packing
cost.** 4,500 files of a median 5,716 bytes, one `open()` each, at about 1.3 ms an open.
`tools/bench_cache_read.py` isolates it: 25.71 MB of cache through **4,500 separate handles costs
3,986 ms; the same bytes through one handle with `seek`+`read` costs 44.6 ms** — an 89× penalty for
the per-file handle, far more than the bytes justify, so this is per-open overhead (a filter driver
or Defender scanning each open) and not disk throughput. Confirmed by re-opening the *same* file
4,500 times, which is nearly as slow as 4,500 distinct files.

### Whose cost is the per-open penalty? (T-101, measured 2026-10-08)

The T-096 note left the cause as "a filter driver or Defender scanning each open" and the fix as an
open question: if an antivirus owns it, the answer is deployment guidance rather than a code change.
**It is not Defender, and it is not deployment guidance: it is a per-`CreateFile` cost that our code
chooses to pay 4,500 times, so it is ours to remove.** The evidence, all on the same machine in one
session, all against the real 4,500-part cache:

| Measurement | Result |
|---|---|
| `(Get-MpComputerStatus).RealTimeProtectionEnabled` **during every run** | **False** |
| 4,500 distinct files, open+read+close | ~3,200 ms |
| The same 25.71 MB through **one** handle | **31–41 ms** |
| The same bytes copied to a different directory on the same volume | ~3,000–4,500 ms (no better) |
| Cost vs file count, constant total bytes: 1 / 100 / 500 / 1,000 / 2,000 / 4,500 files | 19 / 73 / 231 / 617 / 1,300 / 2,834 ms — **flat ~630 µs per open** above 500 files |
| **`stat()` only, no data read at all**, 900 files | 166 µs/file (72 % of a full read) |
| One `os.scandir` of the same directory | 18 µs/file (12× cheaper) |
| 900 files, each opened *and memory-mapped* | 708 µs/file |
| One handle, mapped, 900 slices | 4.2 µs/part |

Two things follow. First, the cost is charged at **`CreateFile`**, not per byte and not per read:
a metadata-only `stat` costs 72 % of a full read, and a directory enumeration that opens no file
escapes it entirely. Second, **holding the handles open does not help** (227 µs/file either way)
while **opening one file and slicing it does** (4.2 µs/part) — so the number of opens is the lever,
and it is ours.

Defender is off on this machine, but it is not unprotected: `Sentinel Agent`, `SentinelMonitor`
(minifilter, running) and `McAfee` are registered, with SentinelOne's static-engine scanners live.
A per-open minifilter is the mechanism; **the deployment is that machine's, the 4,500 calls are
ours.** Excluding a path is not a fix we can ship to a customer, and it is not needed: removing the
per-part open removes the cost on any machine, filtered or not.

Three consequences worth recording:

- **Concurrency does not scale here.** Cold transfer, same project, requests in flight varied
  (`tools/bench_batch_server.py --inflight`): 1 → 8.5 s wall, 2 → 4.96 s, 4 → 4.00 s, 8 → 3.44 s,
  while the *summed* request time rises 8.5 s → 23.5 s. The opens contend; each extra request buys
  less than the one before. `MAX_BATCHES` 2 → 4 was the easy part of that curve.
- **The cache layout, not the codec, is the floor.** `tools/bench_single_cache.py` runs the real
  server with every part's bytes served from one handle (a prototype only — it changes no stored
  format) and the cold transfer falls from **3,886 / 2,731 ms to 850 / 1,086 ms** at 4 in flight.
  That is the size of the prize, and taking it means a single-file (or indexed) cache — a change to
  a format that has tests, deliberately **not** made here.
- **`is_fresh` stats the source file per part on a real project.** On the benchmark the parts carry
  no `source`, so it short-circuits and the count is 0; a project imported from a STEP file has a
  real source and pays one more `is_file()` per part on top of everything above.

### The fix: one packed mesh cache per project (T-101, landed)

`stokes/geometry/cache.py` now keeps the project's meshes in **one file**,
`<name>.stokes/cache/geometry/meshes.bin`, beside the `adjacency.bin` archive that already worked
this way, and reading a part is one `seek`+`read` on a handle the project holds open.

**Layout.** `STKMESH2` (magic and format version in one 8-byte tag), a `uint32` header length, a
JSON header `{"version": 2, "bytes": N, "entries": [{"part", "offset", "length", "name",
"mtime_ns", "size"}]}`, then every part's transport-v2 bytes, each entry a slice. Offsets are **relative to the
start of the data section**, so they do not depend on the header's own length, which is only known
once the header has been serialised. `name`/`mtime_ns`/`size` are the source stamp **the part's own
bytes carry** (their transport header's `source`; `""`/`-1` when they carry none), so a packed part
is judged stale exactly as its per-part file is, without a second file. Layout 1 stamped an entry
with the source *as it stood when the pack was written*, which vouched for bytes made from an older
file for good (found by the 2026-10-11 review); a layout-1 pack now reads as absent and is written
again from the per-part files, which were always correct.

**Versioning and migration: additive, no migration step.** The version lives in the magic, so a pack
written by another layout is `not a mesh pack` and reads as *absent* rather than as valid. The
per-part `<node_id>.mesh` files are still written and still read: they are what a project written by
an older build holds, and what answers when the pack is missing, stale or does not carry a part. An
existing project therefore opens unchanged on the old path, and gains a pack the first time it is
activated (`restore` packs the parts it just loaded). This is why there is no migration command and
no format-version bump for the transport codec, which is untouched.

**Keeping the pack true.** The per-part files stay the source of truth and the pack is derived from
them. (1) A source-backed part is told stale by the stamp inside its bytes. (2) A part with no
source (an operation's output) has nothing to compare, so `write_cache` drops the pack *before*
writing a part the pack holds in different bytes; the per-part files answer until the next open
writes the pack again. (3) An open writes a pack only when it lacks a part the project has, taking
bytes from memory, then from the old pack, and opening a per-part file only for a part neither has;
an unchanged project's open therefore reads and rewrites nothing for the pack. (4) While a writer
replaces the pack, the reader holds no handle and reads as absent (`MeshPack.suspended`), so a
reader cannot reopen the file the rename needs on Windows. (5) The store calls `MeshPack.refresh`
once per restore, which is where a rewrite by another process is noticed.

**Crash safety.** The pack is written to a temp file and `replace`d, exactly as `adjacency.bin`
is, so a reader sees the whole old pack or the whole new one and a half-written cache is never read.
Everything in the project folder is untrusted on read: the magic, the header length, the entry ids
and every slice bound are checked, and a truncated, mis-versioned or out-of-bounds pack raises
`MeshPackError` and reads as absent.

**Costs.** Building the pack for 4,500 parts is **141 ms** (25.71 MB) and happens once per project
load; the stale-handle hazard is real on Windows (a held handle keeps the *replaced* file alive), so
`build_pack` suspends the reader for the whole write and the rename retries a transient
`PermissionError` the same way `stokes.model.persist` does.

**What the figures below measure.** `read_cache` over 4,500 parts, **not** a whole project open. The
review of 2026-10-11 found that an open also read every per-part file again and rewrote the whole pack
every time (point 3 above is the fix), so before that fix an open still paid the per-file opens the
figures say are gone. The open itself has not been re-measured since; do not quote the 11x as an
open-time figure.

**Result**, `read_cache` over all 4,500 parts, same machine and session, alternating the pack's
presence three times so machine load hits both arms equally. Two separate sessions, to show the
ratio is the stable figure and the absolute number is not (the second ran while the full suite was
loading the same disk):

| Session | Arm | min | p50 | every round |
|---|---|---|---|---|
| quiet | per-part files (pre-T-101) | 5,131.5 ms | 5,732.9 ms | 5,132 · 6,579 · 5,733 |
| quiet | **packed `meshes.bin`** | **446.2 ms** | **464.8 ms** | 465 · 562 · 446 |
| quiet | **speedup** | **11.5×** | 12.3× | 11.0× · 11.7× · 12.8× |
| busy | per-part files | 14,598.6 ms | 14,736.3 ms | — |
| busy | packed `meshes.bin` | 919.5 ms | 1,076.5 ms | — |
| busy | speedup | 15.9× | 13.7× | 13.6× · 5.2× · 16.6× |

Absolute times move by 3× between sessions — the same variance T-096 recorded — but **the two arms
never overlap in any run of either session**, and the ratio stays between 11× and 16×. Quote the
ratio.

Through the real server (`tools/bench_single_cache.py`, 4,500 parts, 4 in flight, p50 of 2):

| Shipped path | baseline | the "single handle" prototype |
|---|---|---|
| pack absent (pre-T-101) | 3,558 ms | 1,032 ms |
| **pack present (now)** | **2,248 ms** | 1,880 ms |

The prototype no longer shows a large prize because the shipped path now takes it: the real server's
cold transfer moved **3,558 → 2,248 ms**, and the remaining gap to the prototype is inside this
machine's run-to-run spread. The decode (`unpack`, 11 % of the old cost) is now the largest share of
what is left, which is the next thing to attack if the cold path needs to fall again.

**The wall clock at 4,500 parts is not a usable instrument on this machine today.** Same tool
(`tools/measure_browser_load.py`), same project, same build, alternating: 27,121 · 16,499 ms before
and 18,693 · 14,788 · 20,749 · 16,331 ms after, with the tree phase alone swinging 5.6–15.1 s
between runs. The medians differ by less than the spread. The honest reading is that the duplicate
opens were removed (deterministic, 0 vs ~1,150) and the load-time effect is **below this machine's
noise floor**, not that the fix halved anything.

**One more tool bug this pass found and fixed.** `marks.allMeshes` — the bench's stop condition —
was only checked in `PartsController.sync`, which runs on a store change; the last batch to arrive
does not necessarily change the store, so the mark sometimes never fired and
`measure_browser_load.py` reported `all_meshes_ms: null`. It is now stamped where the count crosses
the threshold (`markComplete`), which is what made the before/after above measurable at all. The
mark moved no work: it is a timestamp, set on the same event.

**Reproduce the profile.** The benchmark project is
`python tools/bench_scene.py build --project <folder.stokes>` (no STEP file needed:
`tests.geometry_fixtures.pin_grid(50, 45)`). Then, against that folder:

| Question | Tool |
|---|---|
| What does the page see, and how much is the server? | `python tools/measure_browser_load.py --project <folder.stokes> --runs 2` |
| Which phase of a cold batch request is slow? | `python tools/bench_batch_server.py --project <folder.stokes> --instrument` |
| Is it the file open or the decode? | `python tools/bench_cache_read.py --project <folder.stokes>` |
| Do two threads read the same part twice? | `python tools/bench_read_timeline.py --project <folder.stokes>` |
| What would a single-handle cache be worth? | `python tools/bench_single_cache.py --project <folder.stokes>` |
| Does the fix hold at unit level? | `python tools/check_cache_claim.py` (PASS: 3/3) |

The one-read-per-part guarantee that `check_cache_claim.py` covers is also asserted as three pytest
tests in `tests/test_geometry_api.py`
(`test_two_threads_wanting_one_part_read_its_cache_once`,
`test_a_claim_on_one_part_does_not_block_another`, `test_a_missing_cache_is_not_a_cached_failure`),
because a concurrency guarantee that lives only in a scratch script regresses unnoticed. Removing the
claim from `_load_cache` makes the first of them fail with **8 reads instead of 1**, so it is a real
guard and not a tautology.

`bench_batch_server.py` and the two "what if" tools patch the server **inside its own process**
(behind `--instrument` / a mode flag), so they measure the shipped path rather than a copy of it,
and none of them ship in the app.

**Fixed: `MAX_BATCHES` 2 → 4.** Same machine, same benchmark, two runs each, from the page's own
`window.__stokesLoad` marks:

| Runs | window, tree → last mesh | server wait, summed | batches |
|---|---|---|---|
| before, 2 in flight | **23,467 ms** · **18,364 ms** | 40,339 ms · 32,316 ms | 18 · 20 |
| after, 4 in flight | **11,102 ms** · **12,797 ms** | 34,940 ms · 40,874 ms | 18 · 21 |

The summed wait barely moves — the server's total work is the same — while the **wall clock roughly
halves**, because four replies now overlap instead of two. Four in flight is also the configuration
the transfer bench measured, so the two tools agree. The 4,500-part figure is still 11 to 13 s rather
than the sub-second the warm path suggests. **What that floor is, is now measured**: it is the one
`open()` per part on the cold path, which is 84 % of the server's cold work, and it does not
parallelise (see the profile below). The decode itself is only 11 %.

Two real fixes came out of the attribution and are in:

- `SceneManager.flushContent` no longer calls `onContent` per part: the store is told what is drawn
  when the picture stops changing instead of once per part, which was a React update plus a
  `syncParts` pass over every part for each of 4,500 arrivals.
- `setGeometry` indexed the drawn parts once instead of searching the growing list per picked body,
  which was quadratic in the part count — it runs on every mesh that arrives.

Neither moved the wall clock, which is consistent with the corrected finding: the load's time is not
in the per-part work those touch. They are kept because both are a real complexity reduction, not
because they were the cause.

### The 4,500-part viewport, on the owner's GPU (T-089)

> **T-108 note — do not cite these figures as reproducible.** They were taken in one run on one
> machine (the owner's Intel Iris Xe laptop, 2026-10-06) and the 55 MB fixture they may have used
> lived in a gitignored `.review/`, so a clean clone cannot rebuild the exact scene. `bench_scene.py`
> builds its own fixture (`pin_grid(50, 45)`), which makes the *shape* of the run reproducible but
> not the numbers: GPU model, driver, Chrome version and machine load all move them, and the p95
> readings taken through `requestAnimationFrame` deltas are additionally capped by the display when
> it holds 33.3 ms. Re-measure on the target machine before quoting any figure below; the
> conclusions drawn from the run (raycaster over id buffer, the O(n²) and per-triangle tint costs)
> are what the section is for and are what T-095 acted on.

Measured 2026-10-06 with `tools/bench_scene.py measure` on the benchmark of `docs/HANDOVER.md` §5b
item 1: `tests.geometry_fixtures.pin_grid(50, 45)` — **4,500 solids, 20,250 faces, 585,076
triangles**, one batched draw of 4,500 instances in 12 draw calls. The tool builds the project
itself (`bench_scene.py build`, no STEP needed) and drives it in headless Chrome 154.

**The renderer is the machine's real GPU, not software**: `ANGLE (Intel, Intel(R) Iris(R) Xe
Graphics (0x0000A7A1) Direct3D11 vs_5_0 ps_5_0, D3D11)`, read back from the WebGL context, with
`EXT_disjoint_timer_query_webgl2` for the frame timings. This is an integrated laptop GPU, so the
figures are a floor for the discrete-GPU budget, not a verdict on it.

| Figure | Value | Notes |
|---|---|---|
| Load, 4,500 parts | 36,547 ms to all parts present (probe 4,427 ms) | dominated by the transport of 585k triangles, unchanged by T-032 |
| Still frame, GPU timer | p50 **12.76 ms**, p95 14.37 ms | meets the 30 fps reduced-quality budget (33 ms); misses the 60 fps discrete-GPU budget (16.7 ms) |
| `perf().frameCostMs` | 4.56 ms, 2.58 ms after an orbit | three's own estimate of the frame it draws |
| Hover pick (raycaster) | p50 5.0 ms, p95 21.6 ms | feeds the face tint, so it stays on the raycaster |
| Click, raycaster | press p50 2.5 ms; **release p50 10.1 ms**, p95 77.0 ms | |
| Click, id buffer | press p50 3.1 ms; **release p50 9.7 ms, p95 307.6 ms** | the `idReadMs` samples are 47.1, 5.3, 1.9, 2.7, 2.2 ms; the first pays the pass the last camera move dirtied |
| Box, face mode (2,128 faces) | release p50 **33.2 ms** | within the 50 ms budget |
| Lasso, body mode (1,158 bodies) | release p50 **0.2 ms** | |
| Lasso, face mode (4,890 faces) | release p50 **0.2 ms** | |
| Box, body mode (3,625 bodies) | release p50 **665 ms** | **failed the 50 ms budget**; fixed by T-095 — see below |

**The box-select budget, fixed (T-095): the body box came down from 626 ms to 26 ms.**

The 50 ms budget above is met. The two things that were wrong, in the order they mattered:

1. **An O(n²) scan in the viewport effect.** `Viewport.tsx` looked for each picked body's patch by
   scanning **all 4,500 patches**: 3,625 × 4,500 is about **16 million iterations** on the release
   path. Replaced with one pass that indexes the patches by part, then a lookup per body. This was
   the single biggest cost and it was invisible in the code.
2. **The tint copied every picked triangle.** `SelectionHighlight` built one world-space geometry for
   a multi-body pick: transform each vertex on the CPU, dedup it through a `Map`, then walk the
   result again for the bounds. A wide body box is 3,624 bodies and **565,992 triangles** —
   **417 to 458 ms of the release** on its own. The bodies already have world matrices, so the tint
   is now one `InstancedMesh` per group of picks sharing a geometry and a face filter: one geometry,
   one matrix per body, no vertex touched. Also cheaper: the signature is a rolling hash instead of
   joining 3,624 names, and a repeated gesture reuses the instance buffers.

| | Before (T-089) | After (T-095) |
|---|---|---|
| Body box, 3,624 picks, 565,992 triangles | 556–626 ms | **26.4, 26.8, 19.0, 24.0, 26.8 ms** (five consecutive runs) |
| Face box, 2,128 picks | 33–91 ms | 17.2 ms |
| Lasso, body (1,158) and face (4,890) | 0.2 ms | 0.2 ms |

The release figure is what the budget is about, and it is the whole event handler: read-back 4–8 ms
plus the pick and the store update. The overlay rebuild itself is a further **50–80 ms**, but it runs
in the **frame after** the release (a React effect), so it is not release latency; `bench_scene.py`
reports it separately and stamps `selection_fresh` so a stale figure can never be read as this
release's. Bringing that later frame down too is open, and would mean cutting the per-instance
matrix upload, which is now the only per-body work left.

Everything that follows is the T-089 run, kept for the before/after.

**Two conclusions the tool exists to make.**

1. **The id buffer has not earned the click path.** At p50 the two are the same (9.7 against
   10.1 ms); at p95 the id buffer is four times worse (307.6 against 77.0 ms), and its first
   read-back pays 47 ms. `idPickEnabled` therefore stays **off by default** and hover stays on the
   raycaster. Making the id path pay off needs the read-back cost down, not the raycaster cost up.
2. **A wide body box over the 4,500-part grid is 588 to 1,262 ms, and it scales with the number of
   bodies picked**, not with the box's area: measured at four widths on the same model,

   | Box width (fraction of the canvas) | Bodies picked | Release p50 (ms) |
   |---|---|---|
   | 0.30 | 0 | 3.1 |
   | 0.50 | 625 | 56.3 |
   | 0.70 | 2,561 | 200.6 |
   | 0.94 | 3,625 | 588.6 |

   The lasso picking 1,158 bodies at 0.2 ms rules out the read-back as the cost: the lasso's mask
   covers *more* pixels than the box's. The cost is what a body box leaves behind — 3,625 picked
   bodies in the store and their selection outlines — and it grows faster than linearly across
   those four points. That is a real hitch on a full-canvas drag, and it is the one M1 budget this
   benchmark fails. It is not fixed here: it belongs to the selection-overlay path, and the four
   numbers above are the evidence a fix has to move.

**Reproduce.** `python tools/bench_scene.py build --project <folder.stokes>` then
`python tools/bench_scene.py measure --project <folder.stokes> --json gpu.json`. The renderer line it
prints is the precondition: a line naming SwiftShader is a regression baseline only, never the
budget. `--wide` varies the body box for the series above.

### Box and lasso selection over the id buffer (T-032)

Measured 2026-10-05 in **headless Chrome 152 with SwiftShader software WebGL** (Intel Iris Xe through
ANGLE/D3D11 is the machine's real GPU and was *not* used), driven over the DevTools protocol by a
throwaway script outside the repository. The project is a `tests/geometry_fixtures.pin_grid(11, 11)`
(121 solids) imported through `stokes geometry import`, plus the sample part: 242 parts, no face-id
limit reached, `GL_ANGLE_multi_draw` absent (three's `_gl_DrawID` fallback). The canvas is
1246 × 441 CSS pixels at a device pixel ratio of 1. The figure is the id pass, the read-back of the
gesture's rectangle, the mask and the `pickMany` update, timed in the page around the `pointerup`.

| Gesture | Parts/faces picked | Gesture end (ms, software WebGL) | First pass, shader compile included (ms) |
|---|---|---|---|
| Box, body mode, whole canvas | 211 to 212 bodies | 19.2 and 21.1 in two runs (4.0 once, with the view unchanged) | 113.8, 209.2 |
| Box, face mode, whole canvas | 453 to 463 faces on 211 to 212 parts | 26.9 and 34.5 | |
| One pixel through the probe (`idPick`) | correct part and face | 12.6 to 19.2 after the first pass | |

- **Superseded 2026-10-06 by T-089.** The 4,500-part benchmark named below as missing was then
  built and measured on the owner's real GPU; see "The 4,500-part viewport, on the owner's GPU"
  above. What that run establishes, and what it still leaves open:
  - The box, the lasso and the id-buffer/raycaster comparison ARE now measured at 4,500 parts, so
    "under 50 ms after release" has a verdict: the face box and both lassos pass, and the wide body
    box **fails** at 588 to 1,262 ms.
  - Orbit p95 **before and after** the per-vertex `faceId` attribute was still not paired on the
    same build, so "orbit p95 unchanged" remains unconfirmed; what exists is the absolute p50 of
    12.76 ms and p95 of 14.37 ms on the current build, on a GPU that is integrated.
  - The paragraph below records what the earlier software-WebGL runs did establish, which is
    unchanged.
- **Not measured, and required by the task's "Done means".** The 4,500-part benchmark of
  `docs/HANDOVER.md` §5b item 1 is not in this worktree and was not built; the box and the lasso
  were not timed across it, so "under 50 ms after release" is unconfirmed. Orbit p95 before and after
  the per-vertex `faceId` attribute (+2 bytes a vertex, four attributes a geometry) was not taken, so
  "orbit p95 unchanged" is unconfirmed — the projects here are 242 parts, below the batching
  threshold's realistic load, and SwiftShader's frame times say nothing about the real GPU. Neither
  was the comparison the brief asks for between the id buffer and the raycaster for hover and click:
  `idPickEnabled` is a probe flag that moves **click** picks onto the buffer and is off by default,
  and the hover path still runs on the raycaster, because the hover pick also feeds the face tint,
  the paint stroke and the `Alt` + wheel candidate cycle, which need a triangle rather than a face
  id. The lasso path (the freehand drag and its point-in-polygon mask) was exercised only through its
  unit test, not in the browser.
- What *is* established by the runs: the id pass renders and its material compiles on a WebGL2
  context with no `GL_ANGLE_multi_draw` (three's `_gl_DrawID` fallback); the part index in R,G and
  the face ordinal in B,A survive the read-back, and the ordinal maps back to a transport face id for
  every one of the 453 and 463 faces picked; hiding one patch with `H` took exactly that part out of
  the pass (212 bodies before, 211 after, the missing one named). Isolation reaches the pass through
  the same `shown` flag as hiding; translucent parts are not excluded by the pass either, which draws
  whatever three draws, but that was not exercised in this run.

### Per-body opacity in the viewport (ADR-0021 4.4)

Measured 2026-10-05 on the 4,500-part benchmark of `docs/HANDOVER.md` §5b item 1
(`tests/geometry_fixtures.pin_grid(50, 45)` through `write_plain` and `stokes geometry import`:
4,500 solids, 20,250 faces, 585,076 triangles), on the development laptop's Intel Iris Xe through
ANGLE/D3D11, in headless Chrome 152 at 1264 × 621 CSS pixels and a device pixel ratio of 1.25,
with other agents sharing the machine. A run loads the page, waits for every part and 4 to 6 s more,
and orbits: 240 drag steps, one per animation frame, each frame timed on the GPU timer through the
`?debug=1` probe (`startFrames`, `stopFrames`), nothing selected. The driver (headless Chrome over
the DevTools protocol, synthetic pointer events on the canvas) is not in the repository.
Translucent parts are spread evenly over the tree, all at 0.5, so they share one bucket batch. The
four cases alternate within each of 14 rounds, half of them in reverse order. A figure is the median
over the rounds of each run's median or p95; "paired" is the median over the rounds of the
difference to "all opaque" in the same round. The still frame is the median GPU time of 60 forced
frames of the view after loading (`measureRender`, 7 rounds).

| Case | Draw calls | Triangles | Orbit median (ms) | Orbit p95 (ms) | p95 change, paired (ms) | Still frame (ms) |
|---|---|---|---|---|---|---|
| All opaque, before T-023 | 12 | 585,076 | 11.5 | 18.0 | -0.6 | 11.0 |
| All opaque | 12 | 585,076 | 11.9 | 18.8 | | 11.2 |
| 50 parts at 0.5 | 14 | 597,476 | 12.0 | 18.4 | -0.2 | 11.8 |
| 500 parts at 0.5 | 14 | 650,076 | 12.1 | 19.1 | -0.9 | 13.2 |

- The p95 of one run moves by up to 5 ms from run to run on this machine (the GPU changes clock
  states; the still frames fall into groups near 9 ms and 11 to 13 ms), so differences under about
  1 ms are not resolved. 50 translucent parts stay within the +1 ms p95 of ADR-0021 4.4. 500 cost
  about 0.5 ms of orbit median and 1 to 2 ms of still frame, since they hide nothing behind them and
  are blended; their p95 change is within the noise.
- Nothing translucent, before against after, over all 36 pairs of runs of the day (the 14 rounds
  above and 22 pairs from runs that alternated the builds alone): orbit median 11.6 and 11.9 ms, p95
  17.8 and 17.9 ms, each +0.1 ms paired; still frame 11.6 and 11.2 ms (29 pairs). Both draw the
  same 12 calls and 585,076 triangles, and `python -m stokes.app --screenshot` of the sample
  project is pixel-identical before and after.
- A bucket batch adds two draw calls, and its parts' triangles are drawn twice: three draws a
  transparent double-sided material back faces first, then front faces.
- These frame times are higher than ADR-0019's 10.6 / 16.6 ms on the same benchmark, before T-023
  as well as after: compare the rows of one table, not figures from different days.
- The first opacity change after a load held its frame for 711 ms while the translucent programs
  compiled at first use. Compiled in the background after the load (`warmGlass` in
  `web/src/viewport/scene.ts`), that frame takes 66 ms once; a further bucket takes a 13 to 18 ms
  frame, and the first selection of a translucent part 51 ms, where the first selection of an
  opaque one takes 15 to 48 ms.

### Import and cold start at 4,500 parts (2026-11-09)

The 4,500-solid pin grid (`tests.geometry_fixtures.pin_grid(50, 45)`) written as one STEP file: 52 MB,
20,250 faces. Windows host, 24 logical CPUs, nothing else running. `geometry.import` with
`wait=True`, milestones from the import events.

| Stage | Before | After |
|---|---|---|
| Read (OCCT parse 4.9 s, translate 17 s, walk 0.4 s) | 26.4 s | 26.5 s |
| First coarse mesh after the read | 0.0 s | 0.2 s |
| Coarse pass, last part | 47.5 s | 34 s |
| Fine pass, last part | 74.9 s | 37 to 39 s |
| Import finished | 75.1 s | 39.8 to 42.8 s |
| Cold open of the saved project | 49.5 s | 1.4 to 1.6 s |

What the time was, and what changed:

- **The tessellation was serial.** In one process the two passes cost 11.2 s and 10.5 s of
  `BRepMesh_IncrementalMesh`. OCCT's own parallel flag gives only 1.27x on many small solids (12.5 s
  to 9.8 s for one pass on 24 CPUs), so the cores sat idle. Helper processes
  (`stokes/geometry/helpers.py`) mesh independent batches: both passes take 4.7 to 6.4 s. Handing a
  batch over is cheap: 0.35 s to write all 4,500 solids as binary B-reps, 0.8 s to read them back.
- **Helpers start before the read**, so their OCP load overlaps the 26 s read.
- **A Windows pipe holds 4 KB.** A request listing hundreds of solids blocked the worker until a
  helper read it, which delayed the first reply by 3.4 s. The descriptions go in a file; requests are
  tiny.
- **Writing 4,500 small cache files is the pathological case on this file system**: 13.2 s on one
  thread, 7.7 s on four, 21.7 s on eight, 37.2 s on thirty-two. More threads are slower. The import
  now writes them on four threads, off the import thread, and drains them before finishing (tail 1.6
  to 5.4 s, it was 4.7 to 20.5 s on eight).
- **The pack at the end of the import.** One pack of the same 4,500 parts takes 0.06 s to write. An
  import used to leave per-part files only, so the first cold open paid 4,500 file opens (49.5 s;
  2.8 s once the OS had them cached) and only then built the pack. The import now builds the pack
  from the bytes it holds, and the first cold open reads one file: 1.5 s.

What is left: the 26.5 s read is the floor (a single `STEPCAFControl_Reader` pass). Splitting it would
mean reading roots in separate processes, which re-parses the file in each (4.9 s) and needs the
assembly's shared geometry to be resolved in one place; not attempted.

The old figures (84 s in decisions log G4, 168 s on the task list) were measured on another laptop
under load: there the translate step alone took 95 s. Run `tests/perf/` with nothing else running for
comparable numbers.

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

## Streamlines (ADR-0030)

| Budget | Target | Measured (2026-11-04, development laptop) |
|---|---|---|
| 200 lines through a 970,299-cell volume, lines of at most 400 points (the trace only, reader excluded) | < 2 s | 0.4 s |
| The answer of that trace (float32 points and speeds) | < 2 MB | 1.2 MB |

The test is `tests/perf/test_streamlines_perf.py`. The case reader that loads the solved mesh is the same
read the slices and iso-surfaces pay for and is not in the figure.
