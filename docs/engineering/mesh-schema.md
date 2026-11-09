# Meshing: settings, results and patches

**Format 1** · ADR-0024 (draft) · node types: `stokes/mesh/model.py` · catalogue:
`stokes/mesh/catalogue.py` · errors: `stokes/mesh/errors.py` · commands:
`stokes/api/commands/mesh.py` · tests: `tests/test_mesh_model.py`, `tests/test_mesh_commands.py`

Three node types under the existing structural group `/mesh`. This document is the contract for the
Python core, the agent, the mesh panel and the viewport.

**Status.** T-143 (T-205) built the node types, the catalogue, the errors and `mesh.meshers`,
`mesh.configure`, `mesh.list`. Generation is **T-145b**; snappy's refinements and layers are
T-151/T-152; patch transport is T-147b. ADR-0024 is a **draft**; the owner accepted the subset the
e2e plan names (`PLAN.md` §C), which covers everything here.

All values in parameters, events and results are **SI**: quantities are `QuantityValue` in metres, and
arrays are SI numbers.

## Nodes

```
/mesh                            group, part of the skeleton (cannot be removed or renamed)
  settings                       mesh_settings, id stable_id("n", "/mesh/settings")
  results                        group, made by mesh.generate (T-145b)
    <mesher>                     mesh_result, id recorded by mesh.generate; one per mesher (ADR-0034)
      <patch>                    mesh_patch, made by T-147b
```

### `mesh_settings` — how the mesh is built

| name | kind | default | notes |
|---|---|---|---|
| `mesher` | enum `blockmesh, snappy, cfmesh, gmsh` | `blockmesh` | |
| `host` | string, `[a-z0-9][a-z0-9_.-]{0,62}` | `local` | a host name (T-144c) |
| `base_size` | quantity, length | `0.005` m | the background cell size |
| `surface_size` | quantity, length | `0.0` m | snappy's surface size; **0 means the base size** |
| `domain` | enum `part, box` | `part` | `region` arrives with snappy (T-150) |
| `domain_part` | ref `part` | null | **a ref**: see "Two kinds of reference" |
| `box` | array, length | `[]` | six numbers `xmin ymin zmin xmax ymax zmax`, SI |
| `margin` | quantity, length | `0.0` m | added on every side of the domain |
| `max_cells` | int | `5000000` | |
| `cores` | int | `1` | |
| `max_non_ortho` | number | `70.0` | checkMesh threshold, degrees (T-146) |
| `max_skewness` | number | `4.0` | checkMesh threshold (T-146) |
| `accept_unassigned` | bool | `false` | faces without a boundary become a wall instead of refusing |
| `layers` | int `0..10` | `0` | default boundary-layer count on every wall; 0 off (Q4: off) |
| `layer_mode` | enum `final_ratio, first_height` | `final_ratio` | how thickness is given. **One mode per project**: snappy's `relativeSizes` is a single bool, so relative and absolute cannot be mixed per patch |
| `expansion_ratio` | number `1.0..2.0` | `1.2` | growth from one layer to the next |
| `final_ratio` | number `0.05..1` | `0.3` | `final_ratio` mode: the last layer's thickness as a fraction of the local cell size |
| `first_layer_height` | quantity, length | `0.0` m | `first_height` mode: the first layer's thickness |
| `min_thickness_ratio` | number `0.01..1` | `0.1` | below this a layer is not added. In `first_height` mode the writer scales it by the first layer height, because with `relativeSizes false` snappy reads `minThickness` as a length and the raw ratio would put a metre-scale floor on a millimetre part |
| `layer_feature_angle` | number `0..180` | `100.0` | layers stop growing round sharper corners (degrees). 100 is 9 of the 15 OF14 tutorials' value (VERIFY evidence in `tests/fixtures/of14/inventory-thinkyboy.md`) |
| `feature_angle` | number `0..180` exclusive | `30.0` | edges sharper than this are kept by the mesher. Reaches both dictionaries: `resolveFeatureAngle` = the angle, and `surfaceFeaturesDict`'s `includedAngle` = 180 − the angle (the utility measures the angle BETWEEN faces). At 30 both are byte-identical to the pre-setting writer. In the `surface` sub-basis |

