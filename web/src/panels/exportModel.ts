/**
 * The image export as a model (R3 of the results overhaul): the settings a figure is made with, the pixel size
 * they give, where the legend and the title go in it, the file name, the sidecar that lets the figure be
 * remade, and the bytes of the PNG text chunk that carries the same sidecar inside the picture. Pure
 * functions, so every rule is tested in Node; `viewport/exportImage.ts` draws what this lays out, and the
 * dialog (`ExportImageDialog.tsx`) only edits the settings.
 *
 * **Why a layout and not "paint the legend on the picture"**: a legend drawn over the model hides part of
 * it, and a journal figure wants the colour bar beside the picture, not on it. For `right` and `bottom` the
 * model is drawn in what is left after the legend's strip and the title's, so the image is exactly the size
 * the user asked for and nothing is covered; `floating` is the one placement that overlays, with a panel
 * behind it so its ticks stay readable.
 *
 * **The legend is always at print size** (`legendModel`'s fonts 1.5x and a thicker bar): an export is for
 * paper, and the on-screen size shrinks to nothing at 4x. Fonts and bars scale with `unit`, the pixels per
 * CSS pixel of the window, so a 4x picture is the 1x picture with everything four times larger.
 *
 * Import rule: no React, no store, no DOM. Types only from other modules' state, so this loads in Node.
 */

import { bandColor, colorAtIn, colormapOf, fractionIn, type ColormapId } from "../state/colormap";
import type { RunFields } from "./resultsApi";
import { NO_FIELD, fieldTitle, fieldUnits, isLocked, rangeForField, type Range, type ResultsView } from "./fieldsModel";
import { legendLabels, legendOf, rangeModeOf, type LegendPlacement, type LegendView } from "./legendModel";
import type { SliceSpec } from "./sliceModel";

// ---------------------------------------------------------------------------------------------- settings
export type ExportScale = 1 | 2 | 4;
export type ExportBackground = "viewport" | "white" | "transparent";

/** What the user chooses in the dialog. */
export interface ExportSettings {
  /** The window's size times this, unless `size` is set. */
  scale: ExportScale;
  /** An exact size in pixels; it wins over `scale`. */
  size?: { w: number; h: number };
  background: ExportBackground;
  legend: boolean;
  /** Printed above the picture; "" for none. */
  title: string;
  scaleBar: boolean;
}

export const EXPORT_SCALES: readonly ExportScale[] = [1, 2, 4];
export const EXPORT_BACKGROUNDS: readonly ExportBackground[] = ["viewport", "white", "transparent"];
/** The words a control shows: one or two, the sentence is the tooltip. */
export const BACKGROUND_WORDS: Record<ExportBackground, string> = { viewport: "Viewport", white: "White", transparent: "Clear" };

/** White is the default: a figure goes onto a white page, and a dark viewport is the thing to ask for. */
export const DEFAULT_EXPORT: ExportSettings = { scale: 2, background: "white", legend: true, title: "", scaleBar: true };

/** No side below this (a smaller picture is not a figure), none above `MAX_SIDE` however large a driver allows. */
export const MIN_SIDE = 64;
export const MAX_SIDE = 16384;
/**
 * A frame of more pixels than this is scaled down (5700 x 5700): the canvas, the copy and the PNG are each this
 * times four bytes, and the drawing buffer, its depth buffer and the anti-aliasing samples come on top.
 * **Was 80 M** until the review of R3: a 4x frame on a large window could exhaust the GPU's memory and come out
 * as a blank PNG with no error, so the cap sits where a mid-range GPU still draws the frame (a 4x export of a
 * 1500 x 900 window is 21.6 M and is unaffected).
 */
export const MAX_PIXELS = 33_000_000;

/** Ticks on the export's legend: the acceptance (plan §5, item 1) asks for six, one more than the live legend draws. */
export const EXPORT_TICKS = 6;

function oneOf<T extends string | number>(value: unknown, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly unknown[]).includes(value) ? (value as T) : fallback;
}

function sideOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? Math.min(MAX_SIDE, Math.max(MIN_SIDE, Math.round(value))) : null;
}

