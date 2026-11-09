import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

interface SplitHandleProps {
  /** `horizontal`: between stacked panels (drag up and down); `vertical`: between panels side by side. */
  orientation: "horizontal" | "vertical";
  /** Accessible name, e.g. "Resize Simulation tree". */
  label: string;
  /** Size of the panel before the handle (its height, or its width), px. */
  value: number;
  min: number;
  max: number;
  /** Live updates while dragging. */
  onChange: (size: number) => void;
  /** Gesture finished (pointer up or a key): write it back. */
  onCommit: (size: number) => void;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * The separator between two panels of a split slot. It lives in the gap before the second panel and
 * sizes the first: pointer drag with capture, arrow keys (Shift for larger steps), Home and End for
 * the limits. Same behaviour as the slot's ResizeHandle, in either direction.
 */
export function SplitHandle({ orientation, label, value, min, max, onChange, onCommit }: SplitHandleProps) {
  const drag = useRef<{ at: number; start: number; last: number } | null>(null);
  const [active, setActive] = useState(false);
  const vertical = orientation === "vertical";

  const finish = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    setActive(false);
    document.body.classList.remove(vertical ? "is-resizing" : "is-resizing-rows");
    if (d.last !== d.start) onCommit(d.last);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { at: vertical ? e.clientX : e.clientY, start: value, last: value };
    setActive(true);
    document.body.classList.add(vertical ? "is-resizing" : "is-resizing-rows");
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    d.last = Math.round(clamp(d.start + (vertical ? e.clientX : e.clientY) - d.at, min, max));
    onChange(d.last);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 48 : 16;
    const grow = vertical ? "ArrowRight" : "ArrowDown";
    const shrink = vertical ? "ArrowLeft" : "ArrowUp";
    let next: number | null = null;
    if (e.key === grow) next = value + step;
    else if (e.key === shrink) next = value - step;
    else if (e.key === "Home") next = min;
    else if (e.key === "End") next = max;
    if (next === null) return;
    e.preventDefault();
    onCommit(clamp(Math.round(next), min, max));
  };

  return (
    <div
      role="separator"
      aria-orientation={vertical ? "vertical" : "horizontal"}
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      className={`split-handle no-drag ${vertical ? "split-handle-v" : "split-handle-h"} ${active ? "active" : ""}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={finish}
      onKeyDown={onKeyDown}
    />
  );
}
