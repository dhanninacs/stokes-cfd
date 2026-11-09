/**
 * The publication image (R3 of the results overhaul): the viewport's model drawn at print resolution, with the
 * legend, the title and the scale bar composited around it, and the settings of the figure written into the
 * PNG (a `tEXt` chunk, `stokes:view`) and into a sidecar `.json`, so the figure can be made again.
 *
 * **This file is thin on purpose.** What goes where is `panels/exportModel.ts` (`exportLayout`, tested in
 * Node), what the bar's colours are is `legendBarColour` (tested against the surface's own tint), and the
 * model's pixels come from `SceneManager.captureExport`, which draws the frame and copies it in ONE task (no
 * `preserveDrawingBuffer`, as `captureThumbnail`). What is left here is the 2-D canvas calls that follow the
 * layout, which only a browser can run.
 *
 * Colours: the legend's ink comes from the theme's tokens when the picture keeps the viewport's background (a
 * dark viewport needs light ink), and is fixed dark ink for the white and the transparent ones, because both
 * go onto a page, not into the dark window. The bar's colours are the colour map's, as everywhere.
 */
import { host } from "../app/host";
import { NO_FIELD } from "../panels/fieldsModel";
import {
  PNG_KEYWORD,
  asciiJson,
  buildPngTextChunk,
  buildSidecar,
  exportLayout,
  exportName,
  exportPixels,
  exportRange,
  insertPngText,
  layoutInputOf,
  legendBarColour,
  sidecarFileName,
  sidecarText,
  type ExportBackground,
  type ExportCamera,
  type ExportLayout,
  type ExportPixels,
  type ExportSettings,
  type ExportView,
  type Sidecar,
} from "../panels/exportModel";
import { legendOf } from "../panels/legendModel";
import { visibleSlices } from "../panels/sliceModel";
import { useStore } from "../state/store";
import type { ScaleBar } from "./scale";
import type { SceneManager } from "./scene";

export interface ExportResult {
  blob: Blob;
  /** The picture's file name. */
  name: string;
  sidecar: Sidecar;
  pixels: ExportPixels;
}

/** What the figure shows now, read from the stores: the one place the export looks at them. */
export function currentExportView(): ExportView {
  const s = useStore.getState();
  const { view, data } = s.results;
  const run = s.run.runs.find((r) => r.id === s.results.run);
  const iso = s.iso.visible && s.iso.surface !== null && s.iso.surface.state === "ready" ? s.iso.surface : null;
  return {
    project: s.project?.name ?? "",
    run: run?.name ?? s.results.run ?? "",
    view,
    data,
    // Slices are drawn only with the run's fields in hand, as in the viewport.
    slices: data === null ? [] : visibleSlices(s.slices),
    iso: iso === null ? null : { field: iso.field, value: iso.value, units: iso.units },
    streamlines: s.streamlines.lines.length > 0,
    arrows: s.arrows.shown ? { density: s.arrows.density, scale: s.arrows.scale } : null,
  };
}

// ---------------------------------------------------------------------------------------------- drawing
interface Ink {
  text: string;
  muted: string;
  /** Behind a floating legend, so its ticks read over the model. */
  panel: string;
  family: string;
}

function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value === "" ? fallback : value;
}

function inkFor(background: ExportBackground): Ink {
  const family = token("--font-sans", "system-ui, sans-serif");
  if (background === "viewport") return { text: token("--text", "#e8ebf0"), muted: token("--text-muted", "#9aa4b2"), panel: "rgba(0, 0, 0, 0.35)", family };
  return { text: "#111418", muted: "#4a5360", panel: "rgba(255, 255, 255, 0.9)", family };
}

/** The viewport's own background (`.viewport-bg`: a radial gradient of `--vp-top` into `--vp-bottom`), in 2-D canvas calls. */
function paintBackground(ctx: CanvasRenderingContext2D, w: number, h: number, background: ExportBackground): void {
  if (background === "transparent") return;
  if (background === "white") {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);
    return;
  }
  // CSS `radial-gradient(120% 90% at 50% 0%, top 0%, bottom 100%)` is an ellipse: a unit circle scaled to it.
  ctx.save();
  ctx.translate(w / 2, 0);
  ctx.scale(1.2 * w, 0.9 * h);
  const gradient = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
  gradient.addColorStop(0, token("--vp-top", "#1a2029"));
  gradient.addColorStop(1, token("--vp-bottom", "#07090c"));
  ctx.fillStyle = gradient;
  // The picture is [-0.5/1.2, 0.5/1.2] x [0, 1/0.9] in the scaled space; past the ellipse the last stop continues, as in CSS.
  ctx.fillRect(-0.5 / 1.2, 0, 1 / 1.2, 1 / 0.9);
  ctx.restore();
}

