# Studies and design tables

**Table format 1** · ADR-0016 (Explore v1: points 1, 2 sweep and doe, 6, 8, 9) · node types:
`stokes/explore/model.py` · commands: `stokes/api/commands/study.py` · tables:
`stokes/explore/study_table.py`, `study_store.py` · engine: `stokes/explore/engine_function.py`

A study varies project parameters and records responses for each design point. The
definitions are tree nodes under `/studies`; the design table (the points) is not a node and
comes from `study.results`. The front end renders both and changes them only through the
commands below. This document is the contract for the Explore stage (Design Table and Plots
panels, Study properties).

All values in tables, events and results are **SI** numbers; each column names its dimension,
its SI unit and the project's display unit, so the front end converts for display.

## Nodes

```
/studies                      group, part of the skeleton (cannot be removed or renamed)
  <study>                     study
    variables                 group of variable
    responses                 group of response
    objectives                group of objective
    constraints               group of constraint
```

| Type | Parameter | Kind | Meaning |
|---|---|---|---|
| `study` | `type` | enum `sweep` \| `doe` | sweep: full factorial of the variables' levels, or points added by hand; doe: the sampler places the points |
| | `sampler` | enum | `full_factorial`, `latin_hypercube`, `sobol`, `halton`, `central_composite`, `box_behnken`, `optimal_space_filling`, `monte_carlo` (a sweep always uses the full factorial) |
| | `seed` | int | seed of the randomised samplers |
| | `count` | int | number of points, for the samplers that take one (Latin hypercube, Sobol, Halton, optimal space filling, Monte Carlo) |
| | `status` | enum | `idle` (no points), `generated` (points to evaluate), `running`, `done` (all evaluated), `failed` (a point failed); set by the table commands, not undoable |
| | `description` | string | |
| `variable` | `parameter` | string | the varied quantity: `U_in`, `/physics/parameters/U_in`, or a node quantity `regions.fluid.inlet.velocity` |
| | `dimension` | enum | the quantity's dimension; the DYNAMIC dimension of the quantities below |
| | `domain` | enum | `values`, `range`, `uniform`, `normal`, `categorical` |
| | `values` | array | levels in SI (values, categorical); a value list is used in ascending order |
| | `labels` | string | category labels separated by commas (categorical, optional) |
| | `min`, `max` | quantity | bounds (range, uniform; truncation of a normal, both 0 for none) |
| | `count` | int | levels of a range, or of a distribution in a full factorial; 0 for none |
| | `step` | quantity | level spacing of a range; 0 for none (a range takes a count or a step) |
| | `mean`, `std` | quantity | normal distribution |
| `response` | `expression` | string | an expression over parameters and node quantities (reports from M3), e.g. `dp_dyn` |
| | `dimension` | enum | the result's dimension (inferred by `study.add_response`) |
| | `label` | string | display name; the node name when empty |
| `objective` | `response` | ref (`response`) | the id of a response of the same study |
| | `direction` | enum `minimise` \| `maximise` | |
| `constraint` | `expression` | string | over the study's responses by name and the parameters |
| | `dimension` | enum | dimension of the expression and the limit |
| | `operator` | enum `<=` \| `>=` \| `==` | |
| | `limit` | quantity | the bound |

Quantity parameters are stored like every other quantity (`{"value", "unit", "expr"?}`, SI);
bounds may be expressions (`max = "U_in * 1.5"`) and follow the base case. Variable and
response names share the table's columns, so they are unique across both groups. Definitions
are ordinary nodes: `node.*` and `parameter.set` work on them, and every definition edit is
undoable. Changing a `dimension` does not convert the values stored with it: it resets the
node's dynamic quantities (a variable's `min`, `max`, `step`, `mean` and `std`, a constraint's
`limit`) to zero in the new dimension, expressions included, and leaves a variable's `values`
list as it is; undo gives the old quantities back, and redo resets them again. Setting the
dimension a node already has changes nothing. Set the dimension first.

## The design table

One row per design point:

```json
{"index": 0, "inputs": {"U_in": 1.0}, "outputs": {"dp_dyn": 499.0, "Q": 0.0028274},
 "status": "done", "run": null, "retain": false, "error": null}
```

| Field | Meaning |
|---|---|
| `index` | the point's id for life; removing points leaves gaps |
| `inputs` | SI value per variable name |
| `outputs` | SI value per response name; empty unless `done` |
| `status` | `pending`, `running`, `done`, `failed` |
| `run` | run bundle of the point (null for the function engine) |
| `retain` | keep the point's run bundle (used from M3) |
| `error` | why the point failed |

