/**
 * The design table as columns of numbers in display units, built once per `study.results` and shared
 * by the table and every plot. A row that has no value for a column (a pending point has no outputs)
 * holds NaN there, so a plot takes the rows where all its columns are finite.
 */
import { columnConverter, columnTitle } from "./format";
import type { Column, PointRow, StudyResults } from "./types";

export interface DataColumn {
  name: string;
  /** What headers and axes call it: the label, else the name. */
  title: string;
  /** The unit of `values` as text ("m/s"); empty for a dimensionless quantity. */
  unit: string;
  dimension: string;
  kind: "input" | "output";
  /** Display values by row position; NaN where the row has none. */
  values: Float64Array;
  /** Over the finite values; NaN when there is none. */
  min: number;
  max: number;
  /** False when the display unit was not known and the values stayed in SI. */
  converted: boolean;
  /** The label of a value of a categorical variable, by display value. */
  categories?: ReadonlyMap<number, string>;
}

export interface Dataset {
  rows: readonly PointRow[];
  /** Point index to row position. */
  position: ReadonlyMap<number, number>;
  inputs: DataColumn[];
  outputs: DataColumn[];
  byName: ReadonlyMap<string, DataColumn>;
}

function build(column: Column, kind: "input" | "output", rows: readonly PointRow[], categories?: ReadonlyMap<number, string>): DataColumn {
  const converter = columnConverter(column);
  const values = new Float64Array(rows.length);
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < rows.length; i++) {
    const si = (kind === "input" ? rows[i].inputs : rows[i].outputs)[column.name];
    if (si === undefined) {
      values[i] = NaN;
      continue;
    }
    const v = converter.toDisplay(si);
    values[i] = v;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  const shown: DataColumn = {
    name: column.name,
    title: columnTitle(column),
    unit: converter.unit,
    dimension: column.dimension,
    kind,
    values,
    min: min === Infinity ? NaN : min,
    max: max === -Infinity ? NaN : max,
    converted: converter.converted,
  };
  if (categories) {
    // The labels are filed by SI value; the column shows display values.
    const toDisplay = converter.toDisplay;
    shown.categories = new Map([...categories].map(([si, label]) => [toDisplay(si), label]));
  }
  return shown;
}

/** `categories` are the labels of the categorical variables by SI value (study.ts `categoryLabels`). */
export function buildDataset(results: StudyResults, categories: Readonly<Record<string, Map<number, string>>> = {}): Dataset {
  const rows = results.points;
  const inputs = results.variables.map((c) => build(c, "input", rows, categories[c.name]));
  const outputs = results.responses.map((c) => build(c, "output", rows));
  const byName = new Map<string, DataColumn>();
  for (const column of [...inputs, ...outputs]) byName.set(column.name, column);
  const position = new Map<number, number>();
  rows.forEach((row, i) => position.set(row.index, i));
  return { rows, position, inputs, outputs, byName };
}

/** Row positions where every given column has a value. */
export function completeRows(data: Dataset, columns: readonly DataColumn[]): number[] {
  const out: number[] = [];
  for (let i = 0; i < data.rows.length; i++) {
    let ok = true;
    for (const c of columns) {
      if (!Number.isFinite(c.values[i])) {
        ok = false;
        break;
      }
    }
    if (ok) out.push(i);
  }
  return out;
}
