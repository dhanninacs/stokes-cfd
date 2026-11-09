/**
 * One uPlot scatter: the response against an input (points, or points joined by a line) and the Pareto
 * plot (rank 0 in the accent, dominated points grey, the front joined). The data arrives as plain points
 * with their design-point index; the selected indices are drawn as accent rings on top, a click on a
 * point selects its row, and the point under the pointer names itself in a small bubble.
 *
 * One chart at a time: it is destroyed before every rebuild (the theme, an axis, the mode) and on
 * unmount, and resized with its container. A new selection or new data only calls `setData`.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { formatTick } from "./format";
import { PlotTip } from "./PlotTip";
import { MAX_DRAWN, dotStyle, readPalette, strideFor, useElementSize, useThemeEpoch, withAlpha, type Palette } from "./plotKit";

export type ScatterKind = "point" | "front" | "dominated" | "infeasible";

export interface ScatterPoint {
  /** The design point's index. */
  index: number;
  x: number;
  y: number;
  kind: ScatterKind;
}

export interface ScatterProps {
  /** `points` and `line` draw one series; `pareto` draws the front, the dominated and the infeasible points. */
  mode: "points" | "line" | "pareto";
  xLabel: string;
  yLabel: string;
  points: readonly ScatterPoint[];
  selected: ReadonlySet<number>;
  /** The text of the bubble over a point. */
  describe(point: ScatterPoint): string;
  /** A click on a point (its index) or on empty plot (null); `additive` when Shift or Ctrl was held. */
  onPick(index: number | null, additive: boolean): void;
  /** What the chart is, for assistive technology. */
  summary: string;
}

interface Model {
  /** The points drawn, by x. */
  sorted: ScatterPoint[];
  data: uPlot.AlignedData;
  /** The kinds the chart has a series for, in order, after the x column. */
  layers: readonly string[];
}

const MIN_SIDE = 60;

/** Sorted by x (uPlot wants it), thinned when there are very many points, one y column per layer. */
function buildModel(points: readonly ScatterPoint[], selected: ReadonlySet<number>, mode: ScatterProps["mode"]): Model {
  const stride = strideFor(points.length);
  const kept = stride === 1 ? points.slice() : points.filter((p, i) => i % stride === 0 || selected.has(p.index));
  const sorted = kept.sort((a, b) => a.x - b.x || a.index - b.index);
  const layers = mode === "pareto" ? ["dominated", "infeasible", "front"] : ["point"];
  const columns = layers.map((layer) => sorted.map((p) => (p.kind === layer ? p.y : null)));
  const picked = sorted.map((p) => (selected.has(p.index) ? p.y : null));
  return { sorted, layers, data: [sorted.map((p) => p.x), ...columns, picked] as uPlot.AlignedData };
}

function padded(min: number, max: number): [number, number] {
  const span = max - min || Math.abs(max) || 1;
  return [min - span * 0.08, max + span * 0.08];
}

function options(width: number, height: number, p: Palette, mode: ScatterProps["mode"], count: number, xLabel: string, yLabel: string, onReady: (u: uPlot) => void): uPlot.Options {
  const axis = (label: string, size: number): uPlot.Axis => ({
    stroke: p.muted,
    font: `11px ${p.mono}`,
    label,
    labelFont: `11px ${p.sans}`,
    labelSize: 16,
    size,
    ticks: { show: false },
    grid: { stroke: p.grid, width: 1 },
    values: (_u, splits) => splits.map((v) => formatTick(v)),
  });
  /** A dot of `radius` px; a crowd of `crowd` dots draws smaller and fainter (`dotStyle`). */
  const dot = (color: string, radius: number, crowd = 0, hollow = false): uPlot.Series.Points => {
    const look = dotStyle(crowd, radius, 1);
    return {
      show: true,
      size: look.radius * 2,
      space: 0,
      width: hollow ? 1.5 : 0,
      stroke: hollow ? withAlpha(color, Math.max(0.5, look.alpha)) : color,
      fill: hollow ? "transparent" : withAlpha(color, look.alpha),
    };
  };
  const none = () => null;
  const series: uPlot.Series[] = [{}];
  if (mode === "pareto") {
    series.push({ label: "Dominated", stroke: p.faint, width: 0, paths: none, points: dot(p.faint, 3.5, count) });
    series.push({ label: "Infeasible", stroke: p.faint, width: 0, paths: none, points: dot(p.faint, 3.5, count, true) });
    series.push({ label: "Front", stroke: p.accent, width: 1.5, spanGaps: true, points: dot(p.accent, 4) });
  } else {
    const color = p.series[1];
    series.push({ label: "Points", stroke: color, width: mode === "line" ? 1.5 : 0, paths: mode === "line" ? undefined : none, points: dot(color, 3.5, count) });
  }
  series.push({ label: "Selected", stroke: p.accent, width: 0, paths: none, points: dot(p.accent, 6.5, 0, true) });
  return {
    width,
    height,
    legend: { show: false },
    cursor: { show: true, x: false, y: false, points: { show: false }, drag: { x: false, y: false, setScale: false } },
    padding: [10, 12, 0, 0],
    scales: { x: { time: false, range: (_u, min, max) => padded(min, max) }, y: { range: (_u, min, max) => padded(min, max) } },
    axes: [axis(xLabel, 46), axis(yLabel, 68)],
    series,
    hooks: { ready: [onReady] },
  };
}