Files, written at once by every table command when the project has a folder (otherwise at its
first save; a save to a new folder carries every table):
`<name>.stokes/studies/<study_id>/points.jsonl` (the rows), `points.csv` (a spreadsheet mirror:
`index, status`, one column per variable and response headed `name [SI unit]`, then
`retain, run, error`) and `table.json` (format version, type, sampler, seed, count, the
sampler's metadata, engine, `generated`/`updated` timestamps, the columns). Tables load on first
use; opening or closing a project drops them. Table commands are journaled and never undone.

## Commands

`study` accepts a study id, its path (`/studies/sweep`) or its name (`sweep`). Commands that
add a node accept `node_id` (journals) and return the node summary.

| Command | Parameters | Result |
|---|---|---|
| `study.create` | `name`, `type` = sweep, `description`, `sampler`, `seed`, `count` | study summary plus `groups` {name: id}; one undo step |
| `study.add_variable` | `study`, `parameter`, `domain` = values, `values` (SI numbers or `"2 [m/s]"`), `labels` (list), `min`, `max`, `count`, `step`, `mean`, `std`, `name` | variable node |
| `study.add_response` | `study`, `expression`, `label`, `dimension` (inferred), `name` | response node; the expression must evaluate on the base case |
| `study.set_objective` | `study`, `response` (name, id or path), `direction` = minimise (min/max accepted) | objective node; on a response that has one, changes its direction |
| `study.add_constraint` | `study`, `expression`, `operator`, `limit`, `dimension` (inferred), `name` | constraint node |
| `study.generate` | `study` | `{study, path, points, sampler, seed, status}`; replaces the table |
| `study.add_point` | `study`, `inputs` {variable: value}; missing variables take the base value | the new row (pending) |
| `study.update` | `study`, `points` = "all" or [indices] | `{study, path, evaluated, done, failed, pending, cancelled, status, points: [rows evaluated]}`; "all" is every point not done or lacking a response |
| `study.cancel` | `study` (optional: all) | `{cancelled: [study ids]}` |
| `study.clear` | `study`, `points` (default all), `remove` = false | `{study, path, points, removed, status}`; resets points to pending, or removes them |
| `study.results` | `study`, `format` = json \| csv | json: `{study, path, name, type, status, sampler, seed, design, engine, generated, updated, variables, responses, counts, points}`; csv: `{study, path, format, text}` |
| `study.pareto` | `study` | `{study, path, objectives, constraints, front, points, excluded}` |
| `study.export` | `study`, `path` (`.csv` or `.json`, outside the project folder), `format` | `{study, file, format}` |

`variables` and `responses` in `study.results` are columns:
`{name, dimension, unit, source, label?, display_unit}` (`source` is the parameter or the
expression). `counts` gives the number of rows per status.

In `study.pareto`, `front` lists the indices of the feasible non-dominated points ordered by
the first objective; `points` has one entry per evaluated point:
`{index, objectives {name: value}, rank, crowding, feasible, violation, on_front, error?}`
(rank 0 is the front; `crowding` is null at the ends of a front; `violation` is the scaled
constraint violation, null when a constraint could not be evaluated); `excluded` lists the
points not evaluated.

`study.update` runs the **function engine**: each point is the base case with its inputs as
parameter overrides; derived parameters (`dp_dyn = 0.5 * rho * U_in**2`) follow the overrides;
every response is evaluated and unit-checked; the live project is never changed. A point fails
with its reason; the other points go on. For now `study.update` returns when every point is
done, and `study.cancel` takes effect only from code that runs during the update (an event
listener); with the run queue (M3) the same commands will run in the background.

Errors carry stable codes: `explore.study_definition` (the definition cannot be generated or
evaluated, the message says what to fix), `explore.study_busy` (an update is running),
`explore.study_table` (a table file is unreadable), `explore.sampler`, `explore.domain`, and the
usual `validation`, `units.mismatch`, `units.expression`, `model.unknown_node`.

## Events

| Event | Payload | When |
|---|---|---|
| `node.added`, `node.changed` | `{node_id, path}`, `{node_id, path, param}` | definition nodes; the study's `status` (`param: "status"`) |
| `study.generated` | `{study, path, points}` | a new table (`points` is its size) |
| `study.point` | `{study, index, status, outputs, error}` | one point: `pending` (added or cleared), `running`, then `done` or `failed` |
| `study.updated` | `{study, path, evaluated, done, failed, pending, cancelled, status}` | an update ended |
| `study.cleared` | `{study, path, points, removed}` | points reset or removed |

Order during an update: `node.changed` (status running), then per point `study.point` running
and `study.point` done or failed, then `node.changed` (final status) and `study.updated`.
Through the WebSocket server, events raised while a command runs follow its reply, so the
reply of `study.update` already holds every evaluated row.

## The demo study

`python -m stokes.app` and `python -m stokes.server` without a project build
`/studies/velocity_sweep`: a sweep over `U_in` = 1, 2, 3 m/s with the responses `dp_dyn`
(label "Dynamic pressure", Pa) and `Q` ("Volume flow", m³/s), generated and evaluated, status
`done`:

| index | U_in [m/s] | dp_dyn [Pa] | Q [m³/s] |
|---|---|---|---|
| 0 | 1 | 499 | 0.0028274 |
| 1 | 2 | 1996 | 0.0056549 |
| 2 | 3 | 4491 | 0.0084823 |

It has no objectives; `study.set_objective` on `dp_dyn` and `Q` gives a Pareto front to draw.

## Known limits (Explore v1)

- The study's `status` parameter is saved with the project; the table files are written at
  once. After reopening a project that was not saved since its last table change,
  `study.results` has the current status.
- `study.update` blocks the server while it runs (fast for the function engine).
