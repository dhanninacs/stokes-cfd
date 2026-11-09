# Model routing: which model does what

Stokes is built by one engineer and a set of AI models. Using the strongest
model for everything is slow and expensive; using a weak model on the wrong
task costs a rewrite. This document is the routing table. `CLAUDE.md` §11 is
the summary.

## The tiers

| Tier | Model (id) | Spawn as | Strengths | Never for |
|---|---|---|---|---|
| **T0 Architect** | Claude Fable 5.1 (`claude-fable-5-1`) | the main session, or `Agent(model="fable")` | architecture, ADRs, cross-cutting refactors, hard algorithms (dependency graphs, expression engines, picking, meshing presets), security and licensing code, performance work with profiles, final reviews of anything in T0 territory | bulk mechanical edits, formatting, boilerplate (waste of its budget) |
| **T1 Builder** | Claude Opus 5.5 (`claude-opus-5-5`) | `Agent(model="opus")`; "fast mode" is Opus with faster output | multi-file features from a clear design, debugging across layers, VTK and Qt integration, protocol and runner code, provider abstractions, PyFluent integration, difficult test failures | decisions that change an ADR (escalate to T0) |
| **T2 Implementer** | Claude Sonnet 5.5 (`claude-sonnet-5-5`) | `Agent(model="sonnet")` | well-specified modules, drivers from a template (mesher dictionaries, BC mappings), tests for code already designed, golden files, docs and tutorials, porting the owner's earlier code with renames, generated artifacts (Excalidraw, HTML) | security, licensing, the expression engine, anything with "design" in the task |
| **T3 Mechanic** | Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) | `Agent(model="haiku")` | lint and type fixes, docstrings, changelog lines, renames across files, classification and extraction over many files, log parsing fixtures, simple scripts, first-pass summaries | any file under `stokes/license`, `stokes/units/expressions.py`, `stokes/api/registry.py`, `tools/`; anything that needs a judgement call |
| **T4 Local** | LM Studio on the workstation through the `local-llm` skill | `/local-llm` or the skill's tools | confidential bulk text (summarising solver logs, drafting release notes from commits, tagging files), first drafts that T2 will rewrite | code that ships; anything requiring tool use or repository context |

Effort: T0 at the default or high effort for design and security; T1 at
default; T2 and T3 at default. Raise effort only for a named hard problem.

## Routing by task type

| Task | Tier | Notes |
|---|---|---|
| New ADR, change to an accepted decision | T0 | Always. Includes dialect design, licence mechanics, agent protocol changes. |
| New package or cross-package refactor | T0 designs, T1 builds | T0 writes the interface and the first test; T1 fills it in. |
| Feature inside one package with a clear spec | T1 or T2 | T2 when the spec names files, types and tests; T1 otherwise. |
| Tests for existing code | T2 | T0/T1 review the assertions for tautologies. |
| Golden-file updates | T2, reviewed by T1 | The PR says why the output changed. |
| Mesher driver (snappyHexMesh, cfMesh, Gmsh dictionaries) | T2 | From the driver template; T1 for the first driver. |
| BC and model mappings per dialect | T2 | Declarative tables; T0 reviews dimensional correctness. |
| OCCT import, healing, tessellation | T1 | T0 for the persistent-identity design. |
| Picking, LOD, VTK pipelines | T1 | T0 for the id-buffer design and budgets. |
| C++ kernels in `native/` | T0 writes, T1 maintains | Fallback and kernel must agree; T2 writes the comparison tests. |
| Expression engine, units | T0 | Property tests by T2. |
| Security-sensitive code (paths, subprocess, network, licences) | T0 writes or reviews | Checklist in `docs/engineering/security.md` pasted into the PR. |
| Performance regressions | T0 with a profile | No optimisation without numbers. |
| Qt/QML panels from the design doc | T1 for the shell, T2 for panels | T0 only if the shell prototype misbehaves. |
| Assistant provider abstraction, MCP server | T1 | T0 reviews the tool schema generation. |
| PyFluent engine | T1 | T2 for the mapping tables. |
| Docs, tutorials, changelog | T2, T3 for polish | T0 only for ADRs and this file. |
| Lint, type, formatting fixes; renames; docstrings | T3 | Never touches logic. |
| Porting the owner's earlier code | T2 | Scrub identifiers (the IP gate), rename to Stokes vocabulary, add tests. |
| Log summaries, release-note drafts, bulk tagging; the jobs in [local-llm-queue.md](local-llm-queue.md) | T4 | Output is reviewed by T2 before it enters the repository. |
| Code review of a PR | one tier above the author, T0 for T0 work | A model does not approve its own tier's security or licensing work. |

