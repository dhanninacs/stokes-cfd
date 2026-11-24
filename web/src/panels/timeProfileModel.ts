/**
 * The time-profile editor's rules (ADR-0022 addendum 2026-11-24): what a boundary value that varies in
 * time looks like while it is edited, and how the draft becomes the `profile` of `boundary.set_profile`
 * and `boundary.profile_preview`.
 *
 * The server owns the profile: it parses each quantity, checks the rules (two rows or more, increasing
 * times, a frequency above zero, a duty between 0 and 1) and samples the waveform for the plot. This module
 * only keeps the text the user typed, in the units the editor shows (m/s, kg/s, Pa, K, s, Hz, degrees),
 * turns a bare number into quantity text in that unit (`2` -> `2 [m/s]`), passes anything else through as
 * typed (`U_in * 1.5`, `30 [km/h]`), reads a stored profile back into text, and parses a block pasted from
 * a spreadsheet into rows.
 *
 * Import rule: no React, no store, no DOM.
 */

/** The forms the picker offers; `constant` is no profile at all. */
export type ProfileForm = "constant" | "table" | "sine" | "square";

export const FORMS: readonly { id: ProfileForm; label: string; tip: string }[] = [
  { id: "constant", label: "Constant", tip: "One value for the whole run." },
  { id: "table", label: "Table", tip: "Time and value pairs, linear in between and held after the last." },
  { id: "sine", label: "Sine", tip: "Mean + amplitude x sin(2 pi frequency t + phase)." },
  { id: "square", label: "Pulse", tip: "High for the first duty of each period, low for the rest (a square wave)." },
];

/** The values that may vary in time and the unit the editor shows each in. */
export const VALUE_UNITS: Readonly<Record<string, string>> = {
  velocity: "m/s",
  mass_flow: "kg/s",
  pressure: "Pa",
  temperature: "K",
};

/** What each value is called in a sentence. */
export const VALUE_WORDS: Readonly<Record<string, string>> = {
  velocity: "Speed",
  mass_flow: "Mass flow",
  pressure: "Pressure",
  temperature: "Temperature",
};

/** A field's quantity: the value's own unit, or a time, a frequency, an angle or a plain fraction. */
type FieldUnit = "value" | "time" | "frequency" | "angle" | "fraction";

export interface FieldDef {
  name: string;
  label: string;
  unit: FieldUnit;
}

export const FORM_FIELDS: Readonly<Record<"sine" | "square", readonly FieldDef[]>> = {
  sine: [
    { name: "mean", label: "Mean", unit: "value" },
    { name: "amplitude", label: "Amplitude", unit: "value" },
    { name: "frequency", label: "Frequency", unit: "frequency" },
    { name: "phase", label: "Phase", unit: "angle" },
  ],
  square: [
    { name: "low", label: "Low", unit: "value" },
    { name: "high", label: "High", unit: "value" },
    { name: "period", label: "Period", unit: "time" },
    { name: "duty", label: "Duty", unit: "fraction" },
  ],
};

const OTHER_UNITS: Readonly<Record<Exclude<FieldUnit, "value">, string>> = {
  time: "s",
  frequency: "Hz",
  angle: "deg",
  fraction: "",
};

/** The unit a field is shown and typed in. */
export function unitOf(field: FieldUnit, parameter: string): string {
  return field === "value" ? (VALUE_UNITS[parameter] ?? "") : OTHER_UNITS[field];
}

export interface ProfileRow {
  time: string;
  value: string;
}

/** The editor's state: the form and the text of every field, as typed. */
export interface ProfileDraft {
  form: ProfileForm;
  fields: Record<string, string>;
  rows: ProfileRow[];
}

/** A size of swing that reads as a swing for each value when the constant gives none (a 0 Pa outlet). */
const SWING: Readonly<Record<string, number>> = { velocity: 0.5, mass_flow: 0.001, pressure: 1000, temperature: 10 };

