# Coding standards

The summary is in `CLAUDE.md` §3. This is the full text. When the two
disagree, this file wins and `CLAUDE.md` gets fixed.

## Language and tooling

- Python 3.13 is the baseline; 3.14 runs in CI. No syntax newer than 3.13.
- `ruff` is the linter and formatter; its configuration in `pyproject.toml`
  is the standard. Do not add `# noqa` without a reason on the same line.
- `mypy --strict` over `stokes/`, `agent/` and `tools/`. Tests are typed too,
  with `disallow_untyped_defs` relaxed. `Any` is allowed only with a comment
  that names the reason (`# Any: raw JSON from the user file`).
- Type aliases live in the module that owns the concept; `typing.Protocol`
  for structural interfaces (runners, dialects, providers), ABCs only when a
  shared implementation exists.

## Structure

- One responsibility per module. Keep modules under about 500 lines; if a
  module grows past that, split by responsibility, not by line count.
- Packages declare their import rules in the package docstring; the layering
  gate (`tools/check_forbidden_imports.py`) enforces the Qt/VTK/OCP rule, and
  reviewers enforce the rest: `model` imports nothing from `api`; `api`
  imports nothing from `cli`, `server`, `mcp`, `ui`; `compile` imports nothing
  from `run`.
- No circular imports. If two modules need each other, a third module owns
  the shared types.
- No module-level side effects except registering node types and commands,
  and those registrations must be idempotent.
- Heavy imports (numpy-heavy modules, VTK, Qt, OCP, pint registries) are
  imported lazily inside the function that needs them when they are not
  needed for the module's primary job. `import stokes.api` must stay under
  300 ms.

## Data and behaviour

- `@dataclass(frozen=True)` for value objects (specs, definitions, records);
  mutable dataclasses only for documents that are mutated through commands.
- Plain classes for behaviour with explicit dependencies passed in the
  constructor. No service locators, no globals holding state. Registries
  (node types, commands) are the only module-level singletons, and tests can
  build their own instances.
- Inheritance at most two levels deep. Prefer composition and Protocols.
- Functions are small, pure where possible, and return values rather than
  mutating arguments. Side effects live in commands, runners and the agent.
- Timestamps are timezone-aware UTC (`datetime.now(timezone.utc)`), stored as
  ISO 8601 strings.
- Identifiers: node ids are short random strings created by
  `stokes.model.ids.new_id`; paths are derived from names and are not
  persisted.

## Errors

- All errors derive from `stokes.errors.StokesError` and carry a stable
  `code` (`units.mismatch`, `model.unknown_node`, `licence.feature_missing`).
  The same objects are serialised to UI dialogs, CLI output, HTTP responses
  and MCP tool errors through `StokesError.to_dict()`.
- Validate at boundaries (CLI arguments, API parameters, file loads, agent
  frames), then trust the typed objects inside.
- Never catch `Exception` broadly unless you re-raise a `StokesError` with the
  cause chained (`raise X from exc`) or log with `exc_info=True` and stop the
  operation. Never swallow errors to keep going.
- Messages are for engineers: say what was expected, what was found, and
  where (`node /physics/parameters/U_in`, `line 12 of fvSolution`).

## Logging

- `from stokes.log import get_logger; log = get_logger(__name__)`.
- Structured JSON lines; pass data as keyword arguments through `extra`
  helpers, not formatted into the message.
- Never log file contents, CAD data, tokens, keys, licence payloads or full
  environment dictionaries. Paths are fine.
- `print` only in `stokes.cli` output paths and tools.

## Naming

- Commands: `area.verb` (`project.new`, `parameter.set`, `mesh.generate`,
  `run.start`). Verbs are imperative; `get`/`list` for queries.
- Node types: lowercase nouns (`parameter`, `boundary`, `region`).
- Parameters and fields: `snake_case`; no units in names (`velocity`, not
  `velocity_ms`); booleans read as predicates (`enabled`, `is_steady`).
- Quantities: dimension names from `stokes.units.dimensions` (`velocity`,
  `pressure`, `thermal_conductivity`).
- Files: `snake_case.py`; tests mirror the module name (`test_commands.py`).

## Documentation in code

- Module docstring: what the module is for and its import rules.
- Public function docstring: one sentence on what, one on why or when, then
  parameters only when the types do not explain them.
- Comments explain decisions and non-obvious constraints, never restate the
  code. Reference ADRs by number where a decision applies.

## Qt and QML (for `stokes.ui`, `stokes.app`, `stokes.viewport`)

- Widgets and QML items never hold model state; they observe the `Session`
  through events and call API commands.
- No work in paint or layout code; no synchronous IO in slots.
- Every worker reports progress at most every 100 ms and supports cancel.
- Keyboard access for every action; accessible names on every control.

## Native kernels (`native/`)

- A pure-Python reference implementation lives in `stokes.native.fallback`
  for every C++ kernel; tests run both and compare.
- Kernels take numpy buffers (`nb::ndarray`), never Python lists; they release
  the GIL for anything above a millisecond; they never allocate per element.
- The loader in `stokes.native` prefers the compiled module and falls back
  silently with a logged warning.
