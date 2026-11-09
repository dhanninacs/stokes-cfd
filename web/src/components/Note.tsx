/**
 * A short muted line: a few words on the screen, the sentence in the tooltip.
 *
 * The owner's rule (2026-11-01): no long sentences in the GUI. `tip` is the explanation, shown on hover and
 * read by assistive tech (`aria-description`), so a page stays as sparse as a Discovery panel. Both modes use
 * it: the guided cards (`guided/controls.tsx` re-exports it) and the Full-mode panels.
 *
 * `userText.test.ts` fails on any literal JSX text over 8 words in `panels/**`, so a longer sentence has to
 * move into `tip`.
 */
import type { ReactNode } from "react";
import "./Note.css";

export function Note({
  children,
  tip,
  className,
  "data-ws": dataWs,
}: {
  children: ReactNode;
  tip?: string;
  className?: string;
  "data-ws"?: string;
}) {
  return (
    <p className={`note-line guided-note${className ? ` ${className}` : ""}`} title={tip} aria-description={tip} data-ws={dataWs}>
      {children}
    </p>
  );
}
