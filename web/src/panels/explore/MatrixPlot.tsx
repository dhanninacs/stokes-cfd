/**
 * The scatter matrix: small multiples on one canvas, a column per input and a row per response, each
 * cell the response against that input. The selected points are drawn again on a second canvas above,
 * so a new selection or the pointer moving never redraws the thousands of points below. A click on a
 * point selects its row (Shift or Ctrl keeps the others); a click on nothing clears the selection.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { DataColumn, Dataset } from "./dataset";
import { formatTick, withUnit } from "./format";
import { PlotTip } from "./PlotTip";
import { dotStyle, fraction, prepareCanvas, readPalette, strideFor, useElementSize, useThemeEpoch, type Palette } from "./plotKit";

export interface MatrixProps {
  dataset: Dataset;
  selected: ReadonlySet<number>;
  onPick(index: number | null, additive: boolean): void;
}

const LEFT = 78;
const TOP = 22;
const RIGHT = 8;
const BOTTOM = 18;
const GAP = 8;
const PAD = 6;
const MIN_CELL = 56;
const MAX_CELL_W = 240;
const MAX_CELL_H = 220;

interface Layout {
  cellW: number;
  cellH: number;
  width: number;
  height: number;
}

/** The cells fill the room there is, within limits: a cell is not smaller than MIN_CELL, not wider than MAX_CELL_W and not taller than 1.6 times its width. */
function layoutFor(width: number, height: number, cols: number, rows: number): Layout {
  const room = (width - LEFT - RIGHT - GAP * (cols - 1)) / cols;
  const cellW = Math.min(MAX_CELL_W, Math.max(MIN_CELL, Math.floor(room)));
  const fitH = Math.floor((height - TOP - BOTTOM - GAP * (rows - 1)) / rows);
  const cellH = Math.max(MIN_CELL, Math.min(MAX_CELL_H, Math.floor(cellW * 1.6), fitH));
  return { cellW, cellH, width: Math.max(width, LEFT + RIGHT + cols * cellW + GAP * (cols - 1)), height: TOP + BOTTOM + rows * cellH + GAP * (rows - 1) };
}

/** For every cell (response row, input column) the table positions of the points that have both values. */
function cellRows(dataset: Dataset): Int32Array[][] {
  return dataset.outputs.map((y) =>
    dataset.inputs.map((x) => {
      const keep: number[] = [];
      for (let i = 0; i < dataset.rows.length; i++) if (Number.isFinite(x.values[i]) && Number.isFinite(y.values[i])) keep.push(i);
      return Int32Array.from(keep);
    }),
  );
}

interface Cell {
  x: DataColumn;
  y: DataColumn;
  /** Top left of the cell's frame, in canvas CSS pixels. */
  ox: number;
  oy: number;
  w: number;
  h: number;
}

function cellAt(dataset: Dataset, layout: Layout, column: number, row: number): Cell {
  return {
    x: dataset.inputs[column],
    y: dataset.outputs[row],
    ox: LEFT + column * (layout.cellW + GAP),
    oy: TOP + row * (layout.cellH + GAP),
    w: layout.cellW,
    h: layout.cellH,
  };
}

const px = (c: Cell, value: number) => c.ox + PAD + fraction(value, c.x.min, c.x.max) * (c.w - 2 * PAD);
const py = (c: Cell, value: number) => c.oy + c.h - PAD - fraction(value, c.y.min, c.y.max) * (c.h - 2 * PAD);

function fit(ctx: CanvasRenderingContext2D, text: string, room: number): string {
  if (ctx.measureText(text).width <= room) return text;
  let cut = text.length;
  while (cut > 1 && ctx.measureText(`${text.slice(0, cut)}…`).width > room) cut--;
  return `${text.slice(0, cut)}…`;
}