/** Values whose swing is a fixed difference, never a share of the constant: a temperature is in kelvin. */
const ABSOLUTE_SCALE: ReadonlySet<string> = new Set(["temperature"]);

/** Inflows: a default that crosses zero would reverse the flow, so a constant of 0 opens above zero. */
const ONE_WAY: ReadonlySet<string> = new Set(["velocity", "mass_flow"]);

/** The mean a new profile is built around when the constant gives none worth using. */
const START: Readonly<Record<string, number>> = { temperature: 293.15 };

function tidy(n: number): string {
  if (!Number.isFinite(n)) return "";
  return String(Number(n.toPrecision(6)));
}

/**
 * The centre and swing a new profile is built around. The centre is the constant; the swing is a quarter of
 * it, except for a temperature (10 K: a quarter of 293 K would swing 220 to 366 K) and a constant of 0, which
 * gives the value's own small swing. An inflow at 0 opens at twice its swing, so the default never reverses.
 */
export function defaultSwing(parameter: string, constant: number | null): { centre: number; swing: number } {
  const fixed = SWING[parameter] ?? 1;
  let centre = constant ?? START[parameter] ?? 0;
  if (centre === 0 && START[parameter] !== undefined) centre = START[parameter];
  if (ABSOLUTE_SCALE.has(parameter)) return { centre, swing: fixed };
  if (centre === 0) return { centre: ONE_WAY.has(parameter) ? 2 * fixed : 0, swing: fixed };
  return { centre, swing: Math.abs(centre) * 0.25 };
}

/** The draft a form starts from, built around the value's constant (a sine's mean is the constant). */
export function defaultDraft(form: ProfileForm, parameter: string, constant: number | null): ProfileDraft {
  const { centre: c, swing } = defaultSwing(parameter, constant);
  const fields: Record<string, string> = {};
  let rows: ProfileRow[] = [];
  if (form === "sine") {
    Object.assign(fields, { mean: tidy(c), amplitude: tidy(swing), frequency: "10", phase: "0" });
  } else if (form === "square") {
    Object.assign(fields, { low: tidy(c - swing), high: tidy(c + swing), period: "0.1", duty: "0.5" });
  } else if (form === "table") {
    rows = [
      { time: "0", value: tidy(c) },
      { time: "0.05", value: tidy(c + swing) },
      { time: "0.1", value: tidy(c) },
    ];
  }
  return { form, fields, rows };
}

/** Another form, keeping what carries over: a sine's mean and a pulse's levels from each other. */
export function switchForm(draft: ProfileDraft, form: ProfileForm, parameter: string, constant: number | null): ProfileDraft {
  if (form === draft.form) return draft;
  const mean = numberOf(draft.fields.mean ?? "");
  const low = numberOf(draft.fields.low ?? "");
  const high = numberOf(draft.fields.high ?? "");
  const centre = mean ?? (low !== null && high !== null ? (low + high) / 2 : constant);
  const next = defaultDraft(form, parameter, centre);
  if (form === "square" && mean !== null && draft.fields.amplitude !== undefined && numberOf(draft.fields.amplitude) !== null) {
    const amplitude = numberOf(draft.fields.amplitude) ?? 0;
    next.fields.low = tidy(mean - amplitude);
    next.fields.high = tidy(mean + amplitude);
  }
  if (form === "sine" && low !== null && high !== null) next.fields.amplitude = tidy(Math.abs(high - low) / 2);
  return next;
}

/** A bare number, or null for anything else (an expression, text with a unit, nothing). */
export function numberOf(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "" || !/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(trimmed)) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/** One field's text as the API takes it: a bare number in the field's unit, anything else as typed. */
export function quantityText(text: string, unit: string): string | number {
  const n = numberOf(text);
  if (n === null) return text.trim();
  return unit === "" ? n : `${n} [${unit}]`;
}

