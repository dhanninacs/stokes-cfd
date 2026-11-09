/**
 * The plot over a line as a PNG (R5): the same chart geometry the card draws (`lineChart`), painted on a canvas at
 * `scale` times its CSS size with the app's own fonts (`--font-sans` for the title, `--font-mono` for the ticks, as
 * the card's plot and the export's legend). White ground and dark ink, as the image export's white page: a plot goes
 * into a document. The curve takes the colour the card is given (the colour map's middle).
 */
import { lineChart, lineTitle, type LinePad } from "../panels/lineModel";

const WIDTH = 480;
const HEIGHT = 300;
const PAD: LinePad = { left: 64, right: 18, top: 38, bottom: 48 };

export interface LinePlotInput {
  /** The field's label (`|U|`) and units. */
  label: string;
  units: string;
  along: readonly number[];
  values: readonly number[];
  /** The curve's CSS colour. */
  colour: string;
}

function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value === "" ? fallback : value;
}

/** The canvas the plot is painted on, `scale` times the plot's size. */
export function paintLinePlot(input: LinePlotInput, scale: number): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(WIDTH * scale);
  canvas.height = Math.round(HEIGHT * scale);
  const ctx = canvas.getContext("2d");
  if (ctx === null) throw new Error("The browser could not make a canvas for the plot.");
  ctx.scale(scale, scale);
  const sans = token("--font-sans", "system-ui, sans-serif");
  const mono = token("--font-mono", "ui-monospace, monospace");
  const ink = "#111418";
  const muted = "#4a5360";
  const grid = "#d5dae1";
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);

  const chart = lineChart(input.along, input.values, WIDTH, HEIGHT, PAD);
  const total = input.along.length > 0 ? input.along[input.along.length - 1]! : 0;
  ctx.fillStyle = ink;
  ctx.font = `600 13px ${sans}`;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(lineTitle(input.label, input.units, total), PAD.left, 22);

  ctx.font = `11px ${mono}`;
  ctx.lineWidth = 1;
  for (const t of chart.yTicks) {
    ctx.strokeStyle = grid;
    ctx.beginPath();
    ctx.moveTo(chart.x0, t.at);
    ctx.lineTo(chart.x1, t.at);
    ctx.stroke();
    ctx.fillStyle = muted;
    ctx.textAlign = "right";
    ctx.fillText(t.label, chart.x0 - 6, t.at + 4);
  }
  for (const t of chart.xTicks) {
    ctx.fillStyle = muted;
    ctx.textAlign = "center";
    ctx.fillText(t.label, t.at, chart.y1 + 16);
  }
  ctx.strokeStyle = muted;
  ctx.beginPath();
  ctx.moveTo(chart.x0, chart.y0);
  ctx.lineTo(chart.x0, chart.y1);
  ctx.lineTo(chart.x1, chart.y1);
  ctx.stroke();
  ctx.fillStyle = ink;
  ctx.font = `12px ${sans}`;
  ctx.textAlign = "center";
  ctx.fillText(`distance (${chart.unit})`, (chart.x0 + chart.x1) / 2, HEIGHT - 10);

  if (chart.d !== "") {
    ctx.strokeStyle = input.colour;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke(new Path2D(chart.d));
  }
  return canvas;
}

/** The plot as a PNG blob at `scale` (2 for the card's button). */
export async function linePlotPng(input: LinePlotInput, scale = 2): Promise<Blob> {
  const canvas = paintLinePlot(input, scale);
  return new Promise((resolve, reject) => canvas.toBlob((blob) => (blob === null ? reject(new Error("The browser could not make a PNG of the plot.")) : resolve(blob)), "image/png"));
}
