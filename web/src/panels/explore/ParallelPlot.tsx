/**
 * Parallel coordinates: one axis per variable and per response, each scaled from its own minimum to its
 * own maximum, one line per design point across them. The lines are coloured by a chosen response
 * through the series tokens (cool to warm). The base canvas holds the lines; a second canvas above holds
 * what changes often: the veil that dims the lines while a selection exists, the selected lines, the
 * line under the pointer and the axes with their labels. A click on a line selects its row.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { completeRows, type DataColumn, type Dataset } from "./dataset";
import { formatTick, withUnit } from "./format";
import { PlotTip } from "./PlotTip";
import { fraction, lineStyle, prepareCanvas, rampColor, rampStops, readPalette, strideFor, useElementSize, useThemeEpoch, type Palette, type RGB } from "./plotKit";

export interface ParallelProps {
  dataset: Dataset;
  /** The response that colours the lines. */
  colourBy: string;
  selected: ReadonlySet<number>;
  onPick(index: number | null, additive: boolean): void;
}

const LEFT = 18;
const RIGHT = 18;
const TOP = 50;
const BOTTOM = 24;
const MIN_SPACING = 56;
const MIN_HEIGHT = 190;
const BUCKETS = 24;

interface Layout {
  width: number;
  height: number;
  xs: number[];
}

function layoutFor(width: number, height: number, axes: number): Layout {
  const span = Math.max(width - LEFT - RIGHT, (axes - 1) * MIN_SPACING);
  const w = LEFT + RIGHT + span;
  const xs = Array.from({ length: axes }, (_, i) => (axes === 1 ? w / 2 : LEFT + (span * i) / (axes - 1)));
  return { width: w, height: Math.max(height, MIN_HEIGHT), xs };
}

const yOf = (layout: Layout, column: DataColumn, value: number) => TOP + (1 - fraction(value, column.min, column.max)) * (layout.height - TOP - BOTTOM);

function fit(ctx: CanvasRenderingContext2D, text: string, room: number): string {
  if (ctx.measureText(text).width <= room) return text;
  let cut = text.length;
  while (cut > 1 && ctx.measureText(`${text.slice(0, cut)}…`).width > room) cut--;
  return `${text.slice(0, cut)}…`;
}

/** The line of one row across all axes, as a path on the context. */
function trace(ctx: CanvasRenderingContext2D, layout: Layout, axes: readonly DataColumn[], at: number) {
  for (let i = 0; i < axes.length; i++) {
    const x = layout.xs[i];
    const y = yOf(layout, axes[i], axes[i].values[at]);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
}

/** The colour of a row along the ramp, by where its colour-column value sits in that column's range. */
function colourOf(stops: readonly RGB[], by: DataColumn, at: number): string {
  return rampColor(stops, fraction(by.values[at], by.min, by.max));
}

function drawLines(ctx: CanvasRenderingContext2D, layout: Layout, axes: readonly DataColumn[], rows: readonly number[], by: DataColumn, stops: readonly RGB[]) {
  const stride = strideFor(rows.length);
  const buckets: number[][] = Array.from({ length: BUCKETS }, () => []);
  for (let k = 0; k < rows.length; k += stride) {
    const t = fraction(by.values[rows[k]], by.min, by.max);
    buckets[Math.min(BUCKETS - 1, Math.floor(t * BUCKETS))].push(rows[k]);
  }
  // Many lines are drawn thinner and fainter, so that where they pile up the colour deepens.
  const look = lineStyle(rows.length / stride);
  ctx.lineWidth = look.width;
  ctx.lineJoin = "round";
  ctx.globalAlpha = look.alpha;
  buckets.forEach((bucket, b) => {
    if (bucket.length === 0) return;
    ctx.strokeStyle = rampColor(stops, (b + 0.5) / BUCKETS);
    ctx.beginPath();
    for (const at of bucket) trace(ctx, layout, axes, at);
    ctx.stroke();
  });
  ctx.globalAlpha = 1;
}

function drawAxes(ctx: CanvasRenderingContext2D, p: Palette, layout: Layout, axes: readonly DataColumn[]) {
  const spacing = axes.length > 1 ? layout.xs[1] - layout.xs[0] : layout.width;
  ctx.lineWidth = 1;
  ctx.strokeStyle = p.gridStrong;
  ctx.textBaseline = "middle";
  axes.forEach((column, i) => {
    const x = Math.round(layout.xs[i]) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, TOP);
    ctx.lineTo(x, layout.height - BOTTOM);
    ctx.stroke();
    // The first and the last axis sit near the edge of the canvas: their labels grow inward from the axis.
    const first = i === 0 && axes.length > 1;
    const last = i === axes.length - 1 && axes.length > 1;
    ctx.textAlign = first ? "left" : last ? "right" : "center";
    const at = first ? x - 4 : last ? x + 4 : x;
    // A label that grows inward from an edge axis shares the gap with half of its neighbour's centred label.
    const room = first || last ? spacing / 2 - 2 : spacing - 8;
    ctx.font = `600 11px ${p.sans}`;
    ctx.fillStyle = p.text;
    ctx.fillText(fit(ctx, column.title, room), at, 11);
    ctx.font = `10px ${p.sans}`;
    ctx.fillStyle = p.faint;
    if (column.unit) ctx.fillText(fit(ctx, `[${column.unit}]`, room), at, 25);
    ctx.font = `9.5px ${p.mono}`;
    ctx.fillStyle = p.muted;
    ctx.fillText(formatTick(column.max), at, TOP - 8);
    ctx.fillText(formatTick(column.min), at, layout.height - BOTTOM + 11);
  });
}