Ranges are enforced by the parameters' `validate` hooks (`ParamSpec` has no min/max fields).

**Layer counts resolve `settings → body → boundary`** (the 2026-10-19 1130 decision): the
settings' count is every wall's default; `part.layers` (int, **no default** — empty = inherit,
`0` = none, `n` = n) overrides it for a body's walls; `boundary.layers` (same shapes) overrides
for one wall. Symmetry and every non-wall kind never inherit; `unassigned` (the part's untagged
skin, which the driver makes a wall) inherits like any wall. The resolver is
`stokes.mesh.layers.effective_layers`, pure over the tree; an override on a non-wall is refused
at the snapshot ("layers need a wall; inlet is an inlet"). A stored `0` written while the
withdrawn 2026-10-18 `part.layers` ran (its default 0 meant INHERIT) is dropped by the persist
v2→v3 migration, so old projects keep their meaning.

**Surface sizes resolve `boundary → body → settings → rule`** (brief M-503): a boundary's
`mesh_size` (quantity, length, **no default** — empty = inherit, and null clears it) wins over
the domain body's `part.mesh_size`, which wins over the settings' `surface_size` (only when
> 0), which wins over the rule — a wall gets half the base size, any other face the base size.
`surface_size` 0 (the default) keeps the rule, so an untouched project is byte-identical. The
resolver is `stokes.mesh.sizing.surface_target`, the one function the snapshot and the Sizing
page's placeholders share; a negative size is refused at the snapshot, naming the boundary. The
estimate behind the Sizing page (`mesh.estimate`, pure `stokes.mesh.estimate`) is calibrated on
this install — four runs recorded in `tests/fixtures/of14/inventory-thinkyboy.md` and in the
module docstring, band ±65 % after one run fell outside ±50 %.

Sizes are **lengths, never snappy "levels"**: a level is a resolution relative to a background mesh and
cannot be converted back to a length without knowing that mesh, so a level's meaning changes when
someone edits a different field.

### `mesh_result` — one meshing job

| name | kind | default |
|---|---|---|
| `mesher` | enum as above | `blockmesh` |
| `host` | string, same pattern | `local` |
| `basis` | string, `[0-9a-f]{0,40}` | `""` |
| `state` | enum (below) | `queued` |
| `job` | string, `(j_[0-9a-f]{12})?` | `""` |
| `cells`, `points`, `faces` | int | `0` |
| `quality` | json, max 65536 bytes | `{}` |
| `ok` | bool | `false` |
| `seconds` | number | `0.0` |
| `error` | string | `""` |
| `active` | bool | `false` |

**One result per mesher is kept** (ADR-0034). `active` marks the one in use: the mesh the viewport
shows and a run solves on, chosen with `mesh.use`. With none marked, the latest is in use, which is
how a project saved before ADR-0034 reads.

`state` is `queued | running | cancelling | done | failed | cancelled | interrupted | skipped`. Two of
those carry meaning the name does not: **`interrupted`** is a job the agent stopped reporting — a
server restart, a killed process — which is not the same as `failed`, because the work may have been
fine and only the reporting stopped; **`skipped`** is a `mesh.generate` replay that found no host, which
must never be `failed` and must never stop a replay.

### `mesh_patch` — one patch of a result

| name | kind | default |
|---|---|---|
| `index` | int | `0` |
| `patch_type` | string | `patch` |
| `faces`, `triangles` | int | `0` |
| `tag_id` | string, `([a-z]{1,4}_[0-9a-f]{8})?` | `""` |
| `visible` | bool | `true` |
| `color` | string, `#rrggbb` | `#9aa3ad` |
| `opacity` | number | `1.0` |

## Two kinds of reference, and why they differ

`domain_part` **is a ref**, so `node.remove` on the part it names raises `model.referenced` until the
settings stop pointing at it. That is intended: settings naming a part that no longer exists would mesh
nothing, and failing at the delete is where the user can still act.

