/**
 * The Section control (T-065): turn a section plane on, choose the axis, and slide the cut through
 * the model.
 *
 * **A popover, not a menu.** The axis is three exclusive choices, which a menu does well, but the
 * offset is a continuous value the user drags while watching the viewport — and a menu row cannot
 * hold a slider that stays under the pointer. So the whole control is one small dialog, the shape
 * `StylePopover` and `DisplaySettings` already use, with the axis as three buttons and the offset as a
 * slider.
 *
 * **Invert keeps the other half.** A clip plane is a half-space, so flipping is the normal's sign
 * and the cut stays exactly where the user put it — see `sectionState.invertSection`.
 *
 * **The slider's range is the model's own bounds.** The store holds the section in metres and knows
 * nothing about the geometry, so the range has to come from the viewport: `onRange` below is given the
 * extent the scene reports for the axis in use, and the slider spans it. Without that the control would
 * have to guess a range from the model's size, and a 300 mm pipe and a 3 m duct would need different
 * guesses — a slider that cannot reach the ends of the model is worse than no slider.
 *
 * Nothing here is journalled: the section is view state, as `viewMode` and `orthographic` are, so it
 * takes no undo step and writes no API command (see `SectionPlane` in the store).
 */
import { useEffect, useRef, useState } from "react";

import { FlipHorizontal } from "lucide-react";

import { Popover, type CloseReason } from "../components/Menu";
import { startingPosition } from "../state/slicePlane";
import { useStore, type SectionAxis } from "../state/store";

const AXES: { id: SectionAxis; label: string }[] = [
  { id: "x", label: "X" },
  { id: "y", label: "Y" },
  { id: "z", label: "Z" },
];

/** A round step for the slider, so the readout is a figure someone can type back. */
const STEP_MM = 0.5;

interface SectionPopoverProps {
  open: boolean;
  anchor: React.RefObject<HTMLElement | null>;
  /** The model's extent along an axis, in metres: `[min, max]`, or null while nothing is drawn. */
  range: (axis: SectionAxis) => [number, number] | null;
  onClose: (reason: CloseReason) => void;
}