/**
 * Settings out of anything (a stored string already parsed, a test literal): every missing or nonsensical
 * field replaced by its default. **Tolerant on purpose**, as `legendOf` is: a stored choice from an older
 * build must not stop the dialog from opening.
 */
export function exportSettingsOf(raw: unknown): ExportSettings {
  const r = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const out: ExportSettings = {
    scale: oneOf(r.scale, EXPORT_SCALES, DEFAULT_EXPORT.scale),
    background: oneOf(r.background, EXPORT_BACKGROUNDS, DEFAULT_EXPORT.background),
    legend: typeof r.legend === "boolean" ? r.legend : DEFAULT_EXPORT.legend,
    title: typeof r.title === "string" ? r.title.slice(0, 200) : DEFAULT_EXPORT.title,
    scaleBar: typeof r.scaleBar === "boolean" ? r.scaleBar : DEFAULT_EXPORT.scaleBar,
  };
  const size = typeof r.size === "object" && r.size !== null ? (r.size as Record<string, unknown>) : null;
  const w = size === null ? null : sideOf(size.w);
  const h = size === null ? null : sideOf(size.h);
  if (w !== null && h !== null) out.size = { w, h };
  return out;
}

// ---------------------------------------------------------------------------------------------- remembering
/** The `localStorage` key the remembered settings live under. */
export const EXPORT_KEY = "stokes.export.settings";

export interface StoredExport {
  /** The user ticked "Remember": the settings come back next time. */
  remember: boolean;
  settings: ExportSettings;
}

/** What the dialog opens with: the remembered settings, else the defaults. A refusing or empty storage is the defaults. */
export function readStoredExport(storage: Pick<Storage, "getItem"> | null): StoredExport {
  const fresh: StoredExport = { remember: false, settings: { ...DEFAULT_EXPORT } };
  if (storage === null) return fresh;
  try {
    const text = storage.getItem(EXPORT_KEY);
    if (text === null) return fresh;
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || (parsed as { remember?: unknown }).remember !== true) return fresh;
    return { remember: true, settings: exportSettingsOf((parsed as { settings?: unknown }).settings) };
  } catch {
    return fresh;
  }
}

/**
 * Keep the settings when "Remember" is on, forget them when it is off. **The title is never kept**: it names
 * one figure, and the next one starting with the last one's title is a mistake waiting to be exported.
 * A refusal is swallowed: a remembered choice is a convenience.
 */
export function writeStoredExport(storage: Pick<Storage, "setItem" | "removeItem"> | null, stored: StoredExport): void {
  if (storage === null) return;
  try {
    if (!stored.remember) storage.removeItem(EXPORT_KEY);
    else storage.setItem(EXPORT_KEY, JSON.stringify({ remember: true, settings: { ...stored.settings, title: "" } }));
  } catch {
    // The settings then last for this session only.
  }
}

// ---------------------------------------------------------------------------------------------- the size
export interface ExportPixels {
  w: number;
  h: number;
  /** Pixels per CSS pixel of the window: what fonts and bars are multiplied by. */
  unit: number;
  /** The size asked for did not fit (`maxSide`, `MAX_PIXELS`) and was scaled down, keeping its shape. */
  clamped: boolean;
}

/**
 * The picture's size in pixels. `size` wins over `scale`; the result is reduced, keeping the aspect, until
 * it fits what the driver allows (`maxSide`, from `SceneManager.maxExportSide`) and `MAX_PIXELS`, and
 * `clamped` says so, so the dialog can tell the user the 4x they asked for was less.
 */
export function exportPixels(settings: Pick<ExportSettings, "scale" | "size">, live: { width: number; height: number }, maxSide: number = MAX_SIDE): ExportPixels {
  const liveW = Math.max(1, live.width);
  const liveH = Math.max(1, live.height);
  let w = settings.size ? settings.size.w : liveW * settings.scale;
  let h = settings.size ? settings.size.h : liveH * settings.scale;
  w = Math.max(MIN_SIDE, Math.round(w));
  h = Math.max(MIN_SIDE, Math.round(h));
  const side = Math.max(1, Math.min(maxSide, MAX_SIDE));
  const k = Math.min(1, side / Math.max(w, h), Math.sqrt(MAX_PIXELS / (w * h)));
  const clamped = k < 1;
  if (clamped) {
    w = Math.max(1, Math.floor(w * k));
    h = Math.max(1, Math.floor(h * k));
  }
  return { w, h, unit: w / liveW, clamped };
}

