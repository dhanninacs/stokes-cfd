# The workspace document

**Schema version 1** · ADR-0015 · Python: `stokes.workspace` · commands:
`stokes/api/commands/workspace.py` · tokens: [theme-tokens.md](theme-tokens.md)

The workspace is everything a user can arrange in the interface, stored as
data: the theme, where each panel, island and top-bar item sits, how the tree
is organised, the shortcuts, the viewport defaults and the default engine. The
front end renders it and changes it only through the `workspace.*` commands,
the same ones the CLI, MCP and the assistant use. This document is the contract
for the front-end implementer.

The workspace is **not project model state**. It has no undo stack
(`history.undo` does not revert it), it is never in `project.json`, and a write
goes to its file at once. Writes are journaled like every mutating command, so
replaying a journal also replays arrangement changes.

## Files and layers

| Layer | File | Notes |
|---|---|---|
| defaults | built in (`stokes/workspace/defaults.py`) | complete; the Stokes arrangement of `web/DESIGN.md` |
| user | `~/.stokes/workspace.json` | `$STOKES_HOME/workspace.json` when `STOKES_HOME` is set (tests set it) |
| project | `<name>.stokes/workspace.json` (beside `project.json`) | the open project's overrides; only for a project saved to a folder |

The effective workspace is defaults, then user, then project, with the higher
layer winning. Every file is a workspace document with `"version": 1`; the
user and project documents are partial and hold only what they change.

- Reads are limited to 256 KiB and 32 levels of nesting. A file that is not
  valid UTF-8 JSON, not an object, too large or too deep, or (for the project
  file) resolves outside the project folder, is ignored as a whole and reported
  (`workspace.unreadable`). It is never fatal.
- Before the first write over an unreadable file, the store moves it to
  `workspace.json.bak`, so a hand edit with a typo is not lost.
- Writes are atomic (a temporary file, then a replace). What `workspace.set`,
  `workspace.import` and `workspace.apply_preset` write is canonical
  (normalised chords, lower-case hex colours, filled `order`, `visible`,
  `anchor`, `x`, `y`); other keys of the file are kept as they were.
- Files are read again when their size or modification time changes, so a
  hand edit shows at the next `workspace.get`.
- **Save as** carries the project's overrides to the new folder (unless that
  folder has its own), whether or not a `workspace.*` command ran since the
  project was opened; opening and closing projects switches the project layer.
  The service follows the project through the session's extension hooks
  (docs/02 §3), which `project.new`, `.open`, `.save` and `.close` call.

## Merge rules

1. Objects merge key by key, recursively; the higher layer wins per key.
2. Lists replace as a whole. A layer that sets `layout.left.panels` sets the
   whole list.
3. The `theme` object replaces as a whole, so tokens never mix between themes.
4. Scalars replace. `null` is a value: a project file may set
   `"engines": {"default": null}` over a user's engine.
5. `version` and `preset` are metadata and never merge.
6. Each layer is sanitised before it merges: an invalid value is removed from
   that layer, so the layer below shows through, and a warning is reported.
   Inside a list, an invalid entry is removed and the valid ones stay.
7. Unknown keys are kept, merge by the same rules, and are reported as
   `workspace.unknown_key` (information: a newer Stokes or a plug-in may own
   them).
8. Rules that need the whole picture run on the merged document:
   - A panel id appears in exactly one place: one slot's `panels`, or
     `layout.hidden`. When two layers place it differently, the place from the
     higher layer wins (the project moves a panel by listing it in another
     slot). Within one layer, the first place in the order left, right,
     bottom, float, hidden wins and a `workspace.duplicate` warning names the
     others.
   - The same for island groups across `bl`, `br`, `tl`, `tr`, `hidden`.
   - A built-in panel or island that is neither placed nor hidden returns to
     its default place (`workspace.restored`). To hide one, list it in
     `layout.hidden` or `islands.hidden`; an empty slot is not enough.
   - Every built-in top-bar item is in `topbar.items`; a missing one is put
     back after the item that precedes it in the default order, visible
     (`workspace.restored`). Hide an item with `"visible": false`.
   - Top-level tree groups missing from `tree.groupOrder` follow the listed
     ones in their default order (silently: a partial order is a valid way to
     say "these first").
   - `palette.open` always keeps a chord: the command palette can never be
     removed (ADR-0015 §5). An unbound palette gets `Ctrl+K` back
     (`workspace.required`).
   - A chord bound to more than one action is reported for each of them
     (`workspace.shortcut_conflict`); both bindings are kept.

## Reading is lenient, writing is strict

Files never fail: bad values become warnings. Commands that write refuse a
value that reading would drop, with `workspace.invalid` and nothing written:
a value of the wrong type or range, an unknown key, a duplicate list entry, or
a result that breaks a whole-document rule (an unbound palette). Paths the
schema does not know are refused with `workspace.path`. Writing inside a list
or the theme is checked as a write of the whole list or theme.