export function SectionPopover({ open, anchor, range, onClose }: SectionPopoverProps) {
  const section = useStore((s) => s.section);
  const setSection = useStore((s) => s.setSection);
  const putSectionAt = useStore((s) => s.putSectionAt);
  const setSectionPlane = useStore((s) => s.setSectionPlane);
  const setSectionInvert = useStore((s) => s.setSectionInvert);
  const [axis, setAxis] = useState<SectionAxis>(section?.axis ?? "z");
  const [offsetMm, setOffsetMm] = useState(0);
  const [text, setText] = useState("0");
  const typing = useRef(false);

  // What the store holds drives the controls, and the axis follows the plane when it changes.
  useEffect(() => {
    if (section === null) return;
    if (!typing.current) {
      setAxis(section.axis);
      const mm = Math.round((section.offset * 1000) / STEP_MM) * STEP_MM;
      setOffsetMm(mm);
      setText(String(Math.round(mm * 10) / 10));
    }
  }, [section]);

  // The range for the axis in use, from the scene. Re-read when the axis changes, because each axis
  // has its own extent — a flat plate is wide in X and Y and thin in Z.
  const extent = open ? range(axis) : null;
  const min = extent === null ? -500 : Math.floor((extent[0] * 1000) / STEP_MM) * STEP_MM;
  const max = extent === null ? 500 : Math.ceil((extent[1] * 1000) / STEP_MM) * STEP_MM;
  const span = Math.max(STEP_MM, max - min);

  /*
    **Opening the control STARTS a section, in one write** (G-301's D6).

    Measured: pressing Section opened the card and started nothing. A user saw an axis highlighted and a
    plane on screen - the card draws one - left the card, and found "From the section plane" in the
    Results panel still greyed out, because no section existed. The button says "Section", not "Section
    options", so pressing it should cut.

    **`putSectionAt`, not `setSection` + `setSectionPlane`.** Two writes leave `DEFAULT_SECTION` - axis z
    at the world origin - in the store for one tick, and the slice layer fetches it: measured, two
    `404 plane=0,0,0,0,0,1` on every open. One write means the first plane the viewport sees is the right
    one.

    Waiting for a real extent matters too: on the first frame the scene has not reported its bounds, and
    `startingPosition(null)` is 0 - the same origin, the same 404.
  */
  /*
    **Only the OPENING starts a section, not every moment the control is open without one** (2026-11-03,
    measured on pipe3: "Remove section" wrote `null`, this effect saw `open && section === null` on the
    next render and put the plane straight back, so the cut never left and the control looked stuck).
    `pendingStart` is armed when the control opens with no section and is spent by the first write;
    Remove clears it and closes the control, so the cut it removed stays removed.
  */
  const pendingStart = useRef(false);
  // Whether the previous render had the control open: the start below is armed on that transition only
  // (the effect that records it is declared after the start, so the start reads the previous render's value).
  const wasOpen = useRef(false);
  useEffect(() => {
    if (!open) {
      pendingStart.current = false;
      return;
    }
    if (section !== null) {
      pendingStart.current = false;
      return;
    }
    if (!wasOpen.current) pendingStart.current = true;
    if (!pendingStart.current) return;
    const bounds = range(axis);
    if (bounds === null) return;
    pendingStart.current = false;
    putSectionAt(axis, startingPosition(bounds, STEP_MM) / 1000);
  }, [open, section, axis, range, putSectionAt]);
  useEffect(() => {
    wasOpen.current = open;
  }, [open]);

  const write = (nextAxis: SectionAxis, nextMm: number) => {
    const clamped = Math.min(max, Math.max(min, nextMm));
    setOffsetMm(clamped);
    setText(String(Math.round(clamped * 10) / 10));
    setSectionPlane(nextAxis, clamped / 1000);
  };

  const commit = () => {
    typing.current = false;
    const parsed = Number(text.trim());
    if (!Number.isFinite(parsed)) {
      setText(String(Math.round(offsetMm * 10) / 10));
      return;
    }
    write(axis, parsed);
  };

  return (
    <Popover
      open={open}
      anchor={anchor}
      onClose={onClose}
      role="dialog"
      label="Section view"
      className="section-popover"
      focusSelector="[data-section-axis]"
    >
      <div className="sp-row sp-colours" role="group" aria-label="Section axis">
        {AXES.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className="sp-swatch section-axis"
            data-section-axis={axis === entry.id ? true : undefined}
            aria-label={`Section plane normal along ${entry.label}`}
            aria-pressed={axis === entry.id}
            onClick={() => {
              setAxis(entry.id);
              // Turning the section on with an axis press: a user who opens this control and presses Z
              // means "cut along Z", not "choose Z for later". Starting from the model's middle shows
              // the cut immediately rather than leaving the model whole until the slider is touched.
              //
              // **`range(entry.id)`, NOT `extent`** (G-301 step 10). `extent` is `range(axis)` for the
              // axis chosen on the PREVIOUS render, and before any section exists it is null - so
              // `middle` was 0 and the plane was written at the WORLD ORIGIN. Measured: a slice added
              // "from the section plane" then requested `plane=0,0,0,0,0,1` and the server answered
              // 404, because the origin lies outside the model. Ask the axis being chosen for its own
              // extent and the plane lands in the middle of the model.
              const middle = startingPosition(range(entry.id), STEP_MM);
              setSection(true);
              write(entry.id, middle);
            }}
          >
            {entry.label}
          </button>
        ))}
      </div>
      <div className="sp-row">
        <span className="sp-label">Offset</span>
        <input
          type="range"
          className="slider"
          min={min}
          max={max}
          step={STEP_MM}
          value={offsetMm}
          aria-label="Section offset in millimetres"
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={Math.round(offsetMm * 10) / 10}
          onChange={(e) => {
            // Dragging writes as it moves: the cut follows the pointer, which is the point of a
            // section slider. It is view state, so this is not one undo step per pixel of anything.
            const next = Number(e.target.value);
            typing.current = false;
            setOffsetMm(next);
            setText(String(Math.round(next * 10) / 10));
            setSectionPlane(axis, next / 1000);
          }}
        />
        <span className="sp-pct">
          <input
            className="field mono"
            inputMode="numeric"
            value={text}
            aria-label="Section offset in millimetres"
            onFocus={() => {
              typing.current = true;
            }}
            onChange={(e) => {
              typing.current = true;
              setText(e.target.value);
            }}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              }
            }}
          />
          <span aria-hidden="true">mm</span>
        </span>
      </div>
      <div className="sp-row sp-foot">
        <button
          type="button"
          className="btn ghost section-invert"
          aria-pressed={section?.invert === true}
          aria-label="Invert the section: keep the other half"
          disabled={section === null}
          onClick={() => setSectionInvert(!(section?.invert ?? false))}
        >
          <FlipHorizontal size={13} /> Invert
        </button>
        <button
          type="button"
          className="btn ghost"
          aria-pressed={section !== null}
          disabled={section === null}
          onClick={() => {
            pendingStart.current = false;
            setSection(false);
            onClose("select");
          }}
        >
          {section === null ? "No section" : "Remove section"}
        </button>
      </div>
      <p className="section-hint" aria-hidden="true">
        Drag to move the cut. Span {Math.round(span)} mm.
      </p>
    </Popover>
  );
}

export default SectionPopover;
