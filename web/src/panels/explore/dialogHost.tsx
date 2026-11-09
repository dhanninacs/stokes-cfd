/**
 * Dialogs opened from code: the Explore actions (a ribbon button, the palette, a chord) must be able to
 * ask a question whether or not an Explore panel is on screen, so a dialog here is a React root of its
 * own, made when it opens and removed when it closes. The `Dialog` component draws into a portal on the
 * body, so the root's container never joins the page. Dialogs read the stores they need through hooks;
 * nothing is passed down from a provider because the app has none.
 */
import { createRoot } from "react-dom/client";
import type { ReactNode } from "react";

/** Open a dialog; `render` receives the call that closes it. Returns that same call. */
export function openDialog(render: (close: () => void) => ReactNode): () => void {
  const root = createRoot(document.createElement("div"));
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    // Not in the middle of the render or the event that asked for the close.
    window.setTimeout(() => root.unmount(), 0);
  };
  root.render(render(close));
  return close;
}