Every warning has the same shape, in `workspace.get` and in command results:

```json
{"code": "workspace.invalid_value", "kind": "invalid", "scope": "project",
 "path": "layout.left.mode", "message": "must be one of tabs, split; got \"grid\""}
```

| `kind` | Meaning |
|---|---|
| `invalid` | The value was left out (or replaced) and the layer below shows. |
| `unknown` | A key this schema does not know; kept unchanged. |
| `notice` | Information; nothing was lost. |

| `code` | Kind | When |
|---|---|---|
| `workspace.invalid_value` | invalid | wrong type, out of range, not an allowed value, missing a required key |
| `workspace.unknown_key` | unknown | a key the schema does not know |
| `workspace.unreadable` | invalid | a whole file was ignored (see Files) |
| `workspace.version` | notice or invalid | version missing or newer (notice), or not a positive integer (invalid); the document is read as version 1 |
| `workspace.duplicate` | notice | a duplicate list entry, or a second place of one panel or island in one layer, was removed |
| `workspace.restored` | notice | a built-in panel, island or top-bar item returned to its default place |
| `workspace.shortcut_conflict` | notice | one chord is bound to two or more actions **of the same context**. A chord shared across contexts is a design, not a clash: `tree.menu` (context `tree`) and `context.menu` (context `global`) both take `Shift+F10` and `ContextMenu`, and the tree consumes them while it has focus. An action id this build does not know counts as `global`, so an unfamiliar id cannot hide a real clash. |
| `workspace.required` | invalid | `palette.open` lost its chord and got its default back |
| `workspace.no_effect` | notice | a `--cup-*` token in a theme whose base is not Cupertino |
| `workspace.kept_aside` | notice | an unreadable file was moved to `.bak` before a write |
| `workspace.preset_name` | notice | a file in the user presets folder was skipped (bad name, or the name of a shipped preset) |

`scope` is `user`, `project`, `effective` (a whole-document rule),
`import`, or `preset:<name>`.

## Paths

Commands address values with a dotted path: `layout.left.size`,
`layout.left.panels.0.size`, `topbar.items.3.visible`, `theme.tokens.--accent`.

- A segment that is all digits is a list index when it meets a list. Writing at
  the index equal to the list's length appends an entry; other indices must
  exist. Negative indices are not supported.
- Keys that contain dots, the action ids of `shortcuts`, need no quoting: the
  longest run of segments that names an existing key is taken, so
  `shortcuts.view.fit` and `shortcuts.history.redo.1` work. A key with
  characters other than letters, digits, `_` and `-`, or one that is all
  digits, can always be written in brackets as a JSON string:
  `shortcuts["view.fit"]`; `[2]` is an explicit index.
- The **canonical** form, which `workspace.get` uses in `sources` and every
  result and warning uses in `path`, writes plain keys with dots, indices as
  `.N` and every other key in brackets: `shortcuts["history.redo"].1`.
  Front-end code that looks up `sources` must build keys the same way.
- Writing inside a list or the theme first copies the whole list or theme
  from what that scope sees (the layers below it plus its own document, after
  the whole-document rules), because those replace as a whole when layers
  merge.

## Sections

The complete default document is at the end of this file.

### `version`

The integer `1`. Stokes writes it first in every file. A missing version is
read as 1; a newer one is read as 1 with its unknown keys kept.

### `theme`

A built-in theme `{"name": N}` with N one of `dark`, `light`, `cupertino`,
`cupertino-dark`, `ubuntu`, `ubuntu-dark`, `auto`, `cupertino-auto`,
`ubuntu-auto` (default `auto`), or a custom theme
`{"base": B, "title": "...", "tokens": {"--token": "value", ...}}` with B one
of the base names above. Token names, kinds and value rules are in
[theme-tokens.md](theme-tokens.md).

### `layout`

Four slots and a hidden list:

| Key | Type | Meaning |
|---|---|---|
| `left`, `right`, `bottom`, `float` | slot | see below |
| `hidden` | list of panel ids | panels that show nowhere |

A slot:

| Key | Type | Default (left / right / bottom / float) | Meaning |
|---|---|---|---|
| `mode` | `"tabs"` or `"split"` | `tabs` | Several panels in a slot show as tabs, or stacked in a split (vertical for left and right, side by side for bottom). |
| `size` | number, 24 to 4096 | 300 / 320 / 240 / 320 | px: width of left and right, expanded height of bottom, default card width of float. The front end clamps to the panels' own minimum and maximum. |
| `collapsed` | boolean | false / false / true / false | Folded: left and right to their icon strip, bottom to the 36 px tab strip, float cards to their header. |
| `panels` | list of panel entries | tree / properties / output / none | The panels in the slot. |

