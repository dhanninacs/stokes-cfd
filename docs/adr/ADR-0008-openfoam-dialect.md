# ADR-0008 · OpenFOAM dialect: Foundation 14 first

**Status:** accepted · 2026-10-02 (decision C1; a third `fluent` dialect emits PyFluent settings calls, see ADR-0011)

## Context

The OpenFOAM Foundation line (openfoam.org, version 14) and the ESI line
(openfoam.com, v2512) differ in solver structure (`foamRun` modular solvers
versus named solvers), dictionary names (`physicalProperties` versus
`transportProperties`, `momentumTransport` versus `turbulenceProperties`,
`fvModels` versus `fvOptions`), multi-region control and interface boundary
conditions. coupledSimpleFoam targets Foundation 14; fingen validated its
conjugate workflow on `foamMultiRun` (Foundation).

## Decision

- The compiler is dialect-based from day one: `stokes.compile.of14` and later
  `stokes.compile.esi`, each implementing the same `write`, `capabilities`,
  `parsers` and (later) `read` interface.
- Foundation 14 is the only dialect in v1. ESI follows in v1.5 with the same
  physics and boundary model; dialect differences never leak into the tree.
- Boundary types, materials and models in the tree are engineer-facing
  concepts; each dialect maps them to its entries. Where a dialect lacks a
  concept, the UI greys it out with the reason.
- The install probe reports the Foundation version it found; a project
  records the dialect and version it was compiled for.

## Alternatives considered

| Alternative | Why not |
|---|---|
| ESI first | The in-house engines and the validated references are on Foundation. |
| Both in v1 | Doubles the compile tests and tutorials before there is a user for the second. |
| One "generic" dictionary set with conditionals | Dialects differ structurally (modular solvers versus named solvers); a single template becomes unreadable. |

## Consequences

- Users on ESI installations wait for v1.5.
- The model must stay free of dialect vocabulary; code review enforces it.

## Amendment (ADR-0029, proposed 2026-10-28)

The consequence "the model must stay free of dialect vocabulary" is amended for one case type. A
`foam_case` node is dialect-bound by definition and records `dialect: of14`. The engineer-facing tree
stays dialect-free. See [ADR-0029](ADR-0029-external-openfoam-cases.md).
