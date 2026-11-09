/**
 * The plot over a line on the Looks card (R5): the Line button that sits beside Probe, and under the buttons a
 * small plot of the field along the line with CSV, PNG and Clear. The rules are `panels/lineModel.ts`, the asks and
 * saves `lineControl.ts`; this only draws. The plot is a lightweight SVG of the `ResidualPlot` family (same fonts
 * and tokens); its geometry is `lineChart`, the one the PNG uses too.
 *
 * The house text rule: a few words on the control, the sentence in the tooltip.
 */
import { useEffect } from "react";
import { Button } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { fieldLabel, fieldUnits } from "../panels/fieldsModel";
import { legendOf } from "../panels/legendModel";
import { curveColour, isLinePicking, lineChart, lineNote, lineTitle } from "../panels/lineModel";
import { useStore } from "../state/store";
import { Note } from "./controls";
import { clearLine, pressLineButton, reconcileLine, saveLineCsv, saveLinePng } from "./lineControl";
import "./linePlot.css";

const WIDTH = 280;
const HEIGHT = 132;

/** The Line button: lit while a pick waits for a click. */
export function LineButton() {
  const picking = useStore((s) => isLinePicking(s.line.phase));
  return (
    <Tooltip label="Click two points on the model to plot the field along the line between them" side="top">
      <Button active={picking} aria-pressed={picking} onClick={pressLineButton} data-ws="guided-line">
        Line
      </Button>
    </Tooltip>
  );
}

/** The note and the plot under the buttons; nothing while there is no line. */
export function LinePlot() {
  const line = useStore((s) => s.line);
  const data = useStore((s) => s.results.data);
  const field = useStore((s) => s.results.view.field);
  const resultsSeq = useStore((s) => s.results.seq);
  const sliceOn = useStore((s) => s.results.view.slice);
  const slices = useStore((s) => s.slices);
  const map = useStore((s) => legendOf(s.results.view).map);
  const reverse = useStore((s) => legendOf(s.results.view).reverse);

  // A new field, new results or a moved slice: the line is asked again along the same two points.
  useEffect(() => {
    reconcileLine();
  }, [field, resultsSeq, sliceOn, slices]);

  const note = lineNote(line);
  const ready = line.phase === "ready" && line.field !== null && line.values.length > 1;
  const units = line.field === null ? "" : fieldUnits(data, line.field);
  const label = line.field === null ? "" : fieldLabel(line.field, units);
  const chart = ready ? lineChart(line.along, line.values, WIDTH, HEIGHT) : null;
  const total = ready ? line.along[line.along.length - 1]! : 0;
  const title = ready ? lineTitle(label, units, total) : "";
  return (
    <>
      {note !== null && (
        <Note tip={note.tip}>{note.text}</Note>
      )}
      {chart !== null && (
        <div className="line-plot" data-ws="guided-line-plot">
          <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={title}>
            {chart.yTicks.map((t) => (
              <g key={`y${t.value}`}>
                <line className="line-grid" x1={chart.x0} x2={chart.x1} y1={t.at} y2={t.at} />
                <text className="line-tick" x={chart.x0 - 4} y={t.at + 3.5} textAnchor="end">
                  {t.label}
                </text>
              </g>
            ))}
            <line className="line-axis" x1={chart.x0} x2={chart.x1} y1={chart.y1} y2={chart.y1} />
            {chart.xTicks.map((t) => (
              <text key={`x${t.value}`} className="line-tick" x={t.at} y={chart.y1 + 12} textAnchor="middle">
                {t.label}
              </text>
            ))}
            <text className="line-tick" x={chart.x1} y={HEIGHT - 3} textAnchor="end">
              {`distance (${chart.unit})`}
            </text>
            <path className="line-curve" d={chart.d} style={{ stroke: curveColour(map, reverse) }} />
          </svg>
          <div className="line-title muted" title={title}>
            {title}
          </div>
        </div>
      )}
      {(ready || line.phase === "outside" || line.phase === "failed") && (
        <div className="guided-actions line-actions">
          {ready && (
            <>
              <Tooltip label="Save the distance and the values as a CSV file" side="top">
                <Button onClick={() => void saveLineCsv()} data-ws="guided-line-csv">
                  CSV
                </Button>
              </Tooltip>
              <Tooltip label="Save the plot as a PNG at 2x" side="top">
                <Button onClick={() => void saveLinePng()} data-ws="guided-line-png">
                  PNG
                </Button>
              </Tooltip>
            </>
          )}
          <Tooltip label="Take the line and its plot away" side="top">
            <Button onClick={clearLine} data-ws="guided-line-clear">
              Clear
            </Button>
          </Tooltip>
        </div>
      )}
    </>
  );
}
