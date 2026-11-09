import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

interface ResizeHandleProps {
  /** Accessible name, e.g. "Resize the simulation tree". */
  label: string;
  value: number;
  min: number;
  max: number;
  /** +1 when dragging right makes the panel wider (handle on its right edge), -1 for the left edge. */
  grow: 1 | -1;
  /** Live updates while dragging or after an arrow key. */
  onChange: (width: number) => void;
  /** Gesture finished: persist. */
  onCommit: (width: number) => void;
  /** Double click: back to the default. */
  onReset: () => void;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Vertical separator between a floating panel and the viewport. Pointer drag with capture,
 * arrow keys (Shift for larger steps), Home/End for the limits, double click to reset.
 */
export default function ResizeHandle({ label, value, min, max, grow, onChange, onCommit, onReset }: ResizeHandleProps) {
  const drag = useRef<{ x: number; start: number; last: number } | null>(null);
  const [active, setActive] = useState(false);

  const finish = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    setActive(false);
    document.body.classList.remove("is-resizing");
    // a plain click must not overwrite the stored width with a window-limited one
    if (d.last !== d.start) onCommit(d.last);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, start: value, last: value };
    setActive(true);
    document.body.classList.add("is-resizing");
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    d.last = clamp(d.start + grow * (e.clientX - d.x), min, max);
    onChange(d.last);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 48 : 16;
    let next: number | null = null;
    if (e.key === "ArrowRight") next = value + grow * step;
    else if (e.key === "ArrowLeft") next = value - grow * step;
    else if (e.key === "Home") next = min;
    else if (e.key === "End") next = max;
    if (next === null) return;
    e.preventDefault();
    onCommit(clamp(next, min, max));
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      className={`resize-handle no-drag ${active ? "active" : ""}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={finish}
      onDoubleClick={onReset}
      onKeyDown={onKeyDown}
    />
  );
}
