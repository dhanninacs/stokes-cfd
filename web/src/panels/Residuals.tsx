import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { formatResidual } from "./runPanelModel";
import { RESIDUALS_PLACEHOLDER, RESIDUALS_UNAVAILABLE, residualLabel, residualsView } from "../state/residualSeries";
import { useStore } from "../state/store";
import ResidualPlot from "./ResidualPlot";
import { findSolverSettings, residualTarget } from "./runPanelModel";

/** Demo traces until a run has reported a residual (T-217 draws the run's own, `ResidualPlot.tsx`). */
const TRACES = [
  { label: "Ux", decay: 0.045, phase: 0 },
  { label: "Uy", decay: 0.041, phase: 1 },
  { label: "p", decay: 0.03, phase: 2 },
  { label: "T", decay: 0.05, phase: 3 },
];
const ITERATIONS = Array.from({ length: 160 }, (_, i) => i + 1);
const DEMO_DATA: uPlot.AlignedData = [
  ITERATIONS,
  ...TRACES.map((t) => ITERATIONS.map((x) => Math.pow(10, -1 - t.decay * x - 0.25 * t.phase) * (1 + 0.3 * Math.abs(Math.sin(x * 0.7 + t.phase))))),
];

// Series colours come from the --series-N tokens (panels.css), read when the plot is built, so every
// theme follows; red stays reserved for errors. Cupertino's accent and blue are one colour, which is
// why the series have tokens of their own instead of reading --accent and --blue.
const SERIES_TOKENS = ["--series-1", "--series-2", "--series-3", "--series-4"];

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** The one label format of the log axis: 1, 1e-1, 1e-2 … (uPlot passes null for a tick it could not place). */
function decadeLabel(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value <= 0) return "";
  const exponent = Math.round(Math.log10(value));
  return exponent === 0 ? "1" : `1e${exponent}`;
}

/**
 * Tick positions of the log axis: whole decades only, thinned by the room there is. uPlot's own
 * splits are every 1-9 x 10^k, which at this height paints a grey wash of grid lines and stacks the
 * labels on top of each other. A decade gets a tick at 20 px or more, every second one from 10 px,
 * every third below that, and in a drawer squeezed to its smallest every n-th decade, n chosen to
 * keep the ticks 20 px apart; the top decade always keeps its tick.
 */
function decadeSplits(u: uPlot, _axis: number, min: number, max: number): number[] {
  const low = Math.ceil(Math.log10(min) - 1e-9);
  const high = Math.floor(Math.log10(max) + 1e-9);
  const perDecade = u.bbox.height / devicePixelRatio / Math.max(1, high - low);
  const step = perDecade >= 20 ? 1 : perDecade >= 10 ? 2 : Math.max(3, Math.ceil(20 / perDecade));
  const ticks: number[] = [];
  for (let e = low; e <= high; e++) if ((high - e) % step === 0) ticks.push(10 ** e);
  return ticks;
}

/** Series the reader switched off in the legend. Module level, so it survives theme rebuilds and tab switches. */
const hidden = new Set<number>();

function createPlot(host: HTMLElement): uPlot {
  const text = token("--text-muted");
  const grid = token("--panel-border");
  // A whole-pixel size on purpose: uPlot scales the font by the pixel ratio with a pattern that reads
  // "10.5px" as "5px", so a fractional size comes out wrong on every display that is not at 100 %.
  const font = `11px ${token("--font-mono") || "monospace"}`;
  const axis = (extra: Partial<uPlot.Axis>): uPlot.Axis => ({ stroke: text, font, ticks: { show: false }, ...extra });
  return new uPlot(
    {
      width: Math.max(1, host.clientWidth),
      height: 100,
      cursor: { show: true },
      // live: false builds no "iteration" row and no value cells, which this legend has no room for.
      legend: { show: true, live: false },
      scales: { x: { time: false }, y: { distr: 3 } },
      axes: [
        axis({ size: 26, grid: { show: false } }),
        axis({ size: 40, grid: { stroke: grid, width: 1 }, splits: decadeSplits, filter: (_u, splits) => splits, values: (_u, splits) => splits.map(decadeLabel) }),
      ],
      series: [
        { label: "iteration" },
        ...TRACES.map((t, i): uPlot.Series => ({ label: t.label, stroke: token(SERIES_TOKENS[i]), width: 1.5, show: !hidden.has(i + 1) })),
      ],
      hooks: {
        // A legend click toggles a series; remember it so a theme rebuild does not bring it back.
        setSeries: [
          (u, index) => {
            if (index === null || index === undefined) return;
            if (u.series[index].show) hidden.delete(index);
            else hidden.add(index);
          },
        ],
      },
    },
    DEMO_DATA,
    host,
  );
}

