# Local LLM work queue

Small, self-contained **text** jobs for the local LM Studio model (tier T4 in
[model-routing.md](model-routing.md)). It is free to run and keeps text on the owner's hardware.

**Rules for every job**
- The model sees only its prompt: paste the input text and state the rules in the prompt itself.
- Text only. No code, formulas or numbers that matter. It is a 26B model and it invents
  plausible-looking answers to technical questions.
- Every output is reviewed before anything is applied. The reviewer, Opus or Sonnet, applies
  the accepted part and runs the gates.
- One job per run, inputs under about 6,000 words. Split larger inputs.

Status 2026-10-03: jobs 1 to 7 and 9 were run twice each (draft, then fact check) on the local model
(gemma-4-26b-a4b-qat at `<local-llm-base-url>`, four parallel requests, no key,
`reasoning_effort: none`). Results are unreviewed drafts in `docs/drafts/local-llm/` (see its README for
which fact-check outputs look unreliable). Job 8 (alt text) and job 10 (release notes) are not run.
The `local-llm` tool in the coding assistant still points at the old `:8443/api/v0` address and returns
404; call the server directly (helpers `.review/llm.py`, `.review/llm_batch.py`) or fix its address.

| # | Job | Input to paste | Output wanted | Review check |
|---|---|---|---|---|
| 1 | **UI copy review** against `web/DESIGN.md` §3b (plain, specific, no filler; sentence case; verbs on buttons) | every user-facing string from `web/src` (tooltips, toasts, empty states, dialog text, menu labels), extracted by a script into a numbered list | numbered list: original, proposed rewrite, one-line reason; untouched strings omitted | wording only; no string may change meaning or lose a shortcut hint |
| 2 | **Stale-statement sweep** of the docs | one doc at a time (`docs/03-ui-design.md`, `web/DESIGN.md`, `README.md`, `web/README.md`, `docs/06-roadmap.md`), plus a fact sheet: six stages; Stokes and Cupertino styles × dark and light; workspace presets; STEP import per solid; Explore stage; materials slice 1 done | list of sentences that contradict the fact sheet, with line text and the corrected sentence | each flag checked against the code before editing |
| 3 | **CHANGELOG tidy**: regroup Unreleased into Added / Changed / Fixed and shorten entries | the Unreleased section of `CHANGELOG.md` | the regrouped section, same facts, no new claims | diff shows only regrouping and shortening |
| 4 | **User guide drafts**, one page per feature: Workspace and Customise mode; Explore stage; STEP import; Materials | the matching contract (`docs/engineering/workspace-schema.md`, `study-schema.md`, ADR-0014, `materials-schema.md`) plus the shortcut list | a task-oriented page: what it is, how to do the common tasks, where things are in the window | every step tried in the app; no invented features |
| 5 | **Material library blurbs**: one sentence per catalogue record for the library browser | record names and kinds from `stokes/physics/materials/data/materials.json` (names only, no values) | one neutral sentence each (what it is, typical use) | no property values or claims of grade or standard compliance |
| 6 | **CFD glossary** for tooltips and help (y+, residual, under-relaxation, Boussinesq, operating pressure, wall function, Courant number, and others) | the term list | one or two plain sentences per term, no formulas | each definition checked against `docs/research` notes or the reference corpus |
| 7 | **Error-message catalogue**: user-facing explanation and next step for each error code | error codes and current messages, extracted by a script from `stokes/**/errors.py` and the command modules | per code: one sentence of meaning and one of what to do | explanations must match what the code actually raises |
| 8 | **Alt text and captions** for the screenshots in `README.md` and `docs/flyer/` | a description of what each image shows (written by the reviewer) | alt text under 125 characters and a one-line caption | describes only what is visible |
| 9 | **Owner checklist** from the decisions log | the "Still open" table of `docs/00-open-questions.md` | a short checklist, one line per decision, in plain words | nothing dropped or reworded into a different decision |
| 10 | **Release notes draft** for the internal demo (M3), later | the CHANGELOG once M3 lands | a one-page summary for a non-developer reader | facts only from the CHANGELOG |

**Not for the local model** (Sonnet jobs instead): stale code comments (`web/src/workspace/Ribbon.tsx` still says stages `1`–`5`; the `styles.css` bottom-right corner fallback and comment), the literal `H` and `I` hints in `web/src/panels/PatchCard.tsx`, the `stokes material` CLI, and anything else that touches code.