/** The draft as the `profile` of `boundary.set_profile`; null for a constant. */
export function toProfile(draft: ProfileDraft, parameter: string): Record<string, unknown> | null {
  if (draft.form === "constant") return null;
  if (draft.form === "table") {
    const value = unitOf("value", parameter);
    return {
      form: "table",
      rows: draft.rows.map((row) => ({ time: quantityText(row.time, "s"), value: quantityText(row.value, value) })),
    };
  }
  const out: Record<string, unknown> = { form: draft.form };
  for (const field of FORM_FIELDS[draft.form]) out[field.name] = quantityText(draft.fields[field.name] ?? "", unitOf(field.unit, parameter));
  return out;
}

/** A stored quantity, `{value, unit, expr?}` in SI. */
interface StoredQuantity {
  value: number;
  unit?: string;
  expr?: string;
}

function isStored(raw: unknown): raw is StoredQuantity {
  return typeof raw === "object" && raw !== null && typeof (raw as { value?: unknown }).value === "number";
}

/** A stored quantity as the editor shows it: its expression, else its SI value in the editor's unit. */
function shown(raw: unknown, field: FieldUnit): string {
  if (!isStored(raw)) return "";
  if (raw.expr) return raw.expr;
  return tidy(field === "angle" ? (raw.value * 180) / Math.PI : raw.value);
}

/** The stored profile of a boundary value, read back into a draft; the constant when there is none. */
export function draftFromStored(stored: unknown, parameter: string, constant: number | null): ProfileDraft {
  if (typeof stored !== "object" || stored === null) return defaultDraft("constant", parameter, constant);
  const raw = stored as Record<string, unknown>;
  const form = raw.form;
  if (form === "table" && Array.isArray(raw.rows)) {
    const rows = raw.rows.map((row) => {
      const r = (typeof row === "object" && row !== null ? row : {}) as Record<string, unknown>;
      return { time: shown(r.time, "time"), value: shown(r.value, "value") };
    });
    return { form: "table", fields: {}, rows };
  }
  if (form === "sine" || form === "square") {
    const fields: Record<string, string> = {};
    for (const field of FORM_FIELDS[form]) fields[field.name] = shown(raw[field.name], field.unit);
    return { form, fields, rows: [] };
  }
  return defaultDraft("constant", parameter, constant);
}

/** The boundary's stored profiles, by value name (an empty object when it has none). */
export function storedProfiles(params: Record<string, unknown>): Record<string, unknown> {
  const held = params.profiles;
  return typeof held === "object" && held !== null && !Array.isArray(held) ? (held as Record<string, unknown>) : {};
}

/**
 * The values of a boundary that vary in time: the server's answer as it is (`varies` on the node's summary, by
 * the server's one rule: the kind, its switches and whether the run solves energy, which a multi-region case
 * always does). The raw stored profiles are not the answer: a profile the boundary does not use (a components
 * inlet's speed, a temperature with energy off) is kept but ignored.
 */
export function varyingOf(node: { varies?: readonly string[] }): string[] {
  return [...(node.varies ?? [])];
}

/** A short line for a value that varies: "sine 2 ± 0.5 m/s, 10 Hz", "pulse 0-500 Pa, 0.02 s", "table, 3 rows". */
export function profileSummary(stored: unknown, parameter: string): string {
  const draft = draftFromStored(stored, parameter, null);
  const unit = VALUE_UNITS[parameter] ?? "";
  if (draft.form === "sine") return `sine ${draft.fields.mean} ± ${draft.fields.amplitude} ${unit}, ${draft.fields.frequency} Hz`;
  if (draft.form === "square") return `pulse ${draft.fields.low} to ${draft.fields.high} ${unit}, ${draft.fields.period} s`;
  if (draft.form === "table") return `table, ${draft.rows.length} rows`;
  return "";
}

/** The note under a varying value: which constant field below is kept but not used ("velocity"). */
export function constantKeptNote(field: string): string {
  return `Constant in "${field}" below: kept, not used`;
}

