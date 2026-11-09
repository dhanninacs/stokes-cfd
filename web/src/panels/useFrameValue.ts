import { useEffect, useRef, useState } from "react";

/**
 * The value as of the last animation frame: a store that changes ten times a second (the residual
 * history, the job log) redraws a chart or a log at most once per frame, never once per event.
 * The first value is shown at once; a hidden page pauses the frames and so pauses the redraws.
 */
export function useFrameValue<T>(value: T): T {
  const [shown, setShown] = useState(value);
  const latest = useRef(value);
  const frame = useRef(0);
  latest.current = value;

  useEffect(() => {
    if (frame.current !== 0) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      setShown(latest.current);
    });
  }, [value]);

  useEffect(
    () => () => {
      if (frame.current !== 0) cancelAnimationFrame(frame.current);
      frame.current = 0;
    },
    [],
  );
  return shown;
}