## Routing by milestone

| Milestone | T0 Fable | T1 Opus | T2 Sonnet | T3 Haiku / T4 Local |
|---|---|---|---|---|
| M0 Foundations | constitution, units and expressions, model and commands, API registry, gates, licence crypto, shell prototype decision | QML + VTK shell prototype, VTK viewport with LOD | tests, docs, CLI polish, STEP import plumbing from the design | lint fixes, docstrings, changelog |
| M1 Geometry | id-buffer picking design, persistent face identity | OCCT import and healing, prep operations, selection tools | named-selection UI, STL export, tests on public STEP files | fixture collection, docs |
| M2 Agent and meshing | protocol changes, staging rules | agent server/client, runners (local, WSL, SSH), probe and attachment | snappyHexMesh, cfMesh, blockMesh, Gmsh drivers; quality mapping; estimator | dictionary templates, log fixtures (T4 summarises long logs) |
| M3 Physics, compile, run, API servers | dialect interface, compiler core, stopping-criteria semantics, tool-schema generation | foamRun engine, monitors streaming, HTTP server, MCP server | BC and model tables, golden files, compiled-case preview, journals tests | docs, changelog |
| M4 Post-processing | artifact format and caching | VTK scenes, displayers, comparison view | reports (HTML), exports, tests | report templates polish |
| M5 Coupled solver and CHT | PETSc presets, parsers, validation gates, build helper design | build helper implementation, multi-region flow | tutorials as tests | — |
| M6 Assistant | prompt-injection rules, confirmation setting | provider abstraction, tool loop, keychain | assistant panel, journal export | — |
| M7 Fluent | — | PyFluent sessions, results readers | mapping tables, tests | — |
| M8 Packaging | licence flags review, signing design | Nuitka build, installers, updater | notices generation, docs site | changelog, release notes draft (T4) |
| M9 Beta | performance budgets review | crash bundles, perf CI | tutorials, usability fixes | docs polish |
| M10 Expansion | ESI dialect design, VOF/Lee model mapping review | sweeps, schedulers, containers | existing-case import, report templates | — |

## How to spawn and hand over

- From a T0 or T1 session, parallel independent work goes to subagents:
  `Agent(subagent_type="general-purpose", model="sonnet", prompt=...)`. The
  prompt names the files to touch, the tests to make pass, the gates to run,
  and the rules that apply (link to `CLAUDE.md`). A subagent never changes an
  ADR.
- A handover always includes: the milestone step, the interface or test the
  work must satisfy, and what is out of scope. Vague prompts produce vague
  code; T2 and T3 need the spec to be explicit.
- Results flow upward: T3 → T2 review for mechanical work; T2 → T1 review for
  features; T1 → T0 review for anything on the T0 list. Reviews use the
  `code-review` skill at the reviewer's tier.
- The owner decides product questions; models add them to
  `docs/00-open-questions.md` "Still open" and proceed on a stated assumption.

## Cost discipline

- T0 time is the scarce resource. Before using it, ask: is there a decision
  here, or only work? Work goes down a tier.
- Batch T3 tasks (ten lint fixes in one prompt, not ten prompts).
- Use T4 for anything that touches confidential logs or long text and does not
  need to be correct on the first pass.
- Keep subagents in worktrees (`isolation="worktree"`) when two of them touch
  the same package.