// ---------------------------------------------------------------- the table

export function addRow(draft: ProfileDraft): ProfileDraft {
  const last = draft.rows[draft.rows.length - 1];
  const before = draft.rows[draft.rows.length - 2];
  const t1 = numberOf(last?.time ?? "");
  const t0 = numberOf(before?.time ?? "");
  const step = t1 !== null && t0 !== null && t1 > t0 ? t1 - t0 : 0.1;
  const time = t1 === null ? "" : tidy(t1 + step);
  return { ...draft, rows: [...draft.rows, { time, value: last?.value ?? "" }] };
}

export function removeRow(draft: ProfileDraft, index: number): ProfileDraft {
  return { ...draft, rows: draft.rows.filter((_, i) => i !== index) };
}

export function setCell(draft: ProfileDraft, index: number, column: keyof ProfileRow, text: string): ProfileDraft {
  return { ...draft, rows: draft.rows.map((row, i) => (i === index ? { ...row, [column]: text } : row)) };
}

export function setField(draft: ProfileDraft, name: string, text: string): ProfileDraft {
  return { ...draft, fields: { ...draft.fields, [name]: text } };
}

/** A block pasted out of a spreadsheet, read into rows, with what was left out and why. */
export interface PastedBlock {
  rows: ProfileRow[];
  /** Lines (counted from 1) that were not rows and were left out: a second header, a note, text. */
  skipped: number[];
  /** Units a header gave its columns ("Time (ms)", "Speed [ft/s]"): bare numbers below it are in them. */
  units: { time?: string; value?: string };
  /** The separator the cells were split on, as the note names it. */
  separator: "tab" | "comma" | "semicolon" | "space";
  /** Whether a comma in a number was read as a decimal point (`0,5` is 0.5). */
  decimalComma: boolean;
  /**
   * Cells left as typed because their comma could be a thousands separator or a decimal comma (`101,325`):
   * the server then refuses them, and the note asks which was meant.
   */
  ambiguous: string[];
  /** Other things the note must say: header units not read, lines with more cells than a comma list allows. */
  notes: string[];
}

const NUMBER = String.raw`[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?`;
const BARE = new RegExp(`^${NUMBER}$`);
const STARTS_NUMERIC = new RegExp(`^${NUMBER}`);
const WITH_UNIT = new RegExp(String.raw`^(${NUMBER})\s*\[?\s*([^\]\s][^\]]*?)\s*\]?$`);
/** A number with one comma in it, and what follows (a unit): `0,5`, `1,5 m/s`. */
const COMMA_NUMBER = /^([-+]?)(\d+),(\d+)(?![\d,.])(.*)$/;
/** A number grouped in thousands, which could equally be a decimal comma: `101,325`, `1,000`. */
const THOUSANDS = /^[-+]?[1-9]\d{0,2}(?:,\d{3})+(?![\d,.])/;
/** A number grouped in thousands with a decimal point, which can only be thousands: `1,234.5`. */
const THOUSANDS_POINT = /^([-+]?[1-9]\d{0,2}(?:,\d{3})+)(\.\d+(?:[eE][-+]?\d+)?)(.*)$/;

/** A header cell's unit: the text in brackets or parentheses ("Time (ms)" gives "ms"). */
function headerUnit(cell: string | undefined): string | undefined {
  const found = /[([]\s*([^)\]]+?)\s*[)\]]/.exec(cell ?? "");
  return found ? found[1] : undefined;
}

/** Whether a cell's comma can only be a decimal comma: not grouped in threes (`0,5`, `1,25`, `0,125`). */
function surelyDecimal(cell: string): boolean {
  const m = COMMA_NUMBER.exec(cell.trim());
  return m !== null && !THOUSANDS.test(cell.trim());
}

