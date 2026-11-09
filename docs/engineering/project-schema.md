# Projects: opening, choosing and previewing them

**Format 1** · node types: `stokes/model/` · commands: `stokes/api/commands/project.py` · tests:
`tests/test_project_start_screen_commands.py`, `tests/test_model_recent.py`,
`tests/test_model_browse.py`, `tests/test_model_preview.py`, `tests/test_project_preview_route.py`

A project is a **folder** holding `project.json` and its caches. This document is the contract for the
commands a **start screen** needs - what to offer, where to look, and what a thumbnail is - plus the
one route that serves one.

The start screen itself is the front end's; this is everything it reads.

## The commands

| Command | Parameters | Result | Notes |
|---|---|---|---|
| `project.recent` | none | `{projects: [{name, path, modified, opened, exists, has_preview}]}` | most recent first, at most 24. **No project needed** - this is what a start screen calls first. |
| `project.recent.remove` | `path` | `{removed, projects}` | removes **the list entry only**. The folder is never touched. |
| `project.browse` | `path?` | `{path, parent, roots, entries: [{name, kind, modified}], truncated}` | `kind` is `"project"` or `"dir"`; at most 500 entries |
| `project.preview.set` | `png_base64` | `{path, bytes}` | validates before writing; `project.preview_invalid` |

**`project.new` with a path, `project.open` and `project.save` all record the project.** A project
created from the start screen must appear there next time; that is the commonest case and the one that
was missing at first, because `project.new` saves through its own branch.

**`exists` and `has_preview` are computed on every call, never stored.** A stored `exists` goes stale
exactly when a start screen needs it - and a row that claims to open something it cannot is worse than
one that says the folder is gone. **A project that has moved or been deleted stays listed**, with
`exists: false`.

**`project.recent.remove` and `project.preview.set` are `mutates=False`.** Removing a list entry
changes nothing about the project and is not undoable; a picture is not model state and storing one
must not make the project look modified. A test compares `project.json`'s `modified` before and after a
preview write.

## Where a listing may look

`project.browse` is confined to **two roots**: the server user's home, and `$STOKES_HOME/projects`
(created on demand). A `path` is **resolved** and must land inside one of them, so `..`, a symlink and
an absolute path all fail the same check.

| Rule | Why |
|---|---|
| resolve, then test containment | a string test is what gets bypassed - `/x/projects-evil` starts with `/x/projects` |
| **symlinks are skipped, not followed** | a link inside a root that points outside is the classic way out |
| hidden names are skipped | `.ssh`, `.git` and `.config` are never projects |
| directories and project folders only | a listing that could reveal arbitrary files is a file browser |
| at most 500 entries, `truncated` reported | a home directory must not be able to exhaust memory |
| no file contents, ever | only a name, a kind and a modification time |
| **a refused path is not echoed** | it is a client's string, and a message is where it would be logged |

**The default listing is `$STOKES_HOME/projects`, not home.** Home is a root because a user's projects
can be anywhere in it, but opening there greets a user with `Videos` and their source trees.

## The thumbnail

`preview.png` lives **in the project folder**, not in the recent list: a list holding binary data for
projects that live anywhere would be an orphan image the moment one is deleted. It is written by
`project.preview.set` and read by the route below.

Validation happens **before anything touches the disk**, and refuses rather than repairs:

* the **PNG signature**;
* the **IHDR dimensions**, 16 to 1024 per edge - **read from the header, not by decoding**, so a
  20000x20000 decompression bomb is refused from eight bytes rather than expanded;
* a **512 KiB** cap;
* a **symlinked `preview.png` is refused** - a link there is a write somewhere else;
* base64 is decoded with **`validate=True`**, as the licence code does: without it Python ignores
  characters outside the alphabet and returns confidently wrong bytes.

## The route

| Route | Reply |
|---|---|
| `GET /api/project/preview?path=…` | `image/png`, with `ETag` and `Last-Modified` |

**This is the one place a start screen asks the server for a file by path**, so the containment is the
design:

1. the path must name a project **the app has already offered** - in the recent list, or inside a
   browse root;
2. it must hold a **`project.json`** - inside a root is not enough, or any folder with a
   `preview.png` in it would be servable;
3. `preview.png` must be a **regular file and not a symlink**, and within the size cap.

`ETag` is the file's own `(mtime, size)`, so an unchanged thumbnail answers **304** and a start screen
that redraws often does not re-fetch a picture that rarely changes. This route is **cacheable** where
every other route in the server answers `Cache-Control: no-store`.

**404 `project.no_preview`** for a project with no thumbnail, so a tile can show a placeholder rather
than a broken image.
