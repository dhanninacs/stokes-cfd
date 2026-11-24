# ADR-0019 · Many-part rendering: batched drawing per look, batched mesh transfer, a tree that scales

**Status:** accepted, implemented 2026-10-04 · relies on ADR-0013 (web front end), ADR-0014 (STEP import, one part per solid); picking is decided in ADR-0020, not here

## Context

The M0 gate measurement (2026-10-03, this laptop with an integrated GPU, other agents loading the
CPU) of a 20,250-face STEP of 4,500 solids found an orbit at a median 250 ms per frame (about 4 fps)
against the 60 fps gate. The performance budget assumes a discrete GPU, but the gap is two orders of
magnitude regardless. Loading was as slow: all 4,500 parts appeared after 347 s. The measured
causes:

| Bottleneck | Cause | Measured impact |
|---|---|---|
| Draw calls | One `THREE.Mesh` per solid; each is a separate draw call | ~250 ms per frame at 4,500 meshes |
| Mesh transfer | One `GET /api/geometry/{id}` per part, served serially | ~40 parts/s, 4,500 parts in ~113 s |
| Tree refresh | `node.tree` serialises the whole tree; 1,440 nodes block the event loop | 1.2–1.9 s on every tree change |
| Tree rendering | The tree panel renders every row as a DOM node | not measured at 4,500 parts |
| Hover picking | The CPU `Raycaster` tests every triangle of every visible mesh | grows with the triangle count |

## Decision

1. **Above a part-count threshold the viewport draws parts in batches, grouped by look.** From
   `BATCH_MESH_THRESHOLD` (100) parts on, `SceneManager` draws them through `THREE.BatchedMesh`
   (three.js 0.186), one batch per look, which is the part's kind and not its colour: a batch's
   material is the one `createLook` makes for the kind, on a white base, and each part's colour
   reaches the GPU per instance, converted from sRGB to linear once. Only a kind with a tint of its
   own (the boundary patches) is batched per colour as well. The draw calls follow the number of
   looks, not the number of parts. Below the threshold each part stays its own mesh and nothing
   changes. The switch follows the part count, counting parts only, in both directions, and the
   part that crosses the threshold migrates with the rest. An opaque batch reuses one draw list,
   sorted once the view is still, instead of culling and sorting every instance in JavaScript before
   every pass. Code: `web/src/viewport/batch.ts`, `materials.ts`, `scene.ts`.
2. **A part's colour and visibility are per-instance values** (`setColorAt`, `setVisibleAt`), not a
   rebuild of its geometry. Each part records the key of the batch that owns it (`batchKey`): every
   batch has its own instance-id space, so an id means something only together with its batch. A part
   keeps its own mesh beside the batch's copy, out of the scene while it is batched. **A hovered or
   selected part is drawn by that mesh**, its instance hidden, until its highlight has faded, so the
   highlight and the selection outline look as they do on a part drawn alone. Part meshes and batches
   share one layer, which the contact shadow and the outline's occluders draw, so both cover batches
   and meshes alike. Wireframe uses a wire material per batch; edges mode is one merged line object
   for all batched parts, found a slice at a time. The materials of every kind are compiled in the
   background, so the first hover does not wait for a shader.
3. **A batch is sized for its own parts and grows by filling before it swaps.** A batch starts at what
   its own parts need (reserved per key) or at a small minimum. Removed parts are deleted from their
   batch and the space is compacted when a part would not fit; if that makes no room, the batch is
   rebuilt at twice its size, **filled from the parts' own geometries before the old batch is
   dropped**, so rebuilds happen O(log n) times and the old batch keeps drawing if a fill fails. A
   part that cannot be added costs that one part, not the queue behind it.
4. **Meshes travel in batches.** `GET /api/geometry/batch?ids=…` returns the meshes of the named parts
   in one binary frame (a uint32 count; per part a uint32 id length, the id, a uint32 payload length
   and the payload; `0xffffffff` as the length of a part with no mesh yet). The route caps a request at
   `MAX_BATCH_IDS` (512) ids and answers more with 413; the viewport asks for `BATCH_SIZE` (256) at a
   time, two requests in flight, alongside the single fetches. A part's `requested` revision is set
   before its request goes out, so it is not queued again meanwhile; a batch that fails is re-queued
   as single fetches, so one bad part or a refused request costs no other part; a request whose
   parts have all been removed is aborted. `_batch_frame` in `stokes/server/app.py` and `readBatch` in
   `web/src/api/client.ts` are the two halves of the format; change both together. `GET
   /api/geometry/{id}` stays, for one part and for older clients.
