# Sequential Windows GUI review

Date: 2026-10-20. Scope: private local Windows checkout, existing dark theme,
Geometry → Mesh → Physics → Solve → Results. This is the first improvement
batch, not a completed end-to-end simulation certification.

## Reference patterns

- [SimScale simulation setup](https://www.simscale.com/docs/simulation-setup/):
  visible setup state and a clear route into each stage.
- [Fluent guided workflows](https://ansyshelp.ansys.com/public/views/secured/corp/v252/en/flu_ug/tgd_user_workflow_guided_intro.html):
  ordered tasks with honest task states.

These reinforce the existing Stokes design. No third-party artwork or code
was copied, and the approved palette was preserved.

## First pass

| Stage | Observation | Outcome |
|---|---|---|
| Geometry | Imported demo renders; virtual folders announce an undefined status | Missing statuses now render no status dot or announcement |
| Mesh | Stage control changes the heading but leaves the outline section folded | Stage focus now uses virtual outline section ids and preserves nested expansion |
| Mesh / Geometry check | Empty findings look successful before a request finishes; root-count dependency misses edits | Explicit loading/disconnected/error/ready states, tree-identity refresh and stale-response rejection |
| Mesh / Geometry check | A failed request has no direct retry | Check again / Retry check control; unavailable while disconnected, checking or fixing |
| Mesh task progress | Heading can follow a different task than the page; in-flight state restarts the query effect while idle | Explicit task selection drives both views; debounced queries depend only on real input changes and reject stale replies |
| Mesh task details | A selected task is followed by another generic next-step/blocker paragraph | A concrete task page replaces the generic checklist instead of appearing above it |
| Physics | Stage navigation inherits the same outline mismatch; setup needs a runnable engine for full validation | Shared stage-focus fix; live native solver validation remains pending |
| Solve | Run is correctly blocked without a mesh/usable host | No readiness claim added; native host adapter is separate work |
| Results | Empty results expose a long list of disabled controls and repeated prerequisites | Recorded for a focused progressive-disclosure pass; existing loaded-result controls retained |

## Verification

- Frontend production build and typecheck pass.
- Frontend unit suite passes: 119 files, 1,557 tests.
- Live desktop inspection confirms outline stage navigation and removal of
  undefined folder status announcements.
- A separate desktop review harness drives Geometry Check through delayed
  responses, same-root-count edits, stale replies, failure, retry and disconnect;
  it also checks the selected step indicator and that idle task queries stop.
  Review captures/logs live only in the ignored local runtime directory.
- Earlier Python baseline limitations remain: broad type checking and the full
  Python suite are not green. This GUI batch does not claim to resolve them.

## Next ordered batches

1. Geometry: selection/fit/visibility and import recovery; measure fit against
   the unobstructed viewport before changing camera mathematics.
2. Mesh: work through every guided task with valid, incomplete and stale models;
   improve task titles and avoid repeated host prerequisites.
3. Physics: material and boundary assignment, bulk edits and validation wording.
4. Solve: integrate the native Windows engine host with verified cancellation;
   then review job progress and failure recovery using real runs.
5. Results: a concise first-run state, then field controls, slices/probes,
   reports and transient stepping on actual solver output.

Custom stage-order shortcut behavior also needs a separate decision: current
numeric action ids are stable even when workspace display order changes.
No modifications were made to the shared source tree or public FoamWin repo.
