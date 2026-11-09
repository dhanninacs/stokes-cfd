import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { useStore } from "../state/store";
import { IconButton } from "../components/ui";
import { readPref, writePref } from "./prefs";

const SEEN_KEY = "hintSeen";
const SHOW_AFTER_MS = 900;
const HIDE_AFTER_MS = 30_000;
/** A press that moves this far on the canvas is a drag (an orbit or a pan), not a click. */
const DRAG_PX = 4;

/**
 * One-time getting-started card at the bottom centre. It names the three camera gestures, which the
 * empty Properties card does not (it carries the shortcuts). It counts as seen the moment it
 * appears, so it never returns; it goes away on its own after 30 s, when dismissed, or as soon as
 * the person does one of the things it names: drags the view, or scrolls it.
 */
export default function FirstRunHint() {
  const [visible, setVisible] = useState(false);
  const dismiss = useCallback(() => setVisible(false), []);

  useEffect(() => {
    if (readPref(SEEN_KEY) === "1") return;
    const timer = window.setTimeout(() => {
      writePref(SEEN_KEY, "1");
      setVisible(true);
    }, SHOW_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!visible) return;
    const timer = window.setTimeout(dismiss, HIDE_AFTER_MS);
    // "Fit" is a camera move too, so the F key and the Fit button count as having found the camera.
    const unsubscribe = useStore.subscribe((state, previous) => {
      if (state.fitRequest !== previous.fitRequest) dismiss();
    });
    let down: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => {
      down = e.target instanceof HTMLCanvasElement ? { x: e.clientX, y: e.clientY } : null;
    };
    const onMove = (e: PointerEvent) => {
      if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > DRAG_PX) dismiss();
    };
    const onUp = () => {
      down = null;
    };
    const onWheel = (e: WheelEvent) => {
      if (e.target instanceof HTMLCanvasElement) dismiss();
    };
    window.addEventListener("pointerdown", onDown, true);
    window.addEventListener("pointermove", onMove, true);
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("pointercancel", onUp, true);
    window.addEventListener("wheel", onWheel, { capture: true, passive: true });
    return () => {
      window.clearTimeout(timer);
      unsubscribe();
      window.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointercancel", onUp, true);
      window.removeEventListener("wheel", onWheel, true);
    };
  }, [visible, dismiss]);

  if (!visible) return null;
  return (
    <div className="hint-card glass" role="note" aria-label="Getting started">
      <span>
        <b>Drag</b> to orbit · <b>right-drag</b> to pan · <b>scroll</b> to zoom
      </span>
      <IconButton label="Dismiss hint" className="!h-6 !w-6 shrink-0" onClick={dismiss}>
        <X size={13} />
      </IconButton>
    </div>
  );
}
