/**
 * The Quality page (brief M-507, task 8): how good the cells are, where the bad ones are, and
 * what to change.
 *
 * **Three states for the distributions** (M-506's plumbing delivers `meta.quality_dist` after
 * the result is done): *pending* — the verdict and statistics are ALREADY shown from checkMesh,
 * with "Working out the distributions…"; *ready* — the histograms; *error* — checkMesh's
 * numbers only, with the reason. The click-a-bar lighting is the viewer half (`qualityLayer`)
 * and lands with the faces route; this page asks for it through `onBar`.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import QualityHistogram from "../QualityHistogram";
import {
  clickBin,
  clearOverlay,
  hasLit,
  sourceName,
  EMPTY_OVERLAY,
  type QualityOverlayState,
} from "../qualityOverlayModel";
import { useStore } from "../../state/store";
import {
  PENDING_LINE,
  axisLabel,
  barsFor,
  cellTypeRows,
  distributionState,
  isLogAxis,
  verdictLine,
  worstCentre,
  worstFaces,
  worstValue,
  type Distributions,
  type MetricHistogram,
  type WorstFace,
} from "../qualityModel";
import { Note } from "../../components/Note";

interface CheckmeshNumbers {
  mesh_ok?: boolean;
  max_non_ortho?: number | null;
  max_skewness?: number | null;
  max_aspect_ratio?: number | null;
  cells?: number | null;
  points?: number | null;
  faces?: number | null;
  internal_faces?: number | null;
  boundary_patches?: number | null;
  thresholds?: { max_non_ortho: number; max_skewness: number };
}

export default function QualityPage() {
  const tree = useStore((s) => s.tree);
  const resultNode = useMemo(() => {
    const results = tree?.children?.find((c) => c.path === "/mesh/results");
    return results?.children?.[0] ?? null;
  }, [tree]);
  // **The overlay's state is the page's** (brief M-507 §UI 3): a click lights a bin,
  // shift+click adds, Esc clears, and a new result disposes. The face FETCH and the viewport
  // drawing are the viewer half, which lands with the faces route — until then the selection
  // is shown as requested-only, honestly.
  const [overlay, setOverlay] = useState<QualityOverlayState>(EMPTY_OVERLAY);
  const onBar = useCallback(
    (metric: string, from: number, to: number, additive: boolean) => {
      setOverlay((current) => clickBin(current, metric, from, to, additive));
    },
    [],
  );
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOverlay((current) => clearOverlay(current));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // **A new result disposes the overlay** (brief M-507 §UI 3): the lit selection belongs to
  // the result's own numbers.
  const resultId = resultNode?.id ?? "";
  useEffect(() => {
    setOverlay(EMPTY_OVERLAY);
  }, [resultId]);

  const quality = (resultNode?.params.quality ?? {}) as CheckmeshNumbers;
  const distributions = resultNode?.meta?.quality_dist as Distributions | undefined;
  const state = distributionState(distributions);
  const limits = quality.thresholds ?? { max_non_ortho: 65, max_skewness: 4 };
  const meshOk = quality.mesh_ok ?? null;
  const nonOrthoMax = quality.max_non_ortho ?? null;

  const metrics = state === "ready" && "metrics" in (distributions ?? {}) ? (distributions as { metrics: Record<string, MetricHistogram> }).metrics : null;
  const order = ["non_ortho", "skewness", "aspect_ratio"];
  const titles: Record<string, string> = {
    non_ortho: "Non-orthogonality",
    skewness: "Skewness",
    aspect_ratio: "Aspect ratio",
  };

  return (
    <div className="task-page" data-ws="task-quality">
      <Note className="task-purpose" tip="How good the cells are, where the bad ones are, and what to change">
        Cell quality
      </Note>
      <p className="task-verdict" data-ws="quality-verdict">
        {verdictLine(meshOk, limits, nonOrthoMax)}
      </p>
      {metrics !== null
        ? order.map((name) => {
            const metric = metrics[name];
            if (metric === undefined) return null;
            const bars = barsFor(metric, name === "non_ortho" ? limits.max_non_ortho : name === "skewness" ? limits.max_skewness : null);
            const log = isLogAxis(metric.unit);
            const values = bars.flatMap((b) => [b.from, b.to]);
            const xMin = log && values.length > 0 ? Math.min(...values.filter((v) => v > 0), 1e-3) : Math.min(...values, 0);
            const xMax = values.length > 0 ? Math.max(...values) : 1;
            return (
              <div key={name} data-ws={`quality-${name}`}>
                <div className="section-title">{titles[name] ?? name}</div>
                {bars.length === 0 && metric.valid === false ? (
                  <p className="muted task-note" data-ws={`quality-${name}-invalid`}>
                    checkMesh maximum {metric.max} — {metric.reason}
                  </p>
                ) : (
                  <QualityHistogram
                    bars={bars}
                    unit={axisLabel(metric)}
                    limit={name === "non_ortho" ? limits.max_non_ortho : name === "skewness" ? limits.max_skewness : null}
                    xMin={log ? Math.log10(Math.max(xMin, 1e-6)) : xMin}
                    xMax={log ? Math.log10(Math.max(xMax, 1e-5)) : xMax}
                    onBar={(from: number, to: number) => onBar(name, from, to, false)}
                  />
                )}
              </div>
            );
          })
        : state === "pending" && (
            <p className="muted task-note" data-ws="quality-pending">
              {PENDING_LINE}
            </p>
          )}
      {state === "error" && "error" in (distributions ?? {}) && (
        <p className="muted task-note" data-ws="quality-dist-error">
          {(distributions as { error: string }).error}
        </p>
      )}
      {hasLit(overlay) && (
        <p className="muted task-note" data-ws="quality-overlay">
          {overlay.lit.length} bin{overlay.lit.length === 1 ? "" : "s"} selected (
          {overlay.lit.map((bin) => sourceName(bin)).join(", ")}) — the faces light up in the
          viewport once the face route is live
        </p>
      )}
      {metrics !== null && (
        <WorstFaces
          reply={distributions as { worst?: WorstFace[] }}
          order={order}
          titles={titles}
          units={Object.fromEntries(
            order.map((name) => [name, metrics[name]?.unit ?? ""] as const),
          )}
        />
      )}
      <div className="section-title">Statistics</div>
      <table className="task-table">
        <tbody>
          <StatRow label="Cells" value={quality.cells} />
          <StatRow label="Points" value={quality.points} />
          <StatRow label="Faces" value={quality.faces} />
          <StatRow label="Internal faces" value={quality.internal_faces} />
          <StatRow label="Boundary patches" value={quality.boundary_patches} />
          <StatRow label="Max non-orthogonality" value={quality.max_non_ortho} suffix="°" />
          <StatRow label="Max skewness" value={quality.max_skewness} />
          <StatRow label="Max aspect ratio" value={quality.max_aspect_ratio} />
        </tbody>
      </table>
      <div className="section-title">Cell types</div>
      <CellTypes cellTypes={cellTypesFromDistributions(distributions, quality)} />
    </div>
  );
}

/** The worst-faces list (brief M-507 §UI 4): the top 10 per metric, value and position. The
 * "Show" camera work is the viewer half; until it lands the rows are read-only. */