/** The row whose line passes nearest to a point, within 7 px; the line is measured where it crosses the point's x. */
function lineAt(layout: Layout, axes: readonly DataColumn[], rows: readonly number[], x: number, y: number): number | null {
  if (axes.length < 2 || x < layout.xs[0] - 8 || x > layout.xs[axes.length - 1] + 8) return null;
  let segment = 0;
  while (segment < axes.length - 2 && x > layout.xs[segment + 1]) segment++;
  const x0 = layout.xs[segment];
  const x1 = layout.xs[segment + 1];
  const t = Math.min(1, Math.max(0, (x - x0) / (x1 - x0)));
  let best: number | null = null;
  let bestDistance = 7;
  for (const at of rows) {
    const y0 = yOf(layout, axes[segment], axes[segment].values[at]);
    const y1 = yOf(layout, axes[segment + 1], axes[segment + 1].values[at]);
    const d = Math.abs(y0 + (y1 - y0) * t - y);
    if (d <= bestDistance) {
      bestDistance = d;
      best = at;
    }
  }
  return best;
}

interface Tip {
  x: number;
  y: number;
  text: string;
}

export default function ParallelPlot({ dataset, colourBy, selected, onPick }: ParallelProps) {
  const scroll = useRef<HTMLDivElement>(null);
  const base = useRef<HTMLCanvasElement>(null);
  const over = useRef<HTMLCanvasElement>(null);
  const size = useElementSize(scroll);
  const epoch = useThemeEpoch();
  /** The table position of the line under the pointer, and the bubble that names it. */
  const [hoverAt, setHoverAt] = useState<number | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);

  const axes = useMemo(() => [...dataset.inputs, ...dataset.outputs].filter((c) => Number.isFinite(c.min)), [dataset]);
  const by = useMemo(() => dataset.byName.get(colourBy) ?? dataset.outputs[0] ?? axes[0], [dataset, colourBy, axes]);
  const rows = useMemo(() => completeRows(dataset, axes), [dataset, axes]);
  const rowSet = useMemo(() => new Set(rows), [rows]);
  const layout = useMemo(() => (size.width > 0 && size.height > 0 ? layoutFor(size.width, size.height, axes.length) : null), [size.width, size.height, axes.length]);
  const dividerAfter = dataset.inputs.filter((c) => Number.isFinite(c.min)).length - 1;

  useEffect(() => {
    const canvas = base.current;
    if (!canvas || !layout || !by) return;
    const ctx = prepareCanvas(canvas, layout.width, layout.height);
    if (!ctx) return;
    drawLines(ctx, layout, axes, rows, by, rampStops(readPalette()));
  }, [layout, axes, rows, by, epoch]);

  useEffect(() => {
    const canvas = over.current;
    if (!canvas || !layout || !by) return;
    const ctx = prepareCanvas(canvas, layout.width, layout.height);
    if (!ctx) return;
    const p = readPalette();
    const stops = rampStops(p);
    const chosen = [...selected].map((index) => dataset.position.get(index)).filter((at): at is number => at !== undefined && rowSet.has(at));
    // Every line selected marks nothing apart from the rest, so the veil and the second drawing are left out.
    const some = chosen.length > 0 && chosen.length < rowSet.size;
    if (some) {
      ctx.globalAlpha = 0.62;
      ctx.fillStyle = p.surface;
      ctx.fillRect(0, 0, layout.width, layout.height);
      ctx.globalAlpha = 1;
    }
    // The divider between the inputs and the responses.
    if (dividerAfter >= 0 && dividerAfter < axes.length - 1) {
      const x = Math.round((layout.xs[dividerAfter] + layout.xs[dividerAfter + 1]) / 2) + 0.5;
      ctx.save();
      ctx.setLineDash([2, 4]);
      ctx.strokeStyle = p.gridStrong;
      ctx.beginPath();
      ctx.moveTo(x, TOP - 10);
      ctx.lineTo(x, layout.height - BOTTOM + 6);
      ctx.stroke();
      ctx.restore();
    }
    ctx.lineJoin = "round";
    if (some) {
      // A few selected lines are drawn bold; a crowd of them thinner and fainter, like the lines below.
      ctx.lineWidth = chosen.length <= 60 ? 2.2 : chosen.length > 300 ? 1.2 : 1.6;
      ctx.globalAlpha = chosen.length <= 60 ? 1 : Math.max(0.12, Math.sqrt(60 / chosen.length));
      for (const at of chosen) {
        ctx.strokeStyle = colourOf(stops, by, at);
        ctx.beginPath();
        trace(ctx, layout, axes, at);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    if (hoverAt !== null) {
      ctx.lineWidth = 4.5;
      ctx.strokeStyle = p.text;
      ctx.globalAlpha = 0.28;
      ctx.beginPath();
      trace(ctx, layout, axes, hoverAt);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.lineWidth = 2.4;
      ctx.strokeStyle = colourOf(stops, by, hoverAt);
      ctx.beginPath();
      trace(ctx, layout, axes, hoverAt);
      ctx.stroke();
    }
    drawAxes(ctx, p, layout, axes);
  }, [layout, axes, rowSet, by, dataset, selected, hoverAt, dividerAfter, epoch]);

  /** The line under the pointer, with the bubble that names it. */
  const hit = (clientX: number, clientY: number): { at: number; tip: Tip } | null => {
    const canvas = over.current;
    if (!canvas || !layout) return null;
    const box = canvas.getBoundingClientRect();
    const x = clientX - box.left;
    const y = clientY - box.top;
    const at = lineAt(layout, axes, rows, x, y);
    if (at === null) return null;
    const parts = axes.map((c) => `${c.title} ${withUnit(formatTick(c.values[at]), c.unit)}`);
    return { at, tip: { x, y: Math.max(TOP, y), text: `#${dataset.rows[at].index}  ${parts.join("  ·  ")}` } };
  };

  if (!by || axes.length === 0) return null;
  return (
    <div ref={scroll} className="pl-scroll" role="img" aria-label={`Parallel coordinates of ${rows.length} design points across ${axes.length} axes`}>
      {layout && (
        <div className="pl-stack" style={{ width: layout.width, height: layout.height }}>
          <canvas ref={base} />
          <canvas
            ref={over}
            style={{ cursor: hoverAt !== null ? "pointer" : undefined }}
            onMouseMove={(e) => {
              const next = hit(e.clientX, e.clientY);
              setHoverAt(next?.at ?? null);
              setTip(next?.tip ?? null);
            }}
            onMouseLeave={() => {
              setHoverAt(null);
              setTip(null);
            }}
            onClick={(e) => {
              const next = hit(e.clientX, e.clientY);
              onPick(next ? dataset.rows[next.at].index : null, e.shiftKey || e.ctrlKey || e.metaKey);
            }}
          />
          {tip && (
            <PlotTip x={tip.x} y={tip.y} text={tip.text} />
          )}
        </div>
      )}
    </div>
  );
}