/**
 * The demo chart (uPlot, log scale). One instance at a time: it is destroyed before every rebuild
 * (theme change) and on unmount, and resized with the drawer through a ResizeObserver.
 *
 * The look lives in panels.css under `.residuals`: the legend is a row of line swatches above the plot.
 */

function DemoResiduals() {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    let plot: uPlot | null = null;

    // The legend sits above the plot area and takes its own height; size the plot to what is left.
    // uPlot applies a size on the next microtask and the legend rewraps only then, so look once more after it.
    const legendHeight = () => {
      const legend = plot?.root.querySelector<HTMLElement>(".u-legend");
      return legend ? legend.offsetHeight + (parseFloat(getComputedStyle(legend).marginBottom) || 0) : 0;
    };
    const plotHeight = () => Math.max(40, el.clientHeight - legendHeight());
    const fit = () => {
      if (!plot) return;
      plot.setSize({ width: Math.max(1, el.clientWidth), height: plotHeight() });
      queueMicrotask(() => {
        if (plot && plot.height !== plotHeight()) plot.setSize({ width: Math.max(1, el.clientWidth), height: plotHeight() });
      });
    };
    const build = () => {
      plot?.destroy();
      plot = createPlot(el);
      fit();
    };

    const resize = new ResizeObserver(() => fit());
    resize.observe(el);
    const theme = new MutationObserver(build);
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    build();

    return () => {
      resize.disconnect();
      theme.disconnect();
      plot?.destroy();
      plot = null;
    };
  }, []);

  return (
    <div className="relative h-full w-full px-2 py-1">
      <div ref={host} className="residuals h-full w-full" />
      {/*
        **The label is the ONLY thing separating a synthetic chart from a run's evidence**, and it was
        11 px of muted grey in the far corner while the legend beside it read `Ux Uy p T` - the same
        names real data carries. Measured: with a status bar saying "Run · it 64 · converged", the plot
        reads as that run's history.
        **So it says what it is, in words a reader cannot skim past**: not "demo traces" but a sentence
        that names the absence. A user who wants the placeholder still sees the shape of a residual plot;
        a user who wants evidence is told there is none.
      */}
      <span className="residuals-note" data-ws="residuals-demo" title="No residual history has been reported for this run, so this chart is a placeholder.">
        {RESIDUALS_PLACEHOLDER}
      </span>
    </div>
  );
}

/**
 * The Residuals tab (T-217): the newest run's residuals, a log-scale SVG with the convergence target,
 * as soon as the run has reported one; the demo traces until then, exactly as before.
 */
export default function Residuals() {
  const residuals = useStore((s) => s.run.residuals);
  const tree = useStore((s) => s.tree);
  // **`activeRun` is the run the tab follows**, and its record carries the final residuals.
  const runId = useStore((s) => s.run.activeRun);
  const final = useStore((s) => {
    const active = s.run.runs.find((r) => r.id === s.run.activeRun);
    return active?.last ?? [];
  });
  const view = residualsView({
    hasRun: runId !== null,
    iterations: residuals?.iterations.length ?? 0,
    final,
  });
  if (residuals !== null && residuals.iterations.length > 0) {
    return (
      <div className="relative h-full w-full px-2 py-1" data-ws="residuals-live">
        <ResidualPlot series={residuals} target={residualTarget(findSolverSettings(tree))} fill />
      </div>
    );
  }
  /*
    **The decision** (Codex's 222732, item 1): a real run with no history gets an explicit EMPTY STATE,
    never synthetic curves. Measured: the demo chart's legend read `Ux Uy p T` beside a status bar saying
    "converged", and it was taken for that run's history - a label was not enough.
  */
  if (view.kind === "unavailable") {
    return (
      <div className="residuals-empty" data-ws="residuals-unavailable">
        <p className="residuals-empty-title">{RESIDUALS_UNAVAILABLE}</p>
        {view.final.length > 0 && (
          <>
            <p className="muted residuals-empty-note">The final residuals it reported:</p>
            <table className="residuals-final" aria-label="Final residuals">
              <tbody>
                {view.final.map((f) => (
                  <tr key={f.name}>
                    <td>{residualLabel(f.name)}</td>
                    <td className="mono" data-ws="residual-final">
                      {formatResidual(f.value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    );
  }
  if (view.kind === "demo") return <DemoResiduals />;
  return null;
}
