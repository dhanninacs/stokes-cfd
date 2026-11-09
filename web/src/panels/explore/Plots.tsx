/**
 * The Plots panel (ADR-0016 point 5): the design table drawn four ways. Response against input (a uPlot
 * scatter, or a line), a scatter matrix (inputs against responses, on one canvas), parallel coordinates
 * (one canvas, a line per design point, coloured by a response) and the Pareto plot (two objectives, the
 * front in the accent and the dominated points grey; offered only when the study has two objectives).
 * Every plot draws the rows selected in the design table and selects rows when it is clicked, so the
 * table and the plots always show the same selection.
 */
import { useMemo, useState, type ReactNode } from "react";
import { ChartLine, ChevronsUpDown, Plus } from "lucide-react";
import { Button, IconButton, Panel, Segmented } from "../../components/ui";
import { findNode, useStore } from "../../state/store";
import { openNewStudy } from "./forms";
import { updateTable } from "./commands";
import type { DataColumn, Dataset } from "./dataset";
import { Empty } from "./Empty";
import { formatTick, plural, withUnit } from "./format";
import MatrixPlot from "./MatrixPlot";
import ParallelPlot from "./ParallelPlot";
import { rampStops, readPalette, useThemeEpoch } from "./plotKit";
import ScatterPlot, { type ScatterPoint } from "./ScatterPlot";
import { useExplore, useExploreFeed } from "./store";
import { groupNodes } from "./study";
import type { ParetoResult } from "./types";

type PlotType = "response" | "matrix" | "parallel" | "pareto";

/** The plot types, with the short names the segmented control has room for: response vs input, scatter matrix, parallel coordinates, Pareto. */
const TYPES: readonly { value: PlotType; label: string }[] = [
  { value: "response", label: "Response" },
  { value: "matrix", label: "Matrix" },
  { value: "parallel", label: "Parallel" },
  { value: "pareto", label: "Pareto" },
];

/** `Pressure [Pa]`: a column's title with its unit, for axes and legends. */
function axisLabel(column: DataColumn): string {
  return column.unit ? `${column.title} [${column.unit}]` : column.title;
}

function pick(index: number | null, additive: boolean): void {
  const explore = useExplore.getState();
  if (index === null) {
    if (!additive) explore.clearSelection();
  } else if (additive) {
    explore.toggle(index);
  } else {
    explore.setSelection([index], index);
  }
}

/** A column chooser; `label` is the word in front of it when it needs one ("Colour"). */
function Pick({ label, aria, value, onChange, columns }: { label?: string; aria: string; value: string; onChange: (name: string) => void; columns: readonly DataColumn[] }) {
  return (
    <label className="pl-pick">
      {label && (
        <span className="muted" aria-hidden="true">
          {label}
        </span>
      )}
      <span className="select-wrap">
        <select className="field" aria-label={aria} title={columns.find((c) => c.name === value)?.title} value={value} onChange={(e) => onChange(e.target.value)}>
          {columns.map((c) => (
            <option key={c.name} value={c.name}>
              {c.title}
            </option>
          ))}
        </select>
        <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
      </span>
    </label>
  );
}

/** The one column named `name`, else the first of `columns`. */
function chosen(columns: readonly DataColumn[], name: string): DataColumn | undefined {
  return columns.find((c) => c.name === name) ?? columns[0];
}

function responsePoints(data: Dataset, x: DataColumn, y: DataColumn): ScatterPoint[] {
  const out: ScatterPoint[] = [];
  for (let i = 0; i < data.rows.length; i++) {
    const px = x.values[i];
    const py = y.values[i];
    if (Number.isFinite(px) && Number.isFinite(py)) out.push({ index: data.rows[i].index, x: px, y: py, kind: "point" });
  }
  return out;
}

function paretoPoints(data: Dataset, pareto: ParetoResult, x: DataColumn, y: DataColumn): ScatterPoint[] {
  const out: ScatterPoint[] = [];
  for (const p of pareto.points) {
    const at = data.position.get(p.index);
    if (at === undefined) continue;
    const px = x.values[at];
    const py = y.values[at];
    if (!Number.isFinite(px) || !Number.isFinite(py)) continue;
    out.push({ index: p.index, x: px, y: py, kind: p.onFront ? "front" : p.feasible ? "dominated" : "infeasible" });
  }
  return out;
}