/** `text` cut with an ellipsis until it fits `maxWidth` at the font the context has. */
function fitText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let cut = text.length;
  while (cut > 1 && ctx.measureText(text.slice(0, cut) + "…").width > maxWidth) cut--;
  return text.slice(0, cut) + "…";
}

function drawLegend(ctx: CanvasRenderingContext2D, layout: ExportLayout, look: ReturnType<typeof legendOf>, range: { min: number; max: number }, ink: Ink): void {
  const legend = layout.legend;
  if (legend === null) return;
  const { bar } = legend;
  if (legend.panel) {
    ctx.fillStyle = ink.panel;
    ctx.fillRect(legend.box.x, legend.box.y, legend.box.w, legend.box.h);
  }
  // One row (or column) of pixels per colour sample: the bar is the same function the surface is tinted with.
  if (legend.vertical) {
    for (let y = 0; y < bar.h; y++) {
      const [r, g, b] = legendBarColour(1 - (y + 0.5) / bar.h, look, range);
      ctx.fillStyle = `rgb(${r} ${g} ${b})`;
      ctx.fillRect(bar.x, bar.y + y, bar.w, 1);
    }
  } else {
    for (let x = 0; x < bar.w; x++) {
      const [r, g, b] = legendBarColour((x + 0.5) / bar.w, look, range);
      ctx.fillStyle = `rgb(${r} ${g} ${b})`;
      ctx.fillRect(bar.x + x, bar.y, 1, bar.h);
    }
  }
  const hair = Math.max(1, Math.round(layout.pad / 10));
  ctx.strokeStyle = ink.muted;
  ctx.lineWidth = hair;
  ctx.strokeRect(bar.x - hair / 2, bar.y - hair / 2, bar.w + hair, bar.h + hair);
  // Tick marks across the bar's edge, then the numbers.
  ctx.beginPath();
  for (const t of legend.ticks) {
    if (legend.vertical) {
      ctx.moveTo(bar.x + bar.w, t.y);
      ctx.lineTo(bar.x + bar.w + layout.gap * 0.7, t.y);
    } else {
      ctx.moveTo(t.x, bar.y + bar.h);
      ctx.lineTo(t.x, bar.y + bar.h + layout.gap * 0.7);
    }
  }
  ctx.stroke();
  ctx.fillStyle = ink.text;
  ctx.font = `${layout.fonts.label}px ${ink.family}`;
  ctx.textAlign = legend.vertical ? "left" : "center";
  ctx.textBaseline = legend.vertical ? "middle" : "top";
  for (const t of legend.ticks) ctx.fillText(t.text, t.x, legend.vertical ? t.y : t.y + layout.gap * 0.3);
  ctx.font = `600 ${layout.fonts.legendTitle}px ${ink.family}`;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(fitText(ctx, legend.title.text, legend.title.maxWidth), legend.title.x, legend.title.y);
}

function drawTitle(ctx: CanvasRenderingContext2D, layout: ExportLayout, ink: Ink): void {
  const title = layout.title;
  if (title === null) return;
  ctx.fillStyle = ink.text;
  ctx.font = `600 ${layout.fonts.title}px ${ink.family}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(fitText(ctx, title.text, title.maxWidth), title.x, title.y);
}

/** The scale bar as the viewport draws it: a line the length of the stated distance, an end tick each side, the distance above. */
function drawScaleBar(ctx: CanvasRenderingContext2D, layout: ExportLayout, bar: ScaleBar, ink: Ink): void {
  const anchor = layout.scaleBar;
  if (anchor === null) return;
  const tick = layout.pad * 0.6;
  ctx.strokeStyle = ink.text;
  ctx.lineWidth = Math.max(1, Math.round(layout.pad / 5));
  ctx.beginPath();
  ctx.moveTo(anchor.x, anchor.y);
  ctx.lineTo(anchor.x + bar.pixels, anchor.y);
  ctx.moveTo(anchor.x, anchor.y - tick);
  ctx.lineTo(anchor.x, anchor.y + tick);
  ctx.moveTo(anchor.x + bar.pixels, anchor.y - tick);
  ctx.lineTo(anchor.x + bar.pixels, anchor.y + tick);
  ctx.stroke();
  ctx.fillStyle = ink.text;
  ctx.font = `${layout.fonts.scaleBar}px ${ink.family}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.fillText(bar.label, anchor.x + bar.pixels / 2, anchor.y - tick - layout.pad * 0.2);
}

function canvasBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => canvas.toBlob((blob) => (blob === null ? reject(new Error("The browser could not make a PNG of this size.")) : resolve(blob)), "image/png"));
}

/**
 * The viewport ran out of graphics memory drawing the frame (the context was lost). **Its own class so the
 * dialog can answer with a toast** and keep its settings, where any other failure is a line in the dialog.
 */
export class ExportMemoryError extends Error {
  constructor() {
    super("Export failed: out of graphics memory, try a smaller size");
    this.name = "ExportMemoryError";
  }
}

/** The camera as the sidecar records it. */
export function cameraOf(scene: SceneManager): ExportCamera {
  const state = scene.rig.getState();
  return {
    target: [state.target.x, state.target.y, state.target.z],
    dir: [state.dir.x, state.dir.y, state.dir.z],
    span: state.span,
    orthographic: scene.rig.isOrthographic,
    ...(state.up === undefined ? {} : { up: [state.up.x, state.up.y, state.up.z] as [number, number, number] }),
  };
}

/**
 * Make the figure: the PNG (with the sidecar in its `stokes:view` text chunk) and the sidecar object.
 * Throws an `Error` whose message is for the user when the picture cannot be made (the viewport cannot
 * draw the frame, the browser refuses a canvas this large).
 *
 * The camera is read BEFORE the frame is drawn: `captureExport` puts the camera back, but the sidecar
 * should say what the user was looking at, not what the frame was drawn with.
 */
export async function exportImage(scene: SceneManager, settings: ExportSettings, view: ExportView = currentExportView()): Promise<ExportResult> {
  const pixels = exportPixels(settings, scene.liveSize, scene.maxExportSide);
  const camera = cameraOf(scene);
  const layout = exportLayout(layoutInputOf(view, settings, pixels));
  const range = exportRange(view);
  const frame = scene.captureExport(layout.render.w, layout.render.h);
  if (frame === "lost") throw new ExportMemoryError();
  if (frame === null) throw new Error("The viewport could not draw the picture. Try a smaller size.");

  // From here nothing needs the scene: the rest is 2-D canvas work and encoding, free to be asynchronous.
  const canvas = document.createElement("canvas");
  canvas.width = pixels.w;
  canvas.height = pixels.h;
  const ctx = canvas.getContext("2d");
  if (ctx === null) throw new Error("The browser could not make a picture canvas of this size. Try a smaller size.");
  const ink = inkFor(settings.background);
  paintBackground(ctx, pixels.w, pixels.h, settings.background);
  ctx.drawImage(frame.canvas, layout.render.x, layout.render.y);
  if (range !== null && view.view.field !== NO_FIELD) drawLegend(ctx, layout, legendOf(view.view), range, ink);
  drawTitle(ctx, layout, ink);
  if (frame.scale !== null) drawScaleBar(ctx, layout, frame.scale, ink);

  const sidecar = buildSidecar(view, camera, settings, pixels);
  const png = new Uint8Array(await (await canvasBlob(canvas)).arrayBuffer());
  const withText = insertPngText(png, buildPngTextChunk(PNG_KEYWORD, asciiJson(sidecar)));
  return { blob: new Blob([withText as BlobPart], { type: "image/png" }), name: exportName(view), sidecar, pixels };
}

// ---------------------------------------------------------------------------------------------- saving
/** Hand `blob` to the browser as a download named `name`. */
export function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

const JSON_FILTER = "JSON files (*.json);;All files (*)";

/**
 * The sidecar `.json`, named after the picture: through the host's save dialog when it has one (the desktop
 * window has no download shelf), else a browser download. Resolves to false when the user cancelled or the
 * host refused. **The PNG itself can only be a browser download**: the host's writer takes text, not bytes.
 */
export async function downloadSidecar(sidecar: Sidecar, pictureName: string): Promise<boolean> {
  const name = sidecarFileName(pictureName);
  const text = sidecarText(sidecar);
  if (host.canSaveFiles) {
    const path = await host.pickSaveFile("Save the figure's settings", name, JSON_FILTER);
    return path ? await host.writeTextFile(path, text) : false;
  }
  download(new Blob([text], { type: "application/json;charset=utf-8" }), name);
  return true;
}
