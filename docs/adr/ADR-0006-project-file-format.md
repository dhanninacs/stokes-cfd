# ADR-0006 · Project file format

**Status:** accepted · 2026-10-02 (decision I1: projects may live on network shares; jobs stay on the engine host's local disk)

## Context

A project holds imported CAD, tessellation caches, the simulation tree, and a
history of runs with results. Users keep projects on network shares, share
them with colleagues, and expect to reopen a project years later.

## Decision

`<name>.stokes/` is a directory:

```
coldplate-v3.stokes/
  project.json        the tree; schema-versioned; the single source of truth
  geometry/           original CAD files as imported; tessellation cache (.npz)
  runs/
    run-0001/
      model.json      snapshot of the tree at run time
      case/           the compiled OpenFOAM case as sent (small; no mesh)
      logs/
      artifacts/      extracted surfaces, slices, reports (content-addressed)
      run.json        engine versions, host, timings, state
  cache/              disposable (LOD, thumbnails)
  index.sqlite        derived run index for fast listing; safe to delete
```

- `project.json` is pretty-printed JSON with stable ordering, so it diffs
  well in git and can be inspected by hand.
- Runs are immutable after completion. "Duplicate run" creates a new run from
  a snapshot.
- The OpenFOAM case is a compiled output; expert edits are stored as
  override commands in `project.json` and re-applied by the compiler.
- Large mesh and result data stay on the engine host under
  `~/.stokes/jobs/<id>`; a run bundle references it and can fetch more on
  demand (for example a full-resolution slice) while the job data exists.
- Optional "archive run" packs the full case including mesh into
  `runs/run-0001/full.tar` for long-term reproducibility.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Single zip or SQLite file | Harder to inspect and diff; large binary churn on every save; poor on network shares. |
| The OpenFOAM case directory *is* the project | Inverts principle 2 (the case is an output) and ties the model to one dialect. |

## Consequences

- Schema migrations are a first-class concern: `project.json` carries a
  schema version and migrations are tested.
- A project can be opened without any engine present (geometry, setup,
  cached results).
