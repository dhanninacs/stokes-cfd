# Workflow

`CLAUDE.md` §7 is the summary.

## Branches and commits

- `main` is always releasable; CI gates protect it.
- Branches: `feat/<area>-<short>`, `fix/<area>-<short>`, `docs/<short>`,
  `perf/<short>`. Short-lived; rebase on `main` before opening the PR.
- Conventional commits: `type(scope): summary` with types `feat`, `fix`,
  `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`. Scope is the
  package (`model`, `units`, `api`, `cli`, `agent`, `ui`, `compile`, …).
- One logical change per commit; the body says why; end with the attribution
  line the harness provides.

## Pull requests

- Use `.github/PULL_REQUEST_TEMPLATE.md`. State the milestone step, what
  changed, how it was tested, numbers for hot paths, and the docs updated.
- CI must be green: ruff, mypy, pytest (unit, property, golden, perf), the
  three gates, the licence audit, pip-audit.
- A reviewer (human or agent) checks the security checklist when the change
  touches inputs, paths, processes, network or secrets.

## Decisions and documentation

- Product and architecture decisions live in `docs/adr/`. New decision: copy
  `docs/adr/TEMPLATE.md`, number it, link it from `README.md`'s ADR table and
  from `docs/02-architecture.md` §10.
- `docs/00-open-questions.md` "Still open" is the place for assumptions made
  without the owner.
- `docs/06-roadmap.md` carries milestone status; update the row you worked on.
- `CHANGELOG.md`: a line under *Unreleased* for every user-visible change.

## Agent session protocol

1. Start: read `CLAUDE.md`, `docs/00-open-questions.md`, the ADRs for the
   area, and the roadmap row. Announce the milestone step.
2. Work in small verified steps: write the test, make it pass, run the gates.
3. Finish: gates green, docs updated, roadmap row updated, a summary that a
   reader who did not watch can act on: what changed, what was verified, what
   is next, what is uncertain.
4. Never leave the tree broken; if a step cannot be completed, revert it and
   say so.

## Releases (from M8)

- Semantic versioning; `stokes.__version__` is the single source.
- Release checklist: gates, licence audit, THIRD-PARTY-NOTICES regenerated,
  signed installers, changelog cut, tag, GitHub release with hashes.
