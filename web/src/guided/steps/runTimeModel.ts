/**
 * The Run card's time rules (ADR-0022 addendum 2026-11-24), pure: what the set-up check says about time, which
 * way its fix switches the run, and how a typed time is read. `RunTime.tsx` draws them.
 *
 * Import rule: no React, no store, no DOM.
 */
import type { SetupCheck, SetupFinding } from "../../api/types";
import { findingFix } from "../../panels/setupFlowModel";

/** What the set-up check says about time: a value that varies on a steady run, a run shorter than a period. */
export function timeFindings(setup: SetupCheck | null): { steady: SetupFinding | null; short: string | null } {
  const found = setup?.findings ?? [];
  const steady = found.find((f) => f.code === "setup.profile_steady") ?? null;
  const short = found.find((f) => f.code === "setup.profile_short");
  return { steady, short: short?.message ?? null };
}

/** Whether the finding's fix switches the run to `transient` (true) or back to steady (false); null for neither. */
export function fixSwitchesTo(finding: SetupFinding | null): boolean | null {
  const fix = finding === null ? null : findingFix(finding);
  if (fix === null || fix.command !== "solve.configure") return null;
  const values = fix.params.values;
  const transient = typeof values === "object" && values !== null ? (values as { transient?: unknown }).transient : undefined;
  return typeof transient === "boolean" ? transient : null;
}

/** The time step against the end time: a step longer than the run is refused under the field, as the server does. */
export function stepProblem(step: number | null, end: number | null): string | null {
  if (step === null || end === null) return null;
  return step > end * (1 + 1e-9) ? `is longer than the end time (${seconds(end)}): shorten it or run for longer` : null;
}

/** A write interval against the step and the end: at least one step, at most the whole run. */
export function saveEveryProblem(every: number, step: number | null, end: number | null): string | null {
  if (step !== null && every < step * (1 - 1e-9)) return `is shorter than one time step (${seconds(step)})`;
  if (end !== null && every > end * (1 + 1e-9)) return `is longer than the run (${seconds(end)})`;
  return null;
}

/** How many frames a run writes at an interval: the end time over the interval, rounded down. */
export function frameCount(end: number | null, every: number | null): number | null {
  if (end === null || every === null || !(end > 0) || !(every > 0)) return null;
  return Math.floor((end / every) * (1 + 1e-9));
}

/** `solve.write_suggestion`, read defensively; null for a payload that is not one. */
export interface WriteSuggestion {
  writeTime: number;
  frames: number;
  period: number | null;
  current: { writeTime: number; frames: number; steps: boolean };
}

export function readWriteSuggestion(raw: unknown): WriteSuggestion | null {
  const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const cur = (typeof r.current === "object" && r.current !== null ? r.current : {}) as Record<string, unknown>;
  const writeTime = num(r.write_time);
  if (writeTime === null) return null;
  return {
    writeTime,
    frames: num(r.frames) ?? 0,
    period: num(r.period),
    current: { writeTime: num(cur.write_time) ?? 0, frames: num(cur.frames) ?? 0, steps: cur.steps === true },
  };
}

/**
 * The note under Save every: the frames the run will write, and the suggestion when the current interval gives
 * clearly fewer frames (under two thirds of it), so a coarse animation is offered a finer one rather than left
 * silent. Null `offer` when the current interval is fine.
 */
export function saveEveryNote(s: WriteSuggestion): { text: string; offer: number | null } {
  const per = s.period !== null && s.current.writeTime > 0 ? Math.floor((s.period / s.current.writeTime) * (1 + 1e-9)) : null;
  const text = `${s.current.frames} ${s.current.frames === 1 ? "frame" : "frames"}${per !== null ? `, ${per} a period` : ""}`;
  const coarse = s.current.frames < s.frames * (2 / 3) && s.writeTime < s.current.writeTime;
  return { text, offer: coarse ? s.writeTime : null };
}

function seconds(t: number): string {
  return `${Number(t.toPrecision(4))} s`;
}

/**
 * A time as `solve.configure` takes it: a bare number is seconds, anything else (quantity text, an expression)
 * goes as typed for the server to read; null with a reason for what is plainly not a time.
 */
export function secondsInput(text: string): { value: number | string } | { error: string } {
  const trimmed = text.trim();
  if (trimmed === "") return { error: "needs a value in seconds" };
  const n = Number(trimmed);
  if (Number.isFinite(n)) return n > 0 ? { value: n } : { error: "must be above 0 s" };
  return { value: trimmed };
}