`mesh_patch.tag_id` **is a string, not a ref**. A derived result must never block an edit: an old patch
remembering a tag that was since deleted is a stale colour, not a broken project, and a ref would make
`node.remove` on a tag refuse because a result nobody is looking at still names it.

## Commands

| command | params | mutates | returns |
|---|---|---|---|
| `mesh.meshers` | none | no (`requires_project=False`) | `{meshers: [{id,label,built,domains,why}], schema}` |
| `mesh.configure` | `mesher?`, `values?`, `node_id?` | yes | `{settings, created}` |
| `mesh.list` | none | no | `{settings, results: [{id,path,name,state,status,cells,ok,mesher,host}]}` |

There is deliberately **no** `mesh.set` (`parameter.set` edits any parameter), **no** `mesh.delete`
(`node.remove` removes any node), and **no** `mesh.generate` here (T-145b).

### `mesh.meshers`

```json
{"meshers": [{"id": "blockmesh", "label": "blockMesh", "built": true,
              "domains": ["part", "box"], "why": ""},
             {"id": "snappy", "label": "snappyHexMesh", "built": false,
              "domains": ["part"], "why": "snappyHexMesh arrives in T-150"}],
 "schema": {"type": "object", "properties": {"mesher": {"enum": [...]}}, ...}}
```

An **unbuilt mesher is listed rather than hidden**, with its reason: a user who cannot find cfMesh
assumes Stokes forgot it, while one who reads "cfMesh waits for its OpenFOAM 14 build gate" knows where
it stands.

### `mesh.configure`

```json
{"mesher": "blockmesh",
 "values": {"base_size": "2 [mm]", "domain": "box",
            "box": ["0 [mm]", "0 [mm]", "0 [mm]", "10 [mm]", "20 [mm]", "30 [mm]"]}}
```

```json
{"settings": {"id": "n_44373f58", "type": "mesh_settings", "name": "settings", "status": "ok",
              "params": {"base_size": {"value": 0.002, "unit": "m"}, "cores": 4, ...},
              "path": "/mesh/settings", "children_count": 0},
 "created": true}
```

The reply is shaped like `node.get`'s: parameters under `params`, as `node.summary()` puts them.

Order of work: the mesher must be **built**; every key of `values` must be a `mesh_settings` parameter;
`box` must be six numbers with a positive extent on each axis; `base_size` must be positive and
`surface_size` non-negative. Everything is checked **before** anything is pushed, so a refusal leaves
the tree exactly as it was. The whole change is **one undo step** (`Composite`), and creating the node
is one `node.added` followed by one `node.changed` per parameter set.

### `mesh.list`

```json
{"settings": {"id": "n_44373f58", "params": {"cores": 2}, "path": "/mesh/settings"},
 "results": [{"id": "n_9f2c", "path": "/mesh/results/blockmesh", "name": "blockmesh",
              "state": "interrupted", "status": "Interrupted: the agent stopped reporting this job",
              "cells": 4200, "ok": false, "mesher": "blockmesh", "host": "local"}]}
```

A `stale` flag is **not** here: T-145b adds it when there is a basis to compare against, and a flag that
cannot yet be computed always says false.

## Error codes

| code | class | when |
|---|---|---|
| `mesh.error` | `MeshError` | the base for all of these |
| `mesh.invalid` | `MeshInvalid` | an unknown mesher; a bad box; a size that is not positive; `mesher` given in `values` |
| `mesh.not_built` | `MeshNotBuilt` | the chosen mesher has no driver; the message carries the catalogue's reason |
| `mesh.no_host` | `NoHost` | the named host is unreachable or has no OpenFOAM (T-144c) |
| `mesh.busy` | `MeshBusy` | a job is already running for this project (T-144a) |

## Settings edits are undo steps; results are not

A settings edit is something the user did, so it is undoable. A **result is written by a job**, so it is
not an undo step: undoing past a completed meshing run would leave the settings that produced it and a
tree that no longer mentions the mesh, and the agent's job would still be running. M2 keeps **one**
result per mesher, the latest.

