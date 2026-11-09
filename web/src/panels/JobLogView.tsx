/**
 * The job log (T-217): the solver's and the mesher's own lines, a monospace scroller over the store's
 * `run.jobLog`. It follows new lines unless the reader scrolled up to look at something, the same
 * pattern as the console's scroller. Lines never enter the console: a run writes ten lines a second
 * for hours and would bury the commands.
 */
import { useLayoutEffect, useRef } from "react";
import { useStore } from "../state/store";
import { useFrameValue } from "./useFrameValue";
import "./runPanel.css";

export default function JobLogView({ inPanel = false }: { inPanel?: boolean }) {
  const lines = useFrameValue(useStore((s) => s.run.jobLog));
  const scroller = useRef<HTMLPreElement>(null);
  const follow = useRef(true);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el !== null && follow.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <pre
      ref={scroller}
      role="log"
      aria-label="Job log"
      tabIndex={0}
      className={`job-log ${inPanel ? "in-panel" : ""}`}
      data-ws="job-log"
      onScroll={(e) => {
        const el = e.currentTarget;
        follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
    >
      {lines.length === 0 ? <span className="muted" title="The solver's own lines appear here while a run or a mesh job is going." aria-description="The solver's own lines appear here while a run or a mesh job is going.">No lines yet</span> : lines.join("\n")}
    </pre>
  );
}