export default function Plots() {
  useExploreFeed();
  const tree = useStore((s) => s.tree);
  const hasProject = useStore((s) => s.project !== null);
  const results = useExplore((s) => s.results);
  const dataset = useExplore((s) => s.dataset);
  const pareto = useExplore((s) => s.pareto);
  const paretoError = useExplore((s) => s.paretoError);
  const selected = useExplore((s) => s.selected);
  const studyId = useExplore((s) => s.studyId);
  const updating = useExplore((s) => s.updating > 0);
  const epoch = useThemeEpoch();

  const [type, setType] = useState<PlotType>("response");
  const [xName, setXName] = useState("");
  const [yName, setYName] = useState("");
  const [joined, setJoined] = useState<boolean | null>(null);
  const [colourName, setColourName] = useState("");
  const [paretoX, setParetoX] = useState("");
  const [paretoY, setParetoY] = useState("");

  const study = findNode(tree, studyId);
  const objectiveNodes = study ? groupNodes(study, "objectives").length : 0;
  const paretoOffered = objectiveNodes >= 2;
  const shown: PlotType = type === "pareto" && !paretoOffered ? "response" : type;

  const inputs = dataset?.inputs ?? [];
  const outputs = dataset?.outputs ?? [];
  const x = chosen(inputs, xName);
  const y = chosen(outputs, yName);
  const colour = chosen(outputs, colourName);
  const line = joined ?? inputs.length === 1;

  // The objectives' response columns, for the Pareto axes.
  const objectiveColumns = useMemo(() => {
    if (!dataset || !pareto) return [] as { objective: string; column: DataColumn; direction: "minimise" | "maximise" }[];
    return pareto.objectives.flatMap((o) => {
      const column = dataset.byName.get(o.response);
      return column ? [{ objective: o.name, column, direction: o.direction }] : [];
    });
  }, [dataset, pareto]);
  const px = objectiveColumns.find((o) => o.column.name === paretoX) ?? objectiveColumns[0];
  const py = objectiveColumns.find((o) => o.column.name === paretoY && o !== px) ?? objectiveColumns.find((o) => o !== px);

  const points = useMemo(() => (dataset && x && y ? responsePoints(dataset, x, y) : []), [dataset, x, y]);
  const frontPoints = useMemo(() => (dataset && pareto && px && py ? paretoPoints(dataset, pareto, px.column, py.column) : []), [dataset, pareto, px, py]);

  const done = results?.counts.done ?? 0;
  const body = (): ReactNode => {
    if (!studyId || !results || !dataset) {
      return (
        <Empty
          action={
            !studyId &&
            hasProject && (
              <Button className="ws-small" onClick={openNewStudy}>
                <Plus size={14} /> New study…
              </Button>
            )
          }
        >
          {studyId ? "Loading the design table…" : hasProject ? "No study yet. Create one from the Explore stage toolbar." : "Open a project to explore it."}
        </Empty>
      );
    }
    if (inputs.length === 0 || outputs.length === 0) {
      return <Empty>{inputs.length === 0 ? "This study has no variables yet. Add one in its properties." : "This study has no responses yet. Add one in its properties."}</Empty>;
    }
    if (done === 0 && shown !== "pareto") {
      return (
        <Empty
          action={
            results.points.length > 0 && (
              <Button className="ws-small" disabled={updating} onClick={() => void updateTable("all")}>
                Update all
              </Button>
            )
          }
        >
          {results.points.length > 0 ? "Nothing to plot yet. Update the design table to evaluate its points." : "Nothing to plot yet. Generate the design table, then update it."}
        </Empty>
      );
    }
    switch (shown) {
      case "response":
        if (!x || !y) return null;
        return (
          <ScatterPlot
            mode={line ? "line" : "points"}
            xLabel={axisLabel(x)}
            yLabel={axisLabel(y)}
            points={points}
            selected={selected}
            onPick={pick}
            summary={`${y.title} against ${x.title}, ${points.length} points`}
            describe={(p) => `#${p.index}  ${x.title} ${withUnit(formatTick(p.x), x.unit)}  ·  ${y.title} ${withUnit(formatTick(p.y), y.unit)}`}
          />
        );
      case "matrix":
        return <MatrixPlot dataset={dataset} selected={selected} onPick={pick} />;
      case "parallel":
        return colour ? <ParallelPlot dataset={dataset} colourBy={colour.name} selected={selected} onPick={pick} /> : null;
      case "pareto": {
        if (paretoError) return <Empty>{`The Pareto set is not available: ${paretoError}`}</Empty>;
        if (!pareto || !px || !py) return <Empty>Loading the Pareto set…</Empty>;
        if (frontPoints.length === 0) return <Empty tip="Update the design table to evaluate its points.">Nothing to plot yet</Empty>;
        const arrow = (d: "minimise" | "maximise") => (d === "minimise" ? "↓" : "↑");
        return (
          <ScatterPlot
            mode="pareto"
            xLabel={`${axisLabel(px.column)} ${arrow(px.direction)}`}
            yLabel={`${axisLabel(py.column)} ${arrow(py.direction)}`}
            points={frontPoints}
            selected={selected}
            onPick={pick}
            summary={`Pareto plot of ${py.column.title} against ${px.column.title}: ${pareto.front.length} points on the front`}
            describe={(p) => `#${p.index}  ${px.column.title} ${withUnit(formatTick(p.x), px.column.unit)}  ·  ${py.column.title} ${withUnit(formatTick(p.y), py.column.unit)}  ·  ${p.kind === "front" ? "on the front" : p.kind === "infeasible" ? "infeasible" : "dominated"}`}
          />
        );
      }
    }
  };

  const picked = selected.size;
  return (
    <Panel className="pl-container flex flex-col overflow-hidden">
      <div className="pl-panel" data-plot={shown}>
        <div className="pl-head">
          <span className="pl-study">{results?.name ?? (studyId ? "" : "Plots")}</span>
          <span className="pl-headtools">
            {results && <span className="muted pl-count">{plural(done, "evaluated point")}</span>}
            {shown === "response" && done > 0 && x && y && (
              <IconButton label={line ? "Draw points only" : "Join the points with a line"} className="pl-join" aria-pressed={line} onClick={() => setJoined(!line)}>
                <ChartLine size={14} />
              </IconButton>
            )}
          </span>
        </div>
        {studyId && (
          <div className="pl-types">
            <Segmented
              label="Plot type"
              value={shown}
              onChange={setType}
              options={TYPES.filter((t) => t.value !== "pareto" || paretoOffered).map((t) => ({ value: t.value, label: t.label }))}
            />
          </div>
        )}
        {dataset && studyId && inputs.length > 0 && outputs.length > 0 && (
          <div className="pl-controls">
            {shown === "response" && x && y && (
              <>
                <Pick aria="Y axis, a response" value={y.name} onChange={setYName} columns={outputs} />
                <span className="pl-vs muted">vs</span>
                <Pick aria="X axis, an input" value={x.name} onChange={setXName} columns={inputs} />
              </>
            )}
            {shown === "parallel" && colour && <Pick label="Colour by" aria="Colour the lines by" value={colour.name} onChange={setColourName} columns={outputs} />}
            {shown === "pareto" && px && py && objectiveColumns.length > 2 && (
              <>
                <Pick aria="Y objective" value={py.column.name} onChange={setParetoY} columns={objectiveColumns.map((o) => o.column)} />
                <span className="pl-vs muted">vs</span>
                <Pick aria="X objective" value={px.column.name} onChange={setParetoX} columns={objectiveColumns.map((o) => o.column)} />
              </>
            )}
          </div>
        )}
        <div className="pl-body">{body()}</div>
        <div className="pl-foot muted">
          {shown === "parallel" && colour ? (
            <Ramp column={colour} epoch={epoch} />
          ) : shown === "pareto" ? (
            <span className="pl-legend">
              <i className="pl-key front" /> front <i className="pl-key dominated" /> dominated <i className="pl-key infeasible" /> infeasible
            </span>
          ) : (
            <span />
          )}
          <span className="pl-picked">{picked > 0 ? `${picked.toLocaleString()} selected` : ""}</span>
        </div>
      </div>
    </Panel>
  );
}

/** The colour scale of the parallel plot: the ramp's three stops as a bar, with the colour response's range. */
function Ramp({ column, epoch }: { column: DataColumn; epoch: number }) {
  void epoch;
  const [a, b, c] = rampStops(readPalette());
  const css = (rgb: readonly [number, number, number]) => `rgb(${rgb[0]} ${rgb[1]} ${rgb[2]})`;
  return (
    <span className="pl-ramp" title={`Lines are coloured by ${column.title}`}>
      <span className="pl-ramp-bar" style={{ background: `linear-gradient(90deg, ${css(a)}, ${css(b)}, ${css(c)})` }} />
      <span className="mono">
        {formatTick(column.min)} to {withUnit(formatTick(column.max), column.unit)}
      </span>
    </span>
  );
}