A panel entry:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `id` | panel id (`[a-z][a-z0-9]*` with `.`, `_` or `-` between parts) | yes | `tree`, `properties`, `output`, `design-table` or `plots` today; registered plug-in panels later. Unknown ids are kept for the front end's registry to resolve. |
| `order` | integer, 0 to 1000 | filled from the position | Display order in the slot (lowest first; ties keep list order). |
| `size` | number, 24 to 4096 | no | In a split, the panel's share in px (height in left and right, width in bottom); panels without one share the rest equally. In float, the card's width. Ignored in tabs. |
| `collapsed` | boolean | no | In a split, the panel folded to its header. In float, the card folded. |
| `tabs` | list of tab ids, at least one | no | For `output`: which tabs show, in order, from `console`, `log`, `residuals`, `reports`. Absent means all four. |
| `anchor` | `"tl"`, `"tr"`, `"bl"`, `"br"` | float only, filled `tl` | The viewport corner `x` and `y` are measured from. |
| `x`, `y` | number, 0 to 10000 | float only, filled 24 + 32 × position | px from the anchor corner of the viewport to the near corner of the card. The front end keeps the card inside the viewport. |

`anchor`, `x` and `y` outside the float slot are unknown keys. The built-in
panels return to `left` (tree), `right` (properties) and `bottom` (output) when
they are neither placed nor hidden.

### `islands`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `bl`, `br`, `tl`, `tr` | list of island ids | `bl: ["modes"]`, `br: ["view", "gizmo"]`, others empty | The control groups floating in each viewport corner, in on-screen order from left to right (so in `br` the last one sits in the corner). |
| `hidden` | list of island ids | `[]` | Groups that show nowhere. |

Island ids today: `modes` (Section, Isolate, Measure, Mesh), `view` (Fit,
Perspective/Ortho, Display settings) and `gizmo` (the view cube). The cube is a
128 px box in its place of the corner's row, which the viewport draws it into
(`web/src/workspace/IslandLayer.tsx` reserves and measures the box,
`gizmoSlot.ts` hands it to `web/src/viewport/gizmo.ts`); in `islands.hidden` it
is not drawn at all. Hiding `view` also hides the mouse route to Display
settings; `Ctrl+,` and the palette still reach it, and `Ctrl+Shift+C` still
enters Customise mode.

### `topbar`

| Key | Type | Meaning |
|---|---|---|
| `items` | list of `{"id", "visible"}` | Every top-bar item in display order. `visible` defaults to true. |
| `stages.style` | `"chips"`, `"ribbon"` or `"hidden"` | How the stage control shows (default `chips`). |

Item ids today, in their default order: `logo`, `project`,
`status.connection`, `status.units`, `status.engine`, `status.licence`,
`stages`, `commands`, `appearance`, `run`, `window`. Items before `stages` go
in the left cell, items after it in the right cell, and the stage control is
centred. With `chips` the stage control is the segmented chips at the place of
the `stages` item; with `ribbon` it is a band under the top bar with the task
page of the current stage, and the `stages` item only marks where the cells
split; with `hidden` there is no stage control (the `1`–`6` keys still switch
the stage). `"visible": false` on `stages` hides the control in every style.
`window` is shown only when hosted; the Cupertino family draws it as traffic
lights at the left whatever its position.