// ---------------------------------------------------------------------------------------------- the file name
/** One part of a file name: letters, digits, dot, underscore and hyphen; anything else is a hyphen, runs and the ends trimmed. */
export function safeNamePart(text: string, max = 40): string {
  const cleaned = text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
  return cleaned.slice(0, max).replace(/[-.]+$/g, "");
}

/** What is drawn, as the inputs `looksShown` reads. */
export interface LookInputs {
  /** The walls are coloured by a field. */
  surface: boolean;
  /** Slices that are on. */
  slices: number;
  iso: boolean;
  streamlines: boolean;
  arrows: boolean;
}

/** The looks on screen, in the order of the Results panel. */
export function looksShown(inputs: LookInputs): string[] {
  const out: string[] = [];
  if (inputs.surface) out.push("surface");
  if (inputs.slices > 0) out.push("slice");
  if (inputs.iso) out.push("iso");
  if (inputs.streamlines) out.push("streamlines");
  if (inputs.arrows) out.push("vectors");
  return out;
}

/**
 * `<project>-<run>-<field>-<look>.png` with safe characters. A part that is empty is left out (no field
 * chosen is no field part), a look is the looks on screen joined by `_` (`surface_slice`) and "view" when
 * nothing is coloured, so the name always has at least a project and a look.
 */
export function exportFileName(parts: { project: string; run: string; field: string; looks: readonly string[] }, extension = "png"): string {
  const look = parts.looks.length > 0 ? parts.looks.join("_") : "view";
  const pieces = [safeNamePart(parts.project) || "stokes", safeNamePart(parts.run), safeNamePart(parts.field), safeNamePart(look)].filter((p) => p !== "");
  return `${pieces.join("-")}.${extension}`;
}

/** The sidecar's name: the picture's, with `.json` for `.png`. */
export function sidecarFileName(pictureName: string): string {
  return pictureName.replace(/\.png$/i, "") + ".json";
}

// ---------------------------------------------------------------------------------------------- the sidecar
/** The camera as the rig keeps it (`ViewState`) and the projection, as plain numbers. */
export interface ExportCamera {
  target: [number, number, number];
  /** The unit vector from the target to the eye. */
  dir: [number, number, number];
  /** The height of the view at the target, in metres. */
  span: number;
  orthographic: boolean;
  /** The camera's up vector, when the rig has one (a face-on view may turn it); a saved view restores it. Absent in files from before R4. */
  up?: [number, number, number];
}

/** What the figure shows, read from the stores by the caller. */
export interface ExportView {
  project: string;
  run: string;
  /** The results view (`results.view`): field, range lock, bands and the legend's settings. */
  view: ResultsView;
  data: RunFields | null;
  /** The slices that are on. */
  slices: readonly SliceSpec[];
  iso: { field: string; value: number; units: string } | null;
  streamlines: boolean;
  arrows: { density: number; scale: number } | null;
}

/** The range the legend and the colours span, or null with no field. */
export function exportRange(view: ExportView): Range | null {
  return view.view.field === NO_FIELD ? null : rangeForField(view.view, view.data, view.view.field);
}

/** The looks on screen for this view. */
export function exportLooks(view: ExportView): string[] {
  return looksShown({
    surface: view.view.field !== NO_FIELD && view.data !== null,
    slices: view.slices.length,
    iso: view.iso !== null,
    streamlines: view.streamlines,
    arrows: view.arrows !== null,
  });
}

/** The name of the picture for this view. */
export function exportName(view: ExportView): string {
  return exportFileName({ project: view.project, run: view.run, field: view.view.field === NO_FIELD ? "" : view.view.field, looks: exportLooks(view) });
}

/** `stokes:view`: the PNG text keyword the sidecar travels under. */
export const PNG_KEYWORD = "stokes:view";

