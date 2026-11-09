/**
 * The bubble that names the point under the pointer in a plot. It sits above the point, flips below when
 * there is no room above, and stays inside the plot's box whatever the width of its text. Its parent is
 * the positioned box the point's coordinates are measured in.
 */
import { useLayoutEffect, useRef } from "react";

export function PlotTip({ x, y, text }: { x: number; y: number; text: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    const box = el?.parentElement;
    if (!el || !box) return;
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    const room = box.clientWidth;
    const left = Math.min(Math.max(x - width / 2, 4), Math.max(4, room - width - 4));
    const top = y - height - 12 < 4 ? y + 14 : y - height - 12;
    el.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  });
  return (
    <span ref={ref} className="pl-tip" role="tooltip">
      {text}
    </span>
  );
}