5. **Replies are gzipped, at level 6, for remote clients and not for loopback clients.** Mesh payloads
   are float arrays and compress about nine to one, which a remote browser needs. Over loopback the
   compression costs more than it saves: the 4,500 parts took 1.73 s warm and 13.5 s cold with gzip
   (2.82 MB) and 0.23 s and 7.9 s without (26.2 MB), on a wire that costs nothing, and the desktop
   host always talks over 127.0.0.1. So `stokes/server/app.py` compresses only when the request's
   client host is not in 127.0.0.0/8 or `::1` (`SkipGzipOnLoopback` in front of `GZipMiddleware`);
   the level is set to 6 because Starlette's default of 9 costs 580 ms per 256-part chunk against
   85 ms for a reply 3 % smaller; and `--compress-loopback` (`create_app(compress_loopback=True)`)
   turns compression on for loopback clients, for a browser behind an SSH tunnel, which arrives
   from 127.0.0.1, and for `tools/bench_viewport.py`, whose gzip rows measure it. Decision 5 first
   compressed every reply; amended 2026-10-04, decisions log G8. A benchmark offers `Accept-Encoding:
   gzip` as a browser does, or it measures a transfer the client never makes.
6. **The tree scales with the part count.** Three parts, all built:
   - *Virtualised rows.* `TreePanel` draws only the rows in view plus an overscan; rows have a fixed
     height of 28 px and gaps stand in for the rest, so reveal and stage scrolling work by row index;
     `buildRows` is memoised per node. The selected row, the one being renamed and the one whose menu
     is open stay mounted wherever they are, and every row carries `aria-posinset` and `aria-setsize`.
   - *Node patches in events.* `Session.emit` attaches to a `node.*` event the summary of its node
     plus `parent`, `index`, `also` (the dependents the change reached) and `resync` (there were too
     many to send), built in `stokes/api/summaries.py`; the geometry events that follow a silent
     status change carry them too. The client (`web/src/state/treePatch.ts`, `treeSync.ts`) applies
     the patches with structural sharing, in one store update per animation frame at an adaptive
     cadence, and fetches the whole tree when a patch cannot apply. The whole tree is still fetched
     for the first load, a reconnect, undo and redo, and a project that opens.
   - *A tree that does not hold the loop.* `node.tree` is built in slices of at most 3 ms, with the
     event loop handed back between them (`RpcHub._tree_reply`); `Node.summary()` no longer
     serialises the subtree; per-message WebSocket deflate is off (`stokes/server/runner.py`), as it
     held the loop for up to 120 ms per 2 MB tree.
7. **This ADR does not decide picking.** Hover stays a CPU raycast until ADR-0020's id-buffer pick,
   which is client code and works with the batches through a per-instance id.
8. **The orbit gate is the median and the p95.** The gate "orbit at 60 fps" is met only when the
   frame times of an orbit stay inside the 60 fps budget at the median and at the p95. They are read
   from the GPU timer, not from `requestAnimationFrame` intervals, because this laptop's display caps
   those at 33.3 ms. The transfer and the tree have budgets in `tests/perf/`
   (`test_geometry_transfer.py`, `test_tree_perf.py`); the browser half is read
   from `window.__stokesScene`, a frozen, read-only object with an async `measureRender` and
   `startFrames`/`stopFrames`, which exists only in development and review builds or with `?debug=1`.

## Measured

The 4,500-part benchmark (20,250 faces, 585 k triangles), 2026-10-04: integrated GPU (Intel Iris Xe
through ANGLE/D3D11), other sessions sharing the CPU. "Before" is the state before the pass that
produced the figure: the first batched build for the rows down to the gzip chunk, the tree as it
was for the rows after.

