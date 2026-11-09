# ADR-0001 · GPL engines run as separate processes

**Status:** accepted · 2026-10-02 (decisions C2, B2)

## Context

OpenFOAM (both lines), snappyHexMesh, cfMesh, Gmsh and the in-house
`coupledSimpleFoam` are GPL. coupledSimpleFoam links `libfiniteVolume`,
`libmomentumTransportModels` and friends, so it is a derivative work and is
GPL whatever we prefer. Stokes is closed source and freemium.

## Decision

1. The desktop and the agent never link, `dlopen`, import or include anything
   GPL. `stokes.native` builds on a machine without OpenFOAM present.
2. GPL programs are driven by files, argv, environment, stdin/stdout/stderr
   and exit codes only.
3. Stokes reads and writes OpenFOAM dictionaries with its own parser and
   printer; results are read with VTK's BSD `vtkOpenFOAMReader`.
4. No GPL source is copied, translated or ported into Stokes.
5. Stokes ships no GPL software. The build helper (ADR-0011) clones public
   repositories at pinned tags and builds in the user's environment; if a GPL
   binary is ever distributed, the complete corresponding source accompanies
   it.
6. In-house GPL work stays GPL in its own repositories; proprietary value is
   kept on the desktop and agent side.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Link OpenFOAM libraries through Python bindings for in-process access | Makes the whole application a GPL derivative. |
| Release Stokes under GPL and sell support | Contradicts the closed-source freemium decision. |
| Dual-license coupledSimpleFoam | Impossible: it derives from GPL code we do not own. |

## Consequences

- Positive: the product can be closed; engines can run on any host; engine
  updates are independent of app releases; HELYX is the precedent.
- Negative: no in-process access to meshes or fields; own dictionary code;
  every engine interaction is a process launch with log parsing.
- Legal review before the first public release.