/** The data lines of a paste: those whose first cell starts as a number does. */
function dataLines(lines: readonly string[]): string[] {
  return lines.filter((line) => STARTS_NUMERIC.test(line.trim()));
}

/**
 * The separator of a paste: a tab (what a spreadsheet copies); else a comma, unless the commas are decimal
 * commas (a semicolon is in the text, or the numbers are space-separated with commas inside them, `0,5 1,5`);
 * else a semicolon; else spaces.
 */
function detectSeparator(lines: readonly string[]): PastedBlock["separator"] {
  if (lines.some((line) => line.includes("\t"))) return "tab";
  if (lines.some((line) => line.includes(";"))) return "semicolon";
  const withComma = dataLines(lines).filter((line) => line.includes(","));
  if (withComma.length > 0) {
    const spacedDecimals = withComma.every((line) => {
      const tokens = line.trim().split(/\s+/);
      return tokens.length >= 2 && tokens.every((t) => BARE.test(t) || /^[-+]?\d+(?:,\d+)+$/.test(t)) && tokens.some((t) => t.includes(","));
    });
    return spacedDecimals ? "space" : "comma";
  }
  return "space";
}

/**
 * A header line's cells, whole: split on the separator, or for spaces, each word with the unit in brackets
 * after it ("Time (ms) Speed (m/s)" gives two cells, not four).
 */
function headerCells(line: string, separator: PastedBlock["separator"]): string[] {
  if (separator === "tab") return line.split("\t").map((c) => c.trim());
  if (separator === "comma") return line.split(",").map((c) => c.trim());
  if (separator === "semicolon") return line.split(";").map((c) => c.trim());
  return line.trim().match(/[([][^)\]]*[)\]]|[^\s([]+(?:\s*[([][^)\]]*[)\]])?/g) ?? [];
}

/** The units of a header: one per column when the cells line up, else none and a note. */
function headerUnits(cells: readonly string[], notes: string[]): PastedBlock["units"] {
  const units = cells.map(headerUnit);
  const found = units.filter((u): u is string => u !== undefined);
  if (found.length === 0) return {};
  if (cells.length === 2) return { time: units[0], value: units[1] };
  // More words than columns ("Time Inlet speed (m/s)"): the units can be placed only when each column has one.
  if (found.length === 2) return { time: found[0], value: found[1] };
  notes.push(`header units (${found.join(", ")}) not read: separate the columns with tabs`);
  return {};
}

/**
 * One pasted cell as the editor keeps it: a bare number as typed (in the header's unit when the header gave
 * one), a number with a unit as quantity text (`2 m/s` and `2 [m/s]` both give `2 [m/s]`, which the server
 * checks against the column, as it does for a typed cell), a blank as blank (the server then says which row
 * needs a value), and anything else as typed. A comma in a number is a decimal point only when the paste has
 * decimal commas; a number grouped in thousands (`101,325`) is otherwise kept as typed and reported.
 */
function pastedCell(cell: string, block: PastedBlock): string {
  let text = cell.trim();
  if (text === "") return "";
  const point = THOUSANDS_POINT.exec(text);
  if (!block.decimalComma && point) text = `${point[1].replace(/,/g, "")}${point[2]}${point[3]}`;
  else if (block.decimalComma) {
    const m = COMMA_NUMBER.exec(text);
    if (m) text = `${m[1]}${m[2]}.${m[3]}${m[4]}`;
  } else if (text.includes(",") && STARTS_NUMERIC.test(text)) {
    block.ambiguous.push(text);
    return text;
  }
  if (BARE.test(text)) return text;
  const withUnit = WITH_UNIT.exec(text);
  if (withUnit) return `${withUnit[1]} [${withUnit[2].trim()}]`;
  return text;
}

/** A cell in the header's unit when it is a bare number. */
function inUnit(text: string, unit: string | undefined): string {
  return unit !== undefined && BARE.test(text) ? `${text} [${unit}]` : text;
}

