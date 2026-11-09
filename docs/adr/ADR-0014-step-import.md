# ADR-0014 · STEP import through OCP in a worker, served as versioned binary meshes

**Status:** accepted · 2026-10-02 · relies on ADR-0003 (CAD kernel), ADR-0010 (API first), ADR-0013 (web front end)

## Context

M0's gate is "open a 20 k-face STEP and orbit at 60 fps". The CAD kernel is
Open CASCADE through the `cadquery-ocp` wheel (ADR-0003; installed, 8.0.1,
Apache-2.0 wrapper around LGPL-2.1-with-exception OCCT, notice required).
Importing OCP costs about a second and tessellating a real part can take
many seconds, so neither may run on the thread that answers the API, and
`stokes.api` may not import OCP at all (forbidden-imports gate). The viewport
already consumes a packed binary mesh transport (version 1: one JSON header,
then positions, normals and indices per group) and draws one mesh per group,
so faces must not become groups: a 20 k-face part would be 20 k draw calls.

## Decision

1. **`stokes.geometry.step` wraps OCP lazily.** `read_step(path)` returns the
   solids with their STEP names and colours (XCAF reader, falling back to
   `solid_1`, …); `tessellate(solid, linear, angular)` returns a `TriMesh`
   whose triangles carry a face id. Deflection is relative to the bounding
   box diagonal: a coarse pass (2·10⁻³ of the diagonal) for first display,
   then a fine pass (5·10⁻⁴) that replaces it.
2. **Import runs in a worker process** (`stokes.geometry.worker`,
   `multiprocessing` with the spawn method) that streams one solid at a time
   over a pipe. The session emits `geometry.import_progress` events (solid
   name, index, count, triangles) and `geometry.part_ready` per solid, so
   parts appear progressively. The import is cancellable and an OCP crash
   kills only the worker.
3. **A `part` node type** is added to the model: parameters `source`
   (path), `units`, `triangles`, `faces`, `bbox`, `color`, `visible`. Parts
   live under `/geometry/parts/<name>`. Faces are addressed by id inside the
   mesh, not as nodes; named selections over faces are M1's job.
4. **Meshes live beside the project, not in it.** The session keeps
   `node_id → PartMesh` in memory and caches each as
   `<name>.stokes/cache/geometry/<node_id>.mesh` (the transport bytes), so a
   reopened project shows its parts at once and re-imports only when the
   cache is missing or the source is newer. `project.json` stays small.
5. **Transport version 2**: each group may carry a `faceIds` buffer (u32 per
   triangle) after its indices, and the header carries `bbox` and `units`.
   The front-end parser accepts versions 1 and 2. `/api/geometry/{node_id}`
   serves one part; `/api/geometry/demo` keeps serving the sample part.
6. **API**: `geometry.import_step(path, name?)` (mutating, undoable: undo
   removes the part node and its mesh), `geometry.list`, `geometry.info
   (node)`, `geometry.cancel_import`. CLI: `stokes geometry import
   part.step`. The command fails with a clear message when the `cad` extra is
   not installed.
7. **UI**: *Import STEP…* in the project menu and the Geometry stage, with
   the host's native file picker; the viewport loads every `part` node by id
   on `node.added` and `geometry.part_ready`, one draw call per solid; a
   hovered or selected face is highlighted by building a small overlay mesh
   from that face's triangles, found through the face-id buffer.
8. **Tests** generate their own fixtures with OCP (a box, a cylinder, a
   two-solid assembly with names) in a temporary folder: solid and face
   counts, face ids, names, worker cancellation, transport round trip in both
   versions, and a perf test that tessellates a synthetic 20 k-face part
   within the budget in `tests/perf`.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Tessellate in the server process | Blocks the API for seconds and an OCCT fault takes the whole application down. |
| One group per face | Simple picking, but 20 k draw calls miss the 60 fps gate by an order of magnitude. |
| Export glTF from OCP and load it with three.js loaders | Two conversions, no face ids, and a second mesh format to maintain next to the mesh transport. |
| pythonocc-core | Conda-only distribution; rejected in ADR-0003. |

## Consequences

- `stokes.geometry` grows `step.py`, `worker.py`, `mesh.py` (TriMesh with
  face ids), `cache.py`, `transport.py` (the v1/v2 codec, re-exported by
  `stokes.server.geometry`, because the store and the CLI write caches without
  the server), `store.py` and `files.py`; `geometry.import_step` has a `wait`
  flag and `geometry.prepare` pre-starts a worker;
  `web/src/viewport/geometry.ts` reads both versions and `scene.ts` loads
  parts by id.
- The OCCT notice joins the third-party notices; `cadquery-ocp` stays an
  optional extra so the headless server installs without it.
- The sample part becomes replaceable by real geometry; the M0 gate can be
  measured.