export interface Sidecar {
  version: 1;
  stokes: {
    project: string;
    run: string;
    camera: ExportCamera;
    field: { name: string; title: string; units: string };
    /** The range the colours span; `locked` says it was fixed by the user, not the run's own. */
    range: { min: number; max: number; locked: boolean } | null;
    map: ColormapId;
    reverse: boolean;
    bands: number;
    looks: {
      surface: boolean;
      slices: { id: string; plane: unknown; field: string; fill: boolean; lines: boolean; bands: boolean; levels: number; lineColour: string }[];
      iso: { field: string; value: number; units: string } | null;
      streamlines: boolean;
      arrows: { density: number; scale: number } | null;
    };
    legend: Pick<LegendView, "rangeMode" | "fullRange" | "format" | "digits" | "placement"> & { shown: boolean; title: string };
    image: { width: number; height: number; scale: ExportScale | null; background: ExportBackground; scaleBar: boolean };
  };
}

function finite(n: number): number | null {
  return Number.isFinite(n) ? n : null;
}

/**
 * Everything needed to make the figure again, as a plain object. **Numbers that are not finite are written as
 * null** (JSON has no NaN), and the object is built from the view's own fields rather than spread from the
 * stores, so a field added to a store tomorrow cannot leak into a file that has a version.
 */
export function buildSidecar(view: ExportView, camera: ExportCamera, settings: ExportSettings, pixels: Pick<ExportPixels, "w" | "h">): Sidecar {
  const look = legendOf(view.view);
  const range = exportRange(view);
  const name = view.view.field;
  const colouring = name !== NO_FIELD && view.data !== null;
  return {
    version: 1,
    stokes: {
      project: view.project,
      run: view.run,
      camera: {
        target: camera.target.map((v) => finite(v) ?? 0) as [number, number, number],
        dir: camera.dir.map((v) => finite(v) ?? 0) as [number, number, number],
        span: finite(camera.span) ?? 0,
        orthographic: camera.orthographic,
        ...(camera.up === undefined ? {} : { up: camera.up.map((v) => finite(v) ?? 0) as [number, number, number] }),
      },
      field: { name, title: colouring ? fieldTitle(view.data, name) : "", units: colouring ? fieldUnits(view.data, name) : "" },
      range: range === null ? null : { min: range.min, max: range.max, locked: isLocked(view.view) },
      map: look.map,
      reverse: look.reverse,
      bands: look.bands,
      looks: {
        surface: colouring,
        slices: view.slices.map((s) => ({ id: s.id, plane: s.plane, field: s.field, fill: s.fill, lines: s.lines, bands: s.bands, levels: s.levels, lineColour: s.lineColour })),
        iso: view.iso,
        streamlines: view.streamlines,
        arrows: view.arrows,
      },
      legend: {
        // The mode in force (a fixed range is locked or manual), not the stored flag, which a later unlock leaves behind.
        rangeMode: rangeModeOf(view.view),
        fullRange: look.fullRange,
        format: look.format,
        digits: look.digits,
        placement: look.placement,
        shown: settings.legend && range !== null,
        title: colouring ? fieldTitle(view.data, name) : "",
      },
      image: { width: pixels.w, height: pixels.h, scale: settings.size ? null : settings.scale, background: settings.background, scaleBar: settings.scaleBar },
    },
  };
}

/** The sidecar as the `.json` file's text: indented, ending in a newline. */
export function sidecarText(sidecar: Sidecar): string {
  return JSON.stringify(sidecar, null, 2) + "\n";
}

/**
 * JSON with every character above ASCII written as `\uXXXX`. A PNG `tEXt` chunk is Latin-1, and a title
 * with `Δp` or a unit with `µ` would otherwise be corrupted or refused; JSON allows the escape anywhere a
 * non-ASCII character can occur (inside a string), so this changes the bytes and not the meaning.
 */
