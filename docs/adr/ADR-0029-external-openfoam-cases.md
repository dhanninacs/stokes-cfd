# ADR-0029 · External OpenFOAM 14 cases as an expert case type

**Status:** proposed · 2026-10-28 · amends ADR-0024 (decisions 1.2 and 1.8) and ADR-0008 (consequences); relies on ADR-0001, ADR-0013, ADR-0025, ADR-0027, ADR-0028

## Context

The owner wants every OpenFOAM 14 tutorial to load, run and show results in the desktop app. The
FoamWin 14 install ships two runnable tutorials; the OpenFOAM 14 source tree ships 260 cases in 21
categories. Re-expressing each case in the engineer-facing model would take years of writers (ADR-0027
and ADR-0028 took weeks for one equation each). A sweep of the 260 cases on this PC (`.local/sweep/`)
ran 35 to a time directory and 9 to the 10-minute cap; 59 failed, some of them only because the sweep
did not mesh Allrun-driven cases. Agent rules today (ADR-0024 1.2 and 1.8, `stokes_agent/lint.py`)
refuse the constructs these cases use: `libs`, `#includeEtc`, `#includeFunc`, runtime code, shell
Allrun scripts.

## Decision

1. A `foam_case` node holds `source` (`install` + tutorial id, or `folder` + absolute path), `snapshot`
   (sha256 of the content-addressed copy), `dialect` (`of14`), `solver` (derived: a module name, a
   `regionSolvers` map, or a legacy application), `recipe` (derived argv steps) and `overrides`
   (ordered list). Commands are `case.*` (ADR-0013: the front end calls them and renders the result).
2. **Nothing GPL enters the repository or the wheel.** Stokes ships no tutorial, no catalogue file
   copied from OpenFOAM and no tutorial-derived golden. The catalogue is read at run time from the
   user's install (`$WM_PROJECT_DIR/tutorials`) or a folder the user sets. Tests use synthetic cases
   authored in the test; engine tests read `STOKES_OF14_TUTORIALS` and skip when it is unset.
3. Import copies the case, and the `resources/` files its Allrun or `#include`s reference, into the
   user's project under `<name>.stokes/cases/<node id>/source/`. The copy is content-addressed and
   read-only. The install and the source folder are never written.
4. Staging is a pure function `write(snapshot, overrides) -> dict[path, bytes]`. Binary and
   unparsed files pass through by hash. An override is `(file, dict path, value)`. Values are typed by
   a schema where one is known (quantities in SI with expression text, rule 6), and otherwise are typed
   OpenFOAM tokens (word, scalar, vector, list). Raw text containing `#` directives is never accepted
   as a value.
5. A new lint profile, `external`, runs in the agent. It refuses `#codeStream`, `#calc`, `#eval`,
   `coded*`, `systemCall`, and `#include` of absolute paths or paths that escape the job. It allows
   `#includeEtc "caseDicts/…"` and `#includeFunc <name>` only when the target exists under the probed
   `$FOAM_ETC`. It allows `libs` only with names found in the probed install's library list. A refused
   case shows the reason and the file and line in the GUI, and never stages.
6. Execution is a recipe of argv steps. The recipe comes from Allrun by a restricted parser that never
   runs a shell, or from the default `[blockMesh (if a blockMeshDict exists), solver]`. Unparsed Allrun
   lines are listed, and the user can edit the recipe (recorded as an override). Parallel steps use
   ADR-0025's ranks and decomposition.
7. Results are generic: fields are discovered from the case, at any time and in any region, and the
   viewer offers what the reader found.
8. Tutorial import is in the free tier. This is an assumption, recorded in `docs/00-open-questions.md`.

### Amendments to accepted decisions

- **ADR-0024 decision 1.8** ("the desktop never forwards a user-supplied dictionary") is amended. An
  external case's dictionaries are re-printed by `stokes.compile.foam` from a parsed tree only where an
  override touches them. Every other file passes through byte-identical. Every staged file passes the
  `external` lint profile (decision 5).
- **ADR-0024 decision 1.2** (the utility allow-list, and `-solver` pinned to two names) is amended. The
  utility set grows to the list in the plan's T-305. `-solver` accepts any module the probe found in the
  install (`lib<name>Solver` present), and never a free word.
- **ADR-0008 consequence** ("the model stays free of dialect vocabulary") is amended. A `foam_case` node
  is dialect-bound by definition and records `dialect: of14`. The engineer-facing tree stays dialect-free.
- ADR-0028 is not contradicted. Its kOmegaSST writer stays as it is. An imported case carries its own
  `momentumTransport`.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Re-express each solver in the engineer-facing model | Years of writers. Results would differ from the reference tutorial, which is a fidelity loss the owner did not ask for. |
| Ship the tutorials inside the product | GPL content in the wheel breaks non-negotiable rule 1. |
| Run user dictionaries with no lint | Breaks ADR-0024's security stance. Refused. |
| Run Allrun scripts through a shell | ADR-0024 1.2 forbids a shell. Replaced by a restricted parser that derives argv steps. |

## Consequences

- The project gains a second, labelled case type: the engineer-facing model stays the default for CAD-in
  work, and an imported case is an expert case type beside it.
- A dictionary reader is needed (`stokes/compile/foam_read.py`, plan T-303). It is a security boundary,
  reviewed at tier T0.
- Results viewing must stop assuming U, p and T at the latest time. Plan T-310 covers that.
- FoamWin lacks several solver modules and libraries, so some imported cases will show "engine lacks X"
  rather than run. The coverage report records each case's verdict.
- The `tests/fixtures/of14/` copies of tutorial content are inconsistent with decision 2 and ship in the
  sdist. The owner decides whether to remove them. Until then no new copies are added.
- Owner review is required before this moves from `proposed` to `accepted`.

## Amendment 2026-11-04: text edits of whole files

The node carries `file_edits`, a list of `{file, text}`, beside `overrides`. `case.file_write` stores a
file's whole text after linting it with the same `external` profile the agent applies at staging
(`case._checks`), so the in-app text editor is not a way around decision 5: a refused text is never
stored. Limits: a file of the copy, UTF-8 text without NUL, at most 256 KB, at most 64 files. A saved
text wins over the key edits of that file and drops them in the same undo step; `case.file_revert`
drops it. Staging writes each text over the copy's file after the key edits; the run basis includes
`file_edits` only when there are some, so earlier runs keep their basis.
