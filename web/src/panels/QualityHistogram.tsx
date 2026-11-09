/**
 * One histogram, as SVG, with the project's limit as a vertical rule (brief M-507 §UI 2).
 *
 * **Tokens only**: the bars and the axis use the theme's own variables, so the dark look needs
 * no re-colour. The bars past the limit are in the warning token. A bar is focusable (a button
 * in an SVG group), and Enter/click asks the page for that bin's faces.
 */
import { BarView } from "./qualityModel";

export interface HistogramProps {
  bars: BarView[];
  /** The axis's label ("deg", or "ratio" for a dimensionless metric). */
  unit: string;
  /** The project's limit, drawn as a vertical rule; null draws no rule. */
  limit: number | null;
  /** The x range to draw: the metric's own extent, or the caller's (log axes pass log values). */
  xMin: number;
  xMax: number;
  onBar?: (from: number, to: number) => void;
}

const HEIGHT = 120;
const PAD_BOTTOM = 18;
const PAD_LEFT = 6;
const PAD_RIGHT = 6;

export default function QualityHistogram({
  bars,
  unit,
  limit,
  xMin,
  xMax,
  onBar,
}: HistogramProps) {
  const width = 280;
  const innerWidth = width - PAD_LEFT - PAD_RIGHT;
  const plotHeight = HEIGHT - PAD_BOTTOM;
  const maxCount = Math.max(1, ...bars.map((b) => b.count));
  const span = xMax - xMin || 1;
  const xOf = (value: number) => PAD_LEFT + ((value - xMin) / span) * innerWidth;
  const barWidth =
    bars.length > 0 ? Math.max(1, (xOf(bars[0].to) - xOf(bars[0].from)) - 1) : innerWidth;

  return (
    <svg
      width={width}
      height={HEIGHT}
      role="img"
      aria-label={`Histogram of ${unit}`}
      data-ws="quality-histogram"
      style={{ display: "block" }}
    >
      {bars.map((bar) => {
        const height = (bar.count / maxCount) * (plotHeight - 4);
        const x = xOf(bar.from);
        const y = plotHeight - height;
        return (
          <g
            key={bar.from}
            role="button"
            tabIndex={0}
            aria-label={`${bar.count} cells between ${trim(bar.from)} and ${trim(bar.to)} ${unit}`}
            style={{ cursor: "pointer", outline: "none" }}
            onClick={() => onBar?.(bar.from, bar.to)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onBar?.(bar.from, bar.to);
            }}
          >
            <title>{`${bar.count} cells between ${trim(bar.from)} and ${trim(bar.to)} ${unit}`}</title>
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={height}
              fill={bar.overLimit ? "var(--warning, #c47f3b)" : "var(--accent, #3b82c4)"}
              opacity={0.85}
            />
          </g>
        );
      })}
      {/* the x axis */}
      <line
        x1={PAD_LEFT}
        y1={plotHeight}
        x2={width - PAD_RIGHT}
        y2={plotHeight}
        stroke="var(--panel-border)"
      />
      <text x={PAD_LEFT} y={HEIGHT - 5} fontSize={9} fill="var(--text-muted)">
        {trim(xMin)}
      </text>
      <text x={width - PAD_RIGHT} y={HEIGHT - 5} fontSize={9} fill="var(--text-muted)" textAnchor="end">
        {trim(xMax)} {unit}
      </text>
      {/* the project's limit, as a vertical rule labelled "limit 65°" */}
      {limit !== null && limit >= xMin && limit <= xMax && (
        <>
          <line
            x1={xOf(limit)}
            y1={0}
            x2={xOf(limit)}
            y2={plotHeight}
            stroke="var(--text-muted)"
            strokeDasharray="3 3"
          />
          <text x={xOf(limit) + 3} y={10} fontSize={9} fill="var(--text-muted)">
            limit {trim(limit)}
          </text>
        </>
      )}
    </svg>
  );
}

function trim(value: number): string {
  return String(parseFloat(value.toFixed(2)));
}