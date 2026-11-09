/**
 * The case card's data rules, apart from React (ADR-0029, T-313).
 *
 * **What a typed value becomes.** `case.set` takes typed OpenFOAM tokens, never raw text, so the card turns
 * what the user typed into one: `2` is a number, `true` a boolean, `Euler` a word, `(1 0 0)` a list, and
 * `uniform (1 0 0)` several tokens side by side. A known physical key (the row carries a `dimension`) is sent
 * as the text itself, so the server reads its unit (`0.5 [s]`, `500 ms`) and writes it in SI. Nothing here
 * decides whether a value is allowed: the server checks every word and refuses what it must, and the card
 * shows that refusal under the row.
 */

/** One row of `case.settings`. */
export interface SettingsRow {
  path: string[];
  kind: "entry" | "block" | "directive";
  text: string;
  truncated: boolean;
  overridden: boolean;
  dimension?: string;
  unit?: string;
  original?: string | null;
}

/** One file of `case.files`. */
export interface CaseFile {
  path: string;
  area: string;
  kind: string;
  size: number | null;
  parsed: boolean;
}

/** A typed token as `case.set` takes it. */
export type Token = number | boolean | string | Token[] | { tokens: Token[] };

/**
 * The files a settings editor offers: the copy's parsed dictionaries and fields. `system/controlDict` comes
 * first, being what a user edits most (the end time, the write interval), then the rest of `system/`,
 * `constant/` and the fields.
 */
export function editableFiles(files: readonly CaseFile[]): CaseFile[] {
  const order = (f: CaseFile) =>
    f.path === "system/controlDict" ? -1 : f.path.startsWith("system/") ? 0 : f.path.startsWith("constant/") ? 1 : 2;
  return files
    .filter((f) => f.area === "source" && f.parsed && (f.kind === "dict" || f.kind === "field"))
    .sort((a, b) => order(a) - order(b) || a.path.localeCompare(b.path));
}

/** The rows a reader wants: the file's own header (`FoamFile`) left out. */
export function visibleRows(rows: readonly SettingsRow[]): SettingsRow[] {
  return rows.filter((r) => r.path[0] !== "FoamFile");
}

/** How deep a row sits, for its indent: a directive sits in its dictionary, an entry one level inside. */
export function depthOf(row: SettingsRow): number {
  return row.kind === "directive" ? row.path.length : Math.max(0, row.path.length - 1);
}

const NUMBER = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

function atom(text: string): Token {
  if (NUMBER.test(text)) return Number(text);
  if (text === "true" || text === "false") return text === "true";
  return text;
}

/** Split typed text into top-level tokens, keeping a `( ... )` list as one token. Null when unbalanced. */
function split(text: string): string[] | null {
  const out: string[] = [];
  let depth = 0;
  let current = "";
  for (const c of text) {
    if (c === "(") {
      if (depth === 0 && current.trim()) {
        out.push(current.trim());
        current = "";
      }
      depth += 1;
      current += c;
    } else if (c === ")") {
      depth -= 1;
      if (depth < 0) return null;
      current += c;
      if (depth === 0) {
        out.push(current.trim());
        current = "";
      }
    } else if (/\s/.test(c) && depth === 0) {
      if (current.trim()) out.push(current.trim());
      current = "";
    } else {
      current += c;
    }
  }
  if (depth !== 0) return null;
  if (current.trim()) out.push(current.trim());
  return out;
}

function token(text: string): Token | null {
  if (text.startsWith("(") && text.endsWith(")")) {
    const inner = split(text.slice(1, -1));
    if (inner === null) return null;
    const items = inner.map(token);
    return items.some((item) => item === null) ? null : (items as Token[]);
  }
  return atom(text);
}

/**
 * What to send `case.set` for a row's typed text, or a reason it cannot be sent as typed.
 *
 * A trailing `;` is forgiven (people type OpenFOAM's own syntax), but `{`, `#`, `$` and quotes are refused
 * here with a reason, because no typed token can hold them and the server would refuse them anyway.
 */
export function valueFor(row: SettingsRow, typed: string): { value: unknown } | { problem: string } {
  const text = typed.trim().replace(/;\s*$/, "").trim();
  if (!text) return { problem: "Type a value." };
  if (row.dimension) return { value: text };
  if (/[{}#$"']/.test(text)) return { problem: "Values are words, numbers and lists; braces, #, $ and quotes cannot be set here." };
  const parts = split(text);
  if (parts === null) return { problem: "The parentheses do not balance." };
  const tokens = parts.map(token);
  if (tokens.some((t) => t === null)) return { problem: "The parentheses do not balance." };
  return { value: tokens.length === 1 ? tokens[0] : { tokens: tokens as Token[] } };
}

/** One line for a recipe: its steps as commands, in order. */
export function recipeSummary(recipe: { steps?: { argv?: string[] }[] } | null | undefined): string {
  const steps = recipe?.steps ?? [];
  if (steps.length === 0) return "no steps";
  return steps.map((s) => (s.argv ?? []).join(" ")).join("  →  ");
}

/** One monitor table of `run.monitor`. */
export interface MonitorTable {
  columns: string[];
  values: Record<string, number[]>;
  truncated: boolean;
}

/**
 * An SVG polyline's `points` for `y` against `x`, scaled into a `width` x `height` box with y up. An empty
 * string when there is nothing to draw; a flat series is drawn along the middle.
 */
export function linePoints(x: readonly number[], y: readonly number[], width: number, height: number): string {
  const n = Math.min(x.length, y.length);
  if (n < 2) return "";
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i += 1) {
    x0 = Math.min(x0, x[i]);
    x1 = Math.max(x1, x[i]);
    y0 = Math.min(y0, y[i]);
    y1 = Math.max(y1, y[i]);
  }
  const sx = x1 > x0 ? width / (x1 - x0) : 0;
  const sy = y1 > y0 ? height / (y1 - y0) : 0;
  const out: string[] = [];
  for (let i = 0; i < n; i += 1) {
    const px = sx ? (x[i] - x0) * sx : (i / (n - 1)) * width;
    const py = sy ? height - (y[i] - y0) * sy : height / 2;
    out.push(`${px.toFixed(1)},${py.toFixed(1)}`);
  }
  return out.join(" ");
}