function drawFrame(ctx: CanvasRenderingContext2D, p: Palette, dataset: Dataset, layout: Layout) {
  const rows = dataset.outputs.length;
  const cols = dataset.inputs.length;
  ctx.lineWidth = 1;
  ctx.textBaseline = "middle";
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const cell = cellAt(dataset, layout, c, r);
      ctx.strokeStyle = p.grid;
      ctx.strokeRect(cell.ox + 0.5, cell.oy + 0.5, cell.w - 1, cell.h - 1);
      if (r === 0) {
        ctx.font = `600 11px ${p.sans}`;
        ctx.fillStyle = p.text;
        ctx.textAlign = "center";
        ctx.fillText(fit(ctx, withUnit(cell.x.title, cell.x.unit && `[${cell.x.unit}]`), cell.w), cell.ox + cell.w / 2, TOP - 10);
      }
      ctx.font = `9.5px ${p.mono}`;
      ctx.fillStyle = p.faint;
      if (r === rows - 1) {
        ctx.textAlign = "left";
        ctx.fillText(formatTick(cell.x.min), cell.ox, cell.oy + cell.h + 9);
        ctx.textAlign = "right";
        ctx.fillText(formatTick(cell.x.max), cell.ox + cell.w, cell.oy + cell.h + 9);
      }
      if (c === 0) {
        ctx.textAlign = "right";
        ctx.fillText(formatTick(cell.y.max), cell.ox - 5, cell.oy + 6);
        ctx.fillText(formatTick(cell.y.min), cell.ox - 5, cell.oy + cell.h - 6);
        ctx.save();
        ctx.translate(11, cell.oy + cell.h / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.font = `600 11px ${p.sans}`;
        ctx.fillStyle = p.text;
        ctx.textAlign = "center";
        ctx.fillText(fit(ctx, withUnit(cell.y.title, cell.y.unit && `[${cell.y.unit}]`), cell.h - 6), 0, 0);
        ctx.restore();
      }
    }
  }
}

