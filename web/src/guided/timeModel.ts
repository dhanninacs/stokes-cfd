/**
 * Transient results on the Results card, as pure functions: the written times a run has, which one is shown,
 * where a step or the next frame of a playback goes, and how a time is written.
 *
 * A time is shown by `run.time` (the host reads that instant's fields; `run.changed` follows when it has), so
 * the card steps through the times the run WROTE, never one in between: there is nothing to show between two.
 * The time 0 is the initial condition, which a playback skips (it is the set-up, not a result).
 *
 * Import rule: no React, no store, no DOM. This loads in Node.
 */

/** The written times, ascending and without repeats; the initial condition (0) left out when there are later ones. */
export function writtenTimes(times: readonly number[] | undefined): number[] {
  const sorted = [...new Set((times ?? []).filter((t) => Number.isFinite(t)))].sort((a, b) => a - b);
  return sorted.length > 1 ? sorted.filter((t) => t > 0) : sorted;
}

/** The index of the written time nearest `time` (the one on screen); -1 for none. */
export function timeIndex(times: readonly number[], time: number): number {
  let best = -1;
  let gap = Infinity;
  times.forEach((t, i) => {
    const d = Math.abs(t - time);
    if (d < gap) {
      gap = d;
      best = i;
    }
  });
  return best;
}

/** The time a step goes to (`-1` back, `+1` on), or null at an end. */
export function stepTime(times: readonly number[], time: number, by: -1 | 1): number | null {
  const at = timeIndex(times, time);
  if (at < 0) return null;
  const next = at + by;
  return next < 0 || next >= times.length ? null : times[next]!;
}

/** The next frame of a playback: the following time, or the first after the last (a loop) when `loop`. */
export function nextFrame(times: readonly number[], time: number, loop: boolean): number | null {
  const step = stepTime(times, time, 1);
  if (step !== null) return step;
  return loop && times.length > 1 ? times[0]! : null;
}

/** A time as the card writes it: seconds with up to four significant digits, or an iteration for a steady run. */
export function timeText(time: number, steady: boolean): string {
  if (steady) return `iteration ${Math.round(time)}`;
  if (time === 0) return "0 s";
  const a = Math.abs(time);
  if (a < 1e-3 || a >= 1e5) return `${time.toExponential(2).replace("e+", "e")} s`;
  return `${Number(time.toPrecision(4))} s`;
}

/** A run is steady when every written time is a whole number of iterations (a transient run writes seconds). */
export function looksSteady(times: readonly number[]): boolean {
  return times.length > 0 && times.every((t) => Number.isInteger(t) && t >= 1) && Math.max(...times) >= 10;
}

/** The x-axis of a monitor's history: iterations for a steady run, seconds for a transient one. */
export function historyAxis(x: readonly number[]): string {
  return x.length > 0 && x.every((v) => Number.isInteger(v)) ? "iteration" : "time (s)";
}