## Hosts

Where a mesh is built, and what that machine has. `local` is the only host in M2; the SSH runner (T-159a)
joins the same registry with the same two commands, so a mesh driver never learns which kind of host it is
using.

| command | params | mutates | requires_project | result |
|---|---|---|---|---|
| `host.list` | none | no | no | `{"hosts": [{name, runner, available, why, record}]}` |
| `host.probe` | `host: string = "local"`, `refresh: boolean = false` | no | no | `{"host", "available", "why", "record"}` |

**Not `engine.*`.** `engine.info` means the solver engine, which is M3's; these are about the machines
Stokes runs OpenFOAM *on*.

**Where the agent looks for OpenFOAM, and what to do when it finds none.** The probe discovers
installs under the standard prefixes (`/usr/lib/openfoam*`, `/opt/openfoam*` and their relatives).
A **user-space install** — a tarball unpacked under `~/opt`, which is a normal way to have one — is
invisible to that search, and the answer is the environment variable:

```bash
export STOKES_OPENFOAM_BASHRC=/path/to/your/openfoam-env.sh   # the file that exports WM_PROJECT_DIR
```

It is checked first and may name any file name, not only `bashrc` (the official tarballs ship
`etc/bashrc`; a wrapper that also exports MPI prefixes is common). It must be **absolute and a
regular file**: a relative path would resolve against whatever directory the agent happens to be in,
and a directory would be sourced as the empty string. With no install found, `mesh.generate` and
`run.start` answer `mesh.no_host`/`run.no_host` with a message that names this variable — a machine
without OpenFOAM is recorded as `skipped`, never `failed` (ADR-0024), so a replay on it still
reproduces the project.

```json
{"hosts": [{"name": "local", "runner": "local", "available": true, "why": "", "record": null}]}
```

```json
{"host": "local", "available": true, "why": "",
 "record": {"agent": "stokes-agent/0.0.1.dev0", "system": "Linux", "cpu_count": 4, "vtk": "9.6.2",
            "installs": [{"bashrc": "…", "project": "OpenFOAM", "version": "14", "flavour": "org",
                          "supported": true, "seconds": 0.216,
                          "utilities": {"blockMesh": "/…/bin/blockMesh", "cartesianMesh": null}}],
            "default_install": "…", "why": ""}}
```

`record` is **null until it is asked for**: `host.list` must not cost a bashrc capture, so the list carries
the record only after a probe. A probe **without** `refresh` uses the remembered answer — the first one
sources the environment (about 0.27 s) and the rest are free.

**An unavailable host answers rather than raises.** `available: false` with a reason lets `mesh.generate`
(T-207) write `skipped`, which is the ADR-0024 rule for a replay that finds no host. An **unknown** host
raises `run.unknown_host`, because that is a mistake in the call rather than a fact about a machine. On
Windows `local` is unavailable with "No OpenFOAM host on this machine: Stokes meshes on Linux, here or over
SSH (coming next)".

### The in-process agent, and why jobs die with it

On thinkyboy the desktop **is** the `python -m stokes.server` process, so the agent runs in-process
(ADR-0005): one daemon thread, `stokes-agent-local`, with its own asyncio loop, and every call crosses to
it through a future.

The consequence is intended: **closing the runner kills every running job**, marking each `interrupted`
(ADR-0024 — a job never outlives the agent that started it). A solver left behind would hold its case
directory open and make the next run fail with a lock error.

**`atexit` alone is not enough.** Python's default `SIGTERM` action ends the process immediately, so
`atexit` never runs — and a utility started with `start_new_session=True` is not in this process's group,
so it survives as an orphan. That is the shape of a `pkill`, a `systemctl stop`, or a closed `nohup`
session. `stokes/server/__main__.py` therefore installs `SIGTERM`/`SIGHUP` handlers that raise
`KeyboardInterrupt`, reusing the shutdown path Ctrl-C already takes.

### Error codes

