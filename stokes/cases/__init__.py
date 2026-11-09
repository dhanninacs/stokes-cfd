"""External OpenFOAM 14 cases as an expert case type (ADR-0029).

## Responsibility

`catalogue` finds and classifies the cases under a tutorial folder; `facts` reads what one case
needs (solver, libraries, etc files, runtime code, Allrun steps) from its files; `roots` keeps the
catalogue folder the user chose in the user's settings. `recipe` holds the argv steps and the
default recipe, `allrun` derives them from an Allrun without a shell (T-306), and `resources` the
shared files it copies in; `model` declares the `foam_case` node type, `store` keeps the
read-only content-addressed copy of an imported case and `importer` ties them together for
`case.import` (T-307).

## Rules

* **Read only.** Nothing here writes inside a tutorial folder or an install, and nothing from a
  case is executed: dictionaries go through the span-preserving reader
  (`stokes.compile.foam_read`), Allrun is read as text. The one write is `store`'s copy into
  the project's `cases/` folder.
* **No tutorial content in the repository** (ADR-0029 decision 2): tests author synthetic cases.
* **The agent's rules come in as parameters**: the external lint and argv allow-list live in
  `stokes_agent`, which this package may not import; the `case.*` commands pass them in.

Import rules: headless. Standard library, `stokes.errors`, `stokes.log`, `stokes.workspace_root`,
`stokes.model.ids`, `stokes.model.schema` (inside `model`'s functions) and
`stokes.compile.foam_read` only; never Qt, VTK, OCP, `stokes.api`, `stokes.run` or `stokes_agent`.
"""
