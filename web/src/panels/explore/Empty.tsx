/** An empty state of an Explore panel: one line that says what to do, and the button that does it. No illustration. */
import type { ReactNode } from "react";

/** `tip` is the sentence behind the few words (the owner's rule: the explanation is a tooltip). */
export function Empty({ children, action, tip }: { children: ReactNode; action?: ReactNode; tip?: string }) {
  return (
    <div className="ex-empty">
      <p className="muted" title={tip} aria-description={tip}>
        {children}
      </p>
      {action}
    </div>
  );
}