function WorstFaces({
  reply,
  order,
  titles,
  units,
}: {
  reply: { worst?: WorstFace[] };
  order: string[];
  titles: Record<string, string>;
  units: Record<string, string>;
}) {
  const sections = order
    .map((name) => ({ name, rows: worstFaces(reply, name) }))
    .filter((section) => section.rows.length > 0);
  if (sections.length === 0) return null;
  return (
    <>
      <div className="section-title">Worst faces</div>
      {sections.map((section) => (
        <div key={section.name} data-ws={`quality-worst-${section.name}`}>
          <p className="muted task-note">{titles[section.name] ?? section.name}</p>
          <table className="task-table">
            <tbody>
              {section.rows.map((row) => (
                <tr key={`${row.metric}-${row.face}`}>
                  <td className="muted">face {row.face}</td>
                  <td className="num">{worstValue(row.value, units[section.name] ?? "")}</td>
                  <td className="muted">at ({worstCentre(row.centre)})</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}

function StatRow({ label, value, suffix = "" }: { label: string; value: number | null | undefined; suffix?: string }) {
  return (
    <tr>
      <td className="muted">{label}</td>
      <td className="num">{value === null || value === undefined ? "—" : `${value.toLocaleString()}${suffix}`}</td>
    </tr>
  );
}

/** The cell types: the distributions' counts when they arrived, else checkMesh's (Part A). */
function cellTypesFromDistributions(
  distributions: Distributions | undefined,
  quality: CheckmeshNumbers,
): Record<string, number> | undefined {
  if (distributions !== undefined && "cell_types" in (distributions ?? {})) {
    return (distributions as { cell_types?: Record<string, number> }).cell_types;
  }
  return (quality as unknown as { cell_types?: Record<string, number> }).cell_types;
}

function CellTypes({ cellTypes }: { cellTypes: Record<string, number> | undefined }) {
  const rows = cellTypeRows(cellTypes);
  if (rows.length === 0) {
    return <p className="muted task-note">no cell types reported</p>;
  }
  return (
    <table className="task-table" data-ws="quality-cell-types">
      <tbody>
        {rows.map((row) => (
          <tr key={row.name}>
            <td className="muted">{row.name}</td>
            <td className="num">{row.count.toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}