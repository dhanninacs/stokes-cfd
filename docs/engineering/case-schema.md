# Imported OpenFOAM cases: the `foam_case` node and its copy

ADR-0029 (proposed), plan T-306, T-307 and T-308. Code: `stokes/cases/` (`model`, `recipe`,
`allrun`, `resources`, `store`, `importer`, `overrides`, `keys`), the writer
`stokes/compile/of14/external.py`, and the `case.*` commands in `stokes/api/commands/case.py` and
`stokes/api/commands/case_settings.py`.

## The node

A `foam_case` lives at `/cases/<name>`. `case.import` creates the `/cases` group (stable id) in the
same undo step when the project lacks it.

| Parameter | Kind | Written by | Value |
|---|---|---|---|
| `source` | json | `case.import` | `{"kind": "install", "tutorial": "<id>"}` or `{"kind": "folder", "path": "<absolute path>"}` |
| `snapshot` | string | `case.import` | sha256 of the copy (64 hex characters) |
| `dialect` | enum | `case.import` | `of14` |
| `solver` | json | `case.import` | a module name, a `{region: module}` map, a legacy application, or null |
| `recipe` | json | `case.import` | see below |
| `overrides` | json | `case.set`, `case.reset` | ordered list of setting edits, see below |

## The recipe

```json
{"origin": "allrun" | "default", "kind": "complete" | "partial", "checked": true,
 "steps": [{"name", "utility", "argv", "parallel", "ranks", "region", "stage", "folder", "line"}],
 "actions": [{"op": "copy" | "mkdir" | "remove", "source", "target", "line"}],
 "resources": [{"source": "resources/<rel>", "target": "<rel in case>", "line"}],
 "unparsed": [{"line", "text", "why"}],
 "refused": [{"line", "text", "why"}]}
```

`stage` is `mesh`, `init`, `solve` or `post`; `folder` is the sub-case a step runs in (`""` for
the case itself). `unparsed` lists every Allrun line the parser does not run (shell features,
conditionals, loops, unknown commands); `refused` lists steps the agent's external flag allow-list
refuses. Either makes the recipe `partial`. `checked` is false when the install was not probed.

The recognised Allrun forms are the module docstring of `stokes/cases/allrun.py`.

## The copy

```
<project>.stokes/cases/<node id>/
    manifest.json   {version: 1, snapshot, files: [{path, area, size, sha256}], derived}
    source/         the case; every file passed the external lint profile
    scripts/        Allrun, Allclean, ...: kept to show, never staged, never run
    extras/         files outside system/, constant/, time folders and 0.orig/ (validation
                    plots, README, helper scripts): kept, never linted, never staged
```

`snapshot` is the sha256 of the sorted `area/path\0sha256\n` lines of `files`. `derived` holds the
recipe, solver and regions, so a journal replay that finds the copy needs neither the source nor
the install. Files are read-only. Undo and `case.remove` leave the copy in place, so redo and undo
bring the case back whole.

## Commands

| Command | Mutates | Result |
|---|---|---|
| `case.import {tutorial? \| folder?, name?, node_id?, snapshot?}` | yes | `{node_id, path, status, snapshot, recipe, refusals}` |
| `case.get {node}` | no | `{id, path, source, snapshot, dialect, solver, regions, recipe, overrides, status}` |
| `case.files {node}` | no | `[{path, area, kind: dict \| field \| mesh \| binary \| script, size, parsed}]` |
| `case.remove {node}` | yes | the removed node's summary |

`status` is `ready`, `needs_recipe`, `unverified` (install not probed), `refused` (import only:
nothing was added or staged) or `missing` (`case.get`: the copy is not in the project).

## Overrides (T-308)

The copy never changes. An edit is an item of the node's `overrides`, and the staged case is
`stokes.compile.of14.external.write(copy, overrides)`, a pure function (writer `of14-external/1`):
a file no edit names stages byte-identical; an edited file differs only in the edited values,
spliced into the copy's own text so comments and layout stay. A key the file lacks is added at the
end of the dictionary that should hold it.

```json
{"file": "system/controlDict", "path": ["PISO", "nCorrectors"], "value": 3}
```

`value` is a typed token, never raw text: a number, a boolean, a word (letters, digits,
`_ . : < > , + - * /`, balanced parentheses), a list `[1, 0, 0]` (written `(1 0 0)`, at most 4
deep), `{"tokens": ["uniform", [1, 0, 0]]}` (written `uniform (1 0 0)`), or, for a known physical
key, `{"quantity": "500 [ms]", "si": 0.5}`. A word cannot hold `#`, `$`, quotes, braces or `;`, so an
edit can never add a directive, a variable, code or a second entry. One item per file and path:
setting a key again replaces its item.