/**
 * Rows from text pasted out of a spreadsheet: one row a line, the cells split on the paste's separator
 * (`detectSeparator`). A first line whose time is not a number is the header; its cells are read whole, and a
 * unit in them ("Time (ms)") applies to the bare numbers below. Any later line whose time is not a number is
 * **left out and reported**, as is a comma-separated line of more than two cells (a decimal or thousands
 * comma would make one). Cells keep their units and blanks, so the server checks every row as it checks a
 * typed one, and says which is wrong. A comma inside a number is a decimal comma only when the paste shows it
 * is (`0,5`, or a semicolon or space separator with such numbers); `101,325` alone is never read as 101.325.
 */
export function parsePasted(text: string): PastedBlock {
  const lines = text.split(/\r?\n/);
  const filled = lines.filter((line) => line.trim() !== "");
  const separator = detectSeparator(filled);
  const split = (line: string): string[] => {
    // Split on tabs before trimming, so a blank first or last cell stays a cell.
    if (separator === "tab") return line.split("\t").map((c) => c.trim());
    if (separator === "semicolon") return line.split(";").map((c) => c.trim());
    if (separator === "comma") return line.trim().split(",").map((c) => c.trim());
    return line.trim().split(/\s+/);
  };
  const cellsOf = dataLines(filled).flatMap((line) => split(line).slice(0, 2));
  const decimalComma = separator !== "comma" && cellsOf.some(surelyDecimal);
  const block: PastedBlock = { rows: [], skipped: [], units: {}, separator, decimalComma, ambiguous: [], notes: [] };
  let first = true;
  const crowded: number[] = [];
  lines.forEach((line, index) => {
    if (line.trim() === "") return;
    if (!STARTS_NUMERIC.test(line.trim())) {
      if (first) block.units = headerUnits(headerCells(line, separator), block.notes);
      else block.skipped.push(index + 1);
      first = false;
      return;
    }
    first = false;
    const cells = split(line);
    if (separator === "comma" && cells.filter((c) => c !== "").length > 2) {
      crowded.push(index + 1);
      return;
    }
    const time = pastedCell(cells[0] ?? "", block);
    const value = pastedCell(cells[1] ?? "", block);
    block.rows.push({ time: inUnit(time, block.units.time), value: inUnit(value, block.units.value) });
  });
  if (crowded.length > 0) {
    const which = crowded.length === 1 ? `line ${crowded[0]} has` : `lines ${crowded.join(", ")} have`;
    block.notes.push(`${which} more than two comma-separated cells (a decimal or thousands comma?), left out`);
  }
  return block;
}

/**
 * Whether a paste is the editor's to take: it brought rows, or lines it left out and says so (every line a
 * thousands-grouped comma list, `0,101,325`). Only a paste with nothing to show goes to the cell as typed text.
 */
export function pasteTaken(block: PastedBlock): boolean {
  return block.rows.length > 0 || block.skipped.length > 0 || block.notes.length > 0 || block.ambiguous.length > 0;
}

/** The rows a paste left in place after its own: "row 5 kept", "rows 5-6 kept", or nothing. */
export function keptAfter(total: number, index: number, pasted: number): string | undefined {
  const from = index + pasted;
  if (pasted === 0 || from >= total) return undefined;
  return from + 1 === total ? `row ${total} kept` : `rows ${from + 1}-${total} kept`;
}

/**
 * What to say under the table after a paste: how many rows came in and in which units, which old rows after
 * them stay (`kept`, from `keptAfter`), which lines were left out, and any cell whose comma is ambiguous.
 */
