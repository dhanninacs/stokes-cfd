/**
 * The residual history of one run, as the chart draws it (T-217): the iterations and, for each field,
 * one number per iteration, `NaN` where the solver did not report that field in that block. A field is
 * never shorter than the iterations, so x and y stay aligned however the data arrived (the events, or
 * `run.residuals` after a reconnect).
 *
 * Fields are a list, in the order they first appeared, not an object: a field's name is data from the
 * wire. Import rule: no React, no store, no DOM.
 */
import type { ResidualBlock } from "../panels/runApi";

/** The most iterations kept; the oldest go first. A 1,000,000 iteration limit must not grow the page without bound. */
export const SERIES_CAP = 20000;

export interface ResidualField {
  name: string;
  values: number[];
}

export interface ResidualSeries {
  iterations: number[];
  fields: ResidualField[];
}

export const EMPTY_SERIES: ResidualSeries = { iterations: [], fields: [] };

/** The last iteration held, or 0 for an empty series: what `run.residuals(since)` is asked with. */
export function lastIteration(series: ResidualSeries | null): number {
  return series === null || series.iterations.length === 0 ? 0 : series.iterations[series.iterations.length - 1];
}

/**
 * Append blocks. A block at or before the last iteration held is dropped (an event and a poll can
 * deliver the same one), a field the series has not seen is added and filled with `NaN` for the
 * iterations before it, and a field a block does not carry is `NaN` for that iteration. Returns the
 * same object when nothing was added, so a store subscriber does not wake for it.
 */
export function appendResidual(series: ResidualSeries, blocks: readonly ResidualBlock[]): ResidualSeries {
  let last = lastIteration(series);
  const fresh: ResidualBlock[] = [];
  for (const block of blocks) {
    if (!Number.isFinite(block.iteration) || block.iteration <= last) continue;
    fresh.push(block);
    last = block.iteration;
  }
  if (fresh.length === 0) return series;

  const names = series.fields.map((f) => f.name);
  const columns = series.fields.map((f) => f.values.slice());
  const iterations = series.iterations.slice();
  for (const block of fresh) {
    const row = iterations.length;
    iterations.push(block.iteration);
    for (const { name, value } of block.values) {
      let at = names.indexOf(name);
      if (at < 0) {
        at = names.length;
        names.push(name);
        columns.push(new Array<number>(row).fill(Number.NaN));
      }
      // A field named twice in one block keeps its last number.
      while (columns[at].length < row) columns[at].push(Number.NaN);
      columns[at][row] = value !== null && Number.isFinite(value) ? value : Number.NaN;
    }
    for (const column of columns) while (column.length < iterations.length) column.push(Number.NaN);
  }

  const drop = Math.max(0, iterations.length - SERIES_CAP);
  return {
    iterations: drop > 0 ? iterations.slice(drop) : iterations,
    fields: names.map((name, i) => ({ name, values: drop > 0 ? columns[i].slice(drop) : columns[i] })),
  };
}

/** A whole `run.residuals` reply as a series. */
export function fromBlocks(blocks: readonly ResidualBlock[]): ResidualSeries {
  return appendResidual(EMPTY_SERIES, blocks);
}

/** The latest reported number of each field, for a one-line readout; fields with none are left out. */
export function latestValues(series: ResidualSeries | null): { name: string; value: number }[] {
  if (series === null) return [];
  const out: { name: string; value: number }[] = [];
  for (const field of series.fields) {
    for (let i = field.values.length - 1; i >= 0; i--) {
      if (Number.isFinite(field.values[i])) {
        out.push({ name: field.name, value: field.values[i] });
        break;
      }
    }
  }
  return out;
}

/**
 * What the synthetic residual chart says about itself when the run has reported no history.
 *
 * **The words are the whole safeguard.** The placeholder's legend reads `Ux Uy p T` - the same names real
 * data carries - beside a status bar that says "converged". Measured: as 11 px muted grey in a corner it
 * read as that run's evidence. It now names the ABSENCE, in the amber "stale" token.
 */
export const RESIDUALS_PLACEHOLDER = "placeholder: this run has reported no residual history";

/**
 * What the Residuals tab should show, from what the run has actually reported.
 *
 * **The decision** (Codex's 222732, item 1): a real run with no history gets an **explicit empty state**,
 * never synthetic curves. A label is not enough - measured, the demo chart's legend read `Ux Uy p T`
 * beside a "converged" status bar and was taken for that run's history.
 *
 * * **`curves`** when there IS history - the case that always worked;
 * * **`unavailable`** when a run is selected and its history is missing - the empty state, which says so
 *   in words and shows the **final residuals** separately when the record carries them;
 * * **`demo`** only when there is no run at all, which is the demo/example context the chart was written
 *   for; it is the tab's shape before any solve, not a stand-in for a solve.
 */
export type ResidualsView =
  | { kind: "curves" }
  | { kind: "unavailable"; final: readonly { name: string; value: number }[] }
  | { kind: "demo" };

/**
 * The name a residual is shown under. OpenFOAM reports the energy equation as the variable it solves
 * (`e` or `h`), which says nothing to a reader who switched on "Energy"; the label names the equation
 * and keeps the solver's variable beside it. Every other field keeps the solver's own name.
 */
export function residualLabel(name: string): string {
  if (name === "e" || name === "h") return `Energy (${name})`;
  return name;
}

/** The sentence the empty state shows. Exported so a test asserts the words, not a screenshot. */
export const RESIDUALS_UNAVAILABLE = "Residual history is not available for this run";

export function residualsView(input: {
  /** True when the store holds a run at all - a selected or most-recent run. */
  hasRun: boolean;
  /** The number of iterations the residual series has; 0 means the history is absent. */
  iterations: number;
  /** The run record's final residuals, when it carries them. */
  final: readonly { name: string; value: number }[];
}): ResidualsView {
  if (input.iterations > 0) return { kind: "curves" };
  if (input.hasRun) return { kind: "unavailable", final: input.final };
  return { kind: "demo" };
}