Known physical keys (`stokes/cases/keys.py`) take a quantity with its unit and are written in SI;
on a dimensioned entry (`nu [0 2 -1 0 0 0 0] 0.01;`) only the number is replaced:

| File | Key | Dimension |
|---|---|---|
| `system/controlDict` | `startTime`, `endTime`, `deltaT`, `maxDeltaT` | time |
| `constant/physicalProperties*`, `constant/transportProperties*` | `nu` | kinematic viscosity |
| the same | `rho` | density |

Any other number is a plain token: Stokes cannot know its dimension, and quantity text there is
refused with `case.override_unit`.

| Command | Mutates | Result |
|---|---|---|
| `case.settings {node, file}` | no | `{file, rows: [{path, kind, text, truncated, overridden, dimension?, unit?, original?}], truncated, overrides}` |
| `case.set {node, file, path, value}` | yes | `{file, path, value, text, overrides}`; one undo step |
| `case.reset {node, file, path?}` | yes | `{file, path, removed, overrides}`; no undo step when nothing is removed |
| `case.diff {node}` | no | `{files: [{file, overrides, diff}]}`, unified diffs against the copy |
| `case.preview {node, file?}` | no | `{writer, files: [{path, size, overridden}]}`, or `{file, text, overridden}` (at most 1 MiB) |
| `case.boundary {node, region?}` | no | `{folder, fields, patches, grid: {field: {patch: {type, value?}}}, unread}` from `0/` (else `0.orig/`) |

A boundary cell is edited with `case.set` on `<folder>/<field>` and the path
`["boundaryField", <patch>, <key>]`. A copy whose file no longer matches its manifest (edited on
disk) is refused with `case.store` rather than staged.

## In the desktop (T-313 to T-315)

Selecting a `foam_case` node shows the case card in Details (`web/src/panels/CaseCard.tsx`): status,
source, solver, the recipe as commands and the Allrun lines not run; a file picker over the parsed
dictionaries and fields (`system/controlDict` first) with each entry editable in place (`case.set`)
and revertible (`case.reset`); Run case (`run.start case=`) with the run's live state; and a picker
of the written times once it has finished (`run.time`). When a run of the case ends with fields,
its job's own mesh becomes the project's mesh result (`MeshService.adopt_job_mesh`, `meta.case` the
case node), so the Mesh and Results stages draw and colour it like any mesh.

Edit codes: `case.override_value`, `case.override_path`, `case.override_unit`,
`case.override_block`, `case.override_file`, `case.file_missing`, `case.file_too_large`.

## Opening a tutorial (T-312)

`tutorial.open {tutorial, path?, node_id?, snapshot?}` (no project needed) creates a new project
named after the case's folder (made a project name: `coded-case` becomes `coded_case`) in the
projects folder, at the first free `<name>`, `<name>_2`, ..., opens it, and imports the case with
`case.import`'s rules. It returns `{project: {name, path}, case: <case.import's reply>}`; a refused
case leaves the new project open and empty, with the refusals in `case`. The journal records the
folder actually used and the case's node id and snapshot, so a replay makes the same project and
case. The Start screen's Tutorials card calls it; the case appears under Solve > OpenFOAM cases.

## Running a case (T-309)

`run.start {case: <node>, host?, force?, wait?, run_id?}` runs an imported case instead of the
model (engine `of14.external`). What is sent (`stokes.run.external.plan_case`):

* every file of the copy's `source` area under `system/`, `constant/`, a time directory or
  `0.orig/`, with the node's overrides written over it; any other file is listed in `skipped`;
* the recipe's file actions (`cp -r 0.orig 0`, `mkdir`, `rm -rf`) applied to those files in order,
  **when they come before the first step**; a later one is listed in `skipped`;
* the recipe's steps as argv lists, with the residual parser on solver steps (`foamRun`,
  `foamMultiRun`, the legacy applications) and `endTime` as its progress end.

The job is created under the agent's `external` profile (`docs/engineering/security.md`). Version 1
runs on one rank: `decomposePar` and `reconstructPar` steps are left out and a parallel step runs
serially, each listed in `adapted`. A recipe with steps in sub-cases is refused with
`run.external_subcase`. The run's basis is the snapshot, the overrides, the recipe and the writer
version, so an unchanged case is reused and any edit makes a new run. A refused start leaves a
`failed` run record with the code. A host without OpenFOAM records `skipped`, as a model run does.

Error codes: `case.source_invalid`, `case.source_relative`, `case.source_missing`,
`case.source_escape`, `case.source_in_project`, `case.snapshot_mismatch`, `case.name_taken`,
`case.not_a_case`, `case.too_large`, `case.store`, `tutorial.no_root`.