export function pasteNote(block: PastedBlock, kept?: string): { text: string; warn: boolean } {
  const parts: string[] = [];
  const units = [block.units.time, block.units.value].filter((u): u is string => u !== undefined);
  parts.push(`${block.rows.length} row${block.rows.length === 1 ? "" : "s"} pasted${units.length > 0 ? `, in ${units.join(" and ")}` : ""}`);
  if (kept !== undefined) parts.push(kept);
  let warn = false;
  if (block.skipped.length > 0) {
    parts.push(`${block.skipped.length === 1 ? `line ${block.skipped[0]}` : `lines ${block.skipped.join(", ")}`} not a row, left out`);
    warn = true;
  }
  if (block.ambiguous.length > 0) {
    const cells = block.ambiguous.map((c) => `'${c}'`).join(", ");
    parts.push(`${cells}: thousands separator or decimal comma? Type the number without the comma, or with a point`);
    warn = true;
  }
  if (block.notes.length > 0) {
    parts.push(...block.notes);
    warn = true;
  }
  return { text: parts.join("; "), warn };
}

/**
 * Pasted rows put in at `index`: they replace as many rows as they are from there on, as a paste over a
 * spreadsheet's cells does, and add rows past the end; the rows after them stay.
 */
export function pasteRows(draft: ProfileDraft, index: number, pasted: ProfileRow[]): ProfileDraft {
  if (pasted.length === 0) return draft;
  return { ...draft, rows: [...draft.rows.slice(0, index), ...pasted, ...draft.rows.slice(index + pasted.length)] };
}

/** Whether a paste is a block of rows (more than one cell) rather than one value for one cell. */
export function isBlockPaste(text: string): boolean {
  return /[\t\n;]/.test(text.trim()) || parsePasted(text).rows.length > 1;
}

// ---------------------------------------------------------------- what the server said

/** Which input a refusal belongs to: `frequency`, `rows.2.time`, or the form as a whole. */
export function errorKey(field: string | undefined): string {
  if (field === undefined || field === "" || field === "parameter") return "form";
  if (field === "value") return "form";
  return field;
}

/** The refusals of a preview, by the input each belongs to: all of them when the server lists them, else the one. */
export function previewRefusals(preview: ProfilePreview | null): { field: string; message: string }[] {
  if (preview === null || preview.ok) return [];
  if (Array.isArray(preview.errors) && preview.errors.length > 0) return preview.errors.map((e) => ({ field: errorKey(e.field), message: e.message }));
  return preview.message ? [{ field: errorKey(preview.field), message: preview.message }] : [];
}

/** What the server allows but likely is not meant, by the input each belongs to (shown under it, not as an error). */
export function previewNotes(preview: ProfilePreview | null): Record<string, string> {
  const found: Record<string, string> = {};
  if (preview === null || !preview.ok) return found;
  for (const note of preview.notes ?? []) {
    const key = errorKey(note.field);
    found[key] = found[key] ? `${found[key]}; ${note.message}` : note.message;
  }
  return found;
}

/** The preview the editor plots: `boundary.profile_preview`'s answer. */
export interface ProfilePreview {
  ok: boolean;
  field?: string;
  message?: string;
  /** Every refusal, when the server reports more than the first (each row that is wrong, at once). */
  errors?: { field?: string; message: string }[];
  times?: number[];
  values?: number[];
  end_time?: number;
  run_end_time?: number | null;
  profile?: { mean: number; cycle: number; min: number; max: number };
  /** What is allowed but likely not meant (a pulse whose high is below its low, a temperature swing read as a level), by field. */
  notes?: { field: string; message: string }[];
}

export interface PlotShape {
  /** The waveform as an SVG path in a `width` x `height` box. */
  path: string;
  /** The time-mean's height in the box, for a dashed line. */
  meanY: number | null;
  low: number;
  high: number;
  end: number;
}