export function asciiJson(value: unknown): string {
  return JSON.stringify(value).replace(/[\u007f-￿]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
}

// ---------------------------------------------------------------------------------------------- the PNG text chunk
export const PNG_SIGNATURE: readonly number[] = [137, 80, 78, 71, 13, 10, 26, 10];

let crcTable: Uint32Array | null = null;

/** CRC-32 as PNG defines it (the IEEE polynomial, reflected, initial and final xor 0xFFFFFFFF); unsigned. */
export function crc32(bytes: Uint8Array): number {
  if (crcTable === null) {
    crcTable = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) crc = crcTable[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function latin1(text: string, what: string): number[] {
  const out: number[] = [];
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code > 255) throw new RangeError(`${what} has a character above Latin-1 (U+${code.toString(16).toUpperCase().padStart(4, "0")}); escape it first (asciiJson)`);
    if (code === 0) throw new RangeError(`${what} has a NUL`);
    out.push(code);
  }
  return out;
}

/**
 * The bytes of a PNG `tEXt` chunk: length, `tEXt`, keyword, NUL, text, CRC. The keyword is 1 to 79 Latin-1
 * characters without leading or trailing space (the PNG rule); the text is Latin-1 (use `asciiJson` for
 * JSON). Throws a `RangeError` on a keyword or text the format cannot hold, so a bad chunk is never written.
 */
export function buildPngTextChunk(keyword: string, text: string): Uint8Array {
  if (keyword.length < 1 || keyword.length > 79 || keyword !== keyword.trim()) throw new RangeError("a PNG text keyword is 1 to 79 characters without leading or trailing space");
  const key = latin1(keyword, "the keyword");
  for (const c of key) if (c < 32 || (c > 126 && c < 161)) throw new RangeError("a PNG text keyword has only printable Latin-1 characters");
  const body = new Uint8Array([0x74, 0x45, 0x58, 0x74, ...key, 0, ...latin1(text, "the text")]); // "tEXt" + data
  const out = new Uint8Array(4 + body.length + 4);
  const view = new DataView(out.buffer);
  view.setUint32(0, body.length - 4);
  out.set(body, 4);
  view.setUint32(4 + body.length, crc32(body));
  return out;
}

/** The PNG's first chunk (IHDR) ends here: 8 signature bytes, then 4 length + 4 type + 13 data + 4 CRC. */
const AFTER_IHDR = 8 + 4 + 4 + 13 + 4;

function isPng(bytes: Uint8Array): boolean {
  return bytes.length >= AFTER_IHDR && PNG_SIGNATURE.every((b, i) => bytes[i] === b) && bytes[12] === 0x49 && bytes[13] === 0x48 && bytes[14] === 0x44 && bytes[15] === 0x52;
}

/** The PNG with `chunk` placed straight after IHDR (a text chunk may sit anywhere before IEND). Throws on bytes that are not a PNG. */
export function insertPngText(png: Uint8Array, chunk: Uint8Array): Uint8Array {
  if (!isPng(png)) throw new RangeError("not a PNG: the signature or the IHDR chunk is missing");
  const out = new Uint8Array(png.length + chunk.length);
  out.set(png.subarray(0, AFTER_IHDR), 0);
  out.set(chunk, AFTER_IHDR);
  out.set(png.subarray(AFTER_IHDR), AFTER_IHDR + chunk.length);
  return out;
}

/** The text of the first `tEXt` chunk with this keyword, or null: how a test (and a re-import) reads the sidecar back. */
export function readPngText(png: Uint8Array, keyword: string): string | null {
  if (!isPng(png)) return null;
  const view = new DataView(png.buffer, png.byteOffset, png.byteLength);
  let at = 8;
  while (at + 12 <= png.length) {
    const length = view.getUint32(at);
    const type = String.fromCharCode(png[at + 4], png[at + 5], png[at + 6], png[at + 7]);
    if (type === "tEXt") {
      const data = png.subarray(at + 8, at + 8 + length);
      const nul = data.indexOf(0);
      if (nul > 0 && String.fromCharCode(...data.subarray(0, nul)) === keyword) return String.fromCharCode(...data.subarray(nul + 1));
    }
    if (type === "IEND") break;
    at += 12 + length;
  }
  return null;
}

// ---------------------------------------------------------------------------------------------- the legend's colours
/**
 * The colour of the legend bar at `p` (0 at the low end, 1 at the high end), as sRGB bytes: **the same table
 * the surface is painted from**, banded as the surface is. A smooth bar is the map at the value's position
 * (`fractionIn`, so a diverging map is centred on zero as the walls are); a banded bar is flat steps, each
 * the band's middle colour (`bandColor`). Sampling this once per row of pixels draws a bar that agrees with
 * the CSS gradient of the on-screen legend and with the tint; the test compares them.
 */
export function legendBarColour(p: number, look: Pick<LegendView, "map" | "reverse" | "bands">, range: Range): [number, number, number] {
  const u = Math.min(1, Math.max(0, Number.isFinite(p) ? p : 0));
  const map = colormapOf(look.map);
  if (look.bands > 0) {
    const band = Math.min(look.bands - 1, Math.floor(u * look.bands));
    return bandColor(band, look.bands, map, look.reverse, range);
  }
  const [r, g, b] = colorAtIn(map, fractionIn(map, range.min + u * (range.max - range.min), range.min, range.max), look.reverse);
  return [Math.round(r), Math.round(g), Math.round(b)];
}

// ---------------------------------------------------------------------------------------------- the layout
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A tick label: its text, the point its middle-left (vertical bar) or top-centre (horizontal bar) sits at. */
export interface TickPlace {
  text: string;
  x: number;
  y: number;
}

export interface LegendLayout {
  /** The whole legend, strip or panel. */
  box: Rect;
  /** Draw a panel in the background's colour behind it (floating only: the others sit on clear ground). */
  panel: boolean;
  vertical: boolean;
  bar: Rect;
  title: { text: string; x: number; y: number; maxWidth: number };
  ticks: TickPlace[];
}

export interface ExportLayout {
  width: number;
  height: number;
  /** Where the model is drawn, and the size it is drawn at. */
  render: Rect;
  legend: LegendLayout | null;
  /** The title's strip across the top, and the point its text is centred on. */
  title: { box: Rect; text: string; x: number; y: number; maxWidth: number } | null;
  /** The scale bar's anchor: the left end of its baseline, at the bottom left of the model's rectangle. */
  scaleBar: { x: number; y: number } | null;
  fonts: { title: number; legendTitle: number; label: number; scaleBar: number };
  /** The bar's thickness, the padding and the gap between bar and ticks. */
  barThickness: number;
  pad: number;
  gap: number;
}

export interface LayoutInput {
  width: number;
  height: number;
  /** Pixels per CSS pixel (`ExportPixels.unit`). */
  unit: number;
  placement: LegendPlacement;
  /** Draw the legend: false for none (also the answer when there is no range). */
  legend: boolean;
  /** The legend's heading (field title with units). */
  legendTitle: string;
  /** Tick text, highest first, as `legendLabels` gives it. */
  labels: readonly string[];
  title: string;
  scaleBar: boolean;
}

/** The width of `text` at `font` px, estimated: 0.6 em a character. The drawing truncates to fit, so an estimate that errs wide costs a margin and one that errs narrow cannot clip. */
export function textWidth(text: string, font: number): number {
  return Math.ceil(text.length * font * 0.6);
}

/** Print size: fonts 1.5x and a bar 1.6x as thick as on screen. */
const PRINT_FONT = 1.5;
const PRINT_BAR = 1.6;

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/**
 * Where everything goes, in pixels of the finished picture. See the file's comment for the rule; the
 * numbers: at `unit` 1 the legend's ticks are 16.5 px (11 x 1.5), its bar 22 px thick, the title 24 px, the
 * padding 10 px. The legend strip of `right` is at most 35% of the width (and `bottom`'s the same of the
 * height), so a long field title is truncated rather than eating the picture.
 */
export function exportLayout(input: LayoutInput): ExportLayout {
  const width = Math.max(1, Math.round(input.width));
  const height = Math.max(1, Math.round(input.height));
  const unit = input.unit > 0 ? input.unit : 1;
  const m = unit * PRINT_FONT;
  const fonts = { title: Math.round(16 * m), legendTitle: Math.round(11.5 * m), label: Math.round(11 * m), scaleBar: Math.round(11 * m) };
  const barThickness = Math.round(14 * unit * PRINT_BAR);
  const pad = Math.round(10 * unit);
  const gap = Math.round(6 * unit);

  const titleText = input.title.trim();
  const titleH = titleText === "" ? 0 : Math.round(fonts.title * 1.9);
  const titleBox: Rect = { x: 0, y: 0, w: width, h: titleH };
  const title =
    titleText === ""
      ? null
      : { box: titleBox, text: titleText, x: width / 2, y: titleH / 2, maxWidth: width - 2 * pad };

  const labels = input.labels;
  const drawLegend = input.legend && labels.length >= 2;
  const labelW = labels.reduce((w, t) => Math.max(w, textWidth(t, fonts.label)), 0);
  const headingW = textWidth(input.legendTitle, fonts.legendTitle);
  const headingH = Math.round(fonts.legendTitle * 1.7);

  let render: Rect = { x: 0, y: titleH, w: width, h: Math.max(1, height - titleH) };
  let legend: LegendLayout | null = null;

  if (drawLegend && input.placement === "bottom") {
    const stripH = pad + headingH + barThickness + gap + Math.round(fonts.label * 1.4) + pad;
    const strip = Math.min(stripH, Math.round(height * 0.35));
    render = { x: 0, y: titleH, w: width, h: Math.max(1, height - titleH - strip) };
    const box: Rect = { x: 0, y: height - strip, w: width, h: strip };
    const barW = Math.round(clamp(width * 0.6, 1, width - 2 * pad));
    const bar: Rect = { x: Math.round((width - barW) / 2), y: box.y + pad + headingH, w: barW, h: barThickness };
    // Highest first in `labels`; a horizontal bar reads low to high, left to right.
    const lowToHigh = [...labels].reverse();
    const ticks = lowToHigh.map((text, i) => ({ text, x: bar.x + (bar.w * i) / (lowToHigh.length - 1), y: bar.y + bar.h + gap }));
    legend = { box, panel: false, vertical: false, bar, title: { text: input.legendTitle, x: bar.x, y: box.y + pad + headingH / 2, maxWidth: bar.w }, ticks };
  } else if (drawLegend) {
    const inner = Math.max(barThickness + gap + labelW, headingW);
    const stripW = Math.min(pad + inner + pad, Math.round(width * 0.35));
    const floating = input.placement === "floating";
    const box: Rect = floating
      ? { x: pad, y: titleH + pad, w: stripW, h: 0 }
      : { x: width - stripW, y: titleH, w: stripW, h: Math.max(1, height - titleH) };
    if (!floating) render = { x: 0, y: titleH, w: Math.max(1, width - stripW), h: render.h };
    const barH = floating
      ? Math.round(Math.min(render.h * 0.45, 220 * m))
      : Math.round(Math.max(1, Math.min(box.h * 0.6, box.h - 2 * pad - headingH - Math.round(fonts.label))));
    if (floating) box.h = pad + headingH + barH + Math.round(fonts.label) + pad;
    const bar: Rect = { x: box.x + pad, y: box.y + pad + headingH + Math.round(fonts.label / 2), w: barThickness, h: barH };
    const ticks = labels.map((text, i) => ({ text, x: bar.x + bar.w + gap, y: bar.y + (bar.h * i) / (labels.length - 1) }));
    legend = { box, panel: floating, vertical: true, bar, title: { text: input.legendTitle, x: bar.x, y: box.y + pad + headingH / 2, maxWidth: stripW - 2 * pad }, ticks };
  }

  const scaleBar = input.scaleBar ? { x: render.x + pad, y: render.y + render.h - pad } : null;
  return { width, height, render, legend, title, scaleBar, fonts, barThickness, pad, gap };
}

/** The layout's input for a view, a size and the settings: six ticks in the view's own format, the field's title for the heading, and no legend without a range. */
export function layoutInputOf(view: ExportView, settings: ExportSettings, pixels: ExportPixels): LayoutInput {
  const range = exportRange(view);
  const colouring = range !== null && view.data !== null;
  return {
    width: pixels.w,
    height: pixels.h,
    unit: pixels.unit,
    placement: legendOf(view.view).placement,
    legend: settings.legend && colouring,
    legendTitle: colouring ? fieldTitle(view.data, view.view.field) : "",
    labels: range === null ? [] : legendLabels(range, view.view, EXPORT_TICKS),
    title: settings.title,
    scaleBar: settings.scaleBar,
  };
}