| code | class | when |
|---|---|---|
| `run.unavailable` | `RunnerUnavailable` | this machine cannot host: no OpenFOAM, or not Linux |
| `run.unknown_host` | `UnknownHost` | no host with that name is registered |
| `run.agent_failed` | `AgentCallFailed` | the agent refused or failed the call |

## blockMesh

The first mesher, and the only one built. It writes **one hexahedral block over the box** —
`system/blockMeshDict` and `system/controlDict`, and nothing else (`checkMesh` needs no schemes; measured).

`stokes/mesh/snapshot.py` reads the tree once into a `MeshSnapshot`; `stokes/mesh/drivers/blockmesh.py`
turns it into files; `stokes/mesh/basis.py` hashes what the mesh was made from. None of them runs anything
— T-207 (`mesh.generate`) does that.

### What maps to what

A **side** of the box becomes a patch named after the **tag** whose faces lie on it. A face lies on a side
when every vertex of every triangle with that face id is within a tolerance of that side's plane — every
vertex, not the centroid, so a face spanning a corner is on neither side rather than arbitrarily on one.

| what the user has | what the mesh gets |
|---|---|
| a tag whose faces are on a side | a patch with that tag's name |
| a side no tag holds | the `unassigned` patch, **written explicitly** |
| a tag whose faces are inside the box | **unmapped** — reported, not silently dropped |
| two tags on one side | `mesh.invalid`: a side takes one patch |

An interior tag cannot become a blockMesh patch — that is what snappy (T-150) is for — so the driver
reports it and T-207 puts it in the result's warnings. Silently dropping it would hide that the boundary is
not what the user tagged.

### The patch type comes from the boundary kind

| boundary kind | `boundary` type |
|---|---|
| `wall` | `wall` |
| *no boundary* (the default patch) | `wall` |
| `symmetry` | **`symmetryPlane`** |
| anything else — an inlet, an outlet, an unassigned tag | `patch` |

**`symmetryPlane`, measured.** Both spellings exist in OF14's own tutorials and both mesh clean. The
solver is what decides: with a mesh using `symmetryPlane` and a `0/` field using `symmetry`, `foamRun`
reports `inconsistent patch and patchField types for patch type symmetryPlane and patchField type
symmetry`. **The mesh and the field must agree**, so a `0/` file written for a symmetry patch must use the
same word. `symmetryPlane` is the right choice here because the condition catalogue's entry means a flat
mirror plane.

### The basis

`mesh_basis(snapshot, driver_version, engine)` is the sha1 of what decides the mesh: the driver version,
the engine (`"org-14"`), the mesher, the base size, the box, the side-to-tag map, the **side kinds**, the
cell limit, the part's basis, and the members of the tags that reached a side.

Deliberately **not** in it: `host` (the same inputs give the same mesh anywhere), `cores` (blockMesh is
serial), and the quality thresholds (`max_non_ortho`, `max_skewness`) — those decide whether a mesh
**passes**, not what it is, and a basis that changes for no reason is one users learn to ignore.

### Error codes

| code | when |
|---|---|
| `mesh.invalid` | no domain part, no display mesh yet, a stale tag, two tags on a side, too many cells |

## Generating a mesh

| command | params | mutates | result |
|---|---|---|---|
| `mesh.generate` | `host?`, `wait=false`, `force=false`, `result_id?` | **yes** | `{result, path, state, job, basis, reused, warnings}` |
| `mesh.cancel` | none | no | `{cancelled: bool}` |
| `mesh.status` | none | no | `{state, result, path, job, step, fraction, stale, warnings}` |
| `mesh.log` | `lines=200` | no | `{step, lines, truncated}` |

**Events:** `mesh.progress {node_id, path, step, stage, fraction, message}`, `mesh.log {node_id, step,
lines}`, `mesh.done {node_id, path, seconds}`, `mesh.failed {node_id, path, code, message}`,
`mesh.cancelled {node_id, path}`, `mesh.skipped {node_id, path, code, message}`.

### Three rules that shape it

