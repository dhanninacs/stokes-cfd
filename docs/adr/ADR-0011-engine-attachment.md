# ADR-0011 · Engines are attached, not shipped; in-house solvers are built from source

**Status:** accepted · 2026-10-02 (decisions B2, C2, C3, C5, B5)

## Context

The earlier draft proposed shipping a "Stokes Engine" WSL image with
OpenFOAM, PETSc and the in-house solvers preinstalled. You decided that
Stokes attaches to existing OpenFOAM installations only, that in-house
solvers reach users through a build-from-source helper, and that ANSYS Fluent
is a v1 engine (paid) through PyFluent.

## Decision

1. **Stokes ships no engine software.** It probes WSL distros and native
   Linux for OpenFOAM installations, meshers (cfMesh, Gmsh), ParaView,
   compilers, MPI, PETSc and GPUs, and records a capability record per host.
2. **v1 supports OpenFOAM Foundation 14.** Other installations are listed as
   found but unsupported, with the reason; the ESI line arrives with its
   dialect in v1.5.
3. **The build helper** installs the in-house solvers into the user's
   environment: clone the public repositories at pinned tags
   (`dhanninacs/coupledSimpleFoam`, `-gpu`), check prerequisites, run
   `Allwmake` against the detected OpenFOAM, smoke-test with the solver's own
   tutorials, record versions. The CUDA variant is offered when `nvidia-smi`
   and a CUDA PETSc are present (NVIDIA only).
4. **cfMesh** is detected or built the same way once M2 confirms which
   OpenFOAM line its source builds against; **Gmsh** is detected on PATH or
   pointed to by the user and driven as a process.
5. **Fluent** is an engine through PyFluent (MIT), gated by the `fluent`
   licence flag and by the user's own Fluent licence; the user's Fluent
   installation is detected, never shipped.
6. **Engine definitions** carry a capability predicate, dictionary or script
   additions, log parsers and validation cases; the UI greys out engines the
   current model cannot use and explains why.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Ship a WSL image | Rejected by decision B2: a large download, a GPL distribution burden, and a support surface for a solo developer. |
| Prebuilt solver binaries per OpenFOAM version | A build matrix to maintain; the helper builds in minutes and is version-proof. |
| Require users to build the solvers themselves | Works for experts only; the helper is a few hundred lines. |

## Consequences

- The first-run experience depends on the user already having OpenFOAM 14;
  the documentation and the probe must make the gap obvious and point to the
  openfoam.org instructions.
- Install variety is the main support risk; the probe must report precisely.
- GPL compliance is trivial: Stokes distributes nothing GPL; the source is the
  distribution.
