/**
 * The top bar's residual sparkline while a run is going: the largest residual of each iteration, on a log
 * scale, over the last `window` iterations, as one SVG path in a `width` × `height` box. The worst field is
 * what decides convergence, so one line is the honest summary. Pure; no React, no store, no DOM.
 */
import type { ResidualSeries } from "./residualSeries";

export function residualSpark(series: ResidualSeries | null, width: number, height: number, window = 120): string {
  if (series === null || series.iterations.length < 2) return "";
  const start = Math.max(0, series.iterations.length - window);
  const logs: number[] = [];
  for (let i = start; i < series.iterations.length; i++) {
    let worst = Number.NaN;
    for (const field of series.fields) {
      const v = field.values[i];
      if (Number.isFinite(v) && v > 0 && !(worst >= v)) worst = v;
    }
    logs.push(Number.isFinite(worst) ? Math.log10(worst) : Number.NaN);
  }
  const finite = logs.filter(Number.isFinite);
  if (finite.length < 2) return "";
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  const span = hi - lo || 1;
  const pad = 1;
  const parts: string[] = [];
  let pen = "M";
  logs.forEach((value, i) => {
    if (!Number.isFinite(value)) {
      pen = "M";
      return;
    }
    const x = (i / (logs.length - 1)) * width;
    const y = pad + (1 - (value - lo) / span) * (height - 2 * pad);
    parts.push(`${pen}${x.toFixed(1)} ${y.toFixed(1)}`);
    pen = "L";
  });
  return parts.join("");
}
