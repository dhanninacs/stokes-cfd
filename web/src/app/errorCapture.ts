/**
 * The stack of an error nobody caught, in the log. The desktop host writes the page's console to its log as
 * `js: <message>`, and an uncaught error reaches it as its message alone ("Uncaught TypeError: Cannot read
 * properties of undefined (reading 'length')", Windows walkthrough 2026-11-13), which says nothing about where.
 * This writes the stack after it, once per distinct error, so the next one can be found. It changes nothing
 * else: the error still reaches the console as before.
 */

/** The line logged for an uncaught error or rejection, or null when there is nothing more to say than the message. */
export function errorReport(kind: "error" | "rejection", reason: unknown): string | null {
  if (!(reason instanceof Error) || reason.stack === undefined || reason.stack === "") return null;
  return `[stokes] uncaught ${kind === "error" ? "error" : "promise rejection"}: ${reason.stack}`;
}

const MAX_REPORTS = 20;
let installed = false;

/** Listens for uncaught errors and rejections for the life of the page; safe to call more than once. */
export function installErrorCapture(target: Pick<Window, "addEventListener"> = window): void {
  if (installed) return;
  installed = true;
  const seen = new Set<string>();
  const report = (line: string | null) => {
    if (line === null || seen.has(line) || seen.size >= MAX_REPORTS) return;
    seen.add(line);
    console.error(line);
  };
  target.addEventListener("error", (e: ErrorEvent) => report(errorReport("error", e.error)));
  target.addEventListener("unhandledrejection", (e: PromiseRejectionEvent) => report(errorReport("rejection", e.reason)));
}
