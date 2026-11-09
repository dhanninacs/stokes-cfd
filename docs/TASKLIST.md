# Task list

*A backlog, not a log: open work only, in the order of `docs/HANDOVER.md` §5b, which has the
detail. When an item is done, delete it here and say so in `CHANGELOG.md`.*

Priorities: **P0** blocks a gate; **P1** an important feature slice; **P2** clean-up and polish;
**P3** a later milestone.

**Rule: the look is not changed unless explicitly asked.**

## P0 — The M0 geometry work (§5b items 1, 2 and 9)

- [ ] STEP import of a large assembly: 41 s for the 4,500-solid, 52 MB pin grid on a 24-CPU host (75 s before the helper processes of 2026-11-09), target 30 to 40 s. The 26.5 s OCCT read is the floor; splitting it means reading roots in separate processes. Measure with nothing else running (the old 168 s figure was another laptop under load).

## P1 — Viewing features the owner asked for, in this order (§5b items 3 and 4)

- [ ] Multi-selection (the other items need it).
- [ ] Box / rubber-band selection: drag to replace, Ctrl-drag to add, Ctrl-click to toggle.
- [ ] Hide and show many bodies at once; isolate across a selection.
- [ ] Transparency per body and across a selection, in both render paths.
- [ ] GPU id-buffer picking, on the client (ADR-0020).

## P1 — Guided mode, from the mixing-elbow walkthrough (2026-11-08)

- [ ] Full mode's Mesh panel: a "Mesh to use" picker like Guided mode's (`mesh.use`, ADR-0034);
  today Full mode shows the kept meshes as tree rows ("in use") but cannot switch between them.
- [ ] cfMesh tet and hex-dominant mesh quality, what is left after the symmetry-plane flatten
  (the agent's `flatten` action, 2026-11-09). The elbow's tet mesh always has faces at 79.75 degrees
  non-orthogonality (12 to 20 above 70) where the outlet meets the wall; the value is identical in
  every run and cfMesh's `meshQualitySettings` do not change it, so it is a fixed feature of the
  surface there, not something to tune. The app's compiled settings cope with it: the app's own
  realizable k-epsilon case on the flattened mesh runs its 500 iterations (peak speed 2.1 m/s,
  293 to 313 K, energy residual 1.7e-3 and slowly falling), where the unflattened mesh died at
  iteration 113 with a negative temperature. A case with PLAIN schemes (no limited corrections, not
  what the app compiles for this mesh) still runs away at a sliver tet at the outlet wall edge from
  about iteration 150, and the polyhedral mesh converges with those same plain schemes (1.5e-5 at
  400). Also: four plane points stay up to 0.58 mm (12 % of a cell) off, since looser back-off
  limits make a highly skewed face (5.0 against 4) and fail `checkMesh`; and hex-dominant meshes
  with cells the mesher already broke beside the plane keep up to 15 points off.

## P1 — M1 geometry (§5b item 6; design in ADR-0020, slices 1–5 listed there)

- [ ] Named selections by click, box and feature angle
- [ ] Primitives and booleans
- [ ] Enclosure and fluid extraction
- [ ] Defeaturing and splitting
- [ ] Workspace shortcut editor and preset sharing

## P2 — Small clean-ups (§5b item 8)

- [ ] `web/src/styles.css`: the 148 px fallback of the bottom-right corner offset next to the 140 px
  reserve of `.slot-right`; derive both from one value.
- [ ] A non-blocking `study.update` with progress and Cancel (needs a worker or a thread in
  `CommandContext`).
- [ ] Move the Explore front end's SI-to-display factors (`web/src/panels/explore/format.ts`) onto
  `units.convert`.
- [ ] Update `CLAUDE.md` §2 for the new modules (`batch.ts`, `units.*`).

## P2 — Decisions to take or check (HANDOVER §8)

- [ ] A3: a trademark search for the name "Stokes". It is a task for the owner; none has been done.
- [ ] Verify the decisions log entries G4 and G6 against the latest measurements.

## P3 — M2 to M3 (§5b item 7)

- [ ] Engine attachment (OpenFOAM Foundation 14 on WSL2 or Linux)
- [ ] Meshing drivers and quality checks
- [ ] Physics and case compilation
- [ ] Runs with live residuals and MCP
- [ ] Internal demo: STEP to a converged run

## P3 — Explore v2 (§5b item 10)

- [ ] Surrogates (polynomial, RBF, Gaussian process)
- [ ] Sensitivity (Spearman, Sobol, Morris)
- [ ] Optimisation with Pareto plots
- [ ] Mesh reuse
- [ ] Run-queue evaluator
