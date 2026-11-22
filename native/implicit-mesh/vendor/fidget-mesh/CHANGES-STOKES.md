# Changes to fidget-mesh 0.5.1 in this copy

This directory is `fidget-mesh` 0.5.1 as published on crates.io (upstream
<https://github.com/mkeeter/fidget>, commit `174d3db5` per `.cargo_vcs_info.json`), by Matt
Keeter, under the Mozilla Public License 2.0 (`LICENSE.txt`). It is used by
`stokes-implicit-mesh` through `[patch.crates-io]` (ADR-0035 decision 32).

This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0. If a copy of
the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

The modified files below remain under MPL-2.0. Every change in the source is marked with a
`stokes patch` comment. The changes are offered upstream; this copy goes when upstream has them.

## Purpose

1. **A finer task split for load balance.** Upstream stops splitting the octree into parallel
   tasks at `threads * 10` tasks. When the body fills a small share of the cube, a few tasks hold
   most of the surface and the largest one bounds the build however many cores there are. This
   copy splits to a fixed level (5, i.e. 32,768 tasks) and hands tasks out one at a time. The
   mesh is unchanged: each cell is built by the same recursion and the results are put back in
   task order.
2. **Progress counts.** Process-wide atomic counters (stage, done, total) that the program reads
   from a reporter thread, so the user sees how far the octree build and the dual walk are.
   Counting is off unless the program calls `progress::enable()`.

## Files

| File | Change |
|---|---|
| `src/progress.rs` | **New file.** The counters (`ENABLED`, `PHASE`, `DONE`, `TOTAL`), `enable`, `enabled`, `start`, `add`, `set`, `read`, and the crate-private `Marks` table of surface cells that gives the octree build its total and each task its weight. |
| `src/lib.rs` | Declares `pub mod progress;`. |
| `src/builder.rs` | `MeshBuilder` gains a `visited` counter; `cell()` counts every cell the dual walk visits and flushes the count to `progress` every 4,096 cells; new `flush_progress()`. |
| `src/octree.rs` | `build_inner_mt`: splits tasks to the fixed level 5 instead of `threads * 10` (a depth-1 octree keeps the upstream rule). When counting is on, a pre-pass marks the cells two levels below the task split that may hold surface (new `OctreeBuilder::mark` and `OctreeBuilder::surface_cells`), which sets the progress total and gives every task a weight. Tasks are handed out from a shared atomic counter by one worker per thread instead of a `par_iter` over the tasks; with weights, the heaviest 1 % go first and the rest follow in a fixed pseudo-random order, without weights in task order; the results are sorted back into task order before the merge. |
| `src/octree.rs` | `recurse`: records whether the interval settled the cell and, when counting is on, adds the marked cells the finished (or settled) cell covers. |
| `src/octree.rs` | `walk_dual`: when counting is on, starts stage 2 with the number of cells the walk will visit, and flushes the builder's count at the end. |
| `CHANGES-STOKES.md` | **New file** (this list). |
| `LICENSE.txt` | **Added**: the text of the Mozilla Public License 2.0, which the published crate refers to (`../LICENSE.txt` in its README) but does not contain. |

## Removed from the earlier patched copy

The earlier patched copy this one descends from had diagnostic switches. They are gone:

- the `*_SPLIT_LEVEL` environment variable that overrode the split level (now the constant
  `SPLIT_LEVEL = 5`);
- the `*_MARK_EXTRA` environment variable that set how many levels below a marked cell its
  weight is counted (now the constant `MARK_EXTRA = 1`);
- the `*_TASK_ORDER` environment variable that chose between plain, heaviest-first and the mixed
  order (now always the mixed order described above when weights exist, task order otherwise);
- the `*_PROFILE` environment variable and every timing `eprintln!` (mark pre-pass, parallel
  tasks with the slowest task times, merge), with the per-task timer and the `secs` field of the
  task output that fed them.

No file of this crate reads an environment variable at run time any more (`build.rs` reads
`OUT_DIR` at build time, as upstream).

## Unchanged upstream files

`build.rs`, `Cargo.toml`, `Cargo.toml.orig`, `README.md`, `.cargo_vcs_info.json`, `.cargo-ok`,
`src/cell.rs`, `src/codegen.rs`, `src/dc.rs`, `src/frame.rs`, `src/output.rs`, `src/qef.rs`,
`src/types.rs`.

## src/octree.rs: non-finite gradients (2026-11-12)

`leaf`: an intersection whose gradient has an infinite component is treated like one with a NaN
component (the vertex snaps to the intersection point instead of solving the QEF). Upstream tests
`is_nan` only; a field built on the square root of a squared distance (ADR-0036's sketch profiles)
has an infinite gradient exactly on its boundary, which made the solved vertex NaN.