interface Hover {
  x: number;
  y: number;
  text: string;
}

export default function ScatterPlot(props: ScatterProps) {
  const chart = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const built = useRef("");
  const size = useElementSize(host);
  const epoch = useThemeEpoch();
  const [hover, setHover] = useState<Hover | null>(null);
  const ready = size.width >= MIN_SIDE && size.height >= MIN_SIDE;

  const model = useMemo(() => buildModel(props.points, props.selected, props.mode), [props.points, props.selected, props.mode]);

  // The handlers of a chart built earlier must see the latest props and model.
  const latest = useRef({ props, model });
  useLayoutEffect(() => {
    latest.current = { props, model };
  });

  const attach = (u: uPlot) => {
    const over = u.over;
    /** The point nearest to the pointer within 14 px, with where to draw its ring (relative to the chart). */
    const at = (e: MouseEvent): { point: ScatterPoint; x: number; y: number } | null => {
      const box = over.getBoundingClientRect();
      const px = e.clientX - box.left;
      const py = e.clientY - box.top;
      const { sorted } = latest.current.model;
      let best = -1;
      let bestDistance = 14 * 14;
      for (let i = 0; i < sorted.length; i++) {
        const dx = u.valToPos(sorted[i].x, "x") - px;
        const dy = u.valToPos(sorted[i].y, "y") - py;
        const d = dx * dx + dy * dy;
        if (d <= bestDistance) {
          bestDistance = d;
          best = i;
        }
      }
      if (best < 0) return null;
      const base = chart.current?.getBoundingClientRect();
      return { point: sorted[best], x: box.left - (base?.left ?? 0) + u.valToPos(sorted[best].x, "x"), y: box.top - (base?.top ?? 0) + u.valToPos(sorted[best].y, "y") };
    };
    over.addEventListener("mousemove", (e) => {
      const hit = at(e);
      setHover(hit ? { x: hit.x, y: hit.y, text: latest.current.props.describe(hit.point) } : null);
      over.style.cursor = hit ? "pointer" : "";
    });
    over.addEventListener("mouseleave", () => setHover(null));
    over.addEventListener("click", (e) => {
      latest.current.props.onPick(at(e)?.point.index ?? null, e.shiftKey || e.ctrlKey || e.metaKey);
    });
  };

  // Rebuild when the look changes; otherwise only the data moves. A hidden panel measures zero, so the
  // first time it has room (`ready`) is when the chart is made.
  // A dot's size and opacity depend on how many there are (`dotStyle`), so the chart is made again when the count has grown or shrunk a good deal.
  const crowd = Math.round(Math.log2(Math.max(100, model.sorted.length) / 100) * 2);
  const key = `${epoch}|${props.mode}|${props.xLabel}|${props.yLabel}|${model.layers.join(",")}|${crowd}`;
  useEffect(() => {
    const el = host.current;
    if (!el || !ready) return;
    if (plot.current && built.current === key) {
      plot.current.setData(model.data);
      return;
    }
    plot.current?.destroy();
    setHover(null);
    plot.current = new uPlot(options(size.width, size.height, readPalette(), props.mode, model.sorted.length, props.xLabel, props.yLabel, attach), model.data, el);
    built.current = key;
  }, [key, model, ready]); // size and props are read when the chart is built; later changes go through setSize and `latest`

  useEffect(() => {
    if (plot.current && ready) plot.current.setSize({ width: size.width, height: size.height });
  }, [size.width, size.height, ready]);

  useEffect(
    () => () => {
      plot.current?.destroy();
      plot.current = null;
      built.current = "";
    },
    [],
  );

  return (
    <div ref={chart} className="pl-chart" role="img" aria-label={props.summary}>
      <div ref={host} className="pl-chart-host" />
      {hover && (
        <>
          <span className="pl-ring" style={{ left: hover.x, top: hover.y }} aria-hidden="true" />
          <PlotTip x={hover.x} y={hover.y} text={hover.text} />
        </>
      )}
      {props.points.length > MAX_DRAWN && (
        <span className="pl-note muted">
          {model.sorted.length.toLocaleString()} of {props.points.length.toLocaleString()} points drawn
        </span>
      )}
    </div>
  );
}
