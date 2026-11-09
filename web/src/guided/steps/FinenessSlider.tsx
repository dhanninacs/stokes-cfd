import { useEffect, useState } from "react";
import { FINENESS, MARKS, baseSizeAt, lengthText, parseCellSize, positionOf, type Box, type Fineness } from "../sizing";

const WORDS: Record<Fineness, string> = { coarse: "Coarse", medium: "Medium", fine: "Fine" };
const STEPS = 1000;

/**
 * How fine the mesh is, as a slider with Coarse, Medium and Fine marked on it and a box to type the cell size.
 *
 * The slider is a log scale over the base cell size (`sizing.ts`): the three marks are the presets, and a
 * position between them is a size between. The value is committed when the thumb is released (or a key is
 * lifted), not on every pixel, because each commit is a settings change and an undo step. The box takes a size
 * in millimetres, or with `cm` / `m`; a size that would give an absurd cell count is refused with a line.
 */
export default function FinenessSlider({
  box,
  baseSize,
  disabled,
  onCommit,
}: {
  box: Box | null;
  baseSize: number | null;
  disabled: boolean;
  /** Called with the base size in metres. */
  onCommit: (size: number) => void;
}) {
  const known = positionOf(box, baseSize);
  const [drag, setDrag] = useState<number | null>(null);
  const [typed, setTyped] = useState<string | null>(null);
  const [problem, setProblem] = useState("");
  const position = drag ?? known ?? 0.5;
  const shown = drag !== null ? baseSizeAt(box, drag) : baseSize;
  const unusable = disabled || box === null;

  useEffect(() => setDrag(null), [baseSize]);

  const commitSlider = () => {
    if (drag === null) return;
    const size = baseSizeAt(box, drag);
    if (size !== null && size !== baseSize) onCommit(size);
    else setDrag(null);
  };
  const commitTyped = () => {
    if (typed === null) return;
    const size = parseCellSize(typed, box);
    setTyped(null);
    if (size === null) {
      setProblem("Give a size such as 2.5 or 0.4 cm.");
      return;
    }
    setProblem("");
    if (size !== baseSize) onCommit(size);
  };

  return (
    <div className="fineness" data-ws="fineness">
      <div className="fineness-head">
        <span className="guided-label">How fine</span>
        <label className="fineness-entry">
          <input
            className="field mono"
            inputMode="decimal"
            spellCheck={false}
            autoComplete="off"
            aria-label="Cell size in millimetres"
            disabled={unusable}
            value={typed ?? (shown === null ? "" : String(Number((shown * 1000).toPrecision(3))))}
            onChange={(e) => setTyped(e.target.value)}
            onBlur={commitTyped}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitTyped();
              else if (e.key === "Escape") setTyped(null);
            }}
          />
          <span className="muted">mm</span>
        </label>
      </div>
      <input
        type="range"
        className="fineness-slider"
        min={0}
        max={STEPS}
        value={Math.round(position * STEPS)}
        disabled={unusable}
        aria-label="How fine the mesh is"
        aria-valuetext={shown === null ? undefined : `${lengthText(shown)} cells`}
        onChange={(e) => setDrag(Number(e.target.value) / STEPS)}
        onPointerUp={commitSlider}
        onKeyUp={commitSlider}
        onBlur={commitSlider}
        data-ws="fineness-slider"
      />
      <div className="fineness-marks" aria-hidden="true">
        {FINENESS.map((f) => (
          <span key={f} style={{ left: `${MARKS[f] * 100}%` }}>
            {WORDS[f]}
          </span>
        ))}
      </div>
      {box === null && <p className="guided-line muted">The size of the fluid is not known yet.</p>}
      {problem !== "" && (
        <p className="guided-line" role="alert" style={{ color: "var(--danger)" }}>
          {problem}
        </p>
      )}
    </div>
  );
}