**A result is not an undo step.** It is written by a job, like an operation's output; undoing past a
completed mesh would leave the settings that produced it with no mesh to show. `mesh.generate` is
**journaled** with the result id it chose, so a replay recreates the same node.

**Nothing meshes by itself.** A change makes the result **stale** — reported in `mesh.status` and on the
node — but does not start a job. Meshing costs minutes, and a user who changed a size has not necessarily
asked for a new mesh. Undoing the change makes it fresh again.

**A host that cannot mesh is `skipped`, never `failed`** (ADR-0024). A machine without OpenFOAM is a fact
about the machine; the result carries `mesh.no_host` and the reason, and a journal replay **continues**
rather than stopping.

### What a result carries

The `mesh_result` parameters are the schema's (`mesher`, `host`, `basis`, `state`, `job`, `cells`, `ok`,
`seconds`, `error`). Everything else lives in **`node.meta`**, which is free-form and persisted:

| key | what |
|---|---|
| `engine` | `"org-14"` — the install the mesh was made by, needed to recompute the basis |
| `warnings` | one per unmapped tag: `tag <name> lies inside the domain; blockMesh cannot mesh it` |
| `patch_tags` | `{patch name: tag id}` — T-210 serves it, T-214 reads it |
| `patch_types` | `{patch name: patch \| wall \| symmetry}` |
| `derives_from` | the settings node and the side tags the result was built from |
| `basis_parts` | the six sub-bases (brief M-501) |
| `layers` | the snappy coverage table the parser read from the run's log: `[{patch, faces, layers_mean, thickness_mean, coverage_percent}]`, present when a table was printed. A wall under 80 % coverage also adds a `warnings` entry naming it and the percentage; the mesh itself is not failed by it |
| `symmetry_flatness` | `[{patch, points, off, max_offset}]` for each planar `symmetry`/`symmetryPlane` patch, measured from its points in the surface artifact (`stokes.mesh.flatness`; metres). A patch with points more than 2 % of the base size off its plane adds a `warnings` entry and makes the result `ok: false` (status `warning`): the mixing elbow's runs diverged from such cells while checkMesh passed the mesh. The polyhedral type flattens planar symmetry faces itself (`improveSymmetryPlanes`, then `foamDictionary` sets the type back to `symmetry`) |
| `quality_dist` | the quality distributions (brief M-506, Part B): `{"pending": true}` from the moment the result is `done` until the follow-up fetch returns (the result is finished FIRST — R14c's ordering), then the reply — `metrics` (40-bin histograms of non-orthogonality, skewness, aspect ratio, each with `unit`, `edges`, `counts`, `max`, `mean`), `cell_types` (the seven checkMesh counts), `worst` (50 faces per metric) — or `{"error": ...}` with the reason, never failing the mesh. Read by the Quality page through `mesh.quality` |

A result is **reused** rather than rebuilt when the basis matches and `force` is false, because a mesh
costs minutes and identical inputs give an identical mesh. Only **one** result is kept: a new generate
cancels the running job, removes the old node and adds a fresh one.

## The Mesh panel (T-211b)

`web/src/panels/MeshPanel.tsx` (logic and tests: `meshPanelModel.ts`, `meshPanelModel.test.ts`; the buttons:
`meshRun.ts`) is a tab in the right slot between Properties and Conditions, and the front tab in the Mesh stage.
It reads **the tree** for the settings and the result (state, counts, `quality`, `meta.warnings`,
`meta.patch_tags`, and `status` for stale), **the events** `mesh.progress` and `mesh.log` for the step and
the log (the agent's own `job.state` and `log.lines` never reach a client), and `mesh.status` every 2 s while a
job is in flight as a fallback. Every settings edit is one `mesh.configure`. Generate is disabled, with a reason,
when there is no connection, no project, no part (part domain) or box (box domain), the mesher is not built, or
the host cannot mesh; anything only the server can know arrives as the command's own error.

Two limits to know: `mesh.log` answers only while the job is open, so a log of a finished run lives in the
panel's memory until the project closes; and the viewport does not show the mesh until T-210 serves its patches
(the panel's "Show in view" button says so and is disabled).

## Mesh quality

Every mesh job runs **two steps**: `blockMesh`, then `checkMesh`. The agent parses the checkMesh log
(ADR-0005: the agent parses logs) and the result carries the numbers.

### What `quality` holds

`cells`, `points`, `faces`, `internal_faces`, `boundary_patches`, `max_non_ortho`, `avg_non_ortho`,
`severe_non_ortho_faces`, `max_skewness`, `max_aspect_ratio`, `min_volume`, `max_volume`,
`negative_volume_cells`, `failed_checks`, `mesh_ok`, and `thresholds` (the settings' `max_non_ortho` and
`max_skewness`). The patch table is **not** in `quality` — it is a list, not a number.

**A key the log did not carry is `null`, never a default.** The one exception is `failed_checks`, which is
`0` when no `Failed n mesh checks.` line appears: checkMesh prints that line only on failure, so its
absence is a reading rather than a gap.

### `ok` is two verdicts at once

    ok = mesh_ok  AND  max_non_ortho <= threshold  AND  max_skewness <= threshold

`mesh_ok` is OpenFOAM's own verdict; the thresholds are **this project's** bar. A mesh can satisfy
OpenFOAM and fail a user's tighter tolerance — that is the reason the thresholds exist, and the node's
status is `warning` (not `failed`) when that happens: the run worked and the mesh is real, the quality is
what missed.

Any figure that is `null` makes `ok` false. **A mesh nobody measured is not one to call good**, and a
defaulted zero would report a failed mesh as fine.

### `checkMesh` exits 0 while reporting `Failed 2 mesh checks.`

Measured against the skewed-cavity fixture. **The exit code is not the verdict** — the job is `done` and
`ok` is false, which are different facts and a UI shows them differently. The parser is the only thing
that reads the verdict, which is why its patterns are checked against real logs rather than written from
memory: the failing forms of the aspect-ratio and cell-volume checks are *different sentences* from the
passing ones (` ***High aspect ratio cells found, Max aspect ratio: …` and
` ***Zero or negative cell volume detected.  Minimum negative volume: …`).

### Extraction performance, measured

`extract.surfaces` on a box, by cell count (thinkyboy: 4 cores, 7.1 GiB, revision `c039ee3d`):

| cells | time | artifact |
|---|---|---|
| 1,000,000 | **1.26 s** | 2.98 MiB |
| 4,096,000 | 5.27 s | 7.62 MiB |

Linear: `t = 0.017 s + 1.293 µs x cells`. **10 M cells extrapolates to 12.9 s, over the 10 s budget** — and
the cost is not in the numpy: the VTK reader is **93%** of it, because it parses the whole ASCII `polyMesh`
however little of it is wanted. A binary mesh, or reading `polyMesh` directly, is the lever.

`blockMesh` itself needs **~1 KB/cell**, so 8 M cells cannot be built on this machine (OOM at 7.4 GB
against 7.28 GB of RAM). The largest measurable size here is 160^3.

### Replay, and why `settle` ignores `coalesce`

The session calls an extension's `settle(timeout, coalesce=…)` after every journal record (ADR-0022
decision 10.1). **`MeshService.settle` accepts `coalesce` and deliberately ignores it**, and the difference
from `OperationStore.settle` is worth knowing before changing either:

* `OperationStore.settle` **forwards** it. An operation may be waiting on its 300 ms debounce, so a burst
  of changes recorded inside one window must run the operation **once**; forcing every pending run at
  every record re-ran it once per record — **measured at four runs for a burst that took two** (T-092).
* **Meshing has no debounce and does not auto-run.** A change makes the result **stale** and starts
  nothing, so there is no pending run to hold open and nothing to collapse.

**If `mesh.generate` ever gains a debounce or an `auto_run`, `settle` must forward `coalesce` exactly as
`opstore` does** — otherwise a replay re-meshes once per journal record, which for a mesh is minutes of
work duplicated rather than milliseconds.
