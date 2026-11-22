/**
 * Sizes typed while drawing (review B4): after the first click of a rectangle, `40 Tab 20 Enter`
 * makes a 40 × 20 mm rectangle; a line takes a length and an angle (degrees, from the u axis,
 * counter-clockwise), a circle a radius or (Tab) a diameter, a polygon its corner radius. The
 * fields are drawn on the canvas beside the cursor and fed by the sketch's own key handler, so
 * digits never reach a text field or the app. A bare number is millimetres; a rectangle's,
 * circle's or polygon's field also takes an expression of the sketch's dimensions (`W`), which
 * binds that size. Pure: the tests run in Node.
 */
import type { Point, Tool } from "./model";

export interface Entry {
  labels: string[];
  fields: string[];
  active: number;
}

const NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

/** The fields a draft takes, or null when its next step is a click only (arc, spline). */
export function entryLabels(tool: Tool, points: readonly Point[]): string[] | null {
  if (points.length === 0) return null;
  if (tool === "line") return ["Length", "Angle"];
  if (points.length !== 1) return null;
  if (tool === "rectangle") return ["Width", "Height"];
  if (tool === "circle") return ["Radius", "Diameter"];
  if (tool === "polygon") return ["Radius"];
  return null;
}

export function newEntry(labels: string[]): Entry {
  return { labels, fields: labels.map(() => ""), active: 0 };
}

export function typeInto(e: Entry, ch: string): Entry {
  return { ...e, fields: e.fields.map((f, i) => (i === e.active ? f + ch : f)) };
}

export function backspace(e: Entry): Entry {
  return { ...e, fields: e.fields.map((f, i) => (i === e.active ? f.slice(0, -1) : f)) };
}

export function nextField(e: Entry, back = false): Entry {
  const n = e.labels.length;
  return { ...e, active: (e.active + (back ? n - 1 : 1)) % n };
}

export function isEmpty(e: Entry | null): boolean {
  return e === null || e.fields.every((f) => f.trim() === "");
}

/** The values the cursor gives each field now (mm, degrees), shown until the user types. */
export function liveValues(tool: Tool, points: readonly Point[], cursor: Point | null): number[] {
  if (cursor === null || points.length === 0) return [];
  const from = points[points.length - 1];
  const du = cursor[0] - from[0];
  const dv = cursor[1] - from[1];
  if (tool === "rectangle") return [Math.abs(du), Math.abs(dv)];
  if (tool === "line") return [Math.hypot(du, dv), (((Math.atan2(dv, du) * 180) / Math.PI) + 360) % 360];
  if (tool === "circle") return [Math.hypot(du, dv), 2 * Math.hypot(du, dv)];
  if (tool === "polygon") return [Math.hypot(du, dv)];
  return [];
}

export type Resolved = { point: Point; binds: Record<string, string> } | { error: string };

/**
 * The click the typed fields stand for, and the sizes they bind (`w`, `h`, `r` to expression
 * texts). An empty field takes the cursor's value; a direction the fields do not give comes from
 * the cursor too. `known` is the last value (mm) the server gave an expression, if any.
 */
export function resolveEntry(tool: Tool, points: readonly Point[], cursor: Point | null, entry: Entry, known: (text: string) => number | undefined): Resolved {
  const live = liveValues(tool, points, cursor);
  const binds: Record<string, string> = {};
  // A number, or an error text (a string) when the field cannot be used.
  const field = (i: number, bind: string | null): number | string => {
    const text = (entry.fields[i] ?? "").trim();
    if (text === "") return live[i] ?? Number.NaN;
    if (NUMBER.test(text)) return Number(text);
    if (bind === null) return `${entry.labels[i]} takes a number here`;
    binds[bind] = text;
    return known(text) ?? live[i] ?? 10;
  };
  const from = points[points.length - 1];
  if (tool === "line") {
    const lengthTyped = (entry.fields[0] ?? "").trim() !== "";
    const angleTyped = (entry.fields[1] ?? "").trim() !== "";
    const length = field(0, null);
    const angle = field(1, null);
    if (typeof length === "string") return { error: length };
    if (typeof angle === "string") return { error: angle };
    let a = angleTyped ? (angle * Math.PI) / 180 : cursor ? Math.atan2(cursor[1] - from[1], cursor[0] - from[0]) : 0;
    if (!Number.isFinite(a)) a = 0;
    let l = length;
    if (!lengthTyped && angleTyped && cursor) l = (cursor[0] - from[0]) * Math.cos(a) + (cursor[1] - from[1]) * Math.sin(a);
    if (!(l > 0)) return { error: "Give the line a length above zero" };
    return { point: [from[0] + l * Math.cos(a), from[1] + l * Math.sin(a)], binds };
  }
  if (tool === "rectangle") {
    const w = field(0, "w");
    const h = field(1, "h");
    if (typeof w === "string" || typeof h === "string") return { error: String(typeof w === "string" ? w : h) };
    if (!(w > 0) || !(h > 0)) return { error: "Give the rectangle a width and a height above zero" };
    const su = cursor && cursor[0] < from[0] ? -1 : 1;
    const sv = cursor && cursor[1] < from[1] ? -1 : 1;
    return { point: [from[0] + su * w, from[1] + sv * h], binds };
  }
  if (tool === "circle" || tool === "polygon") {
    const diameter = tool === "circle" && (entry.fields[0] ?? "").trim() === "" ? (entry.fields[1] ?? "").trim() : "";
    let r: number;
    if (diameter === "") {
      const value = field(0, "r");
      if (typeof value === "string") return { error: value };
      r = value;
    } else if (NUMBER.test(diameter)) {
      r = Number(diameter) / 2;
    } else {
      binds.r = `(${diameter}) / 2`;
      r = known(binds.r) ?? live[0] ?? 5;
    }
    if (!(r > 0)) return { error: "Give it a radius above zero" };
    const a = cursor && (cursor[0] !== from[0] || cursor[1] !== from[1]) ? Math.atan2(cursor[1] - from[1], cursor[0] - from[0]) : 0;
    return { point: [from[0] + r * Math.cos(a), from[1] + r * Math.sin(a)], binds };
  }
  return { error: "This tool takes clicks only" };
}