### `tree`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `groupOrder` | list of top-level group names | `geometry, mesh, physics, regions, solver, monitors, scenes, runs, studies` | Order of the top-level groups; unlisted groups follow in their default order. |
| `hidden` | list of top-level group names | `[]` | Groups the tree does not show. Names, not paths: `runs`, not `/runs`. |
| `stages` | object: stage → list of group paths | see the example | Which groups a stage works in. Stages: `Geometry`, `Mesh`, `Physics`, `Solve`, `Results`, `Explore`. Paths start with `/` and may name sub-groups (`/physics/materials`). It replaces the front end's `STAGE_GROUPS`. |
| `linkStage` | boolean | `true` | Stage chips and the tree navigate each other: choosing a stage opens its groups and folds the others; selecting a node moves to its stage. |
| `defaultExpanded` | integer, 0 to 8 | `2` | Tree levels open when a project loads: 0 all folded, 1 the top-level groups open, 2 also the groups inside them (today's behaviour). |
| `layout` | `outline` or `groups` | `outline` | How the tree is arranged: the outline (sections and rows) or the plain groups. **Optional**, because it was added after documents were already saved - an absent value reads as `outline`. |

### `shortcuts`

An object from action id to binding. A binding is one chord (`"Ctrl+K"`), a
list of chords (the first is the one tooltips show), or `""` / `[]` for none.
Unknown action ids of the right form (`area.verb`, more dotted parts allowed)
are kept for plug-ins.

Chords are modifiers in the order `Ctrl`, `Alt`, `Shift`, `Meta`, then one key,
joined with `+`. `Ctrl` matches Control, or Command on macOS (handlers test
`ctrlKey || metaKey`, as today); `Meta` is the Windows or Command key itself.
Keys: `A`–`Z`, `0`–`9`, `` ` - = [ ] \ ; ' , . / ``, `Plus` (for `+`), `F1`–`F24`,
`Enter`, `Escape`, `Tab`, `Space`, `Backspace`, `Delete`, `Insert`, `Home`,
`End`, `PageUp`, `PageDown`, `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`,
`ContextMenu`. Input is forgiving (case, spaces for `+`, `Cmd`, `Option`,
`Esc`, `Del`, `Up` ...) and stored canonical. Match letters, digits and
punctuation by `KeyboardEvent.code` (`KeyF`, `Digit1`, `Comma`) so that Shift
and keyboard layouts do not change them. Global actions do not fire while a
text field has focus; tree actions fire when the tree has focus.

| Action id | Default | Context | What it does |
|---|---|---|---|
| `stage.1` | `1` | global | Go to the Geometry stage |
| `stage.2` | `2` | global | Go to the Mesh stage |
| `stage.3` | `3` | global | Go to the Physics stage |
| `stage.4` | `4` | global | Go to the Solve stage |
| `stage.5` | `5` | global | Go to the Results stage |
| `stage.6` | `6` | global | Go to the Explore stage |
| `view.fit` | `F` | global | Fit the model to the view |
| `view.fitAll` | `Alt+F` | global | Fit every body, the fluid included |
| `view.projection` | `O` | global | Switch between perspective and orthographic |
| `view.mode.cycle` | `V` | global | Cycle shaded, shaded with edges, wireframe |
| `view.nav.cycle` | `Shift N` | global | Cycle the mouse scheme: CAD, ParaView, Blender, Select/Orbit |
| `view.section` | `S` | global | Section view (not built yet) |
| `view.measure` | `M` | global | Measure: click two points on the model for the distance between them (see below) |
| `selection.isolate` | `I` | global | Isolate the selected patch, or show all again |
| `selection.name` | `Ctrl+Shift+T` | global | Tag the picked faces or bodies |
| `mesh.generate` | `Ctrl+G` | global | Generate the mesh |
| `mesh.task.next` | `Alt+ArrowDown` | global | Move to the next meshing task |
| `mesh.task.previous` | `Alt+ArrowUp` | global | Move to the previous meshing task |
| `pick.mode.cycle` | `B` | global | Switch between picking bodies and faces |
| `selection.grow` | `G` | global | Grow the selection along smooth surfaces |
| `selection.lasso` | `L` | global | Lasso: draw a box around what to pick |
| `selection.hide` | `H` | global | Hide or show the selected patch |
| `selection.clear` | `Escape` | global | Clear the selection and close the palette |
| `selection.mode` | none | global | Toggle body and face picking |
| `selection.previous` | none | global | Previous selection |
| `selection.paint` | `P` | global | Paint select: drag over faces to add them, Ctrl to remove them |
| `prepare.enclosure` | none | global | Enclose the picked bodies |
| `prepare.extractFluid` | none | global | Extract the fluid around a seed point |
| `prepare.boolean` | none | global | Fuse, cut or intersect the picked bodies |
| `prepare.repair` | none | global | Heal, sew or solidify the picked bodies |
| `palette.open` | `Ctrl+K` | global | Open the command palette (always keeps a chord) |
| `history.undo` | `Ctrl+Z` | global | Undo |
| `history.redo` | `Ctrl+Y` or `Ctrl+Shift+Z` | global | Redo |
| `project.save` | `Ctrl+S` | global | Save the project |
| `project.saveAs` | `Ctrl+Shift+S` | global | Save the project to a new folder |
| `project.open` | `Ctrl+O` | global | Open a project |
| `project.importStep` | `Ctrl+I` | global | Import a geometry file into the project |
| `project.new` | `Ctrl+N` | global | Create a new project |
| `project.saveCopy` | none | global | Copy the project without switching to it |
| `project.close` | `Ctrl+W` | global | Close the project |
| `project.startScreen` | none | global | Go to the Start screen |
| `display.settings` | `Ctrl+,` | global | Open Display settings |
| `workspace.customise` | `Ctrl+Shift+C` | global | Enter or leave Customise mode |
| `layout.toggleTree` | `Ctrl+B` | global | Show or hide the tree |
| `layout.toggleDetails` | `Ctrl+Shift+B` | global | Show or hide the Details pane |
| `layout.reset` | — | global | Reset the panel layout |
| `app.settings` | `Ctrl+Shift+,` | global | Open Settings |
| `selection.clearPick` | none | global | Clear the pick |
| `context.menu` | `Shift+F10` or `ContextMenu` | global | Open the context menu |
| `view.fitSelection` | none | global | Fit the selection to the view |
| `view.showAll` | `Shift+F` | global | Show every hidden body again |
| `view.from.front` | `Alt+1` | global | Look from the front |
| `view.from.back` | `Alt+2` | global | Look from the back |
| `view.from.left` | `Alt+3` | global | Look from the left |
| `view.from.right` | `Alt+4` | global | Look from the right |
| `view.from.top` | `Alt+5` | global | Look from the top |
| `view.from.bottom` | `Alt+6` | global | Look from the bottom |
| `view.from.iso` | `Alt+0` | global | Look from an isometric corner |
| `selection.siblings` | none | global | Select the parts of the same file |
| `selection.allFaces` | none | global | Select every face of the body |
| `edit.rename` | none | global | Rename the selection |
| `edit.delete` | none | global | Delete the selection (with an Undo toast) |
| `edit.copyName` | none | global | Copy the selection's name |
| `edit.copyPath` | none | global | Copy the selection's path |
| `tree.rename` | `F2` | tree | Rename the selected node |
| `tree.delete` | `Delete` | tree | Delete the selected node (with an Undo toast) |
| `tree.menu` | `Shift+F10` or `ContextMenu` | tree | Open the row menu |

Tree navigation (arrows, `Home`, `End`, `PageUp`, `PageDown`, `Enter`) and the
properties keys (`Enter` applies, `Esc` reverts) are field behaviour, not
actions, and cannot be rebound.

**Measure** (`view.measure`, `M`, the Modes island's Measure button) is view state,
not a command: it changes no model state and writes no journal record, like the scale
bar. While the mode is on, the button shows its active state and every click that
meets a face records the surface point under the pointer — the ray's point, with no
vertex or edge snapping. The first click sets point A, the second sets point B and
completes the measurement: a line with a marker at each end, drawn in world space in
the `--accent` colour so it stays where it was measured while the camera moves, and
the distance (`formatLength`'s km/m/mm/µm ladder) as a label at the projected
midpoint, in the same small tabular `--text` language as the scale bar. A third click
starts a new measurement at the point it picked, so two unrelated distances are never
chained through a point the user did not click. `Esc` leaves the mode and takes the
measurement with it.

### `viewport`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `projection` | `"perspective"` or `"orthographic"` | `perspective` | Camera projection when a project opens. |
| `viewMode` | `"shaded"`, `"edges"` or `"wireframe"` | `shaded` | View mode when a project opens. |
| `grid` | boolean | `true` | Ground grid shown: its lines, its axes and the faint floor under the part (the contact shadow is not part of it). The viewport follows the value at once. |
| `background` | `"theme"` or a hex colour | `theme` | `theme` uses the `--vp-top`/`--vp-bottom` gradient; a colour (stored as `#rrggbb`) fills the viewport with it. |

These are defaults for a new view. The `O` and `V` keys change the current view
without writing the workspace.

### `engines`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `default` | engine id or `null` | `null` | The engine Run uses (ADR-0011). Per project with a user fallback: set it in the project scope for one project. |
| `runPresets` | object: name → object | `{}` | Named run settings, opaque to the workspace and owned by the engine adapters. Names: a letter, then letters, digits, `_` or `-`. |

### `preferences`

**The user's own settings, and never the project's.** A project file is shared, so a mouse scheme in
one would decide how the interface behaves for whoever opens it — a choice made by someone else,
arriving in a file they were sent. `workspace.set` with `scope=project` refuses a path under this
section (`workspace.user_only`), `sanitise(…, "project")` drops a section it finds on disk with the
same finding, and **presets never carry it**: `catalog.save` drops it, so applying a preset cannot
impose the saver's scheme. "Reset workspace" resets it with the rest of the user scope.

Its keys merge one at a time — the section is not atomic, and not part of `ARRANGEMENT`.

| Key | Type | Default | Meaning | Who reads it |
|---|---|---|---|---|
| `mouse.scheme` | `cad`, `paraview`, `blender`, `toggle` | `paraview` | The mouse scheme. **The ids are the front end's** (`web/src/app/navModes.ts`), which is what applies them; a name invented here would validate on write and do nothing. | the viewport (T-178 wires the dialog) |
| `mouse.orbitLeft` | flag | `false` | Left-drag orbits instead of picking, without changing the scheme. | the viewport |
| `import.units` | `mm`, `cm`, `m`, `in`, `ft` | `mm` | The unit a CAD file is assumed to be drawn in when it declares none — the units dialog's starting answer. | the import flow |

## Commands

All are free and need no open project (`requires_project` false). The writing
ones are journaled (`mutates` true) and are not on the project undo stack.
`scope` is `user` (default) or `project`; project scope needs a project saved to
a folder, otherwise `api.no_project` (no project) or
`workspace.no_project_folder` (an unsaved project).

| Command | Parameters | Result | Event |
|---|---|---|---|
| `workspace.get` | `path?` | Without `path`: `{version, document, sources, warnings, files}`. `document` is the effective workspace; `sources` maps every canonical leaf path (a value, a list, the theme) to `default`, `user` or `project`; `files` is `{user: {path, exists, readable}, project: {...} or null}`. With `path`: `{path, value, source, sources, warnings}` for that subtree. | none |
| `workspace.set` | `path`, `value`, `scope?` | `{scope, path, value, effective, source, warnings}` and `note` when a higher scope overrides the path. `value` is what was stored (canonical); `effective` is what shows. | `{scope, path}` |
| `workspace.reset` | `scope`, `path?` | `{scope, path, removed}`. Without `path` the scope's file is deleted; with it, that key is removed (empty objects are pruned) so the layer below shows. A list is one value: reset the whole list, not an entry. | `{scope, path}` if something was removed |
| `workspace.presets` | none | `{presets: [{name, title, description, source, sections, path?, warnings?}], warnings}` | none |
| `workspace.apply_preset` | `name`, `scope?` | `{name, title, scope, sections, overridden, warnings}`. `overridden` lists the sections the project still overrides when the user scope was written. | `{scope, path: null}` |
| `workspace.save_preset` | `name`, `title?`, `description?` | `{name, path, sections}`: the effective workspace saved as `~/.stokes/presets/<name>.json`. Names are 1–64 of `a-z 0-9 _ -`, not a shipped name; an existing user preset is replaced. **A preset never carries `preferences`** (see below). | `{scope: "presets", path: name}` |
| `app.info` | none | `{version, python, platform, home, workspace: {user, project}}`. Where Stokes keeps its files and what it runs on: what the Settings → About page reads. **No machine name, no user name, no environment dump** — `platform` is `platform.platform()`, never `platform.node()`. `workspace.project` is `null` when no project is open. Needs no project. | none |
| `app.notices` | none | `{text, source}`: the Markdown of `THIRD-PARTY-NOTICES.md`, with `source` `"packaged"` (a built wheel) or `"repository"` (a checkout). Capped at 1 MiB; missing in both places raises `app.notices_missing` naming both paths. Needs no project. | none |
| `workspace.export` | `scope?` (`effective` default, `user`, `project`), `sections?` | `{scope, sections, text}`: a workspace document as JSON text (a scope's document is exported sanitised). | none |
| `workspace.import` | `text`, `scope?` | `{scope, sections, overridden, warnings}`. Each section the document holds replaces that section in the scope; invalid values are left out and reported. Accepts exports, preset files and theme files. | `{scope, path: null}` |

Error codes: `workspace.invalid` (a value, a document or a parameter),
`workspace.path` (a path), `workspace.unknown_preset`,
`workspace.no_project_folder`, `workspace.io` (the disk refused a write), plus
the registry's `validation` for a missing parameter. A refused value carries
`details.warnings`: the findings that caused the refusal.

The registry treats a JSON `null` parameter as absent, so `workspace.set` cannot
store `null`; remove the override with `workspace.reset scope=... path=...`
instead.

## Events

`workspace.changed {scope, path}` after every write: `scope` is `user`,
`project` or `presets`; `path` is the canonical path written, or `null` when a
whole document changed (reset, preset, import, and the project layer appearing
or going away when a project with overrides opens, closes or is saved to a new
folder). The front end refetches `workspace.get` on every `workspace.changed`
and applies the result within one frame; a theme change must not rebuild the
scene.

## Presets

A preset file is a workspace document with
`"preset": {"title": "...", "description": "one line"}`. Applying one replaces,
in the target scope, every section it carries. The shipped presets carry the
four arrangement sections (`layout`, `islands`, `topbar`, `tree`), so they
rearrange the interface and keep the user's theme, shortcuts, viewport and
engine choices. A saved user preset carries all eight sections, so applying it
reproduces the whole workspace. *Reset workspace* is `workspace.reset`, not a
preset.

| | Stokes (default) | Classic | Ribbon | Workflow | Minimal |
|---|---|---|---|---|---|
| For users of | `web/DESIGN.md` | STAR-CCM+ | Fluent | SimScale | anyone who wants the viewport |
| `left` | tree, tabs, 300 | tree over properties, **split**, 340 (tree 420 px) | tree, 300 | tree, 320 | empty |
| `right` | properties, 320 | **output**, 440, open | properties, 340 | properties, 340 | empty |
| `bottom` | output, 240, folded | empty | output, 220, **open** | output, 240, folded | output, 240, folded |
| `float` | empty | empty | empty | empty | **properties card**, top-right, 16/16 px, 320 wide |
| `layout.hidden` | none | none | none | none | **tree** |
| `islands` | modes bl; view, gizmo br | **modes tl**; view, gizmo br | as Stokes | as Stokes | view, gizmo br; **modes hidden** |
| `stages.style` | chips | chips | **ribbon** | **hidden** (`stages` item hidden) | **hidden** |
| top-bar items hidden | none | none | none | `stages` | status ×4, `stages`, `appearance` |
| `tree.groupOrder` | default | default | default | **geometry, physics, regions, mesh, solver, monitors, runs, scenes** | default |
| `tree.linkStage` | true | true | true | **false** | true |
| `tree.defaultExpanded` | 2 | 2 | 2 | **1** | 2 |
| `tree.layout` | outline | outline | outline | outline | outline |

## Consuming the document (front end)

What `web/src/workspace/` must provide to render and edit this document:

- **Load and follow.** Call `workspace.get` at start, after reconnecting and on
  every `workspace.changed`; render `document`. Show `warnings` of kind
  `invalid` in Customise mode (with `path` and `message`); `notice` and
  `unknown` belong in the log.
- **Theme.** Apply `theme` as [theme-tokens.md](theme-tokens.md) describes; the
  theme editor writes a whole theme (`workspace.set path=theme`).
- **Panel registry.** Each panel, island and top-bar item is an entry with id,
  title, icon, component, default place and minimum and maximum size. Ids that
  the registry does not know are skipped (they belong to a plug-in that is not
  loaded) and stay in the document.
- **Slot layout engine.** Replaces the fixed `PanelSlot` placement: renders the
  four slots from `layout`, tabs or split per slot, sizes clamped by the
  registry, collapsed states, float cards placed by `anchor`, `x`, `y`, and
  keeps the islands and the context toolbar clear of the occupied slots (the
  `--tree-width`/`--props-width` outputs generalise to every slot). Panel
  widths and the drawer height move from `localStorage` (`app/prefs.ts`) into
  the workspace; `prefs.ts` keeps only per-device conveniences such as reduced
  motion and the first-run hint.
- **Islands and top bar** from `islands` and `topbar`, split at `stages`, with
  the three stage styles (the ribbon and its task page are new).
- **Tree** from `tree`: group order and hidden groups, the stage mapping in
  place of `STAGE_GROUPS`, `linkStage`, `defaultExpanded`.
- **Keymap** built from `shortcuts` in place of the hard-coded handlers in
  `App.tsx`, `ProjectMenu.tsx` and `Islands.tsx`; tooltips and
  `aria-keyshortcuts` show the first chord of each binding.
- **Viewport defaults** from `viewport` when a project opens; **Run** uses
  `engines.default`.
- **Customise mode** (`Ctrl+Shift+C`, Display settings → Customise, the
  palette): handles and drop zones for panel headers, island groups and top-bar
  items. A drop is one `workspace.set` of the whole changed section or list
  (for example `layout`), so the move is atomic. Every panel menu has *Move
  to…*; the palette offers the same actions. A scope toggle (this project /
  all projects) chooses `scope`. *Reset workspace* and the command palette are
  always reachable.

## Example: the default document

This is `workspace.get`'s `document` with no user or project file, and what
`workspace.export` writes then. A test compares this block with
`stokes.workspace.defaults.default_document()`.

```json
{
  "version": 1,
  "theme": {
    "name": "auto"
  },
  "layout": {
    "left": {
      "mode": "split",
      "size": 300,
      "collapsed": false,
      "panels": [
        {
          "id": "tree",
          "order": 0
        },
        {
          "id": "details",
          "order": 1
        }
      ]
    },
    "right": {
      "mode": "tabs",
      "size": 320,
      "collapsed": true,
      "panels": []
    },
    "bottom": {
      "mode": "tabs",
      "size": 240,
      "collapsed": true,
      "panels": [
        {
          "id": "output",
          "order": 0,
          "tabs": [
            "console",
            "log",
            "residuals",
            "reports"
          ]
        },
        {
          "id": "design-table",
          "order": 1
        }
      ]
    },
    "float": {
      "mode": "tabs",
      "size": 320,
      "collapsed": false,
      "panels": []
    },
    "hidden": [
      "properties",
      "mesh",
      "conditions",
      "solve",
      "results",
      "plots"
    ]
  },
  "islands": {
    "bl": [
      "modes"
    ],
    "br": [
      "view",
      "gizmo"
    ],
    "tl": [],
    "tr": [],
    "hidden": []
  },
  "topbar": {
    "items": [
      {
        "id": "logo",
        "visible": true
      },
      {
        "id": "project",
        "visible": true
      },
      {
        "id": "status.connection",
        "visible": true
      },
      {
        "id": "status.units",
        "visible": true
      },
      {
        "id": "status.engine",
        "visible": true
      },
      {
        "id": "status.licence",
        "visible": true
      },
      {
        "id": "stages",
        "visible": true
      },
      {
        "id": "commands",
        "visible": true
      },
      {
        "id": "appearance",
        "visible": true
      },
      {
        "id": "run",
        "visible": true
      },
      {
        "id": "window",
        "visible": true
      }
    ],
    "stages": {
      "style": "chips"
    }
  },
  "tree": {
    "groupOrder": [
      "geometry",
      "mesh",
      "physics",
      "regions",
      "solver",
      "scenes",
      "runs",
      "studies"
    ],
    "hidden": [],
    "stages": {
      "Geometry": [
        "/geometry"
      ],
      "Mesh": [
        "/mesh"
      ],
      "Physics": [
        "/physics",
        "/regions"
      ],
      "Solve": [
        "/solver",
        "/runs"
      ],
      "Results": [
        "/scenes",
        "/runs"
      ],
      "Explore": [
        "/studies"
      ]
    },
    "linkStage": true,
    "defaultExpanded": 2,
    "layout": "outline"
  },
  "shortcuts": {
    "stage.1": "1",
    "stage.2": "2",
    "stage.3": "3",
    "stage.4": "4",
    "stage.5": "5",
    "stage.6": "6",
    "view.fit": "F",
    "view.fitAll": "Alt+F",
    "view.projection": "O",
    "view.mode.cycle": "V",
    "view.nav.cycle": "Shift+N",
    "view.section": "S",
    "view.measure": "M",
    "selection.name": "Ctrl+Shift+T",
    "mesh.generate": "Ctrl+G",
    "mesh.task.next": "Alt+ArrowDown",
    "mesh.task.previous": "Alt+ArrowUp",
    "pick.mode.cycle": "B",
    "selection.grow": "G",
    "selection.lasso": "L",
    "selection.isolate": "I",
    "selection.hide": "H",
    "selection.clear": "Escape",
    "selection.mode": [],
    "selection.previous": [],
    "selection.paint": "P",
    "prepare.enclosure": [],
    "prepare.extractFluid": [],
    "prepare.boolean": [],
    "prepare.repair": [],
    "palette.open": "Ctrl+K",
    "history.undo": "Ctrl+Z",
    "history.redo": [
      "Ctrl+Y",
      "Ctrl+Shift+Z"
    ],
    "project.save": "Ctrl+S",
    "project.saveAs": "Ctrl+Shift+S",
    "project.open": "Ctrl+O",
    "project.importStep": "Ctrl+I",
    "project.new": "Ctrl+N",
    "project.saveCopy": [],
    "project.close": "Ctrl+W",
    "project.startScreen": [],
    "display.settings": "Ctrl+,",
    "workspace.customise": "Ctrl+Shift+C",
    "layout.toggleTree": "Ctrl+B",
    "layout.toggleDetails": "Ctrl+Shift+B",
    "layout.reset": [],
    "app.settings": "Ctrl+Shift+,",
    "selection.clearPick": [],
    "context.menu": [
      "Shift+F10",
      "ContextMenu"
    ],
    "view.fitSelection": [],
    "view.showAll": "Shift+F",
    "view.from.front": "Alt+1",
    "view.from.back": "Alt+2",
    "view.from.left": "Alt+3",
    "view.from.right": "Alt+4",
    "view.from.top": "Alt+5",
    "view.from.bottom": "Alt+6",
    "view.from.iso": "Alt+0",
    "selection.siblings": [],
    "selection.allFaces": [],
    "edit.rename": [],
    "edit.delete": [],
    "edit.copyName": [],
    "edit.copyPath": [],
    "tree.rename": "F2",
    "tree.delete": "Delete",
    "tree.menu": [
      "Shift+F10",
      "ContextMenu"
    ]
  },
  "viewport": {
    "projection": "perspective",
    "viewMode": "shaded",
    "grid": true,
    "axisLines": true,
    "shadow": true,
    "reflections": true,
    "edges": true,
    "lowDetail": false,
    "gizmo": true,
    "scaleBar": true,
    "background": "theme"
  },
  "engines": {
    "default": null,
    "runPresets": {}
  },
  "preferences": {
    "mouse": {
      "scheme": "paraview",
      "orbitLeft": false
    },
    "import": {
      "units": "mm"
    },
    "compute": {
      "host": "",
      "install": "",
      "auto": true
    },
    "mesh": {
      "stepper": "shown"
    }
  }
}
```

A user document that makes the console sit on the right, uses a custom accent
and binds Fit to `G`:

```json
{
  "version": 1,
  "theme": {"base": "dark", "tokens": {"--accent": "#ff8a3d"}},
  "layout": {
    "right": {"mode": "split", "panels": [{"id": "properties", "order": 0}, {"id": "output", "order": 1, "tabs": ["console"]}, {"id": "plots", "order": 2}]},
    "bottom": {"panels": []},
    "hidden": ["design-table"]
  },
  "shortcuts": {"view.fit": "Ctrl+Shift+F"}
}
```