| | before | now |
|---|---|---|
| CPU per frame, at rest / with a selection | 9.3 / 18 ms | 1.7 / 2.6 ms |
| Orbit frames on the GPU timer, median / p95 | 16.7 / 117 ms as a frame interval under load (250 ms median before batching) | **10.6 / 16.6 ms**: the 60 fps gate is met. The harness interval, which the display caps, reads 16.7 / 33.2 ms |
| Edges mode, GPU per frame | 80 ms median, incomplete | 22.6 ms, complete |
| Wireframe, GPU per frame | not measured | 113 ms for 1.75 M lines |
| Edge finding | `EdgesGeometry` | 4 to 13 times faster, identical output |
| First hover | a 700 ms freeze while its shaders compiled | none: compiled in the background |
| Mesh transfer, all parts, gzip off (loopback) | | 7.9 s cold, 0.23 s warm, 26.2 MB |
| Mesh transfer, all parts, gzip level 6 (remote) | | 13.5 s cold, 1.73 s warm, 2.82 MB |
| Gzip per 256-part chunk, level 6 / level 9 | | 85 ms / 580 ms, the reply 3 % larger at level 6 |
| `node.tree`, 4,514 nodes | 38 to 149 ms | 9 to 25 ms |
| Longest event-loop stall | 169 to 269 ms | 30 to 45 ms |
| Expanding the 4,500-part group | 9.3 s | 60 ms |
| Tree rows in the DOM | 4,513 | 26 |
| Store updates while a project opens | about 4,500 | 245 |
| The viewport's parts sync while a project opens | 16 s | 0.67 s |
| 4,500 `node.changed` events to a current screen | 10.4 s | 2.3 s |

The cold transfer is bound by the disk, at about 4 ms per cached mesh file; one packed cache file
per project would remove it (HANDOVER §5b item 1).

## Alternatives considered

| Alternative | Why not |
|---|---|
| An instanced mesh per material | Needs a fixed palette, and instances repeat one geometry; `BatchedMesh` takes a different geometry per part, which is what a STEP assembly is. |
| Server-side rendering (VTK streamed frames) | ADR-0013 reserves it for scenes beyond about 10 M triangles; CAD tessellations fit in client memory and keep the GPU's interactivity. Switch only when the triangle count outgrows the client. |
| glTF export and the three.js loaders | Rejected in ADR-0014: two conversions, no face ids, a second format. |
| Keep the CPU raycaster and add a bounding-volume hierarchy | Fewer triangle tests per mesh, but every test stays on the CPU. ADR-0020 puts picking on the GPU. |
| WebSocket frames for the meshes | REST is simpler for a first pass; WebSocket fits progressive import better. Revisit if the batch route becomes the limit. |
| Gzip every reply, as decision 5 first did | Over loopback it made the warm transfer 1.73 s instead of 0.23 s, to save bytes on a wire that costs nothing, and the desktop host is always on loopback. Kept for remote clients. |

## Consequences

- Above the threshold the draw-call count no longer depends on the part count. The orbit of the
  4,500-part benchmark meets the 60 fps gate on the GPU timer, at the median and at the p95, on an
  integrated GPU where the budget assumes a discrete one. The p95 of 16.6 ms is a hair inside the
  16.7 ms budget, so a regression shows first there.
- A batched part is drawn in the material its kind has when drawn alone, with its colour per
  instance, and is highlighted by its own mesh, so it looks as it does on its own.
- The scene keeps each part's own geometry beside the batch's copy, which rebuilds and picking need,
  so the client's geometry memory is about doubled.
- The batch route adds a second geometry route and a wire format that two files must keep in step.
- A browser behind an SSH tunnel arrives from 127.0.0.1 and is not compressed unless the server runs
  with `--compress-loopback`; the server binds to loopback only, so today a client counts as remote
  only behind a proxy that forwards the client's address.
- Open work: one packed mesh cache per project (the cold load); events are still one per WebSocket
  message, and batching them per tick needs `web/src/api/client.ts`; the viewport's parts sync is
  O(parts) per update (3 to 5 ms at 4,500) and `project.info` walks the tree (2 to 3 ms); undo, redo
  and a reconnect still fetch the whole tree; and the orbit and frame figures come from a browser
  driver that is not in the repository.
