/**
 * The residual chart of a run (T-217): a lightweight SVG, a log y axis in whole decades, one line per
 * field (the `--series-1..4` tokens, cyclic), a legend that switches a field off, and a dashed line at
 * the convergence target (`solver_settings.residual_target`). The geometry is `state/residualChart.ts`
 * (tested); this file measures its box, asks for the paths and draws them.
 *
 * Colours are CSS classes over tokens (`runPanel.css`), so all four themes follow with no rebuild. The
 * history is drawn at most once per animation frame, however fast the events come.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { buildChart } from "../state/residualChart";
import type { ResidualSeries } from "../state/residualSeries";
import { latestValues, residualLabel } from "../state/residualSeries";
import { formatResidual } from "./runPanelModel";
import { useFrameValue } from "./useFrameValue";
import "./runPanel.css";

/** The fields the reader switched off. Module level, so a tab switch or a new run does not bring them back. */
const hiddenFields = new Set<string>();

interface Props {
  series: ResidualSeries;
  /** `residual_target`, or null. */
  target: number | null;
  /** Fill the parent (the drawer) rather than a fixed panel height. */
  fill?: boolean;
}

export default function ResidualPlot({ series, target, fill = false }: Props) {
  const drawn = useFrameValue(series);
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 320, height: 150 });
  const [, bump] = useState(0);

  useEffect(() => {
    const el = box.current;
    if (el === null) return;
    const measure = () => setSize({ width: Math.max(80, Math.floor(el.clientWidth)), height: Math.max(60, Math.floor(el.clientHeight)) });
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);

  const model = useMemo(() => buildChart(drawn, hiddenFields, target, size.width, size.height), [drawn, target, size, bump]);
  const latest = useMemo(() => latestValues(drawn).map((f) => `${f.name} ${formatResidual(f.value)}`).join(", "), [drawn]);
  const last = drawn.iterations.length > 0 ? drawn.iterations[drawn.iterations.length - 1] : 0;
  const label = `Residuals against iteration, log scale. Iteration ${last}${latest !== "" ? `. Latest: ${latest}` : ""}${target !== null ? `. Convergence target ${formatResidual(target)}` : ""}.`;

  const toggle = (name: string) => {
    if (hiddenFields.has(name)) hiddenFields.delete(name);
    else hiddenFields.add(name);
    bump((n) => n + 1);
  };

  return (
    <div className={`run-chart-box ${fill ? "" : "in-panel"}`} data-ws="run-chart">
      <ul className="run-legend" aria-label="Residual fields">
        {drawn.fields.map((f, index) => (
          <li key={f.name}>
            <button type="button" aria-pressed={!hiddenFields.has(f.name)} title={hiddenFields.has(f.name) ? `Show ${residualLabel(f.name)}` : `Hide ${residualLabel(f.name)}`} onClick={() => toggle(f.name)}>
              <span className={`run-swatch s${index % 4}`} aria-hidden="true" />
              {residualLabel(f.name)}
            </button>
          </li>
        ))}
      </ul>
      <div className="run-chart" ref={box}>
        <svg width={size.width} height={size.height} viewBox={`0 0 ${size.width} ${size.height}`} role="img" aria-label={label}>
          {model.yTicks.map((t) => (
            <g key={`y${t.label}`}>
              <line className="res-grid" x1={model.x0} x2={model.x1} y1={t.at} y2={t.at} />
              <text className="res-tick" x={model.x0 - 5} y={t.at + 3.5} textAnchor="end">
                {t.label}
              </text>
            </g>
          ))}
          <line className="res-axis" x1={model.x0} x2={model.x1} y1={model.y1} y2={model.y1} />
          {model.xTicks.map((t) => (
            <text key={`x${t.value}`} className="res-tick" x={t.at} y={model.y1 + 13} textAnchor="middle">
              {t.label}
            </text>
          ))}
          <text className="res-tick" x={model.x1} y={size.height - 3} textAnchor="end">
            iteration
          </text>
          {model.lines.map((line) => (
            <g key={line.name}>
              {line.d !== "" && <path className={`res-line s${line.index % 4}`} d={line.d} />}
              {line.dots.map((p, i) => (
                <circle key={i} className={`res-dot s${line.index % 4}`} cx={p.x} cy={p.y} r={2} />
              ))}
            </g>
          ))}
          {model.target !== null && (
            <g>
              <line className="res-target" x1={model.x0} x2={model.x1} y1={model.target.y} y2={model.target.y} />
              <text className="res-target-label" x={model.x0 + 4} y={model.target.y - 3} textAnchor="start">
                {model.target.label}
              </text>
            </g>
          )}
        </svg>
      </div>
    </div>
  );
}