function drawPoints(ctx: CanvasRenderingContext2D, p: Palette, dataset: Dataset, layout: Layout, cells: Int32Array[][]) {
  ctx.fillStyle = p.series[1];
  for (let r = 0; r < dataset.outputs.length; r++) {
    for (let c = 0; c < dataset.inputs.length; c++) {
      const cell = cellAt(dataset, layout, c, r);
      const positions = cells[r][c];
      const stride = strideFor(positions.length);
      // A crowded cell draws smaller, fainter dots, so the density shows.
      const dot = dotStyle(positions.length / stride, 2.4, 0.85);
      ctx.globalAlpha = dot.alpha;
      ctx.beginPath();
      for (let k = 0; k < positions.length; k += stride) {
        const at = positions[k];
        const x = px(cell, cell.x.values[at]);
        const y = py(cell, cell.y.values[at]);
        ctx.moveTo(x + dot.radius, y);
        ctx.arc(x, y, dot.radius, 0, Math.PI * 2);
      }
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
}

function drawMarked(ctx: CanvasRenderingContext2D, p: Palette, dataset: Dataset, layout: Layout, selected: ReadonlySet<number>, hover: number | null) {
  const marked = (index: number, radius: number, fill: string | null, ring: string | null, alpha = 1) => {
    const at = dataset.position.get(index);
    if (at === undefined) return;
    ctx.globalAlpha = alpha;
    for (let r = 0; r < dataset.outputs.length; r++) {
      for (let c = 0; c < dataset.inputs.length; c++) {
        const cell = cellAt(dataset, layout, c, r);
        if (!Number.isFinite(cell.x.values[at]) || !Number.isFinite(cell.y.values[at])) continue;
        const x = px(cell, cell.x.values[at]);
        const y = py(cell, cell.y.values[at]);
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        if (fill) {
          ctx.fillStyle = fill;
          ctx.fill();
        }
        if (ring) {
          ctx.strokeStyle = ring;
          ctx.lineWidth = 1.4;
          ctx.stroke();
        }
      }
    }
    ctx.globalAlpha = 1;
  };
  // Every row selected marks nothing apart from the rest; a crowd of them is drawn small and faint, without rings.
  if (selected.size < dataset.rows.length) {
    const look = dotStyle(selected.size, 3.8, 1);
    const ring = selected.size <= 150 ? p.text : null;
    for (const index of selected) marked(index, look.radius, p.accent, ring, look.alpha);
  }
  if (hover !== null) marked(hover, 6, null, p.text);
}

interface Hover {
  index: number;
  x: number;
  y: number;
  text: string;
}

export default function MatrixPlot({ dataset, selected, onPick }: MatrixProps) {
  const scroll = useRef<HTMLDivElement>(null);
  const base = useRef<HTMLCanvasElement>(null);
  const over = useRef<HTMLCanvasElement>(null);
  const size = useElementSize(scroll);
  const epoch = useThemeEpoch();
  const [hover, setHover] = useState<Hover | null>(null);

  const cells = useMemo(() => cellRows(dataset), [dataset]);
  const layout = useMemo(() => (size.width > 0 && size.height > 0 ? layoutFor(size.width, size.height, dataset.inputs.length, dataset.outputs.length) : null), [size.width, size.height, dataset.inputs.length, dataset.outputs.length]);

  useEffect(() => {
    const canvas = base.current;
    if (!canvas || !layout) return;
    const ctx = prepareCanvas(canvas, layout.width, layout.height);
    if (!ctx) return;
    const palette = readPalette();
    drawFrame(ctx, palette, dataset, layout);
    drawPoints(ctx, palette, dataset, layout, cells);
  }, [layout, dataset, cells, epoch]);

  useEffect(() => {
    const canvas = over.current;
    if (!canvas || !layout) return;
    const ctx = prepareCanvas(canvas, layout.width, layout.height);
    if (!ctx) return;
    drawMarked(ctx, readPalette(), dataset, layout, selected, hover?.index ?? null);
  }, [layout, dataset, selected, hover, epoch]);

  /** The point under the pointer: the nearest in the cell it is over, within 10 px. */
  const hit = (clientX: number, clientY: number): Hover | null => {
    const canvas = over.current;
    if (!canvas || !layout) return null;
    const box = canvas.getBoundingClientRect();
    const x = clientX - box.left;
    const y = clientY - box.top;
    const column = Math.floor((x - LEFT) / (layout.cellW + GAP));
    const row = Math.floor((y - TOP) / (layout.cellH + GAP));
    if (column < 0 || row < 0 || column >= dataset.inputs.length || row >= dataset.outputs.length) return null;
    const cell = cellAt(dataset, layout, column, row);
    let best: number | null = null;
    let bestDistance = 10 * 10;
    const positions = cells[row][column];
    for (let k = 0; k < positions.length; k++) {
      const at = positions[k];
      const dx = px(cell, cell.x.values[at]) - x;
      const dy = py(cell, cell.y.values[at]) - y;
      const d = dx * dx + dy * dy;
      if (d <= bestDistance) {
        bestDistance = d;
        best = at;
      }
    }
    if (best === null) return null;
    const point = dataset.rows[best];
    const text = `#${point.index}  ${cell.x.title} ${withUnit(formatTick(cell.x.values[best]), cell.x.unit)}  ·  ${cell.y.title} ${withUnit(formatTick(cell.y.values[best]), cell.y.unit)}`;
    return { index: point.index, x: px(cell, cell.x.values[best]), y: py(cell, cell.y.values[best]), text };
  };

  return (
    <div ref={scroll} className="pl-scroll" role="img" aria-label={`Scatter matrix: ${dataset.outputs.length} responses against ${dataset.inputs.length} inputs`}>
      {layout && (
        <div className="pl-stack" style={{ width: layout.width, height: layout.height }}>
          <canvas ref={base} />
          <canvas
            ref={over}
            style={{ cursor: hover ? "pointer" : undefined }}
            onMouseMove={(e) => {
              const next = hit(e.clientX, e.clientY);
              setHover((prev) => (prev && next && prev.index === next.index && prev.x === next.x && prev.y === next.y ? prev : next));
            }}
            onMouseLeave={() => setHover(null)}
            onClick={(e) => onPick(hit(e.clientX, e.clientY)?.index ?? null, e.shiftKey || e.ctrlKey || e.metaKey)}
          />
          {hover && (
            <PlotTip x={hover.x} y={hover.y} text={hover.text} />
          )}
        </div>
      )}
    </div>
  );
}