/** The waveform scaled into a box, with a little room above and below so a flat line is not on the edge. */
export function plotShape(times: readonly number[], values: readonly number[], width: number, height: number, mean: number | null = null): PlotShape | null {
  const n = Math.min(times.length, values.length);
  if (n < 2) return null;
  let low = Infinity;
  let high = -Infinity;
  for (let i = 0; i < n; i++) {
    low = Math.min(low, values[i]);
    high = Math.max(high, values[i]);
  }
  const span = high - low;
  const pad = span > 0 ? span * 0.1 : Math.abs(high) * 0.1 || 1;
  const bottom = low - pad;
  const top = high + pad;
  const end = times[n - 1] > times[0] ? times[n - 1] : times[0] + 1;
  const x = (t: number) => ((t - times[0]) / (end - times[0])) * width;
  const y = (v: number) => height - ((v - bottom) / (top - bottom)) * height;
  const parts: string[] = [];
  for (let i = 0; i < n; i++) parts.push(`${i === 0 ? "M" : "L"}${x(times[i]).toFixed(1)} ${y(values[i]).toFixed(1)}`);
  return { path: parts.join(" "), meanY: mean === null ? null : y(mean), low, high, end };
}

/** A number for an axis label: few digits, no trailing noise. */
export function axisNumber(n: number): string {
  if (!Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e5)) return n.toExponential(2);
  return String(Number(n.toPrecision(4)));
}

// ---------------------------------------------------------------- the results history

/** A boundary value that varies in time, for the Results history's note. */
export interface VaryingValue {
  boundary: string;
  parameter: string;
  summary: string;
}

/** Every boundary value in the tree that varies in time (`varyingOf`, the server's answer). */
export function varyingInTree(tree: { type: string; name: string; params: Record<string, unknown>; varies?: readonly string[]; children?: unknown[] } | null): VaryingValue[] {
  const out: VaryingValue[] = [];
  const stack = tree ? [tree] : [];
  while (stack.length) {
    const node = stack.pop()!;
    if (node.type === "boundary") {
      const held = storedProfiles(node.params);
      for (const parameter of varyingOf(node)) out.push({ boundary: node.name, parameter, summary: profileSummary(held[parameter], parameter) });
    }
    const children = (node.children ?? []) as (typeof tree)[];
    for (let i = children.length - 1; i >= 0; i--) if (children[i]) stack.push(children[i]!);
  }
  return out;
}

/** What the solver reports over time for a patch: a flow rate for an inflow, a mean pressure for a pressure. */
const REPORTED: Readonly<Record<string, string>> = { velocity: "patchFlowRate", mass_flow: "patchFlowRate", pressure: "patchAverage" };

/**
 * One line per value that varies, for under the Results history: which report follows it (the inlet's flow rate
 * is its speed times its area; an outlet's mean p is its pressure over the density), or that no report does
 * (the solver reports no temperature at a patch).
 */
export function historyNotes(monitors: readonly string[], varying: readonly VaryingValue[]): { text: string; tip: string }[] {
  return varying.map((v) => {
    const kind = REPORTED[v.parameter];
    const report = kind === undefined ? undefined : monitors.find((m) => m.startsWith(kind) && /patch=([^,)]+)/.exec(m)?.[1] === v.boundary);
    const word = (VALUE_WORDS[v.parameter] ?? v.parameter).toLowerCase();
    return report !== undefined
      ? { text: `${v.boundary} ${word}: ${v.summary}`, tip: `The ${kind === "patchFlowRate" ? "flow rate" : "mean pressure"} report of ${v.boundary} follows this profile.` }
      : { text: `${v.boundary} ${word}: no report over time`, tip: `${v.boundary}'s ${word} varies (${v.summary}), and the run reports nothing at that patch that follows it.` };
  });
}

/** The report the history opens on: the first that follows a varying value, else the first. */
export function followingReport(monitors: readonly string[], varying: readonly VaryingValue[]): number {
  for (const v of varying) {
    const kind = REPORTED[v.parameter];
    const index = kind === undefined ? -1 : monitors.findIndex((m) => m.startsWith(kind) && /patch=([^,)]+)/.exec(m)?.[1] === v.boundary);
    if (index >= 0) return index;
  }
  return 0;
}
